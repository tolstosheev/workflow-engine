"""Исполнение шага: от вызова обработчика до записи результата.

Транзакция закрывается **до** внешнего вызова: между «шаг начался» и «шаг
закончился» состояние восстанавливается по журналу, а не по открытому
соединению с БД (§3.2 `AGENTS.md`). Порядок внутри шага: `STEP_STARTED` и
строка `step_runs`; закрытие транзакции и продление закрепления; вызов
обработчика с проверкой `processed_operations` перед эффектом; эффект и
отметка; `STEP_COMPLETED` и освобождение.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
import uuid
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import LeaseLostError
from app.engine import idempotency, interpreter, lease, scheduler
from app.engine.journal import RecordedEvent, append_event, read_events, replay
from app.handlers.registry import resolve_handler
from app.metrics import LEASE_LOST, STEP_DURATION
from app.models import chaos
from app.models.enums import EventType, InstanceStatus, StepRunStatus
from app.models.workflow import StepRun, WorkflowInstance
from app.schemas.definition import WorkflowDefinitionSchema

logger = logging.getLogger(__name__)


class StepFailed(Exception):
    """Шаг не выполнен. Повторяемость определяется политикой шага."""

    def __init__(self, message: str, retryable: bool = True) -> None:
        super().__init__(message)
        self.message = message
        self.retryable = retryable


class PermanentStepError(RuntimeError):
    """Ошибка, которую повтор не исправит.

    Недостаток товара или отказ внешнего сервиса по существу делают шаг
    бессмысленным: повторять его `max_attempts` раз — значит тратить время и
    время ожидания в задержках, не меняя результата. Повтор оправдан для сбоев
    связи и таймаутов, а не для отказа по правилам.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message
        self.retryable = False


def _declared_retryable(exc: BaseException) -> bool:
    """Читает объявленную обработчиком повторяемость ошибки.

    Отсутствие атрибута — обычная ошибка, и она повторяема: иначе любая
    опечатка в обработчике стала бы одноразовой.
    """
    declared = getattr(exc, "retryable", None)
    return True if declared is None else bool(declared)


async def load_state(db_session: AsyncSession, instance_id: uuid.UUID) -> tuple[RecordedEvent, ...]:
    """Читает журнал инстанса. Основа восстановления после падения."""
    return tuple(await read_events(db_session, instance_id))


async def build_context(
    definition: WorkflowDefinitionSchema,
    state_variables: dict[str, Any],
    instance_input: dict[str, Any],
    instance_id: uuid.UUID,
    step_name: str,
    attempt: int,
) -> Any:
    """Собирает контекст обработчика шага.

    Ключ операции — шаговый: повтор шага обязан узнать свой ключ.
    """
    operation_id = str(idempotency.make_key(instance_id, step_name, attempt))
    return await _build_context(
        definition, state_variables, instance_input, instance_id, step_name, attempt, operation_id
    )


async def build_compensation_context(
    definition: WorkflowDefinitionSchema,
    state_variables: dict[str, Any],
    instance_input: dict[str, Any],
    instance_id: uuid.UUID,
    step_name: str,
    attempt: int,
) -> Any:
    """Собирает контекст обработчика компенсации.

    Ключ операции компенсационный, а не шаговый: возврат средств должен узнавать
    свою отметку при повторе шага.

    Номер попытки в ключ не входит: компенсация — одна и та же операция, пока её
    не отменили, а растущий ключ при падении между эффектом и отметкой вернул бы
    средства второй раз (§3.4).
    """
    operation_id = str(idempotency.make_key(instance_id, f"compensate:{step_name}", attempt=0))
    return await _build_context(
        definition, state_variables, instance_input, instance_id, step_name, attempt, operation_id
    )


async def _build_context(
    definition: WorkflowDefinitionSchema,
    state_variables: dict[str, Any],
    instance_input: dict[str, Any],
    instance_id: uuid.UUID,
    step_name: str,
    attempt: int,
    operation_id: str,
) -> Any:
    """Общая часть построения контекста.

    Тип не уточняется здесь намеренно: контекст живёт в `demo.handlers`, и ядро
    не должно зависеть от модуля предметной области.
    """
    step = definition.steps.get(step_name)
    module_path = (step.action if step else "").partition(":")[0]
    if not module_path:  # pragma: no cover - компенсация для неизвестного шага
        return None
    module = __import__(module_path, fromlist=["StepContext"])
    context_class = getattr(module, "StepContext", None)
    if context_class is None:  # pragma: no cover - обработчик без контекста
        return None

    return context_class(
        instance_id=str(instance_id),
        step_name=step_name,
        attempt=attempt,
        operation_id=operation_id,
        input=instance_input,
        variables=state_variables,
    )


