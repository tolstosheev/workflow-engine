"""Сквозные тесты исполнения дефинишена воркером.

Это то, ради чего написан движок: инстанс проходит процесс целиком, а после
сбоя продолжается с места остановки без потерь и дублей.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine import lease
from app.engine.journal import read_events, replay
from app.models.enums import EventType, InstanceStatus, StepRunStatus
from app.models.workflow import (
    StepRun,
    WorkflowDefinition,
    WorkflowEvent,
    WorkflowInstance,
)
from app.schemas.definition import WorkflowDefinitionSchema

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("inventory")]

DEMO = "app.demo.handlers"

# Артикул из заполненного склада: обработчик резервирования обращается к БД
# и без существующей строки падает с «товар не найден на складе».
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

FAILING_FLOW = {
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
            "retry": {"max_attempts": 1},
        },
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


async def _create_instance(
    db_session: AsyncSession, flow: dict, instance_input: dict | None = None
) -> tuple[uuid.UUID, WorkflowDefinition]:
    definition = WorkflowDefinition(name=f"flow-{uuid.uuid4().hex[:8]}", definition=flow)
    db_session.add(definition)
    await db_session.flush()

    instance = WorkflowInstance(
        definition_id=definition.id,
        input=instance_input or {"items": [{"sku": DEFAULT_SKU, "qty": 2}], "amount": "1499.90"},
        variables={},
        status=InstanceStatus.PENDING,
    )
    db_session.add(instance)
    await db_session.flush()
    return instance.id, definition


async def _seed_journal(db_session: AsyncSession, instance_id: uuid.UUID, flow: dict) -> None:
    """Создаёт событие появления инстанса, как это делает сервис запуска."""
    from app.engine.journal import append_event

    await append_event(
        db_session, instance_id, EventType.INSTANCE_CREATED, {"start": flow["start"]}
    )


async def _run_cycles(worker_id: str, count: int = 12) -> int:
    """Прогоняет циклы воркера до опустошения очереди или лимита шагов.

    Останавливаться по `terminal` нельзя: после неудачного шага терминальным
    для цикла оказывается результат шага, а откат выполняется следующим циклом.
    """
    from app import worker

    cache: dict[uuid.UUID, WorkflowDefinitionSchema] = {}
    handled = 0
    for _ in range(count):
        result = await worker.run_cycle(worker_id, cache)
        if not result.handled:
            break
        handled += 1
    return handled


async def _status(db_session: AsyncSession, instance_id: uuid.UUID) -> str | None:
    # expire_all обязателен: воркер работает в других сессиях, а identity map
    # тестовой сессии вернул бы состояние, прочитанное до запуска циклов.
    db_session.expire_all()
    return await db_session.scalar(
        select(WorkflowInstance.status).where(WorkflowInstance.id == instance_id)
    )


async def _step_names(db_session: AsyncSession, instance_id: uuid.UUID) -> list[str]:
    db_session.expire_all()
    rows = await db_session.scalars(
        select(StepRun.step_name)
        .where(StepRun.instance_id == instance_id)
        .order_by(StepRun.started_at)
    )
    return list(rows)


# --- Полный проход ---------------------------------------------------------


async def test_instance_completes_end_to_end(db_session: AsyncSession) -> None:
    instance_id, _ = await _create_instance(db_session, ORDER_FLOW)
    await _seed_journal(db_session, instance_id, ORDER_FLOW)
    await db_session.commit()

    await _run_cycles("w1")

    assert await _status(db_session, instance_id) == InstanceStatus.COMPLETED


async def test_all_steps_executed_in_order(db_session: AsyncSession) -> None:
    instance_id, _ = await _create_instance(db_session, ORDER_FLOW)
    await _seed_journal(db_session, instance_id, ORDER_FLOW)
    await db_session.commit()

    await _run_cycles("w1")

    assert await _step_names(db_session, instance_id) == [
        "validate_cart",
        "reserve_inventory",
        "charge_payment",
        "confirm_delivery",
    ]


async def test_step_outputs_become_variables(db_session: AsyncSession) -> None:
    # Выход шага попадает в переменные: следующий шаг читает данные оттуда.
    instance_id, _ = await _create_instance(db_session, ORDER_FLOW)
    await _seed_journal(db_session, instance_id, ORDER_FLOW)
    await db_session.commit()

    await _run_cycles("w1")
    await db_session.refresh(await db_session.get(WorkflowInstance, instance_id))  # type: ignore[arg-type]

    row = await db_session.get(WorkflowInstance, instance_id)
    assert row is not None
    assert row.variables.get("reserved_units") == 2
    assert row.variables.get("charged") is True


async def test_journal_contains_full_history(db_session: AsyncSession) -> None:
    instance_id, _ = await _create_instance(db_session, ORDER_FLOW)
    await _seed_journal(db_session, instance_id, ORDER_FLOW)
    await db_session.commit()

    await _run_cycles("w1")

    events = await read_events(db_session, instance_id)
    types = [e.type for e in events]
    assert types.count(EventType.STEP_STARTED) == 4
    assert types.count(EventType.STEP_COMPLETED) == 4
    assert types[-1] == EventType.INSTANCE_COMPLETED


async def test_step_runs_record_timings(db_session: AsyncSession) -> None:
    # Тайминги обязательны: без них невозможно предъявить замеры.
    instance_id, _ = await _create_instance(db_session, ORDER_FLOW)
    await _seed_journal(db_session, instance_id, ORDER_FLOW)
    await db_session.commit()

    await _run_cycles("w1")

    rows = (
        await db_session.scalars(select(StepRun).where(StepRun.instance_id == instance_id))
    ).all()
    assert rows
    for run in rows:
        assert run.started_at is not None
        assert run.finished_at is not None
        assert run.status == StepRunStatus.COMPLETED


async def test_step_duration_is_observed(db_session: AsyncSession) -> None:
    # Длительность шага — разгуд по всем шагам, а не по одному
    # имену: вызов — только часть шага, а метрика собрана всего
    # работеого инстанса, внешний в длительность шага не попала бы.
    from app.metrics import STEP_DURATION

    instance_id, _ = await _create_instance(db_session, ORDER_FLOW)
    await _seed_journal(db_session, instance_id, ORDER_FLOW)
    await db_session.commit()
    before = STEP_DURATION.labels(step="reserve_inventory")._sum.get()

    await _run_cycles("w-metrics")

    assert STEP_DURATION.labels(step="reserve_inventory")._sum.get() > before


async def test_lease_acquisition_is_counted(db_session: AsyncSession) -> None:
    from app.metrics import LEASE_ACQUIRED

    instance_id, _ = await _create_instance(db_session, ORDER_FLOW)
    await _seed_journal(db_session, instance_id, ORDER_FLOW)
    await db_session.commit()
    before = LEASE_ACQUIRED._value.get()

    await _run_cycles("w-metrics", count=2)

    assert LEASE_ACQUIRED._value.get() == before + 2


async def test_lease_is_released_after_completion(db_session: AsyncSession) -> None:
    # Удержанная аренда оставила бы инстанс занятым до истечения TTL.
    instance_id, _ = await _create_instance(db_session, ORDER_FLOW)
    await _seed_journal(db_session, instance_id, ORDER_FLOW)
    await db_session.commit()

    await _run_cycles("w1")

    assert await lease.current_owner(db_session, instance_id) is None


# --- Откат -----------------------------------------------------------------


async def test_failed_step_triggers_compensation(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Сбой на шаге оплаты должен откатить уже выполненный резерв.
    import app.demo.handlers as demo

    original = demo.charge_payment

    async def failing(ctx: object) -> dict:
        raise RuntimeError("платёжный шлюз недоступен")

    monkeypatch.setattr(demo, "charge_payment", failing)
    _patch_resolver(monkeypatch, demo)

    instance_id, _ = await _create_instance(db_session, FAILING_FLOW)
    await _seed_journal(db_session, instance_id, FAILING_FLOW)
    await db_session.commit()

    await _run_cycles("w1")
    monkeypatch.setattr(demo, "charge_payment", original)

    assert await _status(db_session, instance_id) == InstanceStatus.COMPENSATED


async def test_compensation_events_are_recorded(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.demo.handlers as demo

    async def failing(ctx: object) -> dict:
        raise RuntimeError("шлюз недоступен")

    monkeypatch.setattr(demo, "charge_payment", failing)
    _patch_resolver(monkeypatch, demo)

    instance_id, _ = await _create_instance(db_session, FAILING_FLOW)
    await _seed_journal(db_session, instance_id, FAILING_FLOW)
    await db_session.commit()

    await _run_cycles("w1")

    types = [e.type for e in await read_events(db_session, instance_id)]
    assert EventType.COMPENSATION_STARTED in types
    assert EventType.COMPENSATION_COMPLETED in types
    assert types[-1] == EventType.INSTANCE_COMPENSATED


async def test_compensation_covers_only_completed_steps(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Резерв компенсируется, валидация — нет: она ничего не меняла.
    import app.demo.handlers as demo

    async def failing(ctx: object) -> dict:
        raise RuntimeError("шлюз недоступен")

    monkeypatch.setattr(demo, "charge_payment", failing)
    _patch_resolver(monkeypatch, demo)

    instance_id, _ = await _create_instance(db_session, FAILING_FLOW)
    await _seed_journal(db_session, instance_id, FAILING_FLOW)
    await db_session.commit()

    await _run_cycles("w1")

    compensated = (
        await db_session.scalars(
            select(StepRun.step_name).where(
                StepRun.instance_id == instance_id, StepRun.compensated.is_(True)
            )
        )
    ).all()
    assert list(compensated) == ["reserve_inventory"]


async def test_failed_compensation_is_visible(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Провал компенсации — не тихий сбой, а состояние, требующее разбора.
    # Ломается компенсация успешного шага: упавший шаг нечего компенсировать.
    import app.demo.handlers as demo

    async def failing_charge(ctx: object) -> dict:
        raise RuntimeError("шлюз недоступен")

    async def failing_release(ctx: object) -> dict:
        raise RuntimeError("возврат резерва невозможен")

    monkeypatch.setattr(demo, "charge_payment", failing_charge)
    monkeypatch.setattr(demo, "release_inventory", failing_release)
    _patch_resolver(monkeypatch, demo)

    instance_id, _ = await _create_instance(db_session, FAILING_FLOW)
    await _seed_journal(db_session, instance_id, FAILING_FLOW)
    await db_session.commit()

    await _run_cycles("w1")

    assert await _status(db_session, instance_id) == InstanceStatus.COMPENSATION_FAILED


async def test_failed_step_is_not_compensated(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Упавший шаг не оставил выполненных шагов после него,
    # компенсация бы не трогала: процесс должен завершиться ошибкой, а не пустым откатом.
    import app.demo.handlers as demo

    async def failing(ctx: object) -> dict:
        raise RuntimeError("шлюз недоступен")

    monkeypatch.setattr(demo, "charge_payment", failing)
    _patch_resolver(monkeypatch, demo)

    instance_id, _ = await _create_instance(db_session, FAILING_FLOW)
    await _seed_journal(db_session, instance_id, FAILING_FLOW)
    await db_session.commit()

    await _run_cycles("w1")

    compensated = (
        await db_session.scalars(
            select(StepRun.step_name).where(
                StepRun.instance_id == instance_id, StepRun.compensated.is_(True)
            )
        )
    ).all()
    assert list(compensated) == ["reserve_inventory"]


def _patch_resolver(monkeypatch: pytest.MonkeyPatch, module: object) -> None:
    """Подменяет резолвер в местах, откуда он импортирован.

    `executor` импортирует `resolve_handler` напрямую, поэтому подмена одного
    модуля registry не изменила бы поведение: подменять нужно точку использования.
    """
    from app.engine import executor as executor_module

    def resolve(reference: str) -> object:
        _, _, name = reference.partition(":")
        return getattr(module, name)

    monkeypatch.setattr(executor_module, "resolve_handler", resolve)
    # В worker резолвер импортируется внутри функции, поэтому подменяется
    # атрибут модуля handlers.registry.
    from app.handlers import registry

    monkeypatch.setattr(registry, "resolve_handler", resolve)


# --- Восстановление после падения воркера -----------------------------------


async def test_worker_killed_mid_step_resumes(db_session: AsyncSession) -> None:
    # Сценарий из ADR-0003: воркер убит посреди шага, другой подхватывает
    # инстанс и продолжает без повторения уже применённых шагов.
    instance_id, _ = await _create_instance(db_session, ORDER_FLOW)
    await _seed_journal(db_session, instance_id, ORDER_FLOW)
    await db_session.commit()

    # Первый воркер исполняет два шага и «умирает».
    from app import worker

    cache: dict[uuid.UUID, WorkflowDefinitionSchema] = {}
    for _ in range(2):
        await worker.run_cycle("w1", cache)

    executed_before = await _step_names(db_session, instance_id)
    assert len(executed_before) == 2

    # Lease протухает — аренда больше не удерживается.
    await db_session.execute(
        update(WorkflowInstance)
        .where(WorkflowInstance.id == instance_id)
        .values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1), heartbeat_at=None)
    )
    await db_session.commit()

    # Второй воркер подхватывает инстанс и доводит процесс до конца.
    await _run_cycles("w2", count=8)

    assert await _status(db_session, instance_id) == InstanceStatus.COMPLETED
    assert len(await _step_names(db_session, instance_id)) == 4


async def test_resumed_worker_does_not_duplicate_steps(
    db_session: AsyncSession,
) -> None:
    # Ключевое свойство: ни один шаг не выполняется дважды после перехвата.
    instance_id, _ = await _create_instance(db_session, ORDER_FLOW)
    await _seed_journal(db_session, instance_id, ORDER_FLOW)
    await db_session.commit()

    from app import worker

    cache: dict[uuid.UUID, WorkflowDefinitionSchema] = {}
    for _ in range(2):
        await worker.run_cycle("w1", cache)
    await db_session.execute(
        update(WorkflowInstance)
        .where(WorkflowInstance.id == instance_id)
        .values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    await db_session.commit()
    await _run_cycles("w2", count=8)

    counts = (
        await db_session.execute(
            select(StepRun.step_name, func.count())
            .where(StepRun.instance_id == instance_id)
            .group_by(StepRun.step_name)
        )
    ).all()
    for step_name, count in counts:
        assert count == 1, f"шаг {step_name} выполнен {count} раз"


async def test_replay_after_pause_matches_continuous_run(
    db_session: AsyncSession,
) -> None:
    # Пауза не меняет результат: журнал — единственный источник истины, а не
    # состояние в памяти воркера.
    instance_id, _ = await _create_instance(db_session, ORDER_FLOW)
    await _seed_journal(db_session, instance_id, ORDER_FLOW)
    await db_session.commit()

    from app import worker

    cache: dict[uuid.UUID, WorkflowDefinitionSchema] = {}
    for _ in range(2):
        await worker.run_cycle("w1", cache)

    state_at_pause = replay(await read_events(db_session, instance_id))
    assert state_at_pause.status is InstanceStatus.RUNNING
    assert state_at_pause.current_step == "charge_payment"

    await db_session.execute(
        update(WorkflowInstance)
        .where(WorkflowInstance.id == instance_id)
        .values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    await db_session.commit()
    await _run_cycles("w2", count=8)

    final_state = replay(await read_events(db_session, instance_id))
    assert final_state.status is InstanceStatus.COMPLETED
    assert final_state.completed_steps == [
        "validate_cart",
        "reserve_inventory",
        "charge_payment",
        "confirm_delivery",
    ]


async def test_two_workers_do_not_execute_same_step(db_session: AsyncSession) -> None:
    # Гонка: инстанс захватывается один раз, второй воркер получает отказ.
    instance_id, _ = await _create_instance(db_session, ORDER_FLOW)
    await _seed_journal(db_session, instance_id, ORDER_FLOW)
    await db_session.commit()

    from app import worker

    cache_a: dict[uuid.UUID, WorkflowDefinitionSchema] = {}
    cache_b: dict[uuid.UUID, WorkflowDefinitionSchema] = {}
    results = await asyncio.gather(worker.run_cycle("w1", cache_a), worker.run_cycle("w2", cache_b))

    handled = [r for r in results if r.handled]
    assert len(handled) == 1
    assert len(await _step_names(db_session, instance_id)) == 1


async def test_processed_operations_block_duplicate_effect(
    db_session: AsyncSession,
) -> None:
    # Повтор шага с тем же ключом обязан быть отвергнут базой: иначе после
    # падения деньги спишутся дважды.
    from app.engine import idempotency

    instance_id, _ = await _create_instance(db_session, ORDER_FLOW)
    await _seed_journal(db_session, instance_id, ORDER_FLOW)
    await db_session.commit()

    key = idempotency.make_key(instance_id, "charge_payment", 1)
    await idempotency.mark_applied(db_session, key)
    await db_session.commit()

    assert await idempotency.already_applied(db_session, key) is True
    await db_session.rollback()

    rows = await db_session.scalars(
        select(WorkflowEvent.seq).where(WorkflowEvent.instance_id == instance_id)
    )
    assert len(rows.all()) == 1


# --- Ветвление по результату шага -------------------------------------------

BRANCH_FLOW = {
    "start": "charge_payment",
    "steps": {
        "charge_payment": {
            "action": f"{DEMO}:charge_payment",
            "compensation": f"{DEMO}:refund_payment",
            "retry": {"max_attempts": 1},
            "next": {"on_success": "happy", "on_failure": "fallback"},
        },
        "happy": {"action": f"{DEMO}:reserve_inventory", "next": "confirm_delivery"},
        "fallback": {"action": f"{DEMO}:create_shipment", "next": "confirm_delivery"},
        "confirm_delivery": {"action": f"{DEMO}:confirm_delivery"},
    },
}


async def test_on_success_branch_is_taken(db_session: AsyncSession) -> None:
    # Ветка объявлена в формате дефинишена и обязана исполняться: иначе
    # `on_success` и `on_failure` были бы декоративными ключами.
    instance_id, _ = await _create_instance(db_session, BRANCH_FLOW)
    await _seed_journal(db_session, instance_id, BRANCH_FLOW)
    await db_session.commit()

    await _run_cycles("w1")

    assert await _step_names(db_session, instance_id) == [
        "charge_payment",
        "happy",
        "confirm_delivery",
    ]
    assert await _status(db_session, instance_id) == InstanceStatus.COMPLETED


async def test_on_failure_branch_is_taken(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Ключевой случай: сбой шага с объявленной веткой `on_failure` обязан
    # перейти в неё, а не уходить в откат. Иначе ветвление по результату —
    # заявленная возможность формата — молча не работает.
    import app.demo.handlers as demo

    original = demo.charge_payment

    async def failing(ctx: object) -> dict:
        raise RuntimeError("платёжный шлюз недоступен")

    monkeypatch.setattr(demo, "charge_payment", failing)
    _patch_resolver(monkeypatch, demo)

    instance_id, _ = await _create_instance(db_session, BRANCH_FLOW)
    await _seed_journal(db_session, instance_id, BRANCH_FLOW)
    await db_session.commit()

    await _run_cycles("w1")
    monkeypatch.setattr(demo, "charge_payment", original)

    names = await _step_names(db_session, instance_id)
    assert names == ["charge_payment", "fallback", "confirm_delivery"], (
        f"ветка on_failure не выполнена: {names}"
    )
    assert await _status(db_session, instance_id) == InstanceStatus.COMPLETED

    # Отмена запрошена: переедача отдачи есть делать нельзя, поэтому откатывать ней быть некому.
    # Пользователь теребует немого, но таймер простоят, и после предупреждения.


CANCEL_FLOW = {
    "start": "reserve_inventory",
    "steps": {
        "reserve_inventory": {
            "action": f"{DEMO}:reserve_inventory",
            "compensation": f"{DEMO}:release_inventory",
            "next": "await_delivery",
        },
        "await_delivery": {
            "action": f"{DEMO}:confirm_delivery",
            "wait_before_seconds": 3600,
        },
    },
}


async def _request_cancel(db_session: AsyncSession, instance_id: uuid.UUID) -> None:
    """Отмена запрошена, но таймер ещё живёт: целы нет."""
    from app.engine.journal import append_event

    await append_event(db_session, instance_id, EventType.INSTANCE_CANCEL_REQUESTED, {})
    instance_row = await db_session.get(WorkflowInstance, instance_id)
    assert instance_row is not None
    instance_row.status = InstanceStatus.CANCELLING


async def test_cancel_rolls_back_completed_steps(db_session: AsyncSession) -> None:
    # Снимание решает таймер: операция отмены дожна прекращать дальше чем становить таймер. Результат решает одно из верхов небьзя:
    # Делать проверку восстановления не нужно: если таймер изанят время после предыдущего инстанса небьзя
    # применить ведущего поведения останься в работе Saga (§3.5).
    instance_id, _ = await _create_instance(db_session, CANCEL_FLOW)
    await _seed_journal(db_session, instance_id, CANCEL_FLOW)
    await db_session.commit()

    await _run_cycles("w1")
    assert await _status(db_session, instance_id) == InstanceStatus.WAITING

    await _request_cancel(db_session, instance_id)
    await db_session.commit()

    await _run_cycles("w2")

    assert await _status(db_session, instance_id) == InstanceStatus.COMPENSATED
    db_session.expire_all()
    steps = (
        await db_session.scalars(
            select(StepRun).where(
                StepRun.instance_id == instance_id,
                StepRun.step_name == "reserve_inventory",
            )
        )
    ).all()
    assert steps
    assert steps[0].compensated is True


async def test_cancel_removes_pending_timer(db_session: AsyncSession) -> None:
    # Неотменённый таймер разбудил бы уже откатанный процесс: отменённый
    # заказ не должен просыпаться по расписанию доставки.
    from app.engine import scheduler
    from app.services import instances as instance_service

    instance_id, _ = await _create_instance(db_session, CANCEL_FLOW)
    await _seed_journal(db_session, instance_id, CANCEL_FLOW)
    await db_session.commit()
    await _run_cycles("w1")
    assert await scheduler.pending_count(db_session, instance_id) == 1

    # Отмена идёт через сервисный слой: именно там снимаются ожидания.
    await instance_service.cancel_instance(db_session, instance_id)
    await db_session.commit()

    assert await scheduler.pending_count(db_session, instance_id) == 0


async def test_cancel_without_work_ends_as_cancelled(db_session: AsyncSession) -> None:
    # Компенсировать нечего, поэтому называть результат `COMPENSATED` было бы
    # враньём: отката не было.
    instance_id, _ = await _create_instance(db_session, ORDER_FLOW)
    await _seed_journal(db_session, instance_id, ORDER_FLOW)
    await db_session.commit()

    await _request_cancel(db_session, instance_id)
    await db_session.commit()

    await _run_cycles("w1")

    assert await _status(db_session, instance_id) == InstanceStatus.CANCELLED


async def test_compensation_started_written_once_per_attempt(db_session: AsyncSession) -> None:
    # Один факт — одно событие. Два `COMPENSATION_STARTED` на одну попытку
    # засоряют журнал и противоречат §3.1.
    instance_id, _ = await _create_instance(db_session, CANCEL_FLOW)
    await _seed_journal(db_session, instance_id, CANCEL_FLOW)
    await db_session.commit()
    await _run_cycles("w1")

    await _request_cancel(db_session, instance_id)
    await db_session.commit()

    await _run_cycles("w2")

    types = [e.type for e in await read_events(db_session, instance_id)]
    assert types.count(EventType.COMPENSATION_STARTED) == 1


async def test_compensation_is_not_applied_twice(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Повтор компенсации после падения воркера обязан быть безопасным: возврат
    # средств дважды — двойной платёж наоборот (§3.4).
    import app.demo.handlers as demo
    from app.engine import idempotency, saga

    calls: list[object] = []
    original = demo.release_inventory

    def counting(ctx: object) -> dict:
        calls.append(ctx)
        return original(ctx)  # type: ignore[arg-type]

    monkeypatch.setattr(demo, "release_inventory", counting)
    _patch_resolver(monkeypatch, demo)

    instance_id, _ = await _create_instance(db_session, CANCEL_FLOW)
    await _seed_journal(db_session, instance_id, CANCEL_FLOW)
    await db_session.commit()
    await _run_cycles("w1")

    await _request_cancel(db_session, instance_id)
    await db_session.commit()

    await _run_cycles("w1")
    assert len(calls) == 1

    # Ключ компенсации отмечен: повтор не должен дойти до обработчика.
    items = await saga.plan(db_session, instance_id, {"reserve_inventory": None})
    assert items == []
    applied = await idempotency.list_applied(db_session, instance_id)
    assert any(key.startswith(f"{instance_id}:compensate:") for key in applied)
    monkeypatch.setattr(demo, "release_inventory", original)


async def test_branch_without_on_failure_goes_to_compensation(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Обратный случай: если ветка не объявлена, сбой шага ведёт в откат, а не
    # в повтор того же шага. Иначе `on_failure` был бы необязательным.
    import app.demo.handlers as demo

    original = demo.charge_payment

    async def failing(ctx: object) -> dict:
        raise RuntimeError("платёжный шлюз недоступен")

    monkeypatch.setattr(demo, "charge_payment", failing)
    _patch_resolver(monkeypatch, demo)

    instance_id, _ = await _create_instance(db_session, FAILING_FLOW)
    await _seed_journal(db_session, instance_id, FAILING_FLOW)
    await db_session.commit()

    await _run_cycles("w1")
    monkeypatch.setattr(demo, "charge_payment", original)

    assert await _status(db_session, instance_id) == InstanceStatus.COMPENSATED
    assert "fallback" not in await _step_names(db_session, instance_id)


# --- Циклы ----------------------------------------------------------------


LOOP_FLOW = {
    "start": "reserve_inventory",
    "steps": {
        "reserve_inventory": {
            "action": f"{DEMO}:reserve_inventory",
            "compensation": f"{DEMO}:release_inventory",
            "next": "charge_payment",
        },
        "charge_payment": {
            "action": f"{DEMO}:charge_payment",
            "compensation": f"{DEMO}:refund_payment",
            "next": "reserve_inventory",
            "loop": True,
            "max_iterations": 3,
        },
    },
}


async def test_loop_stops_at_declared_limit(db_session: AsyncSession) -> None:
    """Проходы заканчиваются на объявленном пределе, и процесс уходит в откат.

    Без учёта итераций этот шаг крутился бы вечно: определение проходило
    валидацию, а `max_iterations` не читался.
    """
    instance_id, _ = await _create_instance(db_session, LOOP_FLOW)
    await _seed_journal(db_session, instance_id, LOOP_FLOW)
    await db_session.commit()

    await _run_cycles("w1", count=40)

    assert await _status(db_session, instance_id) == InstanceStatus.COMPENSATED


async def test_loop_runs_declared_number_of_passes(db_session: AsyncSession) -> None:
    """Шаг с пределом 3 выполняется ровно три раза, не больше и не меньше."""
    instance_id, _ = await _create_instance(db_session, LOOP_FLOW)
    await _seed_journal(db_session, instance_id, LOOP_FLOW)
    await db_session.commit()

    await _run_cycles("w1", count=40)

    db_session.expire_all()
    passes = await db_session.scalar(
        select(func.count())
        .select_from(StepRun)
        .where(StepRun.instance_id == instance_id, StepRun.step_name == "charge_payment")
    )
    assert passes == 3


async def test_loop_exhaustion_is_written_to_journal(db_session: AsyncSession) -> None:
    """Исчерпание предела — событие журнала, а не молчаливая остановка."""
    instance_id, _ = await _create_instance(db_session, LOOP_FLOW)
    await _seed_journal(db_session, instance_id, LOOP_FLOW)
    await db_session.commit()

    await _run_cycles("w1", count=40)

    db_session.expire_all()
    rows = await db_session.scalars(
        select(WorkflowEvent.payload).where(
            WorkflowEvent.instance_id == instance_id, WorkflowEvent.type == "LOOP_EXHAUSTED"
        )
    )
    payloads = [dict(p) for p in rows]

    assert len(payloads) == 1
    assert payloads[0]["step"] == "charge_payment"
    assert payloads[0]["iterations"] == 3
    assert payloads[0]["max_iterations"] == 3


async def test_loop_limit_survives_worker_restart(db_session: AsyncSession) -> None:
    """Счётчик проходов живёт в журнале, поэтому новый воркер его видит.

    Если бы счётчик был в памяти воркера, падение между проходами обнулило бы
    его — и цикл начался бы заново.
    """
    instance_id, _ = await _create_instance(db_session, LOOP_FLOW)
    await _seed_journal(db_session, instance_id, LOOP_FLOW)
    await db_session.commit()

    await _run_cycles("w1", count=2)
    # Смена идентичности воркера: другой процесс, тот же журнал.
    await _run_cycles("w2", count=40)

    db_session.expire_all()
    passes = await db_session.scalar(
        select(func.count())
        .select_from(StepRun)
        .where(StepRun.instance_id == instance_id, StepRun.step_name == "charge_payment")
    )
    assert passes == 3


# --- Ошибки, которые повтор не исправит --------------------------------------

PERMANENT_FLOW = {
    "start": "reserve_inventory",
    "steps": {
        "reserve_inventory": {
            "action": f"{DEMO}:reserve_inventory",
            "next": "charge_payment",
            "retry": {"max_attempts": 3, "backoff_seconds": 0},
        },
        "charge_payment": {"action": f"{DEMO}:charge_payment"},
    },
}


async def test_permanent_error_is_not_retried(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Постоянный отказ не должен повторяться.

    Товара больше нет — он не появится за время backoff. Три попытки лишь
    отложили бы откат резерва, занятого предыдущими шагами.
    """
    import app.demo.handlers as demo
    from app.engine.executor import PermanentStepError

    original = demo.reserve_inventory

    async def rejecting(ctx: object) -> dict:
        raise PermanentStepError("товара больше нет")

    monkeypatch.setattr(demo, "reserve_inventory", rejecting)
    _patch_resolver(monkeypatch, demo)

    instance_id, _ = await _create_instance(db_session, PERMANENT_FLOW)
    await _seed_journal(db_session, instance_id, PERMANENT_FLOW)
    await db_session.commit()

    await _run_cycles("w1")
    monkeypatch.setattr(demo, "reserve_inventory", original)

    db_session.expire_all()
    attempts = await db_session.scalar(
        select(func.count())
        .select_from(StepRun)
        .where(StepRun.instance_id == instance_id, StepRun.step_name == "reserve_inventory")
    )
    events = await read_events(db_session, instance_id)

    assert attempts == 1, f"шаг повторён {attempts} раз при постоянной ошибке"
    assert EventType.STEP_RETRY_SCHEDULED not in [e.type for e in events]
    assert EventType.STEP_FAILED in [e.type for e in events]


