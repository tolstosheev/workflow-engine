"""Тесты lease на реальном PostgreSQL.

Конкуренция двух воркеров не воспроизводится на SQLite: SKIP LOCKED, блокировки
строк и условный UPDATE — поведение сервера, а не клиентской библиотеки.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.engine import lease
from app.models.enums import ACTIVE_STATUSES, EventType, InstanceStatus
from app.models.workflow import WorkflowDefinition, WorkflowInstance

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


async def _new_instance(
    db_session: AsyncSession, status: InstanceStatus = InstanceStatus.PENDING
) -> uuid.UUID:
    definition = WorkflowDefinition(
        name=f"lease-{uuid.uuid4().hex[:8]}",
        definition={"start": "a", "steps": {"a": {"action": "app.demo.handlers:validate_cart"}}},
    )
    db_session.add(definition)
    await db_session.flush()

    instance = WorkflowInstance(definition_id=definition.id, input={}, variables={}, status=status)
    db_session.add(instance)
    await db_session.flush()
    return instance.id


async def _expire(db_session: AsyncSession, instance_id: uuid.UUID, ttl: int = -1) -> None:
    """Делает аренду истёкшей, обходя триггер: истёкшую аренду снимает воркер."""
    await db_session.execute(
        update(WorkflowInstance)
        .where(WorkflowInstance.id == instance_id)
        .values(lease_expires_at=datetime.now(UTC) + timedelta(seconds=ttl), heartbeat_at=None)
    )


# --- Захват ----------------------------------------------------------------


async def test_acquire_succeeds_on_free_instance(db_session: AsyncSession) -> None:
    instance_id = await _new_instance(db_session)

    assert await lease.acquire(db_session, "w1", instance_id) is True
    assert await lease.current_owner(db_session, instance_id) == "w1"


async def test_acquire_fails_when_lease_is_live(db_session: AsyncSession) -> None:
    # Живая аренда блокирует перехват: иначе шаг выполнился бы двумя воркерами.
    instance_id = await _new_instance(db_session)
    await lease.acquire(db_session, "w1", instance_id)

    assert await lease.acquire(db_session, "w2", instance_id) is False


async def test_acquire_succeeds_after_lease_expired(db_session: AsyncSession) -> None:
    instance_id = await _new_instance(db_session)
    await lease.acquire(db_session, "w1", instance_id)
    await _expire(db_session, instance_id)
    await db_session.flush()

    assert await lease.acquire(db_session, "w2", instance_id) is True
    assert await lease.current_owner(db_session, instance_id) == "w2"


async def test_acquire_sets_expiry_and_heartbeat(db_session: AsyncSession) -> None:
    instance_id = await _new_instance(db_session)

    await lease.acquire(db_session, "w1", instance_id, ttl_seconds=30)
    await db_session.refresh(await db_session.get(WorkflowInstance, instance_id))  # type: ignore[arg-type]

    expires = await db_session.scalar(
        select(WorkflowInstance.lease_expires_at).where(WorkflowInstance.id == instance_id)
    )
    heartbeat = await db_session.scalar(
        select(WorkflowInstance.heartbeat_at).where(WorkflowInstance.id == instance_id)
    )

    assert expires is not None
    assert heartbeat is not None
    assert (expires - datetime.now(UTC)).total_seconds() > 20


# --- Подбор задачи ---------------------------------------------------------


async def test_claim_next_picks_pending_instance(db_session: AsyncSession) -> None:
    instance_id = await _new_instance(db_session)

    claimed = await lease.claim_next(db_session, "w1")

    assert claimed is not None
    assert claimed.instance_id == instance_id
    assert claimed.lease_owner == "w1"


async def test_claim_next_skips_claimed_instance(db_session: AsyncSession) -> None:
    instance_id = await _new_instance(db_session)
    await lease.acquire(db_session, "w1", instance_id)

    assert await lease.claim_next(db_session, "w2") is None


async def test_claim_next_ignores_terminal_instances(db_session: AsyncSession) -> None:
    # Накопленные завершённые инстансы не должны попадать в выборку воркера.
    await _new_instance(db_session, status=InstanceStatus.COMPLETED)

    assert await lease.claim_next(db_session, "w1") is None


async def test_claim_next_skips_waiting_instance(db_session: AsyncSession) -> None:
    # Спящий инстанс обслуживает таймер, а не воркер. Если бы он попадал в
    # выборку, воркер брал бы его снова и снова, и очередь за ним стояла бы.
    await _new_instance(db_session, status=InstanceStatus.WAITING)

    assert await lease.claim_next(db_session, "w1") is None


async def test_claim_next_skips_waiting_but_takes_ready_instance(
    db_session: AsyncSession,
) -> None:
    # Второй инстанс обязан обрабатываться, пока первый спит: иначе одно
    # ожидание останавливало бы всю очередь.
    await _new_instance(db_session, status=InstanceStatus.WAITING)
    ready_id = await _new_instance(db_session, status=InstanceStatus.PENDING)

    claimed = await lease.claim_next(db_session, "w1")

    assert claimed is not None
    assert claimed.instance_id == ready_id


async def test_woken_instance_becomes_claimable_again(db_session: AsyncSession) -> None:
    # После таймера инстанс возвращается в RUNNING и обязан снова попадать
    # в подбор: без этого проснувшийся процесс никогда бы не исполнил шаг.
    from app.engine import scheduler
    from app.engine.journal import append_event

    instance_id = await _new_instance(db_session, status=InstanceStatus.WAITING)
    await scheduler.schedule(db_session, instance_id, "await_delivery", 0)
    await append_event(db_session, instance_id, EventType.TIMER_FIRED, {"step": "await_delivery"})
    row = await db_session.get(WorkflowInstance, instance_id)
    assert row is not None
    row.status = InstanceStatus.RUNNING
    await db_session.flush()

    claimed = await lease.claim_next(db_session, "w1")

    assert claimed is not None
    assert claimed.instance_id == instance_id


async def test_claim_next_covers_compensating_instance(db_session: AsyncSession) -> None:
    # Откат должен продолжаться даже после падения воркера — иначе процесс
    # останется с выполненными шагами навсегда.
    await _new_instance(db_session, status=InstanceStatus.COMPENSATING)

    assert await lease.claim_next(db_session, "w1") is not None


async def test_claim_next_returns_none_when_nothing_to_do(db_session: AsyncSession) -> None:
    assert await lease.claim_next(db_session, "w1") is None


async def test_claim_next_takes_exactly_one_instance(db_session: AsyncSession) -> None:
    # Захват пачки оставил бы воркера держать аренду на инстансах, которые он
    # не обрабатывает: они висели бы занятыми до истечения TTL.
    for _ in range(3):
        await _new_instance(db_session)

    claimed = await lease.claim_next(db_session, "w1")

    assert claimed is not None
    owners = dict(
        (await db_session.execute(select(WorkflowInstance.id, WorkflowInstance.lease_owner))).all()
    )
    taken = {instance_id for instance_id, owner in owners.items() if owner == "w1"}
    assert taken == {claimed.instance_id}


async def test_concurrent_workers_claim_different_instances(db_engine: None) -> None:
    # Два воркера не должны получить один инстанс: это тот самый случай, ради
    # которого существует lease. Инстансов два, иначе второму нечего брать.
    from app.core.db import get_session_factory

    factory = get_session_factory()

    async def prepare() -> None:
        async with factory() as session, session.begin():
            definition = WorkflowDefinition(
                name=f"race-{uuid.uuid4().hex[:8]}",
                definition={"start": "a", "steps": {"a": {"action": "app.demo:validate_cart"}}},
            )
            session.add(definition)
            await session.flush()
            for _ in range(2):
                session.add(WorkflowInstance(definition_id=definition.id, input={}, variables={}))

    async def claim(worker_id: str) -> uuid.UUID | None:
        async with factory() as session, session.begin():
            claimed = await lease.claim_next(session, worker_id)
            return claimed.instance_id if claimed else None

    await prepare()
    first, second = await asyncio.gather(claim("w1"), claim("w2"))

    assert first is not None
    assert second is not None
    assert first != second


async def test_concurrent_workers_do_not_share_one_instance(db_engine: None) -> None:
    # Один инстанс и двое воркеров: получить его может только один. Второй
    # обязан получить пусто, а не ждать и не взять чужую работу.
    from app.core.db import get_session_factory

    factory = get_session_factory()

    async def prepare() -> None:
        async with factory() as session, session.begin():
            definition = WorkflowDefinition(
                name=f"race1-{uuid.uuid4().hex[:8]}",
                definition={"start": "a", "steps": {"a": {"action": "app.demo:validate_cart"}}},
            )
            session.add(definition)
            await session.flush()
            session.add(WorkflowInstance(definition_id=definition.id, input={}, variables={}))

    async def claim(worker_id: str) -> uuid.UUID | None:
        async with factory() as session, session.begin():
            claimed = await lease.claim_next(session, worker_id)
            return claimed.instance_id if claimed else None

    await prepare()
    results = await asyncio.gather(claim("w1"), claim("w2"), claim("w3"))

    assert len([r for r in results if r is not None]) == 1


# --- Heartbeat -------------------------------------------------------------


async def test_heartbeat_extends_lease(db_session: AsyncSession) -> None:
    instance_id = await _new_instance(db_session)
    await lease.acquire(db_session, "w1", instance_id, ttl_seconds=5)

    assert await lease.heartbeat(db_session, instance_id, "w1") is True
    await db_session.refresh(await db_session.get(WorkflowInstance, instance_id))  # type: ignore[arg-type]

    expires = await db_session.scalar(
        select(WorkflowInstance.lease_expires_at).where(WorkflowInstance.id == instance_id)
    )
    assert expires is not None
    assert (expires - datetime.now(UTC)).total_seconds() > get_settings().lease_ttl_seconds - 5


async def test_heartbeat_fails_for_foreign_worker(db_session: AsyncSession) -> None:
    # Потерявший lease воркер должен узнать об этом: иначе он продолжит работу
    # поверх нового и продублирует эффект.
    instance_id = await _new_instance(db_session)
    await lease.acquire(db_session, "w1", instance_id)

    assert await lease.heartbeat(db_session, instance_id, "w2") is False


async def test_heartbeat_loop_exits_when_the_lease_is_gone(db_session: AsyncSession) -> None:
    # Отказ продлить аренду — признак, что инстанс ушёл к другому воркеру:
    # цикл обязан выйти из петли, иначе он будет бить по чужой аренде до конца
    # работы. Счётчик потери при этом ведёт проверка перед записью результата —
    # единственная точка, где потеря становится решением.
    from app.engine.executor import heartbeat_loop

    instance_id = await _new_instance(db_session)
    await lease.acquire(db_session, "w1", instance_id)

    await asyncio.wait_for(heartbeat_loop(instance_id, "w2", asyncio.Event()), timeout=5)


async def test_heartbeat_fails_on_free_instance(db_session: AsyncSession) -> None:
    instance_id = await _new_instance(db_session)

    assert await lease.heartbeat(db_session, instance_id, "w1") is False


# --- Освобождение ----------------------------------------------------------


async def test_release_frees_lease(db_session: AsyncSession) -> None:
    instance_id = await _new_instance(db_session)
    await lease.acquire(db_session, "w1", instance_id)

    assert await lease.release(db_session, instance_id, "w1") is True
    assert await lease.current_owner(db_session, instance_id) is None


async def test_release_ignores_foreign_worker(db_session: AsyncSession) -> None:
    # Иначе потерявший воркер сбросил бы прогресс нового.
    instance_id = await _new_instance(db_session)
    await lease.acquire(db_session, "w1", instance_id)

    assert await lease.release(db_session, instance_id, "w2") is False
    assert await lease.current_owner(db_session, instance_id) == "w1"


async def test_released_instance_can_be_claimed_again(db_session: AsyncSession) -> None:
    instance_id = await _new_instance(db_session)
    await lease.acquire(db_session, "w1", instance_id)
    await lease.release(db_session, instance_id, "w1")

    assert await lease.acquire(db_session, "w2", instance_id) is True


# --- Обнаружение брошенных инстансов ---------------------------------------


async def test_active_statuses_cover_exactly_the_open_ones() -> None:
    # Перечисление живёт в перечислении, а не в модуле аренды: от него
    # зависят подбор, фильтры списка и метрики, и разойтись они не должны.
    assert InstanceStatus.RUNNING in ACTIVE_STATUSES
    assert InstanceStatus.PAUSED in ACTIVE_STATUSES
    assert InstanceStatus.COMPLETED not in ACTIVE_STATUSES
