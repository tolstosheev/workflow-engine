"""Обработчики шагов демонстрационной предметной области.

Сигнатура обработчика: `(ctx: StepContext) -> dict`. Возвращаемое значение
становится выходом шага и попадает в переменные инстанса.

Обработчики работают с БД. Это не украшение: компенсация имеет смысл только
тогда, когда отменять что-то есть. Резерв, не записанный в таблицу, нечего
отменять, и «откат» превращается в слово.

Реальные сетевые вызовы не делаются: проверяется транзакционная машина, а не
интеграция с настоящими сервисами.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import session_scope
from app.engine import idempotency
from app.models.workflow import DemoInventoryItem, DemoPayment, DemoShipment


@dataclass
class StepContext:
    """Данные, доступные обработчику.

    Идентификатор операции обязателен: обработчик обязан передать его наружу во
    внешний вызов, иначе повтор шага после падения продублирует операцию
    (§3.4).

    Здесь он же служит ключом идемпотентности внутри БД: обработчик применяет
    эффект и отмечает операцию одной транзакцией, поэтому падение между
    действием и отметкой невозможно.
    """

    instance_id: str
    step_name: str
    attempt: int
    operation_id: str
    input: dict[str, Any] = field(default_factory=dict)
    variables: dict[str, Any] = field(default_factory=dict)


# Эффект шага: изменение, применяемое в уже открытой сессии. Сессия
# передаётся, а не открывается внутри, чтобы проверка ключа, эффект и отметка
# оказались в одной транзакции.
Effect = Callable[[AsyncSession], Awaitable[None]]


def _items(ctx: StepContext) -> list[dict[str, Any]]:
    return list(ctx.input.get("items") or ctx.variables.get("items") or [])


def _instance_uuid(ctx: StepContext) -> uuid.UUID:
    return uuid.UUID(str(ctx.instance_id))


async def _apply_once(ctx: StepContext, effect: Effect) -> bool:
    """Применяет эффект один раз по ключу операции.

    `False` означает, что операция уже применялась. Это штатный повтор после
    падения воркера, а не ошибка.
    """
    async with session_scope() as session:
        return await idempotency.apply_once_by_key(
            session,
            _instance_uuid(ctx),
            str(ctx.operation_id),
            ctx.step_name,
            effect=lambda: effect(session),
        )


def _requested(items: list[dict[str, Any]]) -> list[tuple[str, int]]:
    """Пары `(артикул, количество)` без пустых и нулевых позиций."""
    pairs: list[tuple[str, int]] = []
    for item in items:
        sku = str(item.get("sku", "")).strip()
        qty = int(item.get("qty", 0))
        if sku and qty > 0:
            pairs.append((sku, qty))
    return pairs


async def validate_cart(ctx: StepContext) -> dict[str, Any]:
    items = _items(ctx)
    return {"items_count": len(items), "valid": bool(items)}


async def reserve_inventory(ctx: StepContext) -> dict[str, Any]:
    requested = _requested(_items(ctx))

    async def effect(session: AsyncSession) -> None:
        for sku, qty in requested:
            # Поиск по `sku`, а не по первичному ключу: у таблицы два ключа, и
            # `get()` по умолчанию берёт идентификатор, а не артикул.
            row = await session.scalar(
                select(DemoInventoryItem).where(DemoInventoryItem.sku == sku)
            )
            if row is None:
                raise RuntimeError(f"товар {sku!r} не найден на складе")
            if row.reserved + qty > row.available:
                # Проверка в обработчике, а не только ограничением таблицы:
                # ограничение поймало бы превышение при записи, а сообщение
                # оператору должно объяснять причину.
                raise RuntimeError(
                    f"товара {sku!r} недостаточно: доступно "
                    f"{row.available - row.reserved}, запрошено {qty}"
                )
            row.reserved += qty

    await _apply_once(ctx, effect)
    return {"reserved_units": sum(qty for _, qty in requested)}


async def release_inventory(ctx: StepContext) -> dict[str, Any]:
    requested = _requested(_items(ctx))

    async def effect(session: AsyncSession) -> None:
        for sku, qty in requested:
            row = await session.scalar(
                select(DemoInventoryItem).where(DemoInventoryItem.sku == sku)
            )
            if row is None:  # pragma: no cover - резерв без товара
                continue
            # Отменяется ровно то, что было зарезервировано. Большее
            # списание ушло бы в минус и нарушило бы ограничение остатка.
            row.reserved = max(0, row.reserved - qty)

    await _apply_once(ctx, effect)
    return {"released": True}


async def charge_payment(ctx: StepContext) -> dict[str, Any]:
    raw_amount = ctx.input.get("amount") or ctx.variables.get("amount")
    try:
        amount = Decimal(str(raw_amount))
    except (InvalidOperation, TypeError) as exc:
        raise RuntimeError(f"сумма {raw_amount!r} не является числом") from exc

    async def effect(session: AsyncSession) -> None:
        session.add(DemoPayment(instance_id=_instance_uuid(ctx), amount=amount, status="HELD"))

    await _apply_once(ctx, effect)
    return {"charged": True, "amount": raw_amount}


async def refund_payment(ctx: StepContext) -> dict[str, Any]:
    async def effect(session: AsyncSession) -> None:
        payments = await session.scalars(
            select(DemoPayment).where(
                DemoPayment.instance_id == _instance_uuid(ctx),
                DemoPayment.status == "HELD",
            )
        )
        refunded_at = datetime.now(UTC)
        for payment in payments:
            payment.status = "REFUNDED"
            payment.refunded_at = refunded_at

    await _apply_once(ctx, effect)
    return {"refunded": True}


async def create_shipment(ctx: StepContext) -> dict[str, Any]:
    # Отправление дописывается, а не ищется: тот же шаг, выполненный после
    # повтора с другим ключом, создал бы второе отправление, и это был бы дубль.
    async def effect(session: AsyncSession) -> None:
        session.add(
            DemoShipment(
                instance_id=_instance_uuid(ctx),
                status="CREATED",
                tracking_number=f"trk-{str(ctx.instance_id)[:8]}",
            )
        )

    await _apply_once(ctx, effect)
    return {"shipment_id": f"shp-{str(ctx.instance_id)[:8]}", "status": "CREATED"}


async def cancel_shipment(ctx: StepContext) -> dict[str, Any]:
    async def effect(session: AsyncSession) -> None:
        shipments = await session.scalars(
            select(DemoShipment).where(
                DemoShipment.instance_id == _instance_uuid(ctx),
                DemoShipment.status == "CREATED",
            )
        )
        cancelled_at = datetime.now(UTC)
        for shipment in shipments:
            shipment.status = "CANCELLED"
            shipment.cancelled_at = cancelled_at

    await _apply_once(ctx, effect)
    return {"cancelled": True}


async def confirm_delivery(ctx: StepContext) -> dict[str, Any]:
    # Обратимость по умолчанию: подтверждение не меняет состояние, которое
    # пришлось бы отменять, поэтому компенсация ему не нужна (ADR-0006).
    return {"confirmed": True}