async def heartbeat_loop(instance_id: uuid.UUID, worker_id: str, stop: asyncio.Event) -> None:
    """Продлевает аренду во время долгой работы.

    Без этого шаг или откат длиннее TTL признаётся брошенным, и его начнёт
    другой воркер — то есть работа выполнится дважды.

    Продление продолжается до `stop` и молча заканчивается, когда аренда ушла
    к другому воркеру: решение принимает проверка перед записью результата,
    где оно учитывается ровно один раз.
    """
    settings = get_settings()
    from app.core.db import session_scope

    while not stop.is_set():
        try:
            async with session_scope() as session:
                renewed = await lease.heartbeat(session, instance_id, worker_id)
            if not renewed:
                return
        except Exception:
            # Потерянная аренда обнаружится при следующей записи; молчаливый
            # проглатывание здесь недопустимо, но и падать нельзя — цикл
            # обязан завершиться.
            logger.warning("heartbeat.failed", extra={"instance_id": str(instance_id)})
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=settings.heartbeat_interval_seconds)


@contextlib.asynccontextmanager
async def keep_lease_alive(instance_id: uuid.UUID, worker_id: str) -> AsyncIterator[None]:
    """Держит аренду живой, пока идёт внешний вызов.

    Обёртка нужна и шагу, и компенсации: обе работы идут вне транзакции и могут
    занять больше срока аренды. Продление обрывается на выходе из блока, а
    потерянная аренда обнаруживается проверкой перед записью результата.
    """
    stop = asyncio.Event()
    task = asyncio.create_task(heartbeat_loop(instance_id, worker_id, stop))
    try:
        yield
    finally:
        stop.set()
        with contextlib.suppress(Exception):
            await task


def _observe_duration(step_name: str, started_at: datetime, finished_at: datetime) -> None:
    """Записывает длительность завершённого шага.

    Длительность берётся из границ шага в `step_runs`, а не из времени
    внешнего вызова: между вызовом и фиксацией проходит запись события и
    коммит, и без них метрика занижала бы время работы.

    Границы передаются аргументами, а не читаются из строки прогона: после
    коммита её атрибуты протухают, и обращение к ним из асинхронного кода
    потребовало бы зелёного контекста, которого там нет.
    """
    elapsed = (finished_at - started_at).total_seconds()
    STEP_DURATION.labels(step=step_name).observe(max(0.0, elapsed))


