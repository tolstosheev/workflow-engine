"""Тесты обработчиков предметной области.

Отдельный файл, а не дополнение к тестам реестра: обработчики обращаются к БД и
проверяют данные, а реестр проверяет резолв по строке. Главное здесь —
компенсация отменяет эффект; прежде обработчик возвращал `{"released": True}`,
и проверить было нечего.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session_factory
from app.demo import handlers
from app.demo.handlers import StepContext
from app.engine.journal import append_event
from app.models.enums import EventType
from app.models.workflow import (
    DemoInventoryItem,
    DemoPayment,
    DemoShipment,
    WorkflowDefinition,
    WorkflowInstance,
)

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("inventory", "instance")]

SKU = "SKU-000"


@pytest.fixture
async def instance(db_engine: None) -> AsyncIterator[str]:
    """Инстанс с записью в журнале: обработчики пишут ключ операции.

    Ключ ссылается на `workflow_instances` внешним ключом, и подстановка
    случайного UUID откатывала бы транзакцию. Инстанс здесь нужен ради
    целостности данных, а не ради исполнения процесса.
    """
    flow = {
        "start": "validate_cart",
        "steps": {"validate_cart": {"action": "app.demo.handlers:validate_cart"}},
    }

    async with get_session_factory()() as session:
        definition = WorkflowDefinition(name=f"probe-{uuid.uuid4().hex[:8]}", definition=flow)
        session.add(definition)
        await session.flush()
        row = WorkflowInstance(definition_id=definition.id, input={}, variables={})
        session.add(row)
        await session.flush()
        await append_event(session, row.id, EventType.INSTANCE_CREATED, {"start": flow["start"]})
        # Коммит обязателен: обработчики пишут в таблицы с внешним ключом на
        # инстанс из собственных сессий и увидят только зафиксированную строку.
        await session.commit()
        instance_id = str(row.id)

    yield instance_id


async def _other_instance() -> str:
    """Второй инстанс: резерв конфликтующего заказа должен быть настоящим."""
    flow = {
        "start": "validate_cart",
        "steps": {"validate_cart": {"action": "app.demo.handlers:validate_cart"}},
    }
    async with get_session_factory()() as session:
        definition = WorkflowDefinition(name=f"probe-{uuid.uuid4().hex[:8]}", definition=flow)
        session.add(definition)
        await session.flush()
        row = WorkflowInstance(definition_id=definition.id, input={}, variables={})
        session.add(row)
        await session.flush()
        await append_event(session, row.id, EventType.INSTANCE_CREATED, {"start": flow["start"]})
        await session.commit()
        return str(row.id)


@pytest.fixture
async def db_session(db_engine: None) -> AsyncIterator[AsyncSession]:
    session = get_session_factory()()
    transaction = await session.begin()
    try:
        yield session
    finally:
        if transaction.is_active:
            await transaction.rollback()
        await session.close()


def _ctx(instance_id: str, step_name: str, **kwargs: object) -> StepContext:
    defaults: dict[str, object] = {
        "instance_id": instance_id,
        "step_name": step_name,
        "attempt": 1,
        "operation_id": f"{instance_id}:{step_name}:1",
    }
    defaults.update(kwargs)
    return StepContext(**defaults)  # type: ignore[arg-type]


async def _reserved(sku: str = SKU) -> int:
    async with get_session_factory()() as session:
        row = await session.scalar(select(DemoInventoryItem).where(DemoInventoryItem.sku == sku))
        assert row is not None
        return int(row.reserved)


async def _payments(instance_id: str) -> list[DemoPayment]:
    async with get_session_factory()() as session:
        return list(
            await session.scalars(
                select(DemoPayment).where(DemoPayment.instance_id == uuid.UUID(instance_id))
            )
        )


# --- Резерв и его отмена ---------------------------------------------------


async def test_reserve_increments_stock(instance: str) -> None:
    await handlers.reserve_inventory(
        _ctx(instance, "reserve_inventory", input={"items": [{"sku": SKU, "qty": 3}]})
    )

    assert await _reserved() == 3


async def test_release_returns_reserved_stock(instance: str) -> None:
    ctx = _ctx(instance, "reserve_inventory", input={"items": [{"sku": SKU, "qty": 3}]})
    await handlers.reserve_inventory(ctx)

    await handlers.release_inventory(
        _ctx(
            instance,
            "reserve_inventory",
            attempt=1,
            input=ctx.input,
            operation_id=f"{ctx.instance_id}:c:1",
        )
    )

    # Ключ отличается от шагового: это другая операция, и повтор шага не должен
    # выглядеть как уже выполненная отмена.
    assert await _reserved() == 0


async def test_reserve_is_not_applied_twice(instance: str) -> None:
    ctx = _ctx(instance, "reserve_inventory", input={"items": [{"sku": SKU, "qty": 2}]})

    await handlers.reserve_inventory(ctx)
    await handlers.reserve_inventory(ctx)

    # Повтор с тем же ключом — это падение воркера после эффекта, а не новый заказ.
    assert await _reserved() == 2


async def test_reserve_beyond_stock_fails(instance: str) -> None:
    ctx = _ctx(instance, "reserve_inventory", input={"items": [{"sku": SKU, "qty": 10_001}]})

    with pytest.raises(RuntimeError, match="недостаточно"):
        await handlers.reserve_inventory(ctx)

    assert await _reserved() == 0


async def test_reserve_of_unknown_sku_fails(instance: str) -> None:
    ctx = _ctx(instance, "reserve_inventory", input={"items": [{"sku": "SKU-НЕТ", "qty": 1}]})

    with pytest.raises(RuntimeError, match="не найден"):
        await handlers.reserve_inventory(ctx)


# --- Платёж и его возврат --------------------------------------------------


async def test_charge_creates_payment(instance: str) -> None:
    ctx = _ctx(instance, "charge_payment", input={"amount": "1499.90"})

    result = await handlers.charge_payment(ctx)

    payments = await _payments(ctx.instance_id)
    assert result["charged"] is True
    assert len(payments) == 1
    assert payments[0].amount == Decimal("1499.90")
    assert payments[0].status == "HELD"


async def test_refund_marks_payment_refunded(instance: str) -> None:
    ctx = _ctx(instance, "charge_payment", input={"amount": "100.00"})
    await handlers.charge_payment(ctx)

    await handlers.refund_payment(
        _ctx(
            instance,
            "refund_payment",
            attempt=1,
            operation_id=f"{ctx.instance_id}:compensate:charge:1",
        )
    )

    payments = await _payments(ctx.instance_id)
    assert payments[0].status == "REFUNDED"
    assert payments[0].refunded_at is not None


async def test_charge_with_bad_amount_fails(instance: str) -> None:
    with pytest.raises(RuntimeError, match="не является числом"):
        await handlers.charge_payment(_ctx(instance, "charge_payment", input={"amount": "мусор"}))


# --- Отправление и его отмена ----------------------------------------------


async def test_shipment_lifecycle(instance: str) -> None:
    ctx = _ctx(instance, "create_shipment")

    await handlers.create_shipment(ctx)
    async with get_session_factory()() as session:
        shipments = list(
            await session.scalars(
                select(DemoShipment).where(DemoShipment.instance_id == uuid.UUID(ctx.instance_id))
            )
        )
    assert len(shipments) == 1
    assert shipments[0].status == "CREATED"

    await handlers.cancel_shipment(
        _ctx(
            instance,
            "cancel_shipment",
            attempt=1,
            operation_id=f"{ctx.instance_id}:compensate:ship:1",
        )
    )
    async with get_session_factory()() as session:
        row = await session.scalar(
            select(DemoShipment).where(DemoShipment.instance_id == uuid.UUID(ctx.instance_id))
        )
    assert row is not None
    assert row.status == "CANCELLED"
    assert row.cancelled_at is not None


async def test_shipment_is_not_created_twice(instance: str) -> None:
    ctx = _ctx(instance, "create_shipment")

    await handlers.create_shipment(ctx)
    await handlers.create_shipment(ctx)

    async with get_session_factory()() as session:
        count = len(
            list(
                await session.scalars(
                    select(DemoShipment).where(
                        DemoShipment.instance_id == uuid.UUID(ctx.instance_id)
                    )
                )
            )
        )
    assert count == 1


# --- Откат целиком ---------------------------------------------------------


# --- Доставка и её отмена ---------------------------------------------------
#
# Подтверждение доставки отпускает резерв (ADR-0020): товар доставлен, и холд
# на складе больше не нужен. Раньше резерв держался до бесконечности, и склад
# на стенде пустел после каждого успешного заказа.


async def test_confirmation_releases_reserved_stock(instance: str) -> None:
    ctx = _ctx(instance, "reserve_inventory", input={"items": [{"sku": SKU, "qty": 3}]})
    await handlers.reserve_inventory(ctx)
    assert await _reserved() == 3

    await handlers.confirm_delivery(_ctx(instance, "await_delivery", input=ctx.input))

    # Товар доставлен: резерв возвращается на склад.
    assert await _reserved() == 0


async def test_confirmation_releases_reserved_stock_only_once(instance: str) -> None:
    ctx = _ctx(instance, "reserve_inventory", input={"items": [{"sku": SKU, "qty": 2}]})
    await handlers.reserve_inventory(ctx)
    delivery = _ctx(instance, "await_delivery", input=ctx.input)

    await handlers.confirm_delivery(delivery)
    await handlers.confirm_delivery(delivery)

    # Повтор шага не должен снять чужой резерв: тот же ключ операции, второй
    # раз эффект не применяется.
    assert await _reserved() == 0


async def test_rollback_of_confirmation_reserves_again(instance: str) -> None:
    ctx = _ctx(instance, "reserve_inventory", input={"items": [{"sku": SKU, "qty": 3}]})
    await handlers.reserve_inventory(ctx)
    await handlers.confirm_delivery(_ctx(instance, "await_delivery", input=ctx.input))
    assert await _reserved() == 0

    # Отмена доставки возвращает товар в холд: компенсация шага обязана
    # занять резерв обратно, иначе склад считает товар свободным, хотя он
    # уехал.
    await handlers.reserve_inventory(
        _ctx(
            instance,
            "await_delivery",
            input=ctx.input,
            operation_id=f"{instance}:compensate:await_delivery:1",
        )
    )

    assert await _reserved() == 3


async def test_reserve_after_release_can_conflict(instance: str) -> None:
    """Возврат резерва может упереться в чужой заказ — это видно, а не тихо.

    Товар забрал другой процесс, освободившийся остаток при откате меньше
    нужного. Обработчик обязан сказать об этом постоянной ошибкой, а не
    записать отрицательный остаток или промолчать.
    """
    ctx = _ctx(instance, "reserve_inventory", input={"items": [{"sku": SKU, "qty": 4}]})
    await handlers.reserve_inventory(ctx)
    await handlers.confirm_delivery(_ctx(instance, "await_delivery", input=ctx.input))

    # Чужой заказ занимает почти весь склад, и возврату резерва места не
    # остаётся: свободного места меньше, чем нужно вернуть.
    other = await _other_instance()
    await handlers.reserve_inventory(
        _ctx(other, "reserve_inventory", input={"items": [{"sku": SKU, "qty": 10_000}]})
    )

    with pytest.raises(handlers.PermanentStepError):
        await handlers.reserve_inventory(
            _ctx(
                instance,
                "await_delivery",
                input=ctx.input,
                operation_id=f"{instance}:compensate:await_delivery:1",
            )
        )


async def test_full_order_then_rollback_restores_stock(instance: str) -> None:
    """Заказ, прошедший целиком, и его откат возвращают склад к исходному.

    Это то, ради чего существует Saga: после отката данных не остаётся.
    """
    stock_before = await _reserved()
    ctx = _ctx(instance, "reserve_inventory", input={"items": [{"sku": SKU, "qty": 4}]})

    await handlers.reserve_inventory(ctx)
    payment = _ctx(instance, "charge_payment", input={"amount": "50.00"})
    await handlers.charge_payment(payment)
    assert await _reserved() == stock_before + 4

    await handlers.release_inventory(
        _ctx(
            instance,
            "release_inventory",
            attempt=1,
            input=ctx.input,
            operation_id=f"{instance}:compensate:reserve_inventory:1",
        )
    )
    await handlers.refund_payment(
        _ctx(
            instance,
            "refund_payment",
            attempt=1,
            operation_id=f"{instance}:compensate:charge_payment:1",
        )
    )

    assert await _reserved() == stock_before
    assert (await _payments(payment.instance_id))[0].status == "REFUNDED"
