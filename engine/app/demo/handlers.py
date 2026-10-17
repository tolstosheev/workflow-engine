"""Обработчики шагов демонстрационной предметной области.

Сигнатура `(ctx: StepContext) -> dict`: возвращаемое значение становится
выходом шага и попадает в переменные процесса.

Обработчики пишут в БД: компенсация имеет смысл, только когда отменять есть.
Резерв, не записанный в таблицу, отменить нечем. Сетевых вызовов нет —
проверяется транзакционная машина.
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
from app.engine.executor import PermanentStepError
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
                raise PermanentStepError(f"товар {sku!r} не найден на складе")
            if row.reserved + qty > row.available:
                # Проверка в обработчике, а не только ограничением таблицы:
                # ограничение поймало бы превышение при записи, а сообщение
                # оператору должно объяснять причину.
                # Ошибка постоянная: товар сам не появится, и повтор `max_attempts`
                # раз лишь отложит откат зарезервированного.
                raise PermanentStepError(
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
    """Подтверждает доставку и отпускает резерв (ADR-0020).

    Товар доставлен, поэтому холд на складе больше не нужен. Раньше резерв снимался
    только компенсацией, и каждый успешный заказ навсегда уменьшал склад.

    Шаг стал необратимым, поэтому у него появилась компенсация — повторный резерв
    (`reserve_inventory` под именем шага доставки): при отмене доставки товар
    возвращается в холд, иначе склад считал бы его свободным, хотя он уехал.
    """
    requested = _requested(_items(ctx))

    async def effect(session: AsyncSession) -> None:
        for sku, qty in requested:
            row = await session.scalar(
                select(DemoInventoryItem).where(DemoInventoryItem.sku == sku)
            )
            if row is None:  # pragma: no cover - доставка без товара
                continue
            # `max(0, …)`: резерв мог быть снят чужой отменой, и второй раз
            # списывать нечего. Отрицательный остаток нарушил бы ограничение
            # таблицы и уронил бы весь откат.
            row.reserved = max(0, row.reserved - qty)

    await _apply_once(ctx, effect)
    return {"confirmed": True, "released_units": sum(qty for _, qty in requested)}


async def notify_customer(ctx: StepContext) -> dict[str, Any]:
    """Уведомление о доставке.

    Внешнего эффекта в демо-области нет: письма не отправляются, push не
    уходит. Шагу он нужен не ради эффекта, а чтобы компенсация доставки была
    достижима — откат строится по успешно завершённым шагам, а шаг последний
    ничем не сорвать (ADR-0020).
    """
    return {"notified": True, "step": ctx.step_name}
