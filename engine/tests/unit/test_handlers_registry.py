"""Тесты резолва обработчиков по строке `module:function`.

Дефинишен ссылается на код строкой, поэтому резолв — точка, где опечатка
превращается в падение инстанса через час после запуска.
"""

from __future__ import annotations

import pytest

from app.core.errors import HandlerNotFoundError
from app.demo.handlers import StepContext, validate_cart
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
#
# Обработчики, обращающиеся к БД, проверяются в интеграционных тестах
# `tests/integration/test_demo_handlers.py`: здесь нет соединения с базой.
# Unit-тесты остаются только у резолва и у чистых функций.


def _ctx(**kwargs: object) -> StepContext:
    defaults: dict[str, object] = {
        "instance_id": "inst-1234abcd",
        "step_name": "validate_cart",
        "attempt": 1,
        "operation_id": "inst-1234abcd:validate_cart:1",
    }
    defaults.update(kwargs)
    return StepContext(**defaults)  # type: ignore[arg-type]


async def test_validate_cart_counts_items() -> None:
    ctx = _ctx(input={"items": [{"sku": "A", "qty": 2}, {"sku": "B", "qty": 1}]})

    result = await validate_cart(ctx)

    assert result == {"items_count": 2, "valid": True}


async def test_validate_cart_falls_back_to_variables() -> None:
    # Вход инстанса — только на первом шаге; дальше данные приходят из
    # переменных, накопленных исполнением.
    ctx = _ctx(input={}, variables={"items": [{"sku": "A", "qty": 5}]})

    assert (await validate_cart(ctx))["items_count"] == 1


async def test_validate_cart_handles_empty_order() -> None:
    assert (await validate_cart(_ctx(input={})))["valid"] is False


def test_confirm_delivery_is_registered() -> None:
    """Обработчик доставки разрешается по имени.

    Раньше здесь проверялся вызов, и обработчик был единственным, который не
    ходил в базу: подтверждение ничего не меняло. Теперь оно отпускает резерв
    (ADR-0020), и проверка эффекта живёт в интеграционных тестах, где есть
    настоящий склад, — а на отсутствующей базе вызов падал бы с чужой ошибкой.
    """
    from app.demo.handlers import confirm_delivery

    assert callable(confirm_delivery)
    assert resolve_handler("app.demo.handlers:confirm_delivery") is confirm_delivery


def test_context_defaults_are_not_shared() -> None:
    first = StepContext(instance_id="a", step_name="b", attempt=1, operation_id="c")
    second = StepContext(instance_id="a", step_name="b", attempt=1, operation_id="c")

    first.input["key"] = "value"

    assert second.input == {}


async def test_handler_outside_allowed_roots_is_refused() -> None:
    # Определение сохраняется через HTTP, и строка `os:system` импортировалась бы
    # в адрес воркера как свой собственный модуль (ADR-0013).
    from app.core.errors import HandlerNotFoundError
    from app.handlers.registry import resolve_handler

    clear_handler_cache()
    try:
        with pytest.raises(HandlerNotFoundError) as caught:
            resolve_handler("os:system")
        assert "вне разрешённых" in str(caught.value)
    finally:
        clear_handler_cache()


def test_handler_of_allowed_package_still_resolves() -> None:
    from app.handlers.registry import resolve_handler

    clear_handler_cache()
    try:
        handler = resolve_handler("app.demo.handlers:validate_cart")
        assert callable(handler)
    finally:
        clear_handler_cache()


def test_allowed_roots_may_be_widened_by_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    # Расширение обязано своимкой в одном месте: если привется такая граница, то ею можно легко пристроить.
    from app.core import config as config_module
    from app.handlers.registry import _allowed_roots

    original = config_module.get_settings()
    widened = original.model_copy(update={"handler_module_allowlist": "app, mydomain"})
    monkeypatch.setattr("app.handlers.registry.get_settings", lambda: widened)

    clear_handler_cache()
    try:
        assert _allowed_roots() == frozenset({"app", "mydomain"})
    finally:
        clear_handler_cache()
