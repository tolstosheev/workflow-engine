"""Каждое демонстрационное определение обязано быть исполняемым.

Определения добавляются ради сценариев показа: сценарий «долгое ожидание» без
шага с ожиданием проходит мгновенно и доказывает обратное своему названию, а
сценарий «пауза человека» без `wait_for: manual` не когда-нибудь встанет, а
сразу пройдёт. И то и другое обнаруживалось бы на защите, а не в тестах.

Проверка дешёвая: схема и валидатор графа ходят по определению целиком, а
исполнения не требует.
"""

from __future__ import annotations

import pytest

from app.schemas.definition import WorkflowDefinitionSchema
from app.services.definition_validation import validate_definition
from scripts.definitions import DEFINITION_NAME, DEMO_FLOWS, demo_flow, order_input


@pytest.mark.parametrize("name", sorted(DEMO_FLOWS))
def test_demo_flow_passes_schema_and_validation(name: str) -> None:
    schema = WorkflowDefinitionSchema.model_validate(demo_flow(name))

    result = validate_definition(schema)

    errors = [f"{issue.code}: {issue.message}" for issue in result.errors]
    assert not errors, f"{name}: {errors}"


def test_new_flows_add_no_warning_classes() -> None:
    """Новое определение не должно вносить новых предупреждений.

    Предупреждения у основного определения есть и остаются: отсутствие
    компенсации у проверки корзины и шаг без перехода — свойства самого
    определения, а не дефект наполнения. Но новый вид предупреждения в новом
    определении — это уже забытая часть демонстрации, и на защите она выглядит
    как «движок не понимает, что здесь происходит».
    """
    baseline = _warning_codes(DEFINITION_NAME)
    assert baseline, "у основного определения нет предупреждений — база сравнения пуста"

    for name in sorted(DEMO_FLOWS):
        extra = sorted(_warning_codes(name) - baseline)
        assert not extra, f"{name}: новые предупреждения {extra}"


def _warning_codes(name: str) -> set[str]:
    schema = WorkflowDefinitionSchema.model_validate(demo_flow(name))
    result = validate_definition(schema)
    return {issue.code for issue in result.warnings}


def test_flow_names_are_distinct() -> None:
    # Инструменты берут определение по имени: совпадение имён сделало бы запуск
    # неоднозначным, и смоук проверял бы не то, что демонстрируется.
    assert len(DEMO_FLOWS) == len(set(DEMO_FLOWS))


def test_wait_flow_really_waits() -> None:
    """У ожидания должен быть ненулевой срок, иначе сценарий ждёт мгновенно."""

    steps = demo_flow("order_with_wait")["steps"]

    assert steps["await_delivery"]["wait_before_seconds"] > 0


def test_approval_flow_pauses_before_irreversible_step() -> None:
    """Пауза стоит на оплате: ждать решения после списания бессмысленно."""

    steps = demo_flow("order_with_approval")["steps"]

    assert steps["charge_payment"]["wait_for"] == "manual"
    assert "pause_timeout_seconds" not in steps["charge_payment"]


def test_pause_deadline_flow_has_a_deadline() -> None:
    """Сценарий срока ожидания без срока ждал бы вечно (ADR-0016)."""

    steps = demo_flow("order_with_pause_deadline")["steps"]

    assert steps["charge_payment"]["wait_for"] == "manual"
    assert steps["charge_payment"]["pause_timeout_seconds"] > 0


def test_loop_flow_declares_its_limit() -> None:
    """Цикл без предела исполнялся бы вечно: счётчик обязателен и в схеме."""

    steps = demo_flow("order_with_loop")["steps"]

    looping = [step for step in steps.values() if step.get("loop")]
    assert len(looping) == 1, "цикл должен быть ровно один"
    assert looping[0]["max_iterations"] >= 1


def test_every_flow_starts_at_a_declared_step() -> None:
    for name in DEMO_FLOWS:
        flow = demo_flow(name)
        assert flow["start"] in flow["steps"], name


def test_order_input_survives_validation() -> None:
    """Вход заказа принимается схемой инстанса: иначе сценарий падал бы на старте."""

    payload = order_input(1)

    assert payload["items"], "без позиций корзина пуста и проверка не значит ничего"
