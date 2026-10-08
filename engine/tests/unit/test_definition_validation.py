"""Тесты семантической валидации графа дефинишена.

Каждый тест воспроизводит конкретный способ испортить процесс. Дефект,
пойманный здесь, проявился бы через час работы инстанса в проде.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.handlers.registry import clear_handler_cache
from app.schemas.api import ValidationIssue
from app.schemas.definition import (
    DefinitionCreate,
    WorkflowDefinitionSchema,
)
from app.services.definition_validation import collect_step_names, validate_definition

DEMO = "app.demo.handlers"


@pytest.fixture(autouse=True)
def _clear_handler_cache() -> None:
    # Кэш резолва переживает тесты: подмена модуля в sys.modules не была бы
    # видна закэшированному обработчику.
    clear_handler_cache()
    yield
    clear_handler_cache()


def _definition(**steps: dict[str, Any]) -> WorkflowDefinitionSchema:
    return WorkflowDefinitionSchema.model_validate({"start": next(iter(steps)), "steps": steps})


def _codes(result: Any) -> set[str]:
    return {issue.code for issue in result.errors}


def _messages(result: Any) -> str:
    return " | ".join(issue.message for issue in result.errors)


# --- Корректные дефинишены ------------------------------------------------


def test_linear_flow_is_valid() -> None:
    result = validate_definition(
        _definition(
            first={"action": f"{DEMO}:validate_cart", "next": "second"},
            second={
                "action": f"{DEMO}:charge_payment",
                "compensation": f"{DEMO}:refund_payment",
            },
        )
    )

    assert result.valid
    assert not result.errors


def test_declared_loop_is_valid() -> None:
    # Явный цикл с пределом итераций — разрешённое поведение, а не ошибка.
    result = validate_definition(
        _definition(
            first={"action": f"{DEMO}:validate_cart", "next": "retry_me"},
            retry_me={
                "action": f"{DEMO}:charge_payment",
                "next": "first",
                "loop": True,
                "max_iterations": 3,
            },
        )
    )

    assert result.valid


def test_fork_and_join_are_valid() -> None:
    result = validate_definition(
        _definition(
            start_step={"action": f"{DEMO}:validate_cart", "fork": {"left": ["a"], "right": ["b"]}},
            a={
                "action": f"{DEMO}:reserve_inventory",
                "compensation": f"{DEMO}:release_inventory",
                "next": "merge",
            },
            b={
                "action": f"{DEMO}:create_shipment",
                "compensation": f"{DEMO}:cancel_shipment",
                "next": "merge",
            },
            merge={"action": f"{DEMO}:confirm_delivery", "join": ["a", "b"]},
        )
    )

    assert result.valid


def test_conditional_transitions_are_valid() -> None:
    result = validate_definition(
        _definition(
            first={
                "action": f"{DEMO}:charge_payment",
                "next": {"on_success": "ok", "on_failure": "undo"},
            },
            ok={"action": f"{DEMO}:confirm_delivery"},
            undo={"action": f"{DEMO}:refund_payment"},
        )
    )

    assert result.valid


# --- Разрешимость переходов ------------------------------------------------


def test_start_must_reference_existing_step() -> None:
    definition = WorkflowDefinitionSchema.model_validate(
        {"start": "missing", "steps": {"real": {"action": f"{DEMO}:validate_cart"}}}
    )

    result = validate_definition(definition)

    assert not result.valid
    assert "unresolved_start" in _codes(result)
    assert "missing" in _messages(result)


def test_next_must_reference_existing_step() -> None:
    result = validate_definition(
        _definition(first={"action": f"{DEMO}:validate_cart", "next": "nonexistent"})
    )

    assert "unresolved_transition" in _codes(result)


def test_fork_branch_must_reference_existing_step() -> None:
    result = validate_definition(
        _definition(
            first={"action": f"{DEMO}:validate_cart", "fork": {"left": ["ghost"]}},
            other={"action": f"{DEMO}:confirm_delivery"},
        )
    )

    assert "unresolved_transition" in _codes(result)


def test_join_must_reference_existing_step() -> None:
    result = validate_definition(
        _definition(
            first={"action": f"{DEMO}:validate_cart", "fork": {"left": ["other"]}},
            other={"action": f"{DEMO}:confirm_delivery"},
            merged={"action": f"{DEMO}:charge_payment", "join": ["ghost"]},
        )
    )

    assert "unresolved_join" in _codes(result)


# --- Обработчики -----------------------------------------------------------


def test_unimportable_action_is_error() -> None:
    result = validate_definition(_definition(first={"action": f"{DEMO}:no_such_function"}))

    assert "unresolvable_action" in _codes(result)


def test_unimportable_module_is_error() -> None:
    result = validate_definition(_definition(first={"action": "app.nowhere:handler"}))

    assert "unresolvable_action" in _codes(result)


def test_unresolvable_compensation_is_error() -> None:
    # Компенсация, которой нет, обнаружилась бы только при откате — когда
    # деньги уже списаны.
    result = validate_definition(
        _definition(
            first={
                "action": f"{DEMO}:charge_payment",
                "compensation": f"{DEMO}:missing_refund",
            }
        )
    )

    assert "unresolvable_compensation" in _codes(result)


def test_handler_reference_must_have_colon() -> None:
    # Формат проверяется схемой, а не семантикой: без двоеточия резолвить нечего.
    with pytest.raises(ValueError, match="validation error"):
        WorkflowDefinitionSchema.model_validate(
            {"start": "a", "steps": {"a": {"action": "app.demo.handlers.validate_cart"}}}
        )


def test_step_name_must_be_identifier_like() -> None:
    with pytest.raises(ValueError, match="validation error"):
        WorkflowDefinitionSchema.model_validate(
            {"start": "a b", "steps": {"a b": {"action": f"{DEMO}:validate_cart"}}}
        )


# --- Циклы -----------------------------------------------------------------


def test_cycle_without_loop_flag_is_error() -> None:
    # Цикл без явного признака — процесс, который может не завершиться никогда.
    result = validate_definition(
        _definition(
            first={"action": f"{DEMO}:validate_cart", "next": "second"},
            second={"action": f"{DEMO}:confirm_delivery", "next": "first"},
        )
    )

    assert "cycle_without_loop" in _codes(result)


def test_self_referencing_step_is_cycle() -> None:
    result = validate_definition(
        _definition(first={"action": f"{DEMO}:validate_cart", "next": "first"})
    )

    assert "cycle_without_loop" in _codes(result)


def test_loop_without_bound_is_error() -> None:
    result = validate_definition(
        _definition(
            first={"action": f"{DEMO}:validate_cart", "next": "second"},
            second={"action": f"{DEMO}:confirm_delivery", "next": "first", "loop": True},
        )
    )

    assert "loop_without_bound" in _codes(result)


def test_max_iterations_without_loop_flag_is_structural_error() -> None:
    with pytest.raises(ValueError, match="loop"):
        WorkflowDefinitionSchema.model_validate(
            {
                "start": "a",
                "steps": {
                    "a": {
                        "action": f"{DEMO}:validate_cart",
                        "next": "a",
                        "max_iterations": 3,
                    }
                },
            }
        )


def test_acyclic_graph_has_no_loop_errors() -> None:
    result = validate_definition(
        _definition(
            first={"action": f"{DEMO}:validate_cart", "next": "second"},
            second={"action": f"{DEMO}:confirm_delivery"},
        )
    )

    assert "cycle_without_loop" not in _codes(result)
    assert "loop_without_bound" not in _codes(result)


# --- Достижимость ----------------------------------------------------------


def test_unreachable_step_is_error() -> None:
    # Недостижимый шаг — мёртвый код процесса: о нём знают валидатор и
    # читатель дефинишена, но он никогда не выполнится.
    result = validate_definition(
        _definition(
            first={"action": f"{DEMO}:validate_cart"},
            orphan={"action": f"{DEMO}:confirm_delivery"},
        )
    )

    assert "unreachable_step" in _codes(result)


def test_all_steps_are_reachable_in_chain() -> None:
    result = validate_definition(
        _definition(
            first={"action": f"{DEMO}:validate_cart", "next": "second"},
            second={"action": f"{DEMO}:reserve_inventory", "next": "third"},
            third={"action": f"{DEMO}:confirm_delivery"},
        )
    )

    assert "unreachable_step" not in _codes(result)


def test_steps_after_fork_are_reachable() -> None:
    # Ветви fork не должны считаться недостижимыми: их запускает разветвитель.
    result = validate_definition(
        _definition(
            split={"action": f"{DEMO}:validate_cart", "fork": {"a": ["left"], "b": ["right"]}},
            left={"action": f"{DEMO}:reserve_inventory", "next": "merge"},
            right={"action": f"{DEMO}:create_shipment", "next": "merge"},
            merge={"action": f"{DEMO}:confirm_delivery", "join": ["left", "right"]},
        )
    )

    assert "unreachable_step" not in _codes(result)


# --- Предупреждения --------------------------------------------------------


def test_missing_compensation_is_warning_not_error() -> None:
    # Отсутствие компенсации не ошибка: часть шагов обратима по умолчанию.
    result = validate_definition(_definition(first={"action": f"{DEMO}:charge_payment"}))

    assert result.valid
    assert "missing_compensation" in {issue.code for issue in result.warnings}


def test_compensated_step_has_no_warning() -> None:
    result = validate_definition(
        _definition(
            first={
                "action": f"{DEMO}:charge_payment",
                "compensation": f"{DEMO}:refund_payment",
            }
        )
    )

    assert "missing_compensation" not in {issue.code for issue in result.warnings}


def test_terminal_step_produces_warning() -> None:
    result = validate_definition(
        _definition(
            first={"action": f"{DEMO}:validate_cart", "next": "last"},
            last={
                "action": f"{DEMO}:charge_payment",
                "compensation": f"{DEMO}:refund_payment",
            },
        )
    )

    assert "terminal_step" in {issue.code for issue in result.warnings}


# --- Множественность -------------------------------------------------------


def test_all_problems_are_reported_at_once() -> None:
    # Пользователь должен увидеть все ошибки сразу: правка по одной за прогон
    # превращает валидацию в мучение на длинном определении.
    result = validate_definition(
        _definition(
            first={"action": f"{DEMO}:no_such_handler", "next": "ghost"},
            second={"action": "app.nowhere:handler"},
        )
    )

    assert "unresolvable_action" in _codes(result)
    assert "unresolved_transition" in _codes(result)
    assert "unreachable_step" in _codes(result)


def test_issue_carries_step_name() -> None:
    result = validate_definition(
        _definition(first={"action": f"{DEMO}:validate_cart", "next": "ghost"})
    )

    issue: ValidationIssue = next(i for i in result.errors if i.code == "unresolved_transition")
    assert issue.step == "first"


# --- Схема ввода -----------------------------------------------------------


def test_definition_create_rejects_unknown_field() -> None:
    with pytest.raises(ValueError, match="validation error"):
        DefinitionCreate.model_validate(
            {
                "name": "order",
                "definition": {"start": "a", "steps": {"a": {"action": f"{DEMO}:validate_cart"}}},
                "unexpected": 1,
            }
        )


def test_step_rejects_unknown_field() -> None:
    # Опечатка в ключе (compansation вместо compensation) обязана падать: без
    # forbid она тихо потеряла бы компенсацию.
    with pytest.raises(ValueError, match="validation error"):
        WorkflowDefinitionSchema.model_validate(
            {
                "start": "a",
                "steps": {
                    "a": {"action": f"{DEMO}:charge_payment", "compansation": "app:x:refund"}
                },
            }
        )


def test_empty_steps_rejected() -> None:
    with pytest.raises(ValueError, match="validation error"):
        WorkflowDefinitionSchema.model_validate({"start": "a", "steps": {}})


def test_retry_policy_bounds() -> None:
    with pytest.raises(ValueError, match="validation error"):
        WorkflowDefinitionSchema.model_validate(
            {
                "start": "a",
                "steps": {
                    "a": {
                        "action": f"{DEMO}:validate_cart",
                        "retry": {"max_attempts": 0},
                    }
                },
            }
        )


def test_collect_step_names_preserves_order() -> None:
    definition = _definition(
        first={"action": f"{DEMO}:validate_cart", "next": "second"},
        second={"action": f"{DEMO}:confirm_delivery"},
    )

    assert list(collect_step_names(definition)) == ["first", "second"]
