"""Сервис работы с инстансами workflow.

Запуск, чтение состояния, отмена и форсированное завершение. Все изменения
состояния идут через журнал: прямой `UPDATE status` сделал бы инстанс
невосстанавливаемым (§3.1 AGENTS.md).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.engine import scheduler
from app.engine.journal import (
    RecordedEvent,
    append_event,
    read_events,
    read_events_page,
    replay,
    steps_to_compensate,
)
from app.models.enums import ACTIVE_STATUSES, TERMINAL_STATUSES, EventType, InstanceStatus
from app.models.workflow import StepRun, WorkflowDefinition, WorkflowInstance

_EPOCH = datetime.min.replace(tzinfo=UTC)


async def create_instance(
    db_session: AsyncSession,
    definition_id: uuid.UUID,
    input_data: dict,
) -> WorkflowInstance:
    """Создаёт инстанс и пишет первое событие журнала.

    Событие `INSTANCE_CREATED` обязательно: без него replay не знал бы, с какого
    шага начинать, и инстанс остался бы неисполнимым.
    """
    definition = await db_session.get(WorkflowDefinition, definition_id)
    if definition is None:
        raise NotFoundError(f"определение {definition_id} не найдено")
    if not definition.is_active:
        raise ConflictError(
            f"определение {definition.name!r} версии {definition.version} неактивно"
        )

    instance = WorkflowInstance(definition_id=definition_id, input=input_data, variables={})
    db_session.add(instance)
    await db_session.flush()

    start = definition.definition.get("start")
    await append_event(db_session, instance.id, EventType.INSTANCE_CREATED, {"start": start})
    return instance


async def get_instance(db_session: AsyncSession, instance_id: uuid.UUID) -> WorkflowInstance:
    instance = await db_session.get(WorkflowInstance, instance_id)
    if instance is None:
        raise NotFoundError(f"инстанс {instance_id} не найден")
    return instance


async def list_instances(
    db_session: AsyncSession,
    limit: int,
    offset: int,
    status: str | None,
    definition_id: uuid.UUID | None,
) -> tuple[list[tuple[WorkflowInstance, WorkflowDefinition]], int]:
    """Список инстансов вместе с дефинишенами.

    Соединение делается одним запросом: дефинишена нужна каждой строке списка,
    а поштучная догрузка дала бы N+1 на страницу.
    """
    filters = []
    if status is not None:
        filters.append(WorkflowInstance.status == status)
    if definition_id is not None:
        filters.append(WorkflowInstance.definition_id == definition_id)

    rows = (
        await db_session.execute(
            select(WorkflowInstance, WorkflowDefinition)
            .join(WorkflowDefinition, WorkflowInstance.definition_id == WorkflowDefinition.id)
            .where(*filters)
            .order_by(WorkflowInstance.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()

    total = await db_session.scalar(
        select(func.count()).select_from(WorkflowInstance).where(*filters)
    )
    return list(rows), total or 0


async def get_events(
    db_session: AsyncSession, instance_id: uuid.UUID, from_seq: int, limit: int
) -> tuple[list[RecordedEvent], int]:
    """Журнал инстанса начиная с указанного события.

    Второе значение — общее число событий инстанса, а не число от смещения:
    клиенту нужен размер выборки для догрузки по `from_seq`.

    Предел применяется в запросе, а не после чтения. Читать весь журнал ради
    первых двадцати строк — значит держать в памяти всю историю процесса,
    которая у долгого инстанса растёт месяцами.
    """
    await get_instance(db_session, instance_id)
    events, total = await read_events_page(db_session, instance_id, from_seq=from_seq, limit=limit)
    return events, total


async def get_steps(
    db_session: AsyncSession, instance_id: uuid.UUID, limit: int = 500
) -> list[StepRun]:
    """Прогоны шагов инстанса, не больше `limit` записей.

    Предел по умолчанию тот же, что у таймлайна: без него выборка у процесса
    с циклом на сотни проходов растила бы ответ без нужды.
    """
    await get_instance(db_session, instance_id)
    rows = await db_session.scalars(
        select(StepRun)
        .where(StepRun.instance_id == instance_id)
        .order_by(StepRun.started_at, StepRun.attempt)
        .limit(limit)
    )
    return list(rows)


async def get_timeline(db_session: AsyncSession, instance_id: uuid.UUID, limit: int) -> list[dict]:
    """Единый поток для интерфейса: шаги, события и компенсации по времени.

    Каждая выборка ограничена: лента показывает последние записи, а читать
    ради них весь журнал процесса незачем. Мержится по времени, поэтому
    ограничение каждой части даёт корректный результат — часть, отрезанная по
    времени, всё равно не могла бы попасть в начало ленты.
    """
    await get_instance(db_session, instance_id)

    entries: list[dict] = []

    for run in await get_steps(db_session, instance_id, limit):
        entries.append(
            {
                "kind": "step",
                "step_name": run.step_name,
                "attempt": run.attempt,
                "status": run.status,
                "started_at": run.started_at,
                "finished_at": run.finished_at,
                "error": run.error,
                "compensated": run.compensated,
            }
        )

    page, _ = await read_events_page(db_session, instance_id, from_seq=0, limit=limit)
    for event in page:
        entries.append(
            {
                "kind": "event",
                "seq": event.seq,
                "type": event.type.value,
                "payload": event.payload,
                "created_at": event.created_at,
            }
        )

    entries.sort(key=_entry_time)
    return entries[:limit]


def _entry_time(entry: dict) -> datetime:
    """Время записи таймлайна: у события это создание, у шага — старт."""
    return entry.get("created_at") or entry.get("started_at") or _EPOCH


async def cancel_instance(db_session: AsyncSession, instance_id: uuid.UUID) -> WorkflowInstance:
    """Запрашивает отмену: инстанс переходит в откат.

    Отмена не означает остановку: выполненные шаги надо вернуть, поэтому
    инстанс уходит в `CANCELLING`, а откат выполняет воркер. Терминальный
    `CANCELLED` наступает позже и только если компенсировать было нечего.

    Ожидания снимаются здесь: оставленный таймер разбудил бы уже
    откатываемый процесс (§3.5).
    """
    instance = await get_instance(db_session, instance_id)
    if InstanceStatus(instance.status) in _terminal_statuses():
        raise ConflictError(f"инстанс в статусе {instance.status} отменить нельзя")

    await scheduler.cancel_for(db_session, instance_id)
    await append_event(db_session, instance.id, EventType.INSTANCE_CANCEL_REQUESTED, {})
    instance.status = InstanceStatus.CANCELLING
    return instance


async def force_complete(
    db_session: AsyncSession, instance_id: uuid.UUID, reason: str
) -> WorkflowInstance:
    """Завершает инстанс без компенсаций.

    Требует причины: приёмка в обход компенсаций оставляет побочные эффекты, и
    это должно остаться в данных, а не потеряться.
    """
    instance = await get_instance(db_session, instance_id)
    if InstanceStatus(instance.status) in _terminal_statuses():
        raise ConflictError(f"инстанс в статусе {instance.status} уже завершён")

    await scheduler.cancel_for(db_session, instance_id)
    await append_event(
        db_session, instance.id, EventType.INSTANCE_COMPLETED, {"forced": True, "reason": reason}
    )
    instance.status = InstanceStatus.COMPLETED
    instance.current_step = None
    return instance


async def retry_compensation(db_session: AsyncSession, instance_id: uuid.UUID) -> WorkflowInstance:
    """Перезапускает откат после `COMPENSATION_FAILED`."""
    instance = await get_instance(db_session, instance_id)
    if InstanceStatus(instance.status) is not InstanceStatus.COMPENSATION_FAILED:
        raise ConflictError(f"откат не требовался: статус {instance.status}")

    # Статус меняется только через журнал (§3.1). Прямая запись в колонку дала
    # бы replay'у состояние COMPENSATION_FAILED навсегда: нового события нет, а
    # воркер, взявший инстанс после перезапуска, увидел бы отказ и не стал бы
    # повторять компенсацию.
    await append_event(db_session, instance_id, EventType.COMPENSATION_STARTED, {"retried": True})
    instance.status = InstanceStatus.COMPENSATING
    instance.error = None
    await db_session.flush()
    return instance


async def rebuild_state(db_session: AsyncSession, instance_id: uuid.UUID) -> dict:
    """Состояние инстанса по журналу — то, что увидит воркер после подхвата."""
    events = await read_events(db_session, instance_id)
    state = replay(events)
    return {
        "status": state.status,
        "current_step": state.current_step,
        "variables": state.variables,
        "completed_steps": state.completed_steps,
        "pending_compensations": steps_to_compensate(state),
        "is_terminal": state.is_terminal,
        "step_iterations": state.step_iterations,
    }


def _terminal_statuses() -> frozenset[InstanceStatus]:
    return frozenset(TERMINAL_STATUSES)


def active_statuses() -> frozenset[str]:
    """Активные статусы строкой — для фильтров в списках и метриках."""
    return frozenset(str(status) for status in ACTIVE_STATUSES)
