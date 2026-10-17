"""Исполнитель: цикл подбора процессов и их исполнения.

Между шагами исполнитель ничего не помнит: всё состояние в журнале, поэтому
перезапуск в любой момент не теряет и не дублирует работу — подхваченный
процесс восстанавливается replay'ом.

Цикл: подбор процесса (`claim_next`, он же захват закрепления), восстановление
состояния по журналу и исполнение одного шага, освобождение закрепления, если
продолжение не требуется немедленно.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import socket
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from prometheus_client import multiprocess
from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import dispose_engine, session_scope
from app.core.errors import LeaseLostError
from app.core.logging import configure_logging
from app.engine import deadline, executor, interpreter, lease, saga, scheduler
from app.engine.journal import append_event, read_events, replay
from app.metrics import IS_MULTIPROCESS, LEASE_ACQUIRED
from app.models import chaos
from app.models.enums import (
    PAUSE_DEADLINE,
    TERMINAL_STATUSES,
    EventType,
    InstanceStatus,
)
from app.models.workflow import CompensationLog, WorkflowDefinition, WorkflowInstance
from app.schemas.definition import WorkflowDefinitionSchema
from app.services import registry

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CycleResult:
    """Итог одного цикла — для логов и метрик."""

    handled: bool
    step_executed: bool
    terminal: bool
    lease_lost: bool
    # Идентификатор инстанса: без него в логе не сказать, какой процесс оборвал
    # цикл, и разбор аварии по журналу воркера был бы невозможен.
    instance_id: uuid.UUID | None = None


def _worker_id() -> str:
    # Идентификатор включает имя узла: по нему видно, какой воркер подхватил
    # инстанс, и записи разных процессов различаются в журнале.
    return f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"


def _install_stop_handlers(stop_event: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        # add_signal_handler недоступен на Windows и вне главного потока:
        # без него SIGTERM завершит процесс мимо finally, не закрыв пул.
        with contextlib.suppress(NotImplementedError, AttributeError, ValueError):
            loop.add_signal_handler(sig, stop_event.set)


async def _load_definition(
    definition_id: uuid.UUID, cache: dict[uuid.UUID, tuple[datetime, WorkflowDefinitionSchema]]
) -> WorkflowDefinitionSchema | None:
    """Читает определение, кэшируя разобранную схему.

    Кэш хранит вместе со схемой время последней правки: определение
    перезаписывается на месте, и кэш без проверки свежести держал бы прежний
    граф до перезапуска воркера — правка в редакторе не дошла бы до процесса,
    который её исполняет. Дешёвое чтение строки остаётся, дорогая проверка
    схемы выполняется только при изменении.
    """
    async with session_scope() as session:
        row = await session.get(WorkflowDefinition, definition_id)
        if row is None:
            return None
        cached = cache.get(definition_id)
        if cached is not None and cached[0] == row.updated_at:
            return cached[1]
        parsed = WorkflowDefinitionSchema.model_validate(row.definition)

    cache[definition_id] = (row.updated_at, parsed)
    return parsed


def _next_compensation_attempt_at(failed_attempts: int) -> datetime:
    """Когда брать следующую попытку отката.

    Задержка растёт по числу неудач и упирается в потолок: недоступный сервис
    не станет доступнее от того, что по нему бьют чаще. Первая пауза короткая —
    сбой мог быть разовым, и ждать пять минут ради этого незачем.
    """
    settings = get_settings()
    exponent = max(0, failed_attempts - 1)
    delay = min(
        settings.compensation_retry_base_seconds * (2**exponent),
        settings.compensation_retry_max_seconds,
    )
    return datetime.now(UTC) + timedelta(seconds=delay)


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
            # Итог зависит от последнего события журнала: отмену запросили —
            # инстанс отменён, шаг упал — отказ, потому что откатывать было нечего.
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

        # Откат идёт вне транзакции и занимает столько же времени, сколько
        # шаги: без продления аренда истекает, и второй воркер начинает тот же
        # откат, удвоив возврат средств (§3.3).
        async with executor.keep_lease_alive(instance_id, worker_id):
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
                    instance_row.compensation_not_before = _next_compensation_attempt_at(
                        await saga.failed_count(session, instance_id)
                    )
                    await session.commit()
            return False

    async with session_scope() as session:
        instance_row = await session.get(WorkflowInstance, instance_id)
        if instance_row is not None:
            # Пауза отката снимается вместе с успехом: процесс терминален, и
            # оставшееся поле только сбивало бы с толку при разборе.
            instance_row.compensation_not_before = None
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
    from app.engine.executor import StepFailed, build_compensation_context
    from app.handlers.registry import resolve_handler

    key = saga.operation_key(instance_id, item)

    async with session_scope() as session:
        if await idempotency.already_applied(session, key):
            # Повтор после падения воркера: эффект уже применён.
            logger.info(
                "compensation.skipped_already_applied",
                extra={"step": item.step_name, "instance_id": str(instance_id)},
            )
            return True, None

    try:
        # Проверка инъекции до обработчика отката: правило адресовано именно
        # ему, и прямой шаг оно не задевает (ADR-0015).
        async with session_scope() as session:
            rule = await chaos.find_rule_for_handler(session, instance_id, item.compensation_ref)
        if rule is not None:
            raise StepFailed(rule.error, retryable=False)

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
    """Будит процессы, чьи таймеры сработали.

    Отдельная транзакция обязательна: `fire_due` берёт строку процесса под
    блокировкой, а `claim_next` выбирает кандидатов через `FOR UPDATE SKIP LOCKED`
    — в одной транзакции он пропустил бы строку, которую только что обновил.
    """
    async with session_scope() as session:
        fired = 0
        for timer in await scheduler.claim_due(session):
            # Вид важнее таймера: срок паузы отменяет процесс, а ожидание шага
            # просто будит его. Обработчик выбирается здесь, чтобы у каждого
            # вида была ровно одна точка срабатывания.
            if timer.kind == PAUSE_DEADLINE:
                done = await deadline.expire_pause_deadline(session, timer)
            else:
                done = await scheduler.fire_due(session, timer)
            if done:
                fired += 1
        return fired


async def run_cycle(
    worker_id: str, cache: dict[uuid.UUID, tuple[datetime, WorkflowDefinitionSchema]]
) -> CycleResult:
    """Один шаг: разбуждение таймеров, захват одного инстанса и его обработка.

    Разбуждение спящих инстансов идёт до подбора: иначе ожидание просыпалось бы
    только в те циклы, когда очередь пуста.
    """
    await _fire_due_timers()

    async with session_scope() as session:
        claimed = await lease.claim_next(session, worker_id)
        if claimed is None:
            return CycleResult(handled=False, step_executed=False, terminal=False, lease_lost=False)
        # Аренда удалась этому шагу: метрика считает удачные захваты, а не
        # пустые циклы, в которых никто ничего не взял.
        LEASE_ACQUIRED.inc()

    return await _handle_claimed(claimed, worker_id, cache)


async def run_batch_cycle(
    worker_id: str, cache: dict[uuid.UUID, tuple[datetime, WorkflowDefinitionSchema]]
) -> CycleResult:
    """Пачка шагов за один проход по базе (ADR-0023).

    Захват, разбор журнала и проверка таймеров делаются один раз на пачку, а
    не на каждый шаг. Обработка идёт по очереди, и каждый процесс доводится до
    конца с освобождением закрепления: взятое «в запас» висело бы до истечения TTL.

    Возвращается итог по **последнему** обработанному процессу и сумме по шагам:
    цикл решает по нему, продолжать ли.
    """
    await _fire_due_timers()

    async with session_scope() as session:
        claimed_batch = await lease.claim_batch(
            session, worker_id, get_settings().worker_claim_batch
        )
        for _ in claimed_batch:
            # Метрика считает удачные захваты, а не пустые циклы.
            LEASE_ACQUIRED.inc()
    if not claimed_batch:
        return CycleResult(handled=False, step_executed=False, terminal=False, lease_lost=False)

    last = CycleResult(handled=False, step_executed=False, terminal=False, lease_lost=False)
    steps = 0
    lost = False
    for claimed in claimed_batch:
        # Провал одного не должен остановить пачку: иначе один испорченный
        # процесс блокировал бы очередь до конца цикла.
        result = await _handle_claimed(claimed, worker_id, cache)
        last = result
        steps += 1 if result.step_executed else 0
        lost = lost or result.lease_lost

    return CycleResult(
        handled=True,
        step_executed=steps > 0,
        terminal=last.terminal,
        lease_lost=lost,
        instance_id=last.instance_id,
    )


async def _handle_claimed(
    claimed: lease.ClaimedInstance,
    worker_id: str,
    cache: dict[uuid.UUID, tuple[datetime, WorkflowDefinitionSchema]],
) -> CycleResult:
    """Обрабатывает уже захваченный инстанс до конца: шаг, откат или завершение."""
    async with session_scope() as session:
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
    if state.status in (
        InstanceStatus.COMPENSATING,
        InstanceStatus.CANCELLING,
        # Провал компенсации не конец процесса: он уже в очереди на повтор
        # по §3.5. Раньше инстанс падал в обход отката и доводил упавший шаг
        # до конца, а брал его уже другой воркер.
        InstanceStatus.COMPENSATION_FAILED,
    ):
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

    # Пауза ждёт человека: шаг не исполняется, пока не придёт команда
    # возобновления (ADR-0014).
    step = definition.steps[state.current_step]
    if interpreter.waits_for_person(step) and state.resumed_step != state.current_step:
        async with session_scope() as session:
            await append_event(
                session,
                claimed.instance_id,
                EventType.INSTANCE_PAUSED,
                {"step": state.current_step},
            )
            instance_row = await session.get(WorkflowInstance, claimed.instance_id)
            if instance_row is not None:
                instance_row.status = InstanceStatus.PAUSED
            # Срок планируется здесь же, в той же транзакции, что и пауза:
            # событие без таймера оставило бы паузу без будильника, а таймер
            # без события не был бы виден в разборе.
            seconds = interpreter.pause_deadline(step)
            if seconds is not None and not await deadline.deadline_planned(
                session, claimed.instance_id, state.current_step
            ):
                await deadline.schedule_deadline(
                    session, claimed.instance_id, state.current_step, seconds
                )
            await lease.release(session, claimed.instance_id, worker_id)
        return CycleResult(handled=True, step_executed=False, terminal=False, lease_lost=False)

    # Ожидание с таймером не требует исполнения: таймер сам разбудит инстанс.
    if state.status is InstanceStatus.WAITING:
        async with session_scope() as session:
            await lease.release(session, claimed.instance_id, worker_id)
        return CycleResult(handled=True, step_executed=False, terminal=False, lease_lost=False)
    # Ожидание, объявленное в дефинишене, планируется здесь, а не внутри шага:
    # это позволяет отдать транзакцию до внешнего вызова (§3.2) и сделать паузу
    # переживающей рестарт — таймер лежит в БД, а не в памяти процесса.
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
    except LeaseLostError:
        # Аренда ушла другому воркеру во время шага: результат не записан, и
        # записывать его было бы уже нельзя (§3.3).
        logger.warning(
            "step.lease_lost",
            extra={"step": state.current_step, "worker_id": worker_id},
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

    # Реестр воркеров (ADR-0018). Без него простаивающий воркер не виден нигде:
    # аренды у него нет, и след в системе не остаётся.
    async with session_scope() as session:
        await registry.register(session, worker_id)

    definition_cache: dict[uuid.UUID, tuple[datetime, WorkflowDefinitionSchema]] = {}
    # Граница молчания та же, что и у читателя реестра: пока цикл не вернулся к
    # записи сердца, исполнитель должен подтвердить, что он жив. Замер на стенде:
    # под нагрузкой оба занятых исполнителя мигали «не отвечает» — система звала
    # мёртвым того, кто работает.
    silent_after = registry.stale_after(settings.worker_poll_interval_seconds).total_seconds()
    try:
        while not stop_event.is_set():
            steps_done = 0
            cycles = 0
            lease_lost = 0
            last_confirm = time.monotonic()
            while steps_done < settings.worker_max_steps_per_cycle and not stop_event.is_set():
                result = await run_batch_cycle(worker_id, definition_cache)
                if not result.handled:
                    break
                cycles += 1
                if time.monotonic() - last_confirm > silent_after / 2:
                    # Подтверждение без счётчиков: счётчики дописывает конец цикла,
                    # и прибавление здесь выдало бы шаги дважды.
                    async with session_scope() as session:
                        await registry.touch(session, worker_id)
                    last_confirm = time.monotonic()
                if result.lease_lost:
                    lease_lost += 1
                    # Цикл оборвался недоведённо: без паузы воркер сразу забрал бы
                    # следующий инстанс и упал бы на нём тем же самым.
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
            # Сердце продлевается раз за цикл опроса, а не за шаг: запись в БД
            # на каждом шаге съедала бы часть выигранного времени, а «воркер жив»
            # должно означать «воркер опрашивает базу», а не «часто исполняет шаги».
            async with session_scope() as session:
                await registry.heartbeat(
                    session, worker_id, cycles=cycles, steps=steps_done, lease_lost=lease_lost
                )
            # wait_for вместо sleep: событие останавливает цикл немедленно,
            # а не по истечении интервала опроса.
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    stop_event.wait(), timeout=settings.worker_poll_interval_seconds
                )
    finally:
        # Отметка об остановке не должна мешать выходу: если БД недоступна,
        # воркер всё равно должен завершиться, а строка просто останется stale.
        with contextlib.suppress(Exception):
            async with session_scope() as session:
                await registry.mark_stopped(session, worker_id)
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
