"""Обработчики шагов демонстрационной предметной области.

Сигнатура обработчика: `(ctx: StepContext) -> dict`. Возвращаемое значение
становится выходом шага и попадает в переменные инстанса.

Реальные сетевые вызовы здесь намеренно не делаются: на Фазе 1 проверяется
не предметная область, а регистрация и разрешимость обработчиков.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class StepContext:
    """Данные, доступные обработчику шага.

    Идентификатор операции обязателен: обработчик обязан передать его наружу во
    внешний вызов, иначе повтор шага после падения продублирует операцию
    (§3.4 AGENTS.md).
    """

    instance_id: str
    step_name: str
    attempt: int
    operation_id: str
    input: dict[str, Any] = field(default_factory=dict)
    variables: dict[str, Any] = field(default_factory=dict)


def validate_cart(ctx: StepContext) -> dict[str, Any]:
    items = ctx.input.get("items") or ctx.variables.get("items") or []
    return {"items_count": len(items), "valid": bool(items)}


def reserve_inventory(ctx: StepContext) -> dict[str, Any]:
    items = ctx.input.get("items") or ctx.variables.get("items") or []
    return {"reserved_units": sum(int(item.get("qty", 0)) for item in items)}


def release_inventory(ctx: StepContext) -> dict[str, Any]:
    return {"released": True}


def charge_payment(ctx: StepContext) -> dict[str, Any]:
    amount = ctx.input.get("amount") or ctx.variables.get("amount")
    return {"charged": True, "amount": amount}


def refund_payment(ctx: StepContext) -> dict[str, Any]:
    return {"refunded": True}


def create_shipment(ctx: StepContext) -> dict[str, Any]:
    return {"shipment_id": f"shp-{ctx.instance_id[:8]}", "status": "CREATED"}


def cancel_shipment(ctx: StepContext) -> dict[str, Any]:
    return {"cancelled": True}


def confirm_delivery(ctx: StepContext) -> dict[str, Any]:
    return {"confirmed": True}