async def execute_step(
    db_session: AsyncSession,
    instance: WorkflowInstance,
    definition: WorkflowDefinitionSchema,
    step_name: str,
    attempt: int,
    worker_id: str,
) -> interpreter.StepOutcome:
    """Исполняет один шаг и фиксирует результат в журнале."""
    step = definition.steps[step_name]
    # Идентификатор читается один раз и дальше используется как скаляр:
    # обработчик открывает собственную сессию, после чего атрибуты `instance`
    # протухают, и обращение к ним потребовало бы синхронной подгрузки в
    # асинхронном коде — `MissingGreenlet`.
    instance_id = instance.id
    events = await load_state(db_session, instance_id)
    state = replay(list(events))

    # Вход читается до внешнего вызова: дальше атрибуты инстанса протухают,
    # и их чтение потребовало бы подгрузки из асинхронного контекста.
    instance_input = dict(instance.input or {})

    # --- Транзакция 1: начало шага -------------------------------------------
    await append_event(
        db_session, instance_id, EventType.STEP_STARTED, {"step": step_name, "attempt": attempt}
    )
    step_started_at = datetime.now(UTC)
    step_run = StepRun(
        instance_id=instance_id,
        step_name=step_name,
        attempt=attempt,
        status=StepRunStatus.RUNNING,
        started_at=step_started_at,
    )
    db_session.add(step_run)
    instance.current_step = step_name
    await db_session.commit()

    # --- Транзакция закрыта. Внешний вызов ниже --------------------------
    try:
        async with keep_lease_alive(instance_id, worker_id):
            outcome = await _invoke(
                db_session,
                definition,
                state.variables,
                instance_input,
                instance_id,
                step_name,
                attempt,
                step.timeout_seconds,
            )
    except Exception as exc:
        outcome = interpreter.StepOutcome(
            success=False,
            output={},
            retryable=isinstance(exc, LeaseLostError) is False and _declared_retryable(exc),
        )
        error_message = str(exc)

    # --- Аренда могла уйти другому воркеру, пока шаг выполнялся --------------
    # Записывать результат тогда нельзя: шаг уже исполняет другой воркер, и
    # второй STEP_COMPLETED разошёлся бы с его журналом и счётчиком цикла
    # (§3.3). Продление аренды здесь ещё и проверяет владение ею.
    if not await lease.heartbeat(db_session, instance_id, worker_id):
        LEASE_LOST.inc()
        await db_session.rollback()
        raise LeaseLostError(
            f"закрепление процесса {instance_id} утрачено: его забрал другой исполнитель"
        )

    # --- Транзакция 2: результат шага ---------------------------------------
    if outcome.success:
        # Счётчик берётся из replay'а до записи текущего события, поэтому к
        # нему прибавляется единица: решение принимается для числа проходов,
        # включая этот.
        iterations = state.step_iterations.get(step_name, 0) + 1
        decision = interpreter.decide_after_success(definition, step_name, step, iterations)
        await append_event(
            db_session,
            instance_id,
            EventType.STEP_COMPLETED,
            {"step": step_name, "output": outcome.output, "next": decision.step_name},
        )
        if decision.kind == "stop":
            # Шаг успешен, но продолжать нельзя: счётчик цикла исчерпан.
            # Событие идёт после STEP_COMPLETED, иначе replay не знал бы, что
            # шаг выполнен, — а компенсировать его надо.
            await append_event(
                db_session,
                instance_id,
                EventType.LOOP_EXHAUSTED,
                {
                    "step": step_name,
                    "iterations": iterations,
                    "max_iterations": step.max_iterations,
                    "reason": decision.reason,
                },
            )
        step_run.status = StepRunStatus.COMPLETED
        step_run.output = outcome.output
        finished_at = datetime.now(UTC)
        step_run.finished_at = finished_at
        _observe_duration(step_name, step_started_at, finished_at)
        instance.variables = {**state.variables, **outcome.output}
        instance.current_step = decision.step_name
        await db_session.commit()
    else:
        max_attempts = interpreter.attempts_for(step, get_settings().step_max_attempts)
        decision = interpreter.decide_after_failure(
            definition, step_name, step, attempt, max_attempts, permanent=not outcome.retryable
        )
        if decision.step_name == step_name and attempt < max_attempts and outcome.retryable:
            # Повтор делается только для повторяемой ошибки: см. `PermanentStepError`.
            # Постоянный отказ (`retryable=False`) сразу уходит в `STEP_FAILED`.
            await append_event(
                db_session,
                instance_id,
                EventType.STEP_RETRY_SCHEDULED,
                {"step": step_name, "attempt": attempt + 1, "error": error_message},
            )
            step_run.status = StepRunStatus.FAILED
            step_run.error = error_message
            finished_at = datetime.now(UTC)
            step_run.finished_at = finished_at
            _observe_duration(step_name, step_started_at, finished_at)
            await db_session.commit()
            delay = interpreter.backoff_for(step, attempt, max_attempts)
            if delay:
                await scheduler.schedule(db_session, instance_id, step_name, delay)
            return interpreter.StepOutcome(success=False, output={}, retryable=outcome.retryable)

        await append_event(
            db_session,
            instance_id,
            EventType.STEP_FAILED,
            {
                "step": step_name,
                "attempt": attempt,
                "error": error_message,
                "terminal": decision.kind == "stop",
                # Решение интерпретатора едет в журнале: без него ветка
                # `on_failure` терялась, и процесс уходил в откат вместо
                # перехода по объявленной ветке.
                "next": decision.step_name,
            },
        )
        step_run.status = StepRunStatus.FAILED
        step_run.error = error_message
        finished_at = datetime.now(UTC)
        step_run.finished_at = finished_at
        _observe_duration(step_name, step_started_at, finished_at)
        # Колонка догоняет журнал: `claim_next` смотрит на неё, и без
        # обновления инстанс остался бы на упавшем шаге.
        if decision.step_name is not None:
            instance.current_step = decision.step_name
        await db_session.commit()

    return outcome


