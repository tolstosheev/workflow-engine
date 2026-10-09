"""Durable-таймеры в исполнении: инстанс обязан проснуться.

Набор проверяет связку «ожидание объявлено → таймер сработал → шаг выполнен».
Схема и `scheduler` покрыты отдельно; здесь важно поведение целиком, потому
что именно оно ломалось: `wait_before_seconds` не применялся вовсе, а
сработавшие таймеры помечались без события — инстанс оставался в `WAITING`
навсегда.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.journal import read_events
from app.models.enums import EventType, InstanceStatus, StepRunStatus
from app.models.workflow import StepRun, Timer, WorkflowDefinition, WorkflowInstance
from app.schemas.definition import WorkflowDefinitionSchema

pytestmark = pytest.mark.integration

DEMO = "app.demo.handlers"

# Ожидание задано заведомо большим: тест не ждёт его настолько, а срок
# таймера он сдвигает сам — см. `_expire_timers`.
LONG_WAIT = 3600

WAITING_FLOW = {
    "start": "validate_cart",
    "steps": {
        "validate_cart": {"action": f"{DEMO}:validate_cart", "next": "await_delivery"},
        "await_delivery": {
            "action": f"{DEMO}:confirm_delivery",
            "wait_before_seconds": LONG_WAIT,
        },
    },
}

NO_WAIT_FLOW = {
    "start": "validate_cart",
    "steps": {
        "validate_cart": {"action": f"{DEMO}:validate_cart", "next": "confirm_delivery"},
        "confirm_delivery": {"action": f"{DEMO}:confirm_delivery"},
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


async def _new_instance(db_session: AsyncSession, flow: dict | None = None) -> uuid.UUID:
    definition = WorkflowDefinition(
        name=f"timer-{uuid.uuid4().hex[:8]}", definition=flow or WAITING_FLOW
    )
    db_session.add(definition)
    await db_session.flush()

    instance = WorkflowInstance(
        definition_id=definition.id,
        input={"items": [{"sku": "SKU-1", "qty": 1}], "amount": "10.00"},
        variables={},
        status=InstanceStatus.PENDING,
    )
    db_session.add(instance)
    await db_session.flush()

    from app.engine.journal import append_event

    start = (flow or WAITING_FLOW)["start"]
    await append_event(db_session, instance.id, EventType.INSTANCE_CREATED, {"start": start})
    return instance.id


async def _expire_timers(db_session: AsyncSession, instance_id: uuid.UUID) -> int:
    """Сдвигает срок ожиданий в прошлое — тест вместо ожидания реального времени."""
    result = await db_session.execute(
        update(Timer)
        .where(Timer.instance_id == instance_id, Timer.fired.is_(False))
        .values(due_at=datetime.now(UTC) - timedelta(seconds=1))
        .returning(Timer.id)
    )
    await db_session.flush()
    return len(result.all())


async def _run_cycles(worker_id: str, count: int = 12) -> int:
    from app import worker as worker_module

    cache: dict[uuid.UUID, WorkflowDefinitionSchema] = {}
    handled = 0
    for _ in range(count):
        result = await worker_module.run_cycle(worker_id, cache)
        if not result.handled:
            break
        handled += 1
    return handled


async def _status(db_session: AsyncSession, instance_id: uuid.UUID) -> str | None:
    db_session.expire_all()
    return await db_session.scalar(
        select(WorkflowInstance.status).where(WorkflowInstance.id == instance_id)
    )


async def _step_names(db_session: AsyncSession, instance_id: uuid.UUID) -> list[str]:
    db_session.expire_all()
    return list(
        await db_session.scalars(
            select(StepRun.step_name)
            .where(StepRun.instance_id == instance_id)
            .order_by(StepRun.started_at)
        )
    )


async def _events(db_session: AsyncSession, instance_id: uuid.UUID) -> list[EventType]:
    db_session.expire_all()
    return [event.type for event in await read_events(db_session, instance_id)]


# --- Планирование ожидания ---------------------------------------------------


async def test_declared_wait_is_scheduled_not_executed(db_session: AsyncSession) -> None:
    # Ожидание обязано стать durable-объектом, а не sleep: без записи таймера
    # падение воркера потеряло бы паузу, а инстанс завис бы в RUNNING.
    instance_id = await _new_instance(db_session)
    await db_session.commit()

    await _run_cycles("w1")

    assert await _status(db_session, instance_id) == InstanceStatus.WAITING
    db_session.expire_all()
    timers = list(await db_session.scalars(select(Timer).where(Timer.instance_id == instance_id)))
    assert len(timers) == 1
    assert timers[0].step_name == "await_delivery"


async def test_waiting_step_is_not_executed_before_timer(db_session: AsyncSession) -> None:
    instance_id = await _new_instance(db_session)
    await db_session.commit()

    await _run_cycles("w1")

    assert await _step_names(db_session, instance_id) == ["validate_cart"]


async def test_waiting_step_does_not_create_second_timer(db_session: AsyncSession) -> None:
    # Повторное планирование на каждом цикле отодвинуло бы ожидание бесконечно,
    # и шаг не наступил бы никогда.
    instance_id = await _new_instance(db_session)
    await db_session.commit()

    await _run_cycles("w2", count=6)

    db_session.expire_all()
    timers = list(await db_session.scalars(select(Timer).where(Timer.instance_id == instance_id)))
    assert len(timers) == 1


async def test_flow_without_wait_completes(db_session: AsyncSession) -> None:
    # Регрессия: шаг без объявленного ожидания исполняется сразу и не ждёт
    # таймера, которого никто не планировал.
    instance_id = await _new_instance(db_session, NO_WAIT_FLOW)
    await db_session.commit()

    await _run_cycles("w1")

    assert await _status(db_session, instance_id) == InstanceStatus.COMPLETED
    assert await _step_names(db_session, instance_id) == ["validate_cart", "confirm_delivery"]


# --- Разбужение --------------------------------------------------------------


async def test_due_timer_wakes_instance(db_session: AsyncSession) -> None:
    # Таймер без разбуждения оставляет процесс в WAITING навсегда: следующий
    # шаг не выполняется, и оператор видит зависший заказ без причины.
    instance_id = await _new_instance(db_session)
    await db_session.commit()

    await _run_cycles("w1")
    assert await _status(db_session, instance_id) == InstanceStatus.WAITING

    await _expire_timers(db_session, instance_id)
    await db_session.commit()

    await _run_cycles("w1")

    assert await _status(db_session, instance_id) == InstanceStatus.COMPLETED


async def test_fired_timer_writes_event(db_session: AsyncSession) -> None:
    # Событие TIMER_FIRED обязательно: без него replay не вернул бы инстанс
    # в RUNNING, и восстановление после падения воркера застряло бы.
    instance_id = await _new_instance(db_session)
    await db_session.commit()
    await _run_cycles("w1")

    await _expire_timers(db_session, instance_id)
    await db_session.commit()
    await _run_cycles("w1")

    assert EventType.TIMER_FIRED in await _events(db_session, instance_id)


async def test_step_after_timer_is_executed(db_session: AsyncSession) -> None:
    instance_id = await _new_instance(db_session)
    await db_session.commit()
    await _run_cycles("w1")

    await _expire_timers(db_session, instance_id)
    await db_session.commit()
    await _run_cycles("w1")

    assert await _step_names(db_session, instance_id) == ["validate_cart", "await_delivery"]


async def test_future_timer_does_not_wake_instance(db_session: AsyncSession) -> None:
    # Досрочное пробуждение сломало бы смысл ожидания: шаг начал бы выполняться
    # раньше объявленного срока.
    instance_id = await _new_instance(db_session)
    await db_session.commit()

    await _run_cycles("w2", count=6)

    assert await _status(db_session, instance_id) == InstanceStatus.WAITING
    assert await _step_names(db_session, instance_id) == ["validate_cart"]


async def test_timer_is_fired_only_once(db_session: AsyncSession) -> None:
    # Второе срабатывание вернуло бы уже завершённый процесс в RUNNING.
    instance_id = await _new_instance(db_session)
    await db_session.commit()
    await _run_cycles("w1")

    await _expire_timers(db_session, instance_id)
    await db_session.commit()
    await _run_cycles("w1")
    await _run_cycles("w1")

    types = await _events(db_session, instance_id)
    assert types.count(EventType.TIMER_FIRED) == 1


async def test_pending_timer_does_not_block_other_instances(db_session: AsyncSession) -> None:
    # Спящий инстанс не должен занимать подбор: иначе воркер брал бы его снова
    # и снова, и очередь за ним стояла бы.
    waiting_id = await _new_instance(db_session)
    await db_session.commit()
    await _run_cycles("w1")

    fresh_id = await _new_instance(db_session, NO_WAIT_FLOW)
    await db_session.commit()

    await _run_cycles("w1")

    assert await _status(db_session, fresh_id) == InstanceStatus.COMPLETED
    assert await _status(db_session, waiting_id) == InstanceStatus.WAITING


async def test_timers_fire_without_new_instances(db_session: AsyncSession) -> None:
    # Разбуждение не должно зависеть от наличия работы в очереди: проверка
    # таймеров обязана происходить и тогда, когда подбирать нечего.
    instance_id = await _new_instance(db_session)
    await db_session.commit()
    await _run_cycles("w1")

    await _expire_timers(db_session, instance_id)
    await db_session.commit()

    from app import worker as worker_module

    cache: dict[uuid.UUID, WorkflowDefinitionSchema] = {}
    result = await worker_module.run_cycle("w1", cache)

    assert result.handled is True
    assert EventType.TIMER_FIRED in await _events(db_session, instance_id)


async def test_step_run_records_timings_after_wait(db_session: AsyncSession) -> None:
    # Замеры невозможны без таймингов: шаг после ожидания обязан их иметь.
    instance_id = await _new_instance(db_session)
    await db_session.commit()
    await _run_cycles("w1")

    await _expire_timers(db_session, instance_id)
    await db_session.commit()
    await _run_cycles("w1")

    db_session.expire_all()
    rows = (
        await db_session.scalars(
            select(StepRun).where(
                StepRun.instance_id == instance_id, StepRun.step_name == "await_delivery"
            )
        )
    ).all()
    assert rows
    assert rows[0].status == StepRunStatus.COMPLETED
    assert rows[0].finished_at is not None


async def test_worker_cycle_is_available_to_scripts(db_session: AsyncSession) -> None:
    # Скрипты замеров вызывают цикл напрямую; отсутствие публичной функции
    # означало бы, что они зависят от приватного API воркера.
    from app import worker as worker_module

    assert callable(worker_module.run_cycle)
