"""Демонстрационные определения workflow и входные данные.

Определения живут здесь, а не в `seed.py`, потому что их используют три
скрипта: наполнение, сквозная проверка и нагрузочный прогон. Расхождение
копий означало бы, что замеряется не тот процесс, который демонстрируется.
"""

from __future__ import annotations

from typing import Any

DEFINITION_NAME = "order_fulfillment"
DEMO_VERSION = 1

# Модуль предметной области один: обработчики различаются именами, а не
# файлами. Разделение по файлам добавило бы резолв `package.module:function`
# без выигрыша в проверяемости.
HANDLERS = "app.demo.handlers"


def _step(action: str, **extra: Any) -> dict[str, Any]:
    return {"action": f"{HANDLERS}:{action}", **extra}


def order_fulfillment(wait_before_seconds: float = 0) -> dict[str, Any]:
    """Заказ: резерв → оплата → отправление → подтверждение.

    У каждого шага, меняющего внешнее состояние, объявлена компенсация: без неё
    откат оставил бы резерв или списание (§3.5 AGENTS.md). Валидация и
    подтверждение обратимы по умолчанию, поэтому компенсаций не имеют.

    `wait_before_seconds` у ожидания — единственный долгий участок процесса:
    именно он делает процесс долгоживущим, а не быстрым запросом.
    """
    return {
        "start": "validate_cart",
        "description": "Демонстрационный заказ с компенсациями на каждом изменении",
        "metadata": {"domain": "demo", "scenario": "order_fulfillment"},
        "steps": {
            "validate_cart": _step(
                "validate_cart",
                next="reserve_inventory",
                description="Проверка корзины перед любым побочным эффектом",
            ),
            "reserve_inventory": _step(
                "reserve_inventory",
                compensation=f"{HANDLERS}:release_inventory",
                next="charge_payment",
                description="Резерв товара на складе",
            ),
            "charge_payment": _step(
                "charge_payment",
                compensation=f"{HANDLERS}:refund_payment",
                next="create_shipment",
                retry={"max_attempts": 3, "backoff_seconds": 1, "backoff_multiplier": 2},
                description="Списание средств — главный необратимый шаг",
            ),
            "create_shipment": _step(
                "create_shipment",
                compensation=f"{HANDLERS}:cancel_shipment",
                next="await_delivery",
                description="Создание отправления",
            ),
            "await_delivery": _step(
                "confirm_delivery",
                wait_before_seconds=wait_before_seconds,
                description="Ожидание доставки: durable-таймер, а не sleep",
            ),
        },
    }


def order_input(index: int = 0, items: int = 2) -> dict[str, Any]:
    """Вход инстанса: корзина и сумма.

    Индекс входит во вход, чтобы два инстанса в нагрузочном прогоне не были
    для журнала неотличимыми при разборе инцидента.
    """
    return {
        "order_number": f"ORD-{index:06d}",
        "items": [{"sku": f"SKU-{i:03d}", "qty": i + 1} for i in range(items)],
        "amount": f"{100 + index % 50}.00",
    }


# Данные склада: без них резерв выглядит успешным всегда, и замер ничего
# не говорит о поведении под исчерпанием остатка.
DEMO_INVENTORY: list[dict[str, Any]] = [
    {"sku": "SKU-000", "available": 10_000, "reserved": 0},
    {"sku": "SKU-001", "available": 10_000, "reserved": 0},
    {"sku": "SKU-002", "available": 10_000, "reserved": 0},
    {"sku": "SKU-003", "available": 10_000, "reserved": 0},
]