async def test_permanent_error_carries_retryable_false() -> None:
    """Контракт: обработчик объявляет постоянство через `retryable`."""
    from app.engine.executor import PermanentStepError, StepFailed

    assert PermanentStepError("x").retryable is False
    assert StepFailed("x").retryable is True


async def test_failed_without_completed_steps_ends_as_failed(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Отказ на первом шаге — это FAILED, а не «откат завершён».

    Компенсировать нечего, и журнал не должен утверждать обратного.
    """
    import app.demo.handlers as demo

    original = demo.validate_cart

    async def failing(ctx: object) -> dict:
        raise RuntimeError("корзина пуста")

    monkeypatch.setattr(demo, "validate_cart", failing)
    _patch_resolver(monkeypatch, demo)

    flow = {
        "start": "validate_cart",
        "steps": {
            "validate_cart": {
                "action": f"{DEMO}:validate_cart",
                "next": "reserve_inventory",
                # Без этого шаг ушёл бы в повтор с backoff, и инстанс остался
                # бы RUNNING: тест проверяет терминальное состояние, а не повтор.
                "retry": {"max_attempts": 1},
            },
            "reserve_inventory": {"action": f"{DEMO}:reserve_inventory"},
        },
    }
    instance_id, _ = await _create_instance(db_session, flow)
    await _seed_journal(db_session, instance_id, flow)
    await db_session.commit()

    await _run_cycles("w1")
    monkeypatch.setattr(demo, "validate_cart", original)

    events = await read_events(db_session, instance_id)
    types = [e.type for e in events]

    assert EventType.INSTANCE_FAILED in types
    assert EventType.INSTANCE_COMPENSATED not in types
    assert replay(events).status is InstanceStatus.FAILED


async def test_compensation_retries_after_failure(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Провал компенсации не конец процесса: он уходит в очередь повторов
    # отката (§3.5). Раньше инстанс в COMPENSATION_FAILED попадал в ветку
    # обычного шага — воркер возвращался к упавшему шагу и доводил процесс
    # до конца вместо повторного отката.
    import app.demo.handlers as demo

    original = demo.release_inventory
    attempts = {"count": 0}

    async def flaky_release(ctx: object) -> dict:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("склад недоступен")
        return await original(ctx)

    monkeypatch.setattr(demo, "release_inventory", flaky_release)
    _patch_resolver(monkeypatch, demo)

    instance_id, _ = await _create_instance(db_session, CANCEL_FLOW)
    await _seed_journal(db_session, instance_id, CANCEL_FLOW)
    await db_session.commit()
    await _run_cycles("w1")
    await _request_cancel(db_session, instance_id)
    await db_session.commit()
    # Отдельно один цикл: иначе повтор отката случился бы в той же пачке
    # и состояние после провала нельзя было бы увидеть.
    await _run_cycles("w2", count=1)

    assert await _status(db_session, instance_id) == InstanceStatus.COMPENSATION_FAILED

    # Время до срока пропускаем: пауза между попытками не берёт
    # обращение, и в тесте одного вызова не должно быть.
    instance_row = await db_session.get(WorkflowInstance, instance_id)
    assert instance_row is not None
    instance_row.compensation_not_before = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.commit()

    await _run_cycles("w3")

    assert await _status(db_session, instance_id) == InstanceStatus.COMPENSATED
    assert attempts["count"] == 2


async def test_retry_does_not_repeat_finished_compensation(db_session: AsyncSession) -> None:
    # План отката не должен возвращать шаг, который уже откатан: порядок
    # берётся из журнала, где выполненный шаг остаётся навсегда, а новый
    # проход откатывал бы его повторно — под новым ключом идемпотентности.
    from app.models.enums import CompensationStatus
    from app.models.workflow import CompensationLog

    instance_id, _ = await _create_instance(db_session, CANCEL_FLOW)
    await _seed_journal(db_session, instance_id, CANCEL_FLOW)
    await db_session.commit()
    await _run_cycles("w1")
    await _request_cancel(db_session, instance_id)
    await db_session.commit()
    await _run_cycles("w2", count=1)

    # Откат состоялся: попытка закрыта успешно, резерв освобождён.
    finished = (
        await db_session.scalars(
            select(CompensationLog).where(
                CompensationLog.instance_id == instance_id,
                CompensationLog.status == CompensationStatus.COMPLETED,
            )
        )
    ).all()
    assert len(finished) == 1

    assert await _compensation_plan(db_session, instance_id) == []


async def _compensation_plan(db_session: AsyncSession, instance_id: uuid.UUID) -> list[str]:
    """План отката, построенный так же, как его строит воркер."""
    from app.engine import saga
    from app.engine.journal import read_events, replay

    instance_row = await db_session.get(WorkflowInstance, instance_id)
    assert instance_row is not None
    definition_row = await db_session.get(WorkflowDefinition, instance_row.definition_id)
    assert definition_row is not None
    definition = WorkflowDefinitionSchema.model_validate(definition_row.definition)
    refs = {name: step.compensation for name, step in definition.steps.items()}
    state = replay(await read_events(db_session, instance_id))
    items = await saga.plan(db_session, instance_id, refs, executed_steps=state.completed_steps)
    return [item.step_name for item in items]


async def test_compensation_retry_waits_before_the_next_attempt(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Провал компенсации обычно недоступен: пауза понадолжна, иначе воркер
    # бьёт по недоступному сервису раз в секунду на каждый процесс.
    import app.demo.handlers as demo

    calls = {"count": 0}

    async def failing_release(ctx: object) -> dict:
        calls["count"] += 1
        raise RuntimeError("склад недоступен")

    monkeypatch.setattr(demo, "release_inventory", failing_release)
    _patch_resolver(monkeypatch, demo)

    instance_id, _ = await _create_instance(db_session, CANCEL_FLOW)
    await _seed_journal(db_session, instance_id, CANCEL_FLOW)
    await db_session.commit()
    await _run_cycles("w1")
    await _request_cancel(db_session, instance_id)
    await db_session.commit()
    await _run_cycles("w2", count=1)

    assert calls["count"] == 1
    assert await _status(db_session, instance_id) == InstanceStatus.COMPENSATION_FAILED

    db_session.expire_all()
    instance_row = await db_session.get(WorkflowInstance, instance_id)
    assert instance_row is not None
    not_before = instance_row.compensation_not_before
    assert not_before is not None, "пауза не записала"
    assert not_before > datetime.now(UTC)

    # Инстанс не берётся воркером до срока: опрос отката уверен в записке.
    await _run_cycles("w3", count=3)
    assert calls["count"] == 1


async def test_backoff_grows_with_failures_and_is_capped() -> None:
    from app.core.config import get_settings
    from app.worker import _next_compensation_attempt_at

    settings = get_settings()
    first = _next_compensation_attempt_at(1)
    second = _next_compensation_attempt_at(2)

    assert second > first

    # Потолок существует: бесконечный следотель доводит в десять минут,
    # а если недоступен сотни месяцев, откат не лучше того.
    huge = _next_compensation_attempt_at(50)
    assert (huge - datetime.now(UTC)).total_seconds() <= settings.compensation_retry_max_seconds + 1
