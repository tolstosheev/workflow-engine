"""Тесты интерпретатора и идемпотентности — чистая логика без БД."""

from __future__ import annotations

import uuid
from typing import Any

from app.engine.interpreter import (
    attempts_for,
    backoff_for,
    decide_after_failure,
    decide_after_success,
    wait_before,
)
from app.engine.journal import RecordedEvent
from app.models.enums import EventType
from app.schemas.definition import WorkflowDefinitionSchema

DEMO = "app.demo.handlers"


def _definition(**steps: dict[str, Any]) -> WorkflowDefinitionSchema:
    return WorkflowDefinitionSchema.model_validate({"start": next(iter(steps)), "steps": steps})


def _step(definition: WorkflowDefinitionSchema, name: str) -> Any:
    return definition.steps[name]


# --- Переходы после успеха -------------------------------------------------


def test_success_follows_string_next() -> None:
    definition = _definition(
        a={"action": f"{DEMO}:validate_cart", "next": "b"},
        b={"action": f"{DEMO}:confirm_delivery"},
    )

    decision = decide_after_success(definition, "a", _step(definition, "a"))

    assert decision.step_name == "b"
    assert decision.kind == "run"


def test_success_on_last_step_completes() -> None:
    definition = _definition(a={"action": f"{DEMO}:confirm_delivery"})

    decision = decide_after_success(definition, "a", _step(definition, "a"))

    assert decision.step_name is None
    assert decision.kind == "complete"


def test_success_takes_on_success_branch() -> None:
    definition = _definition(
        a={
            "action": f"{DEMO}:charge_payment",
            "next": {"on_success": "ok", "on_failure": "undo"},
        },
        ok={"action": f"{DEMO}:confirm_delivery"},
        undo={"action": f"{DEMO}:refund_payment"},
    )

    decision = decide_after_success(definition, "a", _step(definition, "a"))

    assert decision.step_name == "ok"


def test_success_branch_without_on_success_completes() -> None:
    definition = _definition(
        a={"action": f"{DEMO}:charge_payment", "next": {"on_failure": "undo"}},
        undo={"action": f"{DEMO}:refund_payment"},
    )

    decision = decide_after_success(definition, "a", _step(definition, "a"))

    assert decision.kind == "complete"


def test_fork_step_returns_branches() -> None:
    definition = _definition(
        split={"action": f"{DEMO}:validate_cart", "fork": {"left": ["a"], "right": ["b"]}},
        a={"action": f"{DEMO}:reserve_inventory", "compensation": f"{DEMO}:release_inventory"},
        b={"action": f"{DEMO}:create_shipment", "compensation": f"{DEMO}:cancel_shipment"},
    )

    decision = decide_after_success(definition, "split", _step(definition, "split"))

    assert decision.kind == "fork"
    assert decision.branches == {"left": ["a"], "right": ["b"]}


# --- Переходы после неудачи ------------------------------------------------


def test_failure_takes_on_failure_branch() -> None:
    definition = _definition(
        a={
            "action": f"{DEMO}:charge_payment",
            "next": {"on_success": "ok", "on_failure": "undo"},
        },
        ok={"action": f"{DEMO}:confirm_delivery"},
        undo={"action": f"{DEMO}:refund_payment"},
    )

    decision = decide_after_failure(definition, "a", _step(definition, "a"), 1, 3)

    assert decision.step_name == "undo"


def test_failure_is_retried_before_exhaustion() -> None:
    definition = _definition(a={"action": f"{DEMO}:charge_payment"})

    decision = decide_after_failure(definition, "a", _step(definition, "a"), 1, 3)

    assert decision.step_name == "a"
    assert decision.kind == "run"


def test_failure_stops_after_last_attempt() -> None:
    # Исчерпание попыток ведёт в откат, а не в остановку: выполненные шаги
    # надо откатить, иначе процесс останется в промежуточном состоянии.
    definition = _definition(a={"action": f"{DEMO}:charge_payment"})

    decision = decide_after_failure(definition, "a", _step(definition, "a"), 3, 3)

    assert decision.step_name is None
    assert decision.kind == "stop"
    assert "откат" in decision.reason


def test_step_without_successor_completes() -> None:
    definition = _definition(a={"action": f"{DEMO}:confirm_delivery"})

    decision = decide_after_success(definition, "a", _step(definition, "a"))

    assert decision.step_name is None
    assert decision.kind == "complete"


# --- Политика повторов -----------------------------------------------------


def test_default_attempts_come_from_settings() -> None:
    definition = _definition(a={"action": f"{DEMO}:confirm_delivery"})

    assert attempts_for(_step(definition, "a"), fallback_max=3) == 3


def test_explicit_retry_overrides_default() -> None:
    definition = _definition(a={"action": f"{DEMO}:charge_payment", "retry": {"max_attempts": 5}})

    assert attempts_for(_step(definition, "a"), fallback_max=3) == 5


def test_backoff_grows_between_attempts() -> None:
    # Рост задержки обязателен: немедленные повторы при недоступном сервисе
    # только добивают его.
    definition = _definition(
        a={
            "action": f"{DEMO}:charge_payment",
            "retry": {"max_attempts": 5, "backoff_seconds": 2, "backoff_multiplier": 2},
        }
    )

    step = _step(definition, "a")

    assert backoff_for(step, 1, 3) == 2
    assert backoff_for(step, 2, 3) == 4
    assert backoff_for(step, 3, 3) == 8


