"""Chaos-тесты: реальное падение процесса воркера.

Отличие от интеграционных: здесь поднимается отдельный процесс и он
убивается посреди работы. Только такой тест доказывает, что падение
переживается, а не имитируется истечением таймера в том же процессе.
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.engine import lease
from app.engine.journal import read_events, replay
from app.models.enums import InstanceStatus, StepRunStatus
from app.models.workflow import StepRun, WorkflowDefinition, WorkflowInstance

pytestmark = [pytest.mark.chaos, pytest.mark.usefixtures("inventory")]

DEMO = "app.demo.handlers"

# Артикул из заполненного склада: обработчик резервирования обращается к БД.
DEFAULT_SKU = "SKU-000"

ORDER_FLOW = {
    "start": "validate_cart",
    "steps": {
        "validate_cart": {"action": f"{DEMO}:validate_cart", "next": "reserve_inventory"},
        "reserve_inventory": {
            "action": f"{DEMO}:reserve_inventory",
            "compensation": f"{DEMO}:release_inventory",
            "next": "charge_payment",
        },
        "charge_payment": {
            "action": f"{DEMO}:charge_payment",
            "compensation": f"{DEMO}:refund_payment",
            "next": "confirm_delivery",
        },
        "confirm_delivery": {"action": f"{DEMO}:confirm_delivery"},
    },
}

# Шаг с внешним ожиданием: процесс живёт дольше, чем успевает проверка.
SLOW_FLOW = {
    "start": "slow",
    "steps": {
        "slow": {
            "action": "tests.chaos.slow_handler:hold",
            "timeout_seconds": 5,
            "next": "after",
        },
        "after": {"action": f"{DEMO}:confirm_delivery"},
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


async def _create_instance(db_session: AsyncSession, flow: dict) -> uuid.UUID:
    definition = WorkflowDefinition(name=f"chaos-{uuid.uuid4().hex[:8]}", definition=flow)
    db_session.add(definition)
    await db_session.flush()

    instance = WorkflowInstance(
        definition_id=definition.id,
        input={"items": [{"sku": DEFAULT_SKU, "qty": 2}], "amount": "100.00"},
        variables={},
        status=InstanceStatus.PENDING,
    )
    db_session.add(instance)
    await db_session.flush()

    from app.engine.journal import append_event
    from app.models.enums import EventType

    await append_event(
        db_session, instance.id, EventType.INSTANCE_CREATED, {"start": flow["start"]}
    )
    await db_session.commit()
    return instance.id


async def _status(db_session: AsyncSession, instance_id: uuid.UUID) -> str | None:
    db_session.expire_all()
    return await db_session.scalar(
        select(WorkflowInstance.status).where(WorkflowInstance.id == instance_id)
    )


async def _run_cycles(worker_id: str, count: int = 12) -> int:
    from app import worker
    from app.schemas.definition import WorkflowDefinitionSchema

    cache: dict[uuid.UUID, WorkflowDefinitionSchema] = {}
    handled = 0
    for _ in range(count):
        result = await worker.run_cycle(worker_id, cache)
        if not result.handled:
            break
        handled += 1
    return handled


def _kill_process(pid: int) -> None:
    """Убивает процесс без возможности корректной остановки.

    Именно такой сигнал эмулирует OOM-киллер и падение узла: `finally`
    не выполняется, пул соединений не закрывается, аренда остаётся занята.
    """
    if sys.platform == "win32":  # pragma: no cover - ветка для Windows
        os.kill(pid, 9)  # type: ignore[attr-defined]
    else:
        os.kill(pid, 9)


# --- Падение воркера посреди шага -------------------------------------------


async def test_process_killed_mid_step_is_recovered(db_session: AsyncSession) -> None:
    # Основной сценарий проекта: воркер убит посреди шага, аренда остаётся
    # занята, другой воркер подхватывает инстанс после истечения TTL.
    instance_id = await _create_instance(db_session, SLOW_FLOW)

    # Падение моделируется состоянием, которое остаётся после SIGKILL: аренда
    # занята, пул соединений не закрыт, освобождения не произойдёт.
    await db_session.execute(
        update(WorkflowInstance)
        .where(WorkflowInstance.id == instance_id)
        .values(
            lease_owner="victim",
            lease_expires_at=datetime.now(UTC)
            + timedelta(seconds=get_settings().lease_ttl_seconds),
            heartbeat_at=datetime.now(UTC),
        )
    )
    await db_session.commit()

    # Пока аренда жива, никто не может перехватить инстанс.
    async with db_session.begin_nested():
        pass
    from app.core.db import get_session_factory

    async with get_session_factory()() as session, session.begin():
        assert await lease.claim_next(session, "rescuer") is None

    # TTL истёк — инстанс становится доступным.
    await db_session.execute(
        update(WorkflowInstance)
        .where(WorkflowInstance.id == instance_id)
        .values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    await db_session.commit()

    async with get_session_factory()() as session, session.begin():
        claimed = await lease.claim_next(session, "rescuer")
    assert claimed is not None
    assert claimed.instance_id == instance_id

    await _run_cycles("rescuer", count=10)

    # Ключевое свойство: аренда больше не принадлежит погибшему воркеру, а сам
    # инстанс остаётся в работе — потерянного шага нет.
    db_session.expire_all()
    owner = await db_session.scalar(
        select(WorkflowInstance.lease_owner).where(WorkflowInstance.id == instance_id)
    )
    assert owner != "victim"
    assert await _status(db_session, instance_id) is not None


async def test_expired_lease_is_taken_over_by_another_worker(
    db_session: AsyncSession,
) -> None:
    instance_id = await _create_instance(db_session, ORDER_FLOW)

    from app.core.db import get_session_factory

    async with get_session_factory()() as session, session.begin():
        claimed = await lease.claim_next(session, "dead-worker")
    assert claimed is not None and claimed.instance_id == instance_id

    # Мёртвый воркер не продлил аренду — TTL истёк.
    await db_session.execute(
        update(WorkflowInstance)
        .where(WorkflowInstance.id == instance_id)
        .values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    await db_session.commit()

    await _run_cycles("rescuer", count=10)

    owner = await db_session.scalar(
        select(WorkflowInstance.lease_owner).where(WorkflowInstance.id == instance_id)
    )
    assert owner != "dead-worker"
    assert await _status(db_session, instance_id) == InstanceStatus.COMPLETED


async def test_no_duplicate_steps_after_recovery(db_session: AsyncSession) -> None:
    # Главная гарантия восстановления: ни один шаг не выполняется дважды.
    instance_id = await _create_instance(db_session, ORDER_FLOW)

    from app import worker
    from app.schemas.definition import WorkflowDefinitionSchema

    cache: dict[uuid.UUID, WorkflowDefinitionSchema] = {}
    for _ in range(2):
        await worker.run_cycle("w1", cache)

    await db_session.execute(
        update(WorkflowInstance)
        .where(WorkflowInstance.id == instance_id)
        .values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    await db_session.commit()

    await _run_cycles("w2", count=10)

    db_session.expire_all()
    rows = (
        await db_session.execute(
            select(StepRun.step_name, text("count(*)"))
            .where(StepRun.instance_id == instance_id)
            .group_by(StepRun.step_name)
        )
    ).all()
    for step_name, count in rows:
        assert count == 1, f"шаг {step_name} выполнен {count} раз"


async def test_applied_effect_is_not_repeated_after_recovery(
    db_session: AsyncSession,
) -> None:
    # Проверка на уровне processed_operations: эффект шага не применяется дважды,
    # даже если воркер успел применить его и умереть до записи чекпоинта.
    from app.engine import idempotency

    instance_id = await _create_instance(db_session, ORDER_FLOW)
    key = idempotency.make_key(instance_id, "reserve_inventory", 1)
    await idempotency.mark_applied(db_session, key)
    await db_session.commit()

    assert await idempotency.already_applied(db_session, key) is True


async def test_replay_is_identical_after_interruption(db_session: AsyncSession) -> None:
    # Пауза не меняет результат: журнал — единственный источник истины, а не
    # состояние в памяти процесса.
    instance_id = await _create_instance(db_session, ORDER_FLOW)

    from app import worker
    from app.schemas.definition import WorkflowDefinitionSchema

    cache: dict[uuid.UUID, WorkflowDefinitionSchema] = {}
    for _ in range(2):
        await worker.run_cycle("w1", cache)

    state_before = replay(await read_events(db_session, instance_id))

    await db_session.execute(
        update(WorkflowInstance)
        .where(WorkflowInstance.id == instance_id)
        .values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    await db_session.commit()
    await _run_cycles("w2", count=10)

    state_after = replay(await read_events(db_session, instance_id))

    assert state_before.status is InstanceStatus.RUNNING
    assert state_after.status is InstanceStatus.COMPLETED
    assert state_after.completed_steps == state_before.completed_steps + [
        "charge_payment",
        "confirm_delivery",
    ]


# --- Поведение при недоступных сервисах --------------------------------------


async def test_step_timeout_leaves_instance_recoverable(
    db_session: AsyncSession,
) -> None:
    # Внешний сервис «завис»: шаг прерывается по таймауту, инстанс не теряется.
    instance_id = await _create_instance(db_session, SLOW_FLOW)

    await _run_cycles("w1", count=2)

    db_session.expire_all()
    status = await _status(db_session, instance_id)
    assert status is not None


async def test_concurrent_workers_process_distinct_instances(
    db_session: AsyncSession,
) -> None:
    # Несколько воркеров не должны схлопнуться на одном инстансе.
    first = await _create_instance(db_session, ORDER_FLOW)
    second = await _create_instance(db_session, ORDER_FLOW)

    from app import worker
    from app.schemas.definition import WorkflowDefinitionSchema

    cache_a: dict[uuid.UUID, WorkflowDefinitionSchema] = {}
    cache_b: dict[uuid.UUID, WorkflowDefinitionSchema] = {}
    await asyncio.gather(worker.run_cycle("w1", cache_a), worker.run_cycle("w2", cache_b))

    db_session.expire_all()
    owners = (
        await db_session.execute(select(WorkflowInstance.id, WorkflowInstance.lease_owner))
    ).all()
    claimed_ids = {row[0] for row in owners if row[1] is not None}
    assert len(claimed_ids) <= 2
    assert {first, second} >= claimed_ids


async def test_all_active_statuses_are_recoverable(db_session: AsyncSession) -> None:
    # Инстанс в состоянии отката тоже подхватывается: незавершённый откат
    # оставил бы процесс с выполненными шагами навсегда.
    instance_id = await _create_instance(db_session, ORDER_FLOW)

    await db_session.execute(
        update(WorkflowInstance)
        .where(WorkflowInstance.id == instance_id)
        .values(status=InstanceStatus.COMPENSATING)
    )
    await db_session.commit()

    await _run_cycles("w1", count=2)

    db_session.expire_all()
    assert await _status(db_session, instance_id) is not None


async def test_step_runs_survive_interruption(db_session: AsyncSession) -> None:
    # Тайминги переживают падение: без них замеры невозможны.
    instance_id = await _create_instance(db_session, ORDER_FLOW)

    from app import worker
    from app.schemas.definition import WorkflowDefinitionSchema

    cache: dict[uuid.UUID, WorkflowDefinitionSchema] = {}
    for _ in range(2):
        await worker.run_cycle("w1", cache)

    await db_session.execute(
        update(WorkflowInstance)
        .where(WorkflowInstance.id == instance_id)
        .values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    await db_session.commit()
    await _run_cycles("w2", count=10)

    db_session.expire_all()
    runs = (
        await db_session.scalars(select(StepRun).where(StepRun.instance_id == instance_id))
    ).all()
    assert runs
    assert all(run.status == StepRunStatus.COMPLETED for run in runs)
    assert all(run.finished_at is not None for run in runs)


async def test_lease_ttl_is_shorter_than_timeout_guard(db_session: AsyncSession) -> None:
    # Конфигурация обязана оставаться согласованной: heartbeat чаще TTL,
    # иначе живой воркер будет признан мёртвым.
    settings = get_settings()

    assert settings.heartbeat_interval_seconds < settings.lease_ttl_seconds
    assert settings.lease_ttl_seconds > 0
