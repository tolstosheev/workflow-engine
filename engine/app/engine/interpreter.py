"""Интерпретатор: переходы графа, ветвления, ожидания и циклы.

Интерпретатор отвечает на два вопроса: какой шаг исполнить сейчас и что
произойдёт после его результата. Никаких внешних вызовов и записей в БД —
это делает воркер, чтобы правила переходов оставались чистой логикой,
проверяемой без базы.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.schemas.definition import (
    ForkStep,
    JoinStep,
    RetryPolicy,
    Step,
    WorkflowDefinitionSchema,
)


@dataclass(frozen=True, slots=True)
class StepDecision:
    """Решение о следующем действии."""

    step_name: str | None
    kind: str  # run | wait | fork | join | complete | stop
    branches: dict[str, list[str]] | None = None
    wait_seconds: float = 0.0
    reason: str = ""


@dataclass(frozen=True, slots=True)
class StepOutcome:
    """Результат шага, влияющий на переход."""

    success: bool
    output: dict[str, Any]
    retryable: bool = True


def _default_retry(step: Step, fallback: int) -> RetryPolicy:
    return step.retry or RetryPolicy(max_attempts=fallback)


def decide_after_success(
    definition: WorkflowDefinitionSchema, step_name: str, step: Step
) -> StepDecision:
    """Переход после успешного шага."""
    if isinstance(step, ForkStep):
        return StepDecision(step_name=None, kind="fork", branches=dict(step.fork))

    target = step.next
    if isinstance(target, str):
        return StepDecision(step_name=target, kind="run")
    if isinstance(target, dict):
        on_success = target.get("on_success")
        if on_success is None:
            # Ветка сработала в никуда — процесс дошёл до конца этой ветки.
            return StepDecision(step_name=None, kind="complete", reason="ветвление без on_success")
        return StepDecision(step_name=on_success, kind="run")

    return StepDecision(step_name=None, kind="complete", reason="шаг не объявляет перехода")


def decide_after_failure(
    definition: WorkflowDefinitionSchema,
    step_name: str,
    step: Step,
    attempt: int,
    max_attempts: int,
) -> StepDecision:
    """Переход после неудачного шага.

    Исчерпание попыток ведёт в компенсацию, а не в остановку: выполненные шаги
    надо откатить, иначе процесс останется в промежуточном состоянии.
    """
    target = step.next
    on_failure = target.get("on_failure") if isinstance(target, dict) else None

    if on_failure is not None:
        return StepDecision(step_name=on_failure, kind="run")

    if attempt < max_attempts:
        return StepDecision(step_name=step_name, kind="run", reason="повтор шага")

    return StepDecision(step_name=None, kind="stop", reason="попытки исчерпаны, нужен откат")


def attempts_for(step: Step, fallback_max: int) -> int:
    """Сколько попыток разрешено шагу."""
    return _default_retry(step, fallback_max).max_attempts


def backoff_for(step: Step, attempt: int, fallback_max: int) -> float:
    """Задержка перед очередной попыткой с нарастанием.

    Рост задержки обязателен: немедленные повторы при недоступном сервисе
    только добивают его, не оставляя времени на восстановление.
    """
    policy = _default_retry(step, fallback_max)
    return policy.backoff_seconds * (policy.backoff_multiplier ** max(attempt - 1, 0))


def next_step(
    definition: WorkflowDefinitionSchema,
    ready_step: str,
    outcome: StepOutcome,
    attempt: int,
    max_attempts: int,
) -> StepDecision:
    """Главная точка входа: что делать после результата шага."""
    step = definition.steps.get(ready_step)
    if step is None:
        return StepDecision(step_name=None, kind="stop", reason="шаг отсутствует в определении")

    if outcome.success:
        return decide_after_success(definition, ready_step, step)
    return decide_after_failure(definition, ready_step, step, attempt, max_attempts)


def wait_before(step: Step) -> float:
    """Задержка перед шагом. Ожидание — durable-объект, не sleep в памяти."""
    return step.wait_before_seconds


def timeout_for(step: Step) -> float:
    return step.timeout_seconds


def pending_branches(step: Step) -> list[str]:
    """Все шаги параллельных ветвей, объявленные на fork-шаге."""
    return (
        [name for branch in step.fork.values() for name in branch]
        if isinstance(step, ForkStep)
        else []
    )


def is_join(step: Step) -> bool:
    return isinstance(step, JoinStep)


def join_waits_for(step: JoinStep) -> list[str]:
    """Шаги, которые join обязан дождаться."""
    return list(step.join)


def loop_budget(step: Step) -> int | None:
    """Предел итераций цикла. `None` — цикл не заявлен.

    Валидация дефинишена требует предела для каждого `loop: true`: без него
    процесс не завершится никогда.
    """
    return step.max_iterations if step.loop else None


def handlers_of(step: Step) -> tuple[str, str | None]:
    """Обработчик действия и компенсации."""
    return step.action, step.compensation
