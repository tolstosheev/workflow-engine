"""Тесты Saga и durable-таймеров на реальной БД."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine import saga, scheduler
from app.engine.journal import read_events, replay
from app.models.enums import CompensationStatus, EventType, InstanceStatus, StepRunStatus
from app.models.workflow import (
    CompensationLog,
    StepRun,
    Timer,
    WorkflowDefinition,
    WorkflowInstance,
)

pytestmark = pytest.mark.integration


@pytest.fixture
async def db_session(db_engine: None) -> AsyncIterator[AsyncSession]:
    from app.core.db import get_session_factory

    session = get_session_factory()()
    transaction = await session.begin()
    try:
        yield session
    finally:
        if transaction.is_active:
            await transaction.rollback()
        await session.close()


async def _new_instance(db_session: AsyncSession) -> uuid.UUID:
    definition = WorkflowDefinition(
        name=f"saga-{uuid.uuid4().hex[:8]}",
        definition={"start": "a", "steps": {"a": {"action": "app.demo.handlers:validate_cart"}}},
    )
    db_session.add(definition)
    await db_session.flush()

    instance = WorkflowInstance(definition_id=definition.id, input={}, variables={})
    db_session.add(instance)
    await db_session.flush()
    return instance.id


async def _completed_step(
    db_session: AsyncSession, instance_id: uuid.UUID, step_name: str
) -> uuid.UUID:
    """Записывает успешное выполнение шага с возрастающим временем завершения."""
    existing = await db_session.scalar(
        select(func_count()).where(
            StepRun.step_name == step_name, StepRun.instance_id == instance_id
        )
    )
    step_run = StepRun(
        instance_id=instance_id,
        step_name=step_name,
        attempt=1,
        status=StepRunStatus.COMPLETED,
        finished_at=datetime.now(UTC) + timedelta(seconds=existing or 0),
    )
    db_session.add(step_run)
    await db_session.flush()
    return step_run.id


def func_count() -> object:
    from sqlalchemy import func

    return func.count()


async def _failed_step(db_session: AsyncSession, instance_id: uuid.UUID, step_name: str) -> None:
    """Шаг с провалом не компенсируется: эффекта не было."""
    db_session.add(
        StepRun(
            instance_id=instance_id,
            step_name=step_name,
            attempt=1,
            status=StepRunStatus.FAILED,
            finished_at=datetime.now(UTC),
        )
    )
    await db_session.flush()


# --- Выбор шагов к компенсации ---------------------------------------------


async def test_completed_steps_are_returned(db_session: AsyncSession) -> None:
    instance_id = await _new_instance(db_session)
    await _completed_step(db_session, instance_id, "a")

    assert await saga.completed_steps(db_session, instance_id) == ["a"]


async def test_failed_steps_are_not_compensated(db_session: AsyncSession) -> None:
    # Шаг, упавший на первой попытке, эффекта не оказал — отменять нечего.
    instance_id = await _new_instance(db_session)
    await _failed_step(db_session, instance_id, "charge")

    assert await saga.completed_steps(db_session, instance_id) == []


async def test_compensation_plan_is_reverse_order(db_session: AsyncSession) -> None:
    instance_id = await _new_instance(db_session)
    await _completed_step(db_session, instance_id, "validate")
    await _completed_step(db_session, instance_id, "reserve")
    await _completed_step(db_session, instance_id, "charge")

    plan = await saga.plan(
        db_session,
        instance_id,
        {
            "validate": "app.demo.handlers:undo_validate",
            "reserve": "app.demo.handlers:release_inventory",
            "charge": "app.demo.handlers:refund_payment",
        },
    )

    assert [item.step_name for item in plan] == ["charge", "reserve", "validate"]


async def test_steps_without_compensation_are_skipped(db_session: AsyncSession) -> None:
    instance_id = await _new_instance(db_session)
    await _completed_step(db_session, instance_id, "reserve")
    await _completed_step(db_session, instance_id, "validate")

    plan = await saga.plan(
        db_session,
        instance_id,
        {"reserve": "app.demo.handlers:release_inventory", "validate": None},
    )

    assert [item.step_name for item in plan] == ["reserve"]


async def test_plan_is_empty_when_nothing_completed(db_session: AsyncSession) -> None:
    instance_id = await _new_instance(db_session)

    assert await saga.plan(db_session, instance_id, {}) == []


async def test_compensated_steps_leave_the_plan(db_session: AsyncSession) -> None:
    # Повторный откат уже откатанного привёл бы к двойному возврату.
    instance_id = await _new_instance(db_session)
    step_run_id = await _completed_step(db_session, instance_id, "reserve")
    step_run = await db_session.get(StepRun, step_run_id)
    assert step_run is not None
    step_run.compensated = True
    await db_session.flush()

    plan = await saga.plan(
        db_session, instance_id, {"reserve": "app.demo.handlers:release_inventory"}
    )

    assert plan == []


# --- Попытки компенсации ----------------------------------------------------


async def test_attempt_start_writes_event(db_session: AsyncSession) -> None:
    instance_id = await _new_instance(db_session)
    await _completed_step(db_session, instance_id, "reserve")
    item = (
        await saga.plan(db_session, instance_id, {"reserve": "app.demo.handlers:release_inventory"})
    )[0]

    log = await saga.start_attempt(db_session, instance_id, item, attempt=1)

    assert log.status == CompensationStatus.RUNNING
    events = await read_events(db_session, instance_id)
    assert EventType.COMPENSATION_STARTED in [e.type for e in events]


async def test_successful_attempt_marks_step_compensated(db_session: AsyncSession) -> None:
    instance_id = await _new_instance(db_session)
    await _completed_step(db_session, instance_id, "reserve")
    item = (
        await saga.plan(db_session, instance_id, {"reserve": "app.demo.handlers:release_inventory"})
    )[0]
    log = await saga.start_attempt(db_session, instance_id, item, attempt=1)

    outcome = await saga.finish_attempt(db_session, instance_id, log, item, succeeded=True)

    assert outcome.succeeded is True
    assert log.status == CompensationStatus.COMPLETED
    assert log.finished_at is not None
    assert (await saga.is_compensated(db_session, instance_id)) is True


async def test_failed_attempt_is_visible_for_manual_review(db_session: AsyncSession) -> None:
    # Провал компенсации — не тихий сбой: он обязан остаться в состоянии,
    # которое видно и требует разбора.
    instance_id = await _new_instance(db_session)
    await _completed_step(db_session, instance_id, "reserve")
    item = (
        await saga.plan(db_session, instance_id, {"reserve": "app.demo.handlers:release_inventory"})
    )[0]
    log = await saga.start_attempt(db_session, instance_id, item, attempt=1)

    outcome = await saga.finish_attempt(
        db_session, instance_id, log, item, succeeded=False, error="сервис недоступен"
    )

    assert outcome.succeeded is False
    assert log.status == CompensationStatus.FAILED
    assert await saga.failed_count(db_session, instance_id) == 1
    pending = await saga.pending_compensations(db_session, instance_id)
    assert [p.error for p in pending] == ["сервис недоступен"]


async def test_failure_event_carries_error(db_session: AsyncSession) -> None:
    instance_id = await _new_instance(db_session)
    await _completed_step(db_session, instance_id, "reserve")
    item = (
        await saga.plan(db_session, instance_id, {"reserve": "app.demo.handlers:release_inventory"})
    )[0]
    log = await saga.start_attempt(db_session, instance_id, item, attempt=1)
    await saga.finish_attempt(db_session, instance_id, log, item, False, "таймаут")

    state = replay(await read_events(db_session, instance_id))

    assert state.status.value == "COMPENSATION_FAILED"


async def test_skipped_step_is_recorded(db_session: AsyncSession) -> None:
    instance_id = await _new_instance(db_session)

    await saga.mark_skipped(db_session, instance_id, "validate")

    events = await read_events(db_session, instance_id)
    assert [e.type for e in events] == [EventType.COMPENSATION_SKIPPED]


async def test_attempts_counted_from_log(db_session: AsyncSession) -> None:
    # Счётчик обязан переживать перезапуск воркера, поэтому берётся из БД.
    instance_id = await _new_instance(db_session)
    step_run_id = await _completed_step(db_session, instance_id, "reserve")
    item = (
        await saga.plan(db_session, instance_id, {"reserve": "app.demo.handlers:release_inventory"})
    )[0]

    assert item.attempts_made == 0
    await saga.start_attempt(db_session, instance_id, item, attempt=1)

    assert await saga.attempts_made(db_session, step_run_id) == 1


async def test_operation_key_is_namespaced_by_compensation(db_session: AsyncSession) -> None:
    # Ключ компенсации не должен совпадать с ключом основного шага: иначе
    # откат был бы признан уже применённым.
    instance_id = await _new_instance(db_session)
    await _completed_step(db_session, instance_id, "reserve")
    item = (
        await saga.plan(db_session, instance_id, {"reserve": "app.demo.handlers:release_inventory"})
    )[0]

    key = saga.operation_key(instance_id, item, 1)

    assert key.step_name == "compensate:reserve"
    assert str(key) == f"{instance_id}:compensate:reserve:1"


async def test_stream_pending_steps_yields_uncompensated(db_session: AsyncSession) -> None:
    instance_id = await _new_instance(db_session)
    await _completed_step(db_session, instance_id, "reserve")
    compensated_id = await _completed_step(db_session, instance_id, "validate")
    step_run = await db_session.get(StepRun, compensated_id)
    assert step_run is not None
    step_run.compensated = True
    await db_session.flush()

    names = [sr.step_name async for sr in saga.stream_pending_steps(db_session, instance_id)]

    assert names == ["reserve"]


async def test_status_update_is_visible_in_journal_state(db_session: AsyncSession) -> None:
    instance_id = await _new_instance(db_session)
    instance = await db_session.get(WorkflowInstance, instance_id)
    assert instance is not None

    await saga.set_instance_status(db_session, instance, "COMPENSATING")

    assert instance.status == "COMPENSATING"


# --- Durable-таймеры ---------------------------------------------------------


async def test_timer_is_created_with_due_date(db_session: AsyncSession) -> None:
    instance_id = await _new_instance(db_session)

    timer = await scheduler.schedule(db_session, instance_id, "confirm", 60)

    assert timer.due_at > datetime.now(UTC)
    assert timer.fired is False


async def test_scheduling_writes_event(db_session: AsyncSession) -> None:
    # Таймер без события не виден в replay, и инстанс остался бы RUNNING
    # вместо WAITING.
    instance_id = await _new_instance(db_session)

    await scheduler.schedule(db_session, instance_id, "confirm", 60)

    events = await read_events(db_session, instance_id)
    assert events[-1].type == EventType.TIMER_SCHEDULED


async def test_replay_shows_waiting_state(db_session: AsyncSession) -> None:
    instance_id = await _new_instance(db_session)
    await scheduler.schedule(db_session, instance_id, "confirm", 60)

    state = replay(await read_events(db_session, instance_id))

    assert state.status.value == "WAITING"


async def test_future_timer_is_not_claimed(db_session: AsyncSession) -> None:
    instance_id = await _new_instance(db_session)
    await scheduler.schedule(db_session, instance_id, "confirm", 3600)

    assert await scheduler.claim_due(db_session) == []


async def test_due_timer_is_claimed(db_session: AsyncSession) -> None:
    instance_id = await _new_instance(db_session)
    timer = await scheduler.schedule(db_session, instance_id, "confirm", 0)
    timer.due_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.flush()

    claimed = await scheduler.claim_due(db_session)

    assert [t.id for t in claimed] == [timer.id]
    # Подбор не помечает таймер: отметка идёт в fire_due вместе с событием,
    # иначе падение между ними оставило бы инстанс спящим навсегда.
    assert claimed[0].fired is False


async def test_timer_is_fired_only_once(db_session: AsyncSession) -> None:
    # Второй воркер не должен сработать тот же таймер: он уже обработан.
    instance_id = await _new_instance(db_session)
    timer = await scheduler.schedule(db_session, instance_id, "confirm", 0)
    timer.due_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.flush()

    claimed = (await scheduler.claim_due(db_session))[0]
    assert await scheduler.fire_due(db_session, claimed) is True
    assert await scheduler.fire_due(db_session, claimed) is False


async def test_fired_timer_wakes_instance(db_session: AsyncSession) -> None:
    # Статус в колонке и в replay обязан стать RUNNING: без этого воркер не
    # подберёт инстанс и шаг после ожидания не выполнится.
    instance_id = await _new_instance(db_session)
    timer = await scheduler.schedule(db_session, instance_id, "confirm", 0)
    timer.due_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.flush()
    claimed = (await scheduler.claim_due(db_session))[0]

    await scheduler.fire_due(db_session, claimed)

    state = replay(await read_events(db_session, instance_id))
    assert state.status.value == "RUNNING"
    assert state.current_step == "confirm"

    row = await db_session.get(WorkflowInstance, instance_id)
    assert row is not None
    assert row.status == InstanceStatus.RUNNING


async def test_fire_due_is_skipped_when_already_fired(db_session: AsyncSession) -> None:
    # Гонка двух воркеров: выигрывает один, второй не пишет событие вторично.
    instance_id = await _new_instance(db_session)
    timer = await scheduler.schedule(db_session, instance_id, "confirm", 0)
    timer.due_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.flush()
    claimed = (await scheduler.claim_due(db_session))[0]

    assert await scheduler.fire_due(db_session, claimed) is True
    await scheduler.fire_due(db_session, claimed)

    events = await read_events(db_session, instance_id)
    assert [e.type for e in events].count(EventType.TIMER_FIRED) == 1


async def test_planned_for_sees_fired_timer(db_session: AsyncSession) -> None:
    # `wait_before_seconds` ждёт один раз: после срабатывания таймера новый
    # ожидатель создаваться не должен, иначе шаг не наступил бы никогда.
    instance_id = await _new_instance(db_session)
    timer = await scheduler.schedule(db_session, instance_id, "confirm", 0)
    timer.due_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.flush()

    assert await scheduler.planned_for(db_session, instance_id, "confirm") is True
    assert await scheduler.planned_for(db_session, instance_id, "other") is False

    await scheduler.fire_due(db_session, (await scheduler.claim_due(db_session))[0])

    assert await scheduler.planned_for(db_session, instance_id, "confirm") is True


async def test_pending_count_tracks_unfired(db_session: AsyncSession) -> None:
    instance_id = await _new_instance(db_session)
    await scheduler.schedule(db_session, instance_id, "confirm", 3600)

    assert await scheduler.pending_count(db_session, instance_id) == 1


async def test_cancel_marks_timers_fired(db_session: AsyncSession) -> None:
    # При откате ждать таймер бессмысленно, а неотменённый таймер разбудил бы
    # процесс уже после компенсации.
    instance_id = await _new_instance(db_session)
    await scheduler.schedule(db_session, instance_id, "confirm", 3600)

    assert await scheduler.cancel_for(db_session, instance_id) == 1
    assert await scheduler.pending_count(db_session, instance_id) == 0


async def test_cancel_is_idempotent(db_session: AsyncSession) -> None:
    instance_id = await _new_instance(db_session)
    await scheduler.schedule(db_session, instance_id, "confirm", 3600)
    await scheduler.cancel_for(db_session, instance_id)

    assert await scheduler.cancel_for(db_session, instance_id) == 0


async def test_compensation_log_has_single_active_attempt(db_session: AsyncSession) -> None:
    # Частичный индекс не даёт открыть вторую параллельную компенсацию одного
    # шага: две отмены резерва — это двойной возврат.
    from sqlalchemy.exc import IntegrityError

    instance_id = await _new_instance(db_session)
    step_run_id = await _completed_step(db_session, instance_id, "reserve")
    item = (
        await saga.plan(db_session, instance_id, {"reserve": "app.demo.handlers:release_inventory"})
    )[0]
    await saga.start_attempt(db_session, instance_id, item, attempt=1)

    db_session.add(
        CompensationLog(
            instance_id=instance_id,
            step_run_id=step_run_id,
            compensation_step=item.compensation_ref,
            attempt=2,
            status=CompensationStatus.RUNNING,
        )
    )

    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


async def test_timer_rows_are_scoped_to_instance(db_session: AsyncSession) -> None:
    first = await _new_instance(db_session)
    second = await _new_instance(db_session)
    await scheduler.schedule(db_session, first, "confirm", 3600)

    assert await scheduler.pending_count(db_session, second) == 0
    rows = await db_session.scalars(select(Timer).where(Timer.instance_id == second))
    assert rows.all() == []
