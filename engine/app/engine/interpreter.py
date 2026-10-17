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
    definition: WorkflowDefinitionSchema,
    step_name: str,
    step: Step,
    iterations: int = 1,
) -> StepDecision:
    """Переход после успешного шага.

    `iterations` — сколько раз шаг завершился, включая текущий. Учитывается
    только для шага с `loop: true`: именно ему ограничение числа проходов и
    объявляется валидатором.
    """
    if isinstance(step, ForkStep):
        return StepDecision(step_name=None, kind="fork", branches=dict(step.fork))

    exhausted = _loop_exhausted(step, iterations)
    if exhausted is not None:
        return StepDecision(step_name=None, kind="stop", reason=exhausted)

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


def _loop_exhausted(step: Step, iterations: int) -> str | None:
    """Причина исчерпания цикла, либо `None`, если проходы ещё остались.

    Проверяется именно достижение предела, а не его превышение: при
    `max_iterations: 3` шаг выполняется ровно три раза, и на третьем цикл
    заканчивается.
    """
    if not step.loop or step.max_iterations is None:
        return None
    if iterations < step.max_iterations:
        return None
    return f"исчерпан предел цикла: {iterations} из {step.max_iterations} проходов"


def decide_after_failure(
    definition: WorkflowDefinitionSchema,
    step_name: str,
    step: Step,
    attempt: int,
    max_attempts: int,
    permanent: bool = False,
) -> StepDecision:
    """Переход после неудачного шага.

    Исчерпание попыток ведёт в компенсацию, а не в остановку: выполненные шаги
    надо откатить, иначе процесс останется в промежуточном состоянии.
    """
    target = step.next
    on_failure = target.get("on_failure") if isinstance(target, dict) else None

    if on_failure is not None:
        return StepDecision(step_name=on_failure, kind="run")

    if permanent:
        # Отказ по существу дела: товар не появится, и ещё несколько попыток
        # только отложат откат уже выполненного шага.
        return StepDecision(
            step_name=None, kind="stop", reason="постоянная ошибка, повтор не поможет"
        )

    if attempt < max_attempts:
        return StepDecision(step_name=step_name, kind="run", reason="повтор шага")

    return StepDecision(step_name=None, kind="stop", reason="попытки исчерпаны, нужна компенсация")


def attempts_for(step: Step, fallback_max: int) -> int:
    """Сколько попыток разрешено шагу."""
    return _default_retry(step, fallback_max).max_attempts


def backoff_for(step: Step, attempt: int, fallback_max: int) -> float:
    """Задержка перед очередной попыткой с нарастанием.

    Рост задержки обязателен: немедленные повторы при недоступном сервисе
    только добивают его, не оставляя времени на восстановление.

    Рост ограничен потолком политики: без него степень растёт до величин,
    которые переводят ожидание за пределы срока службы процесса.
    """
    policy = _default_retry(step, fallback_max)
    delay = policy.backoff_seconds * (policy.backoff_multiplier ** max(attempt - 1, 0))
    return min(delay, policy.max_backoff_seconds)


def waits_for_person(step: Step) -> bool:
    """Шаг требует решения человека перед своим началом.

    Отдельная проверка, а не сравнение с `wait_before_seconds`: у таймера есть
    срок, у решения человека — нет, и смешивать их в одном числе значило бы
    терять различие.
    """
    return step.wait_for == "manual"


def pause_deadline(step: Step) -> float | None:
    """Срок ожидания человека, если он задан (ADR-0016).

    `None` — срока нет, и пауза живёт, пока не придёт команда. Это поведение
    по умолчанию: срок выдумывать нельзя, он меняет смысл паузы.
    """
    return step.pause_timeout_seconds


def wait_before(step: Step) -> float:
    """Задержка перед шагом. Ожидание — durable-объект, не sleep в памяти."""
    return step.wait_before_seconds


# Параллельные ветки описаны в схеме, но не исполняются: интерпретатор не
# запускает ветви fork и не ведёт счётчик ожидающих join. Валидатор отвергает
# такие определения при сохранении, поэтому до исполнения они не доходят.
# Цена решения зафиксирована в ADR-0008: честный отказ вместо объявленной
# поддержки, которой нет. Границы применимости — в `docs/DEPLOYMENT-ACT.md`.
# Циклы, в отличие от веток, исполняются: см. `_loop_exhausted`.