async def _apply_chaos(
    db_session: AsyncSession,
    instance_id: uuid.UUID,
    step_name: str,
    timeout_seconds: float,
) -> None:
    """Применяет правило инъекции отказа, если оно зарегистрировано.

    Проверка идёт после идемпотентности: уже применённый шаг повторно ломать
    бессмысленно, а пропущенный чекпоинт важнее демонстрации отказа.
    """
    rule = await chaos.find_rule(db_session, instance_id, step_name)
    if rule is None:
        return

    if rule.mode == "timeout":
        # Имитация зависшего сервиса: вызов не возвращается, и срабатывает
        # таймаут шага. Так проверяется, что lease продлевается во время
        # долгой работы.
        await asyncio.sleep(timeout_seconds + 1)

    raise StepFailed(rule.error, retryable=True)


async def _invoke(
    db_session: AsyncSession,
    definition: WorkflowDefinitionSchema,
    variables: dict[str, Any],
    instance_input: dict[str, Any],
    instance_id: uuid.UUID,
    step_name: str,
    attempt: int,
    timeout_seconds: float,
) -> interpreter.StepOutcome:
    """Вызывает обработчик с таймаутом и проверкой идемпотентности."""
    step = definition.steps[step_name]
    key = idempotency.make_key(instance_id, step_name, attempt)

    if await idempotency.already_applied(db_session, key):
        # Повтор после падения: эффект уже применён, второй вызов был бы дублем.
        logger.info("step.skipped_already_applied", extra={"step": step_name, "attempt": attempt})
        return interpreter.StepOutcome(success=True, output=dict(variables))

    await _apply_chaos(db_session, instance_id, step_name, timeout_seconds)

    handler: Callable[..., Any] = resolve_handler(step.action)
    context = await build_context(
        definition, variables, instance_input, instance_id, step_name, attempt
    )

    try:
        pending = handler(context)
    except Exception as exc:
        # Повторяемость ошибки сохраняется: обработчик вправе объявить отказ
        # постоянным, и обёртка не должна молча делать его повторяемым.
        raise StepFailed(str(exc), retryable=_declared_retryable(exc)) from exc

    if inspect.isawaitable(pending):
        # Таймаут применим только к awaitable: синхронный обработчик блокирует
        # event loop, и прервать его извне нельзя. Такие обработчики обязаны
        # быть короткими — это зафиксировано в docs/ENGINE.md §5.
        try:
            result = await asyncio.wait_for(pending, timeout=timeout_seconds)
        except TimeoutError as exc:
            raise StepFailed(
                f"шаг {step_name!r} не ответил за {timeout_seconds} с", retryable=True
            ) from exc
    else:
        result = pending

    output = dict(result) if isinstance(result, dict) else {"result": result}
    await idempotency.mark_applied(db_session, key)
    await db_session.commit()
    return interpreter.StepOutcome(success=True, output=output)


async def finish(
    db_session: AsyncSession, instance: WorkflowInstance, status: InstanceStatus
) -> None:
    """Переводит инстанс в терминальное состояние через событие журнала."""
    event_type = {
        InstanceStatus.COMPLETED: EventType.INSTANCE_COMPLETED,
        InstanceStatus.COMPENSATED: EventType.INSTANCE_COMPENSATED,
        InstanceStatus.CANCELLED: EventType.INSTANCE_CANCELLED,
        InstanceStatus.FAILED: EventType.INSTANCE_FAILED,
    }.get(status, EventType.INSTANCE_FAILED)

    await append_event(db_session, instance.id, event_type, {})
    instance.status = status
    instance.current_step = None
    await db_session.commit()


__all__ = [
    "StepFailed",
    "build_compensation_context",
    "build_context",
    "execute_step",
    "finish",
    "keep_lease_alive",
    "load_state",
]
