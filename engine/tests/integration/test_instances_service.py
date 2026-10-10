"""Тесты сервиса инстансов без HTTP-слоя.

Проверяются правила, которые HTTP-слой скрывает: поведение при повреждённом
состоянии, сортировка таймлайна и отмена ожиданий при форсированном
завершении.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.engine import scheduler
from app.engine.journal import append_event, read_events, replay
from app.models.enums import CompensationStatus, EventType, InstanceStatus, StepRunStatus
from app.models.workflow import (
    CompensationLog,
    StepRun,
    WorkflowDefinition,
    WorkflowInstance,
)
from app.services import instances as service

pytestmark = pytest.mark.integration

DEMO = "app.demo.handlers"

FLOW = {
    "start": "a",
    "steps": {
        "a": {"action": f"{DEMO}:validate_cart", "next": "b"},
        "b": {
            "action": f"{DEMO}:reserve_inventory",
            "compensation": f"{DEMO}:release_inventory",
            "next": "c",
        },
        "c": {"action": f"{DEMO}:confirm_delivery"},
    },
}


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


async def _definition(db_session: AsyncSession, active: bool = True) -> WorkflowDefinition:
    definition = WorkflowDefinition(name=f"svc-{uuid.uuid4().hex[:8]}", definition=FLOW)
    definition.is_active = active
    db_session.add(definition)
    await db_session.flush()
    return definition


async def _instance(db_session: AsyncSession, active: bool = True) -> WorkflowInstance:
    definition = await _definition(db_session, active)
    instance = await service.create_instance(db_session, definition.id, {"amount": "10.00"})
    return instance


# --- Создание --------------------------------------------------------------


async def test_created_instance_is_pending(db_session: AsyncSession) -> None:
    instance = await _instance(db_session)

    assert instance.status == InstanceStatus.PENDING
    assert instance.variables == {}


async def test_created_instance_records_start_step(db_session: AsyncSession) -> None:
    instance = await _instance(db_session)

    events = await read_events(db_session, instance.id)

    assert events[0].type is EventType.INSTANCE_CREATED
    assert events[0].payload["start"] == "a"


async def test_create_rejects_inactive_definition(db_session: AsyncSession) -> None:
    definition = await _definition(db_session, active=False)

    with pytest.raises(ConflictError, match="неактивно"):
        await service.create_instance(db_session, definition.id, {})
    await db_session.rollback()


async def test_create_rejects_unknown_definition(db_session: AsyncSession) -> None:
    with pytest.raises(NotFoundError):
        await service.create_instance(db_session, uuid.uuid4(), {})
    await db_session.rollback()


async def test_get_instance_raises_for_unknown(db_session: AsyncSession) -> None:
    with pytest.raises(NotFoundError):
        await service.get_instance(db_session, uuid.uuid4())


# --- Список ----------------------------------------------------------------


async def test_list_joins_definition_name(db_session: AsyncSession) -> None:
    instance = await _instance(db_session)
    definition = await db_session.get(WorkflowDefinition, instance.definition_id)

    rows, total = await service.list_instances(db_session, 10, 0, None, None)

    assert total == 1
    assert rows[0][0].id == instance.id
    assert rows[0][1].name == definition.name


async def test_list_filters_by_status(db_session: AsyncSession) -> None:
    await _instance(db_session)

    pending, _ = await service.list_instances(db_session, 10, 0, "PENDING", None)
    completed, _ = await service.list_instances(db_session, 10, 0, "COMPLETED", None)

    assert len(pending) == 1
    assert completed == []


async def test_list_filters_by_definition(db_session: AsyncSession) -> None:
    instance = await _instance(db_session)

    rows, _ = await service.list_instances(db_session, 10, 0, None, instance.definition_id)
    other = await _definition(db_session)
    empty, total = await service.list_instances(db_session, 10, 0, None, other.id)

    assert len(rows) == 1
    assert empty == []
    assert total == 0


# --- Чтение состояния -------------------------------------------------------


async def test_rebuild_state_from_journal(db_session: AsyncSession) -> None:
    instance = await _instance(db_session)
    await append_event(
        db_session,
        instance.id,
        EventType.STEP_COMPLETED,
        {"step": "a", "output": {"n": 1}, "next": "b"},
    )

    state = await service.rebuild_state(db_session, instance.id)

    assert state["status"] is InstanceStatus.RUNNING
    assert state["current_step"] == "b"
    assert state["variables"] == {"n": 1}
    assert state["completed_steps"] == ["a"]
    assert state["is_terminal"] is False


async def test_rebuild_state_lists_pending_compensations(db_session: AsyncSession) -> None:
    instance = await _instance(db_session)
    for step in ("a", "b"):
        await append_event(
            db_session, instance.id, EventType.STEP_COMPLETED, {"step": step, "next": "c"}
        )
    await append_event(
        db_session, instance.id, EventType.STEP_FAILED, {"step": "c", "terminal": True}
    )

    state = await service.rebuild_state(db_session, instance.id)

    assert state["status"] is InstanceStatus.COMPENSATING
    assert state["pending_compensations"] == ["b", "a"]


async def test_rebuild_state_detects_terminal(db_session: AsyncSession) -> None:
    instance = await _instance(db_session)
    await append_event(db_session, instance.id, EventType.INSTANCE_COMPLETED, {})

    state = await service.rebuild_state(db_session, instance.id)

    assert state["is_terminal"] is True
    assert state["current_step"] is None


# --- Таймлайн --------------------------------------------------------------


async def test_timeline_orders_by_time(db_session: AsyncSession) -> None:
    instance = await _instance(db_session)
    db_session.add(
        StepRun(
            instance_id=instance.id,
            step_name="a",
            attempt=1,
            status=StepRunStatus.COMPLETED,
            started_at=datetime.now(UTC) + timedelta(seconds=10),
        )
    )
    await db_session.flush()

    entries = await service.get_timeline(db_session, instance.id, 100)

    moments = [e.get("created_at") or e.get("started_at") for e in entries]
    assert moments == sorted(moments)


async def test_timeline_respects_limit(db_session: AsyncSession) -> None:
    instance = await _instance(db_session)
    for index in range(5):
        await append_event(db_session, instance.id, EventType.STEP_STARTED, {"step": f"s{index}"})

    entries = await service.get_timeline(db_session, instance.id, 3)

    assert len(entries) == 3


async def test_get_steps_returns_attempts_in_order(db_session: AsyncSession) -> None:
    instance = await _instance(db_session)
    for attempt in (1, 2):
        db_session.add(
            StepRun(
                instance_id=instance.id,
                step_name="a",
                attempt=attempt,
                status=StepRunStatus.FAILED if attempt == 1 else StepRunStatus.COMPLETED,
            )
        )
    await db_session.flush()

    runs = await service.get_steps(db_session, instance.id)

    assert [run.attempt for run in runs] == [1, 2]


async def test_get_events_counts_all_from_offset(db_session: AsyncSession) -> None:
    instance = await _instance(db_session)
    for index in range(4):
        await append_event(db_session, instance.id, EventType.STEP_STARTED, {"step": str(index)})

    events, total = await service.get_events(db_session, instance.id, 2, 10)

    assert total == 5
    assert [e.seq for e in events] == [2, 3, 4]


# --- Отмена и завершение ----------------------------------------------------


async def test_cancel_is_rejected_for_completed(db_session: AsyncSession) -> None:
    instance = await _instance(db_session)
    await service.force_complete(db_session, instance.id, "ручная приёмка")

    with pytest.raises(ConflictError, match="отменить нельзя"):
        await service.cancel_instance(db_session, instance.id)
    await db_session.rollback()


@pytest.mark.parametrize("status", ["COMPLETED", "COMPENSATED", "CANCELLED", "FAILED"])
async def test_force_complete_is_rejected_for_terminal_status(
    db_session: AsyncSession, status: str
) -> None:
    instance = await _instance(db_session)
    instance.status = status

    with pytest.raises(ConflictError, match="уже завершён"):
        await service.force_complete(db_session, instance.id, "повтор")
    await db_session.rollback()


async def test_force_complete_cancels_pending_timers(db_session: AsyncSession) -> None:
    # Неотменённый таймер разбудил бы процесс уже после завершения.
    instance = await _instance(db_session)
    await scheduler.schedule(db_session, instance.id, "later", 3600)

    await service.force_complete(db_session, instance.id, "клиент подтвердил")

    assert await scheduler.pending_count(db_session, instance.id) == 0
    assert instance.status == InstanceStatus.COMPLETED


async def test_retry_compensation_requires_failed_state(db_session: AsyncSession) -> None:
    instance = await _instance(db_session)
    await append_event(db_session, instance.id, EventType.COMPENSATION_FAILED, {"error": "x"})
    instance.status = InstanceStatus.COMPENSATION_FAILED

    updated = await service.retry_compensation(db_session, instance.id)

    assert updated.status == InstanceStatus.COMPENSATING
    assert updated.error is None


async def test_retry_compensation_rejects_running_instance(db_session: AsyncSession) -> None:
    instance = await _instance(db_session)
    instance.status = InstanceStatus.RUNNING

    with pytest.raises(ConflictError, match="откат не требовался"):
        await service.retry_compensation(db_session, instance.id)
    await db_session.rollback()


async def test_compensation_failure_survives_in_log(db_session: AsyncSession) -> None:
    # Провал компенсации обязан остаться в данных для ручного разбора.
    instance = await _instance(db_session)
    step_run = StepRun(
        instance_id=instance.id,
        step_name="a",
        attempt=1,
        status=StepRunStatus.COMPLETED,
    )
    db_session.add(step_run)
    await db_session.flush()
    db_session.add(
        CompensationLog(
            instance_id=instance.id,
            step_run_id=step_run.id,
            compensation_step="app.demo.handlers:release_inventory",
            attempt=1,
            status=CompensationStatus.FAILED,
            error="сервис недоступен",
        )
    )
    await db_session.flush()

    rows = await db_session.scalars(select(CompensationLog))
    assert [row.error for row in rows] == ["сервис недоступен"]


def test_active_statuses_contains_running() -> None:
    assert "RUNNING" in service.active_statuses()
    assert "COMPLETED" not in service.active_statuses()


async def test_retry_compensation_is_visible_to_replay(
    db_session: AsyncSession,
) -> None:
    """Повтор отката обязан статься в журнале.

    Без события выполненного события replay вернул бы в статус
    COMPENSATION_FAILED навсегда, и воркер после перезапуска не стал бы повторять
    компенсацию.
    """
    instance = await _instance(db_session)
    await append_event(db_session, instance.id, EventType.COMPENSATION_FAILED, {"error": "x"})
    instance.status = InstanceStatus.COMPENSATION_FAILED

    await service.retry_compensation(db_session, instance.id)

    state = replay(await read_events(db_session, instance.id))
    assert state.status is InstanceStatus.COMPENSATING

    types = [e.type for e in await read_events(db_session, instance.id)]
    # Повтор — это событие, а не дельго: без него replay вернул бы к
    # COMPENSATION_FAILED и воркер не повторял бы компенсацию.
    assert types == [
        EventType.INSTANCE_CREATED,
        EventType.COMPENSATION_FAILED,
        EventType.COMPENSATION_STARTED,
    ]
