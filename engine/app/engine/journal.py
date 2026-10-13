"""Журнал событий: единственный источник истины об инстансе.

Две операции, и обе принципиальны:

- `append` — запись события. Событие описывает уже применённое изменение,
  поэтому пишется в одной транзакции с ним.
- `replay` — восстановление состояния. Чистая функция от списка событий: ни БД,
  ни времени, ни внешних систем. Именно поэтому replay одинаков до и после
  падения воркера.

Инварианты (§3.1–3.2 AGENTS.md): таблица append-only, транзакция не
пересекает внешний вызов, `seq` монотонен без пропусков.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import PAUSE_DEADLINE, EventType, InstanceStatus, StepRunStatus
from app.models.workflow import WorkflowEvent, WorkflowInstance


@dataclass(frozen=True, slots=True)
class RecordedEvent:
    """Событие журнала в виде, пригодном для replay.

    Отдельный от модели: replay не должен тянуть за собой ORM и сессию.
    """

    seq: int
    type: EventType
    payload: dict[str, Any]
    created_at: datetime


@dataclass
class InstanceState:
    """Состояние инстанса, восстановленное из журнала.

    `ready_step` — шаг, готовый к исполнению. `None` означает, что процесс либо
    завершён, либо ждёт таймера, либо откатывается.

    `resumed_step` — шаг, ожидание которого уже снято человеком. Существует,
    чтобы воркер, дойдя до того же шага снова, не встал в ту же паузу: без него
    возобновление превращалось бы в бесконечный цикл «пауза — возобновление».
    Обнуляется, когда шаг начинается: возобновление относится к одному проходу.

    `step_iterations` — сколько раз шаг успешно завершился. Отдельный от
    `completed_steps`: тот хранит порядок для компенсации и уникален по имени,
    а циклу нужен именно счётчик. Живёт здесь, а не в `step_runs`, потому что
    состояние обязано выводиться replay'ом (§3.1 AGENTS.md) — иначе подхват
    воркера дал бы другой счётчик, чем тот, что видел упавший.
    """

    status: InstanceStatus = InstanceStatus.PENDING
    current_step: str | None = None
    variables: dict[str, Any] = field(default_factory=dict)
    completed_steps: list[str] = field(default_factory=list)
    failed_step: str | None = None
    resumed_step: str | None = None
    compensation_for: str | None = None
    step_iterations: dict[str, int] = field(default_factory=dict)
    cancel_requested: bool = False

    @property
    def is_terminal(self) -> bool:
        return self.status in {
            InstanceStatus.COMPLETED,
            InstanceStatus.COMPENSATED,
            InstanceStatus.CANCELLED,
            InstanceStatus.FAILED,
        }


async def append_event(
    db_session: AsyncSession,
    instance_id: uuid.UUID,
    event_type: EventType,
    payload: dict[str, Any] | None = None,
) -> RecordedEvent:
    """Записывает событие, выделяя следующий `seq` под блокировкой строки.

    Блокировка нужна для строгой монотонности без пропусков: конкурирующая
    запись ждёт, а не получает тот же номер. Без неё два воркера, писавших
    событие одновременно, развели бы историю инстанса на две ветки.
    """
    await db_session.execute(
        select(WorkflowInstance.id).where(WorkflowInstance.id == instance_id).with_for_update()
    )
    next_seq = await db_session.scalar(
        select(func.coalesce(func.max(WorkflowEvent.seq), -1) + 1).where(
            WorkflowEvent.instance_id == instance_id
        )
    )
    seq = int(next_seq or 0)

    record = WorkflowEvent(
        instance_id=instance_id,
        seq=seq,
        type=event_type.value,
        payload=payload or {},
        created_at=datetime.now(UTC),
    )
    db_session.add(record)
    await db_session.flush()

    return RecordedEvent(
        seq=seq, type=event_type, payload=payload or {}, created_at=record.created_at
    )


async def read_events(
    db_session: AsyncSession, instance_id: uuid.UUID, from_seq: int = 0
) -> list[RecordedEvent]:
    """Читает журнал инстанса по порядку."""
    rows = await db_session.scalars(
        select(WorkflowEvent)
        .where(WorkflowEvent.instance_id == instance_id, WorkflowEvent.seq >= from_seq)
        .order_by(WorkflowEvent.seq)
    )
    return [
        RecordedEvent(
            seq=row.seq,
            type=EventType(row.type),
            payload=row.payload,
            created_at=row.created_at,
        )
        for row in rows
    ]


async def read_events_page(
    db_session: AsyncSession, instance_id: uuid.UUID, from_seq: int = 0, limit: int = 0
) -> tuple[list[RecordedEvent], int]:
    """Читает страницу журнала и общее число событий инстанса.

    Страница, а не весь журнал: у долгого процесса события копятся месяцами, и
    выборка для экрана не должна тянуть в память всю историю. `limit` ноль
    означает «без предела» — так ведёт себя и сам `read_events`.
    """
    rows_query = (
        select(WorkflowEvent)
        .where(WorkflowEvent.instance_id == instance_id, WorkflowEvent.seq >= from_seq)
        .order_by(WorkflowEvent.seq)
    )
    total_query = (
        select(func.count())
        .select_from(WorkflowEvent)
        .where(WorkflowEvent.instance_id == instance_id)
    )

    if limit > 0:
        rows_query = rows_query.limit(limit)

    rows = await db_session.scalars(rows_query)
    total = await db_session.scalar(total_query)
    events = [
        RecordedEvent(
            seq=row.seq,
            type=EventType(row.type),
            payload=row.payload,
            created_at=row.created_at,
        )
        for row in rows
    ]
    return events, total or len(events)


def _apply(state: InstanceState, event: RecordedEvent) -> None:
    """Применяет одно событие к состоянию.

    Обновление статуса допустимо только здесь: `UPDATE workflow_instances SET
    status` в обход журнала сделал бы состояние невосстанавливаемым.
    """
    payload = event.payload

    match event.type:
        case EventType.INSTANCE_CREATED:
            state.status = InstanceStatus.PENDING
            state.current_step = payload.get("start")

        case EventType.STEP_STARTED:
            state.status = InstanceStatus.RUNNING
            state.current_step = payload.get("step")
            # Возобновление израсходовано: шаг начат, и повторно спрашивать
            # перед ним не нужно.
            state.resumed_step = None

        case EventType.STEP_COMPLETED:
            state.variables.update(payload.get("output") or {})
            step_name = payload.get("step", "")
            if payload.get("step") not in state.completed_steps:
                state.completed_steps.append(payload.get("step", ""))
            # Счётчик растёт на каждом успешном завершении: он ограничивает
            # число проходов цикла, а число попыток ошибочных повторов сюда не
            # относится — они не завершают шаг.
            state.step_iterations[step_name] = state.step_iterations.get(step_name, 0) + 1
            state.status = InstanceStatus.RUNNING
            state.current_step = payload.get("next", state.current_step)

        case EventType.LOOP_EXHAUSTED:
            # Число проходов исчерпано. Процесс уходит в компенсацию, а не
            # «успешно» останавливается: цель цикла не достигнута, и сделать
            # вид, что она достигнута, было бы враньём в журнале.
            state.status = InstanceStatus.COMPENSATING
            state.failed_step = payload.get("step")

        case EventType.STEP_FAILED:
            state.failed_step = payload.get("step")
            # terminal=True означает, что попытки исчерпаны: дальше нужен откат.
            # Без отметки шаг просто повторится, и статус остаётся RUNNING.
            state.status = (
                InstanceStatus.COMPENSATING if payload.get("terminal") else InstanceStatus.RUNNING
            )
            if payload.get("terminal"):
                # Откат восстанавливается по журналу: если компенсация пошла из
                # отказа, инстанс закончится отказом, а не отменой. Различие
                # хранится в самом механизме, а не догадывается по колонке.
                state.cancel_requested = False
            # Переход по объявленной ветке `on_failure`. Терминальный отказ
            # означает откат, а не продолжение, поэтому в нём шага нет.
            if not payload.get("terminal") and payload.get("next"):
                state.current_step = payload["next"]

        case EventType.STEP_RETRY_SCHEDULED:
            state.status = InstanceStatus.RUNNING
            state.current_step = payload.get("step")

        case EventType.INSTANCE_PAUSED:
            # Процесс встал и ждёт человека. Шаг не начат: `STEP_STARTED` не
            # написан, эффекта не было, откатывать нечего.
            state.status = InstanceStatus.PAUSED
            state.current_step = payload.get("step")

        case EventType.INSTANCE_RESUMED:
            state.status = InstanceStatus.RUNNING
            state.resumed_step = payload.get("step")

        case EventType.TIMER_SCHEDULED:
            # Срок ожидания человека планируется тем же событием, и без оглядки
            # на вид он переводил паузу в `WAITING`: страница называла одно
            # состояние двумя словами — «Ждёт решения» и «Ожидание» (ADR-0016).
            if payload.get("kind") != PAUSE_DEADLINE:
                state.status = InstanceStatus.WAITING
            state.current_step = payload.get("step")

        case EventType.TIMER_FIRED:
            state.status = InstanceStatus.RUNNING
            state.current_step = payload.get("step")

        case EventType.COMPENSATION_STARTED:
            state.status = InstanceStatus.COMPENSATING
            state.compensation_for = payload.get("for_step")

        case EventType.COMPENSATION_COMPLETED:
            state.status = InstanceStatus.COMPENSATING

        case EventType.COMPENSATION_FAILED:
            state.status = InstanceStatus.COMPENSATION_FAILED

        case EventType.INSTANCE_COMPLETED:
            state.status = InstanceStatus.COMPLETED
            state.current_step = None

        case EventType.INSTANCE_COMPENSATED:
            state.status = InstanceStatus.COMPENSATED
            state.current_step = None

        case EventType.INSTANCE_FAILED:
            # Отказ, а не откат: компенсировать было нечего — процесс упал
            # раньше, чем выполненный эффект появился.
            state.status = InstanceStatus.FAILED
            state.current_step = None

        case EventType.INSTANCE_CANCEL_REQUESTED:
            state.cancel_requested = True
            # Отмена запрошена, но выполненные шаги ещё не откачены. Текущий
            # шаг сохраняется: он указывает, где процесс остановился, и по
            # нему видно, что ждать отката ещё есть чего.
            state.status = InstanceStatus.CANCELLING

        case EventType.INSTANCE_CANCELLED:
            # Терминально: компенсировать было нечего, и откат завершён.
            state.status = InstanceStatus.CANCELLED
            state.current_step = None

        case _:  # pragma: no cover - событие из более новой версии движка
            # Неизвестный тип не останавливает разбор: событие появилось в
            # более новой версии движка, и старый воркер обязан продолжить
            # работу с журналом, а не упасть на каждом чтении состояния.
            # Известные типы перечислены выше явно, опечатка в enum невозможна.
            pass


def replay(events: list[RecordedEvent]) -> InstanceState:
    """Восстанавливает состояние инстанса по журналу.

    Чистая функция: повторный проход по тому же журналу обязан дать тот же
    результат — это и есть гарантия восстановимости.
    """
    state = InstanceState()
    for event in events:
        _apply(state, event)
    return state


def steps_to_compensate(state: InstanceState) -> list[str]:
    """Шаги к компенсации в обратном порядке выполнения.

    Порядок зеркален порядку выполнения: зависимости между шагами обратимы
    только так.
    """
    return list(reversed(state.completed_steps))


__all__ = [
    "InstanceState",
    "RecordedEvent",
    "StepRunStatus",
    "append_event",
    "read_events",
    "replay",
    "steps_to_compensate",
]
