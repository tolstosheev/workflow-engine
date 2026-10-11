"""Воркер: цикл подбора инстансов и их исполнения.

Воркер ничего не помнит между шагами. Всё состояние — в журнале, поэтому
перезапуск воркера в любой момент не приводит ни к потере, ни к дублированию
работы: подхваченный инстанс восстанавливается replay'ом.

Цикл состоит из трёх шагов:

1. подбор инстанса (`claim_next`) — он же захват аренды;
2. восстановление состояния по журналу и исполнение одного шага;
3. освобождение аренды, если инстанс не требует немедленного продолжения.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import socket
import uuid
from dataclasses import dataclass

from prometheus_client import multiprocess
from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import dispose_engine, session_scope
from app.core.logging import configure_logging
from app.engine import executor, interpreter, lease, saga, scheduler
from app.engine.journal import read_events, replay
from app.metrics import IS_MULTIPROCESS, LEASE_ACQUIRED
from app.models.enums import TERMINAL_STATUSES, EventType, InstanceStatus
from app.models.workflow import CompensationLog, WorkflowDefinition, WorkflowInstance
from app.schemas.definition import WorkflowDefinitionSchema

logger = logging.getLogger(__name__)

STATUS_EVENTS = {
    InstanceStatus.PENDING: EventType.INSTANCE_STARTED,
    InstanceStatus.RUNNING: EventType.INSTANCE_STARTED,
    InstanceStatus.WAITING: EventType.INSTANCE_STARTED,
    InstanceStatus.COMPENSATING: EventType.INSTANCE_STARTED,
}


@dataclass(frozen=True, slots=True)
class CycleResult:
    """Итог одного цикла — для логов и метрик."""

    handled: bool
    step_executed: bool
    terminal: bool
    lease_lost: bool
    # Идентификатор инстанса: без него в логе невозможно сказать, какой инстанс
    # цикл оборвал и в поведения аварии не показан места.
    instance_id: uuid.UUID | None = None


def _worker_id() -> str:
    # Идентификатор включает имя узла: по нему видно, какой из воркеров подхватил.
    # docker-compose сейчас держит инстанс.
    return f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"


def _install_stop_handlers(stop_event: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        # add_signal_handler недоступен на Windows и вне главного потока:
        # без него SIGTERM завершит процесс мимо finally, не закрыв пул.
        with contextlib.suppress(NotImplementedError, AttributeError, ValueError):
            loop.add_signal_handler(sig, stop_event.set)


async def _load_definition(
    definition_id: uuid.UUID, cache: dict[uuid.UUID, WorkflowDefinitionSchema]
) -> WorkflowDefinitionSchema | None:
    """Читает определение, кэшируя разобранную схему.

    Кэш только по id: версия дефинишена неизменяема, а инстанс всегда ссылается
    на конкретную версию.
    """
    if definition_id in cache:
        return cache[definition_id]

    async with session_scope() as session:
        row = await session.get(WorkflowDefinition, definition_id)
        if row is None:
            return None
        parsed = WorkflowDefinitionSchema.model_validate(row.definition)

    cache[definition_id] = parsed
    return parsed


async def _compensate(
    instance_id: uuid.UUID,
    definition: WorkflowDefinitionSchema,
    worker_id: str,
) -> bool:
    """Выполняет откат инстанса. `True` — откат завершён.

    Откат обязан быть виден в интерфейсе: провал компенсации переводит
    инстанс в `COMPENSATION_FAILED`, а не молча завершает процесс.
    """
    compensation_refs = {name: step.compensation for name, step in definition.steps.items()}

    async with session_scope() as session:
        instance_row = await session.get(WorkflowInstance, instance_id, with_for_update=True)
        if instance_row is None:  # pragma: no cover - гонка с удалением
            return False

        # Порядок выполнения и переменные берутся из replay'а журнала, а не из
        # колонок инстанса (§3.1). Колонка отстаёт: шаг, завершившийся в текущей
        # итерации, в ней ещё не учтён, и план отката вырвался бы в пустой —
        # при том что резерв на складе уже занят.
        state = replay(await read_events(session, instance_id))
        instance_input = dict(instance_row.input or {})
        variables = dict(state.variables)

        items = await saga.plan(
            session, instance_row.id, compensation_refs, executed_steps=state.completed_steps
        )
        if not items:
            # Откатывать нечего. Отмена при этом всё равно завершилась, но
            # называть её `COMPENSATED` было бы враньём: компенсаций не было.
            # Итог утечённое состояние зависит от последнего события:
            # пользователь запросил отмену — инстанс отменён,
            # а упавший шаг — отказан — отказан в себя успешно не был.
            empty_status = (
                InstanceStatus.CANCELLED if state.cancel_requested else InstanceStatus.FAILED
            )
            await executor.finish(session, instance_row, empty_status)
            return True

        instance_row.status = InstanceStatus.COMPENSATING
        await session.commit()

    for item in items:
        async with session_scope() as session:
            log = await saga.start_attempt(
                session, instance_id, item, attempt=item.attempts_made + 1
            )
            await session.commit()

        succeeded, error = await _invoke_compensation(
            instance_id,
            item,
            attempt=item.attempts_made + 1,
            definition=definition,
            instance_input=instance_input,
            variables=variables,
        )

        async with session_scope() as session:
            log_row = await session.get(CompensationLog, log.id, with_for_update=True)
            if log_row is None:  # pragma: no cover - гонка с удалением
                continue
            await saga.finish_attempt(session, instance_id, log_row, item, succeeded, error)
            await session.commit()

        if not succeeded:
            async with session_scope() as session:
                instance_row = await session.get(WorkflowInstance, instance_id)
                if instance_row is not None:
                    instance_row.status = InstanceStatus.COMPENSATION_FAILED
                    await session.commit()
            return False

    async with session_scope() as session:
        instance_row = await session.get(WorkflowInstance, instance_id)
        if instance_row is not None:
            await executor.finish(session, instance_row, InstanceStatus.COMPENSATED)
    return True


async def _invoke_compensation(
    instance_id: uuid.UUID,
    item: saga.CompensationItem,
    attempt: int,
    definition: WorkflowDefinitionSchema,
    instance_input: dict,
    variables: dict,
) -> tuple[bool, str | None]:
    """Вызывает обработчик компенсации вне транзакции БД.

    Поддерживаются sync и async обработчики — так же, как у обычных шагов.

    Ключ идемпотентности проверяется **до** вызова: падение воркера между
    успешной компенсацией и её фиксацией привело бы к повторному возврату
    средств (§3.4). Обратный порядок не защищает ничего — к моменту проверки
    дубль уже применён.
    """
    import inspect

    from app.core.db import session_scope
    from app.engine import idempotency
    from app.engine.executor import build_compensation_context
    from app.handlers.registry import resolve_handler

    key = saga.operation_key(instance_id, item, attempt)

    async with session_scope() as session:
        if await idempotency.already_applied(session, key):
            # Повтор после падения воркера: эффект уже применён.
            logger.info(
                "compensation.skipped_already_applied",
                extra={"step": item.step_name, "instance_id": str(instance_id)},
            )
            return True, None

    try:
        handler = resolve_handler(item.compensation_ref)
        # Контекст строится так же, как для обычного шага. Раньше компенсация
        # получала None и знать не могла, что отменяет, — освободить резерв
        # по идентификатору инстанса ей было нечем.
        context = await build_compensation_context(
            definition,
            variables,
            instance_input,
            instance_id,
            item.step_name,
            attempt,
        )
        pending = handler(context)
        if inspect.isawaitable(pending):
            await pending
    except Exception as exc:
        logger.warning(
            "compensation.failed",
            extra={
                "step": item.step_name,
                "error": str(exc),
                "instance_id": str(instance_id),
            },
        )
        return False, str(exc)

    async with session_scope() as session:
        await idempotency.mark_applied(session, key)
    return True, None


async def _fire_due_timers() -> int:
    """Будит инстансы, чьи таймеры сработали.

    Отдельная транзакция обязательна: `fire_due` берёт строку инстанса под
    блокировкой, а `claim_next` выбирает кандидатов через `FOR UPDATE SKIP
    LOCKED` — в одной транзакции он пропустил бы строку, которую только что
    обновил, и проснувшийся инстанс ждал бы ещё одного цикла.

    Отметка и событие внутри `fire_due` идут вместе: падение воркера между ними
    оставила бы инстанс спящим навсегда, хотя таймер уже сгорел.
    """
    async with session_scope() as session:
        fired = 0
        for timer in await scheduler.claim_due(session):
            if await scheduler.fire_due(session, timer):
                fired += 1
        return fired


async def run_cycle(
    worker_id: str, cache: dict[uuid.UUID, WorkflowDefinitionSchema]
) -> CycleResult:
    """Один цикл: подбор инстанса и исполнение одного шага.

    Разбуждение спящих инстансов идёт до подбора: иначе ожидание просыпалось бы
    только в те циклы, когда очередь пуста.
    """
    await _fire_due_timers()

    async with session_scope() as session:
        claimed = await lease.claim_next(session, worker_id)
        if claimed is None:
            return CycleResult(handled=False, step_executed=False, terminal=False, lease_lost=False)
        # Аренда удачена этого шага: любой успешный захват воркера, а не этот
        # скалькой цикл вообще. Метрика считает, сколько раз аренд был десятько всегда нечего стоало недельзя.
        LEASE_ACQUIRED.inc()

        instance_row = await session.get(
            WorkflowInstance, claimed.instance_id, with_for_update=True
        )
        if instance_row is None:  # pragma: no cover - гонка с удалением
            return CycleResult(handled=False, step_executed=False, terminal=False, lease_lost=False)

        state = replay(await read_events(session, instance_row.id))
        instance_row.status = state.status
        definition = await _load_definition(instance_row.definition_id, cache)
        if definition is None:  # pragma: no cover - определение удалено
            instance_row.status = InstanceStatus.FAILED
            await session.commit()
            return CycleResult(handled=True, step_executed=False, terminal=True, lease_lost=False)

        await session.commit()

    # Отмена и провал шага приводят в одну точку: выполненные шаги надо
    # вернуть. Различаются они только тем, как процесс сюда попал.
    if state.status in (InstanceStatus.COMPENSATING, InstanceStatus.CANCELLING):
        done = await _compensate(claimed.instance_id, definition, worker_id)
        async with session_scope() as session:
            await lease.release(session, claimed.instance_id, worker_id)
        return CycleResult(handled=True, step_executed=False, terminal=done, lease_lost=False)

    if state.is_terminal or state.current_step is None:
        async with session_scope() as session:
            instance_row = await session.get(WorkflowInstance, claimed.instance_id)
            if instance_row is not None:
                # Отсутствие готового шага при нетерминальном статусе означает,
                # что граф дошёл до конца, а не что откат нужен: без шага нечего
                # компенсировать.
                final_status = (
                    state.status if state.status in TERMINAL_STATUSES else InstanceStatus.COMPLETED
                )
                await executor.finish(session, instance_row, final_status)
            await lease.release(session, claimed.instance_id, worker_id)
        return CycleResult(handled=True, step_executed=False, terminal=True, lease_lost=False)

    # Ожидание с таймером не требует исполнения: таймер сам разбудит инстанс.
    if state.status is InstanceStatus.WAITING:
        async with session_scope() as session:
            await lease.release(session, claimed.instance_id, worker_id)
        return CycleResult(handled=True, step_executed=False, terminal=False, lease_lost=False)
    # Ожидание, объявленное в дефинишене, планируется здесь, а не внутри шага:
    # это позволяет отдать транзакцию до внешнего вызова (§3.2) и сделать паузу
    # переживающей рестарт — таймер лежит в БД, а не в памяти процесса.
    step = definition.steps[state.current_step]
    delay = interpreter.wait_before(step)
    if delay > 0:
        async with session_scope() as session:
            if not await scheduler.planned_for(session, claimed.instance_id, state.current_step):
                await scheduler.schedule(session, claimed.instance_id, state.current_step, delay)
                instance_row = await session.get(WorkflowInstance, claimed.instance_id)
                if instance_row is not None:
                    instance_row.status = InstanceStatus.WAITING
                await lease.release(session, claimed.instance_id, worker_id)
                return CycleResult(
                    handled=True, step_executed=False, terminal=False, lease_lost=False
                )
        # Ожидание уже отбыло: таймер сгорел, и шаг пора исполнять. Без явного
        # спуска сюда шаг ждал бы нового таймера, которого никто не планирует.

    attempt = await _next_attempt(claimed.instance_id, state.current_step)
    try:
        async with session_scope() as session:
            instance_row = await session.get(WorkflowInstance, claimed.instance_id)
            if instance_row is None:  # pragma: no cover - гонка с удалением
                return CycleResult(
                    handled=True, step_executed=False, terminal=False, lease_lost=False
                )
            outcome = await executor.execute_step(
                session, instance_row, definition, state.current_step, attempt, worker_id
            )
            instance_row.error = None if outcome.success else "шаг не выполнен"
            await session.commit()
    except Exception:
        logger.exception(
            "step.failed_unexpected", extra={"step": state.current_step, "worker_id": worker_id}
        )
        async with session_scope() as session:
            await lease.release(session, claimed.instance_id, worker_id)
        return CycleResult(
            handled=True,
            step_executed=False,
            terminal=False,
            lease_lost=True,
            instance_id=claimed.instance_id,
        )

    terminal = not outcome.success
    # Аренда освобождается всегда: удерживать её после шага означало бы ждать
    # истечения TTL перед следующим шагом, а после неудачи — перед откатом.
    # Инстанс без аренды немедленно подхватывается следующим циклом.
    async with session_scope() as session:
        await lease.release(session, claimed.instance_id, worker_id)
    return CycleResult(handled=True, step_executed=True, terminal=terminal, lease_lost=False)


async def _next_attempt(instance_id: uuid.UUID, step_name: str) -> int:
    """Номер следующей попытки шага.

    Считается по `step_runs`, а не по памяти: после перезапуска воркера счётчик
    обязан продолжиться, иначе процесс зациклится на первой попытке.
    """
    from sqlalchemy import func

    from app.models.workflow import StepRun

    async with session_scope() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(StepRun)
            .where(StepRun.instance_id == instance_id, StepRun.step_name == step_name)
        )
    return int(count or 0) + 1


async def run_worker(stop_event: asyncio.Event | None = None) -> None:
    """Цикл воркера до получения сигнала остановки.

    `stop_event` внедряется, чтобы тест мог остановить цикл без отправки
    сигнала всему процессу.
    """
    settings = get_settings()
    worker_id = _worker_id()

    if stop_event is None:
        stop_event = asyncio.Event()
        _install_stop_handlers(stop_event)

    logger.info(
        "worker.started",
        extra={
            "worker_id": worker_id,
            "lease_ttl_seconds": settings.lease_ttl_seconds,
            "pid": os.getpid(),
        },
    )

    definition_cache: dict[uuid.UUID, WorkflowDefinitionSchema] = {}
    try:
        while not stop_event.is_set():
            steps_done = 0
            while steps_done < settings.worker_max_steps_per_cycle and not stop_event.is_set():
                result = await run_cycle(worker_id, definition_cache)
                if not result.handled:
                    break
                if result.lease_lost:
                    # Цикл оборваны недоведённо: шаг пал с неожиданной ошибкой.
                    # Без паузы воркер сразу забирает следующий инстанс и берёт его сразу.
                    logger.warning(
                        "cycle.aborted",
                        extra={
                            "worker_id": worker_id,
                            "instance_id": str(result.instance_id),
                        },
                    )
                    await asyncio.sleep(settings.worker_poll_interval_seconds)
                if result.step_executed:
                    steps_done += 1
            # wait_for вместо sleep: событие останавливает цикл немедленно,
            # а не по истечении интервала опроса.
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    stop_event.wait(), timeout=settings.worker_poll_interval_seconds
                )
    finally:
        await dispose_engine()
        # Файлы метрик умершего воркера остаются в общем
        # каталоге: иначе счётчики падавшего воркера продолжали бы давать
        # весь снимок выбранных инстансов (ADR-0012).
        if IS_MULTIPROCESS:
            multiprocess.mark_process_dead(os.getpid())
        logger.info("worker.stopped", extra={"worker_id": worker_id})


async def main() -> None:
    configure_logging()
    async with session_scope() as session:
        from sqlalchemy import text

        await session.execute(text("SELECT 1"))
    await run_worker()


if __name__ == "__main__":
    asyncio.run(main())