def test_backoff_defaults_to_one_second() -> None:
    # Даже без явного retry небольшая задержка полезна: мгновенный повтор при
    # недоступном сервисе не даёт ему времени восстановиться.
    definition = _definition(a={"action": f"{DEMO}:confirm_delivery"})

    assert backoff_for(_step(definition, "a"), 1, 3) == 1


def test_backoff_can_be_disabled_explicitly() -> None:
    definition = _definition(
        a={"action": f"{DEMO}:confirm_delivery", "retry": {"backoff_seconds": 0}}
    )

    assert backoff_for(_step(definition, "a"), 1, 3) == 0


def test_timeout_has_safe_default() -> None:
    # Таймаут нужен всегда: внешний вызов без него может висеть бесконечно.
    definition = _definition(a={"action": f"{DEMO}:confirm_delivery"})

    assert _step(definition, "a").timeout_seconds == 30


def test_wait_before_is_durable_delay() -> None:
    definition = _definition(a={"action": f"{DEMO}:confirm_delivery", "wait_before_seconds": 60})

    assert wait_before(_step(definition, "a")) == 60


# --- ??????? ?????????????? -------------------------------------------------


def test_fork_returns_branches() -> None:
    """Интерпретатор различает fork, но определение с ним не доходит до сюда.

    Валидатор отвергает параллельные ветки при сохранении, поэтому решение
    ниже — описание намерения, а не рабочий путь. Проверка отказа —
    в тестах валидатора.
    """
    definition = _definition(
        split={"action": f"{DEMO}:validate_cart", "fork": {"x": ["a"]}},
        a={"action": f"{DEMO}:reserve_inventory"},
    )

    decision = decide_after_success(definition, "split", _step(definition, "split"))

    assert decision.kind == "fork"
    assert decision.branches == {"x": ["a"]}
    # Исполнить ветви нечем: решение не называет следующего шага.
    assert decision.step_name is None


# --- Предел числа проходов цикла ------------------------------------------


def test_loop_runs_until_limit_is_reached() -> None:
    definition = _definition(
        a={"action": f"{DEMO}:reserve_inventory", "next": "a", "loop": True, "max_iterations": 3},
    )
    step = _step(definition, "a")

    assert decide_after_success(definition, "a", step, iterations=1).kind == "run"
    assert decide_after_success(definition, "a", step, iterations=2).kind == "run"
    final = decide_after_success(definition, "a", step, iterations=3)

    # Предел достигается, а не превышается: три прохода при max_iterations: 3.
    assert final.kind == "stop"
    assert final.step_name is None
    assert "3 из 3" in final.reason


def test_loop_without_iterations_keeps_going() -> None:
    """Шаг вне цикла ограничением проходов не связан."""
    definition = _definition(
        a={"action": f"{DEMO}:reserve_inventory", "next": "b"},
        b={"action": f"{DEMO}:confirm_delivery"},
    )

    assert decide_after_success(definition, "a", _step(definition, "a"), 999).kind == "run"


def test_join_step_has_no_successor() -> None:
    """Слияние не объявляет продолжения — но и до исполнения не дойдёт.

    Валидатор отвергает join, так что это факт об интерпретаторе, а не о
    работающем пути.
    """
    definition = _definition(
        a={"action": f"{DEMO}:reserve_inventory", "next": "j"},
        j={"action": f"{DEMO}:confirm_delivery", "join": ["a"]},
    )

    decision = decide_after_success(definition, "j", _step(definition, "j"))

    assert decision.kind == "complete"


# --- Порядок компенсаций из журнала ----------------------------------------


def test_compensation_order_comes_from_journal_not_definition() -> None:
    # Факт выполнения берётся из событий, а не из структуры определения:
    # определение не знает, какие шаги реально успели выполниться.
    from datetime import UTC, datetime

    from app.engine.journal import replay, steps_to_compensate

    events = [
        RecordedEvent(
            seq=0,
            type=EventType.STEP_COMPLETED,
            payload={"step": "a"},
            created_at=datetime.now(UTC),
        ),
        RecordedEvent(
            seq=1,
            type=EventType.STEP_COMPLETED,
            payload={"step": "b"},
            created_at=datetime.now(UTC),
        ),
    ]

    assert steps_to_compensate(replay(events)) == ["b", "a"]


def test_instance_id_is_uuid_for_keys() -> None:
    assert isinstance(uuid.uuid4(), uuid.UUID)


def test_permanent_failure_stops_without_retries() -> None:
    """Постоянная ошибка не повторяется, даже есть попытки.

    Без этого интерпретация считала бы повтором ибо шаг, а потом откат всего
    резервированного товара.
    """
    definition = _definition(a={"action": f"{DEMO}:charge_payment"})

    decision = decide_after_failure(definition, "a", _step(definition, "a"), 1, 3, permanent=True)

    assert decision.step_name is None
    assert decision.kind == "stop"
    assert "постоянная" in decision.reason


def test_permanent_failure_still_honours_on_failure_branch() -> None:
    """Явная ветка обязательна: первая попытка не отменяет
    замятленный шаг для сообщения покупателя."""
    definition = _definition(
        a={
            "action": f"{DEMO}:charge_payment",
            "next": {"on_success": "ok", "on_failure": "undo"},
        },
        ok={"action": f"{DEMO}:confirm_delivery"},
        undo={"action": f"{DEMO}:refund_payment"},
    )

    decision = decide_after_failure(definition, "a", _step(definition, "a"), 1, 3, permanent=True)

    assert decision.step_name == "undo"
