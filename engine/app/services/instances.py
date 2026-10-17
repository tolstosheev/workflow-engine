"""Сервис работы с инстансами workflow.

Запуск, чтение состояния, отмена и форсированное завершение. Все изменения
состояния идут через журнал: прямой `UPDATE status` сделал бы инстанс
невосстанавливаемым (§3.1 AGENTS.md).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

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
from app.models.workflow import StepRun, WorkflowDefinition, WorkflowEvent, WorkflowInstance

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
    archived: str = "active",
) -> tuple[list[tuple[WorkflowInstance, WorkflowDefinition]], int]:
    """Список инстансов вместе с дефинишенами.

    Соединение делается одним запросом: дефинишена нужна каждой строке списка,
    а поштучная догрузка дала бы N+1 на страницу.

    `archived`: `active` — только неархивные (по умолчанию), `archived` —
    только архивные, `all` — и те и другие. По умолчанию архивные скрыты,
    иначе уборка списка ничего бы не убирала.
    """
    # Явный тип: без него список выводится по первому условию, и добавление
    # `is_(None)` — это уже не сравнение, а проверка на пустое значение.
    filters: list[ColumnElement[bool]] = []
    if archived == "active":
        filters.append(WorkflowInstance.archived_at.is_(None))
    elif archived == "archived":
        filters.append(WorkflowInstance.archived_at.is_not(None))
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


#: Архивировать можно только то, что пришло к концу. Набор уже есть в
#: `enums` и совпадает с нужным: `COMPENSATION_FAILED` и `PAUSED` в него не
#: входят — первый ждёт человека, второй ждёт решения. Свой список здесь
#: разошёлся бы с этим через месяц.


async def count_archived(db_session: AsyncSession) -> int:
    """Сколько процессов убрано в архив."""
    return (
        await db_session.scalar(
            select(func.count())
            .select_from(WorkflowInstance)
            .where(WorkflowInstance.archived_at.is_not(None))
        )
    ) or 0


async def archive_terminal(db_session: AsyncSession, before: datetime | None = None) -> int:
    """Убирает завершённые процессы из списка, не удаляя их (ADR-0017).

    Обновляется только `workflow_instances`: журнал не трогается, и состояние
    архивного процесса по-прежнему выводится повтором событий. Отбор идёт по
    терминальным статусам, поэтому `PAUSED` и `COMPENSATION_FAILED` остаются
    видимыми: первый ждёт решения человека, второй — повтора отката.
    """
    conditions: list[ColumnElement[bool]] = [
        WorkflowInstance.status.in_(TERMINAL_STATUSES),
        WorkflowInstance.archived_at.is_(None),
    ]
    if before is not None:
        conditions.append(WorkflowInstance.created_at < before)

    result = await db_session.execute(
        update(WorkflowInstance)
        .where(*conditions)
        .values(archived_at=datetime.now(UTC))
        .returning(WorkflowInstance.id)
    )
    return len(result.all())


async def unarchive(db_session: AsyncSession, instance_id: uuid.UUID) -> WorkflowInstance | None:
    """Возвращает процесс из архива в список."""
    result = await db_session.execute(
        update(WorkflowInstance)
        .where(WorkflowInstance.id == instance_id, WorkflowInstance.archived_at.is_not(None))
        .values(archived_at=None)
        .returning(WorkflowInstance)
    )
    return result.scalar_one_or_none()


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
    """Перезапускает откат после `COMPENSATION_FAILED`.

    Пауза перед повтором снимается: человек решил, что служба ожила, и ждать
    остаток автоматической паузы незачем — решение оператора побеждает таймер.
    """
    instance = await get_instance(db_session, instance_id)
    if InstanceStatus(instance.status) is not InstanceStatus.COMPENSATION_FAILED:
        raise ConflictError(f"откат не требовался: статус {instance.status}")

    # Статус меняется только через журнал (§3.1). Прямая запись в колонку дала
    # бы replay'у состояние COMPENSATION_FAILED навсегда: нового события нет, а
    # воркер, взявший инстанс после перезапуска, увидел бы отказ и не стал бы
    # повторять компенсацию.
    await append_event(db_session, instance_id, EventType.COMPENSATION_STARTED, {"retried": True})
    instance.status = InstanceStatus.COMPENSATING
    instance.compensation_not_before = None
    instance.error = None
    await db_session.flush()
    return instance


async def resume_instance(db_session: AsyncSession, instance_id: uuid.UUID) -> WorkflowInstance:
    """Снимает паузу: человек решил, что процесс можно продолжать.

    Пауза записывается событием, и снимать её надо так же: `status` меняется
    только через журнал (§3.1), иначе подхват воркера после перезапуска не
    увидел бы решения человека и вернул бы процесс к ожиданию.
    """
    instance = await get_instance(db_session, instance_id)
    if InstanceStatus(instance.status) is not InstanceStatus.PAUSED:
        raise ConflictError(f"инстанс не на паузе: статус {instance.status}")

    await append_event(
        db_session, instance_id, EventType.INSTANCE_RESUMED, {"step": instance.current_step}
    )
    instance.status = InstanceStatus.RUNNING
    await db_session.flush()
    return instance


async def rebuild_state(db_session: AsyncSession, instance_id: uuid.UUID) -> dict:
    """Состояние инстанса по журналу — то, что увидит воркер после подхвата.

    Разбор стоит работы, пропорциональной длине журнала, а страница опрашивает
    состояние каждые секунды. Поэтому результат помнится в процессе API и
    действителен, пока журнал не изменился: свежесть проверяется по головному
    `seq`, который берётся индексом и стоит один запрос. Семантика не меняется —
    кэш отдаёт ровно то, что дал бы повторный разбор того же журнала.
    """
    head = await db_session.scalar(
        select(func.max(WorkflowEvent.seq)).where(WorkflowEvent.instance_id == instance_id)
    )
    head_seq = int(head if head is not None else -1)
    cached = _STATE_CACHE.get(instance_id)
    if cached is not None and cached[0] == head_seq:
        return cached[1]

    events = await read_events(db_session, instance_id)
    state = replay(events)

    # Убранный журнал (ADR-0019) разбирается в пустой процесс: replay по
    # нулю событий даёт `PENDING`, и ответ выглядел бы как испорченный.
    # Пометка уборки обязана попасть в ответ — иначе вызывающий приметил бы
    # её за состояние процесса.
    instance = await db_session.get(WorkflowInstance, instance_id)
    pruned_at = instance.journal_pruned_at if instance is not None else None
    if pruned_at is not None:
        return {
            "status": instance.status if instance is not None else state.status,
            "current_step": None,
            "variables": {},
            "completed_steps": [],
            "pending_compensations": [],
            "is_terminal": True,
            "step_iterations": {},
            "journal_pruned_at": pruned_at,
        }

    result = {
        "status": state.status,
        "current_step": state.current_step,
        "variables": state.variables,
        "completed_steps": state.completed_steps,
        "pending_compensations": steps_to_compensate(state),
        "is_terminal": state.is_terminal,
        "step_iterations": state.step_iterations,
        "journal_pruned_at": None,
    }
    if len(_STATE_CACHE) >= _STATE_CACHE_SIZE:
        _STATE_CACHE.clear()
    _STATE_CACHE[instance_id] = (head_seq, result)
    return result


# Кэш состояния живёт в процессе API: инстансов на стенде десятки, а потеря
# кэша стоит одного повторного разбора. Общая папка не нужна — состояние
# выводится журналом, и кэш только избавляет от повторной работы.
_STATE_CACHE_SIZE = 512
_STATE_CACHE: dict[uuid.UUID, tuple[int, dict]] = {}


def clear_state_cache() -> None:
    """Сбрасывает кэш состояния — для тестов и диагностики."""
    _STATE_CACHE.clear()


def _terminal_statuses() -> frozenset[InstanceStatus]:
    return frozenset(TERMINAL_STATUSES)


def active_statuses() -> frozenset[str]:
    """Активные статусы строкой — для фильтров в списках и метриках."""
    return frozenset(str(status) for status in ACTIVE_STATUSES)
