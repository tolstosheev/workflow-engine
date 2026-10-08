"""Тесты резолва обработчиков по строке `module:function`.

Дефинишен ссылается на код строкой, поэтому резолв — точка, где опечатка
превращается в падение инстанса через час после запуска.
"""

from __future__ import annotations

import pytest

from app.core.errors import HandlerNotFoundError
from app.demo.handlers import StepContext, charge_payment, validate_cart
from app.handlers.registry import (
    clear_handler_cache,
    is_handler_importable,
    resolve_handler,
)

DEMO = "app.demo.handlers"


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    clear_handler_cache()
    yield
    clear_handler_cache()


def test_resolves_existing_function() -> None:
    assert resolve_handler(f"{DEMO}:validate_cart") is validate_cart


def test_resolution_is_cached() -> None:
    # Импорт на каждом шаге дорог: набор дефинишенов ограничен.
    assert resolve_handler(f"{DEMO}:charge_payment") is resolve_handler(f"{DEMO}:charge_payment")


def test_raises_on_missing_function() -> None:
    with pytest.raises(HandlerNotFoundError, match="нет функции"):
        resolve_handler(f"{DEMO}:no_such_handler")


def test_raises_on_missing_module() -> None:
    with pytest.raises(HandlerNotFoundError, match="не импортируется"):
        resolve_handler("app.nowhere:handler")


def test_raises_on_reference_without_colon() -> None:
    with pytest.raises(HandlerNotFoundError, match="module:function"):
        resolve_handler(f"{DEMO}.validate_cart")


def test_raises_on_empty_reference() -> None:
    with pytest.raises(HandlerNotFoundError, match="module:function"):
        resolve_handler("")


def test_raises_when_attribute_is_not_callable() -> None:
    # Обработчик должен быть функцией: невызываемый объект означал бы падение
    # в момент исполнения шага, а не при валидации.
    with pytest.raises(HandlerNotFoundError, match="не является функцией"):
        resolve_handler("app.core.config:Settings")


def test_is_handler_importable_true() -> None:
    assert is_handler_importable(f"{DEMO}:validate_cart") is True


def test_is_handler_importable_false() -> None:
    assert is_handler_importable(f"{DEMO}:ghost") is False


def test_clear_cache_forces_reimport() -> None:
    first = resolve_handler(f"{DEMO}:validate_cart")
    clear_handler_cache()

    assert resolve_handler(f"{DEMO}:validate_cart") is first


# --- Демонстрационные обработчики -----------------------------------------


def _ctx(**kwargs: object) -> StepContext:
    defaults: dict[str, object] = {
        "instance_id": "inst-1234abcd",
        "step_name": "validate_cart",
        "attempt": 1,
        "operation_id": "inst-1234abcd:validate_cart:1",
    }
    defaults.update(kwargs)
    return StepContext(**defaults)  # type: ignore[arg-type]


def test_validate_cart_counts_items() -> None:
    ctx = _ctx(input={"items": [{"sku": "A", "qty": 2}, {"sku": "B", "qty": 1}]})

    result = validate_cart(ctx)

    assert result == {"items_count": 2, "valid": True}


def test_validate_cart_falls_back_to_variables() -> None:
    # Вход инстанса — только на первом шаге; дальше данные приходят из
    # переменных, накопленных исполнением.
    ctx = _ctx(input={}, variables={"items": [{"sku": "A", "qty": 5}]})

    assert validate_cart(ctx)["items_count"] == 1


def test_validate_cart_handles_empty_order() -> None:
    assert validate_cart(_ctx(input={}))["valid"] is False


def test_charge_payment_uses_input_amount() -> None:
    ctx = _ctx(input={"amount": "1499.90"})

    assert charge_payment(ctx) == {"charged": True, "amount": "1499.90"}


def test_charge_payment_uses_variables_amount() -> None:
    ctx = _ctx(input={}, variables={"amount": "10.00"})

    assert charge_payment(ctx)["amount"] == "10.00"


def test_charge_payment_without_amount_is_none() -> None:
    assert charge_payment(_ctx(input={}))["amount"] is None


def test_compensation_handlers_are_idempotent_noops() -> None:
    # Компенсация необратима и должна быть безопасна при повторе: эти функции
    # только подтверждают факт отмены, не обращаясь к внешнему миру.
    from app.demo.handlers import cancel_shipment, refund_payment, release_inventory

    ctx = _ctx(step_name="refund_payment")

    assert release_inventory(ctx) == {"released": True}
    assert refund_payment(ctx) == {"refunded": True}
    assert cancel_shipment(ctx) == {"cancelled": True}


def test_create_shipment_is_derived_from_instance() -> None:
    from app.demo.handlers import create_shipment

    result = create_shipment(_ctx(step_name="create_shipment"))

    assert result["shipment_id"] == "shp-inst-123"


def test_confirm_delivery_is_positive() -> None:
    from app.demo.handlers import confirm_delivery

    assert confirm_delivery(_ctx()) == {"confirmed": True}


def test_context_defaults_are_not_shared() -> None:
    first = StepContext(instance_id="a", step_name="b", attempt=1, operation_id="c")
    second = StepContext(instance_id="a", step_name="b", attempt=1, operation_id="c")

    first.input["key"] = "value"

    assert second.input == {}
