"""Saga: компенсации выполненных шагов в обратном порядке.

Порядок компенсации зеркален порядку выполнения — зависимости между шагами
обратимы только так. Компенсируется лишь то, что действительно выполнено:
факт берётся из журнала, а не из структуры схемы. Компенсация необратима и
идемпотентна: её нельзя компенсировать компенсацией, а повтор после сбоя
обязан быть безопасным.

Провал компенсации — не тихий сбой, а состояние `COMPENSATION_FAILED`,
требующее ручного разбора.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.idempotency import OperationKey
from app.engine.journal import append_event
from app.models.enums import CompensationStatus, EventType, StepRunStatus
from app.models.workflow import CompensationLog, StepRun

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CompensationItem:
    """Шаг к компенсации."""

    step_run_id: uuid.UUID
    step_name: str
    compensation_ref: str
    attempts_made: int


@dataclass(frozen=True, slots=True)
class CompensationOutcome:
    """Результат попытки компенсации."""

    step_name: str
    succeeded: bool
    attempts_used: int
    error: str | None = None


async def completed_steps(db_session: AsyncSession, instance_id: uuid.UUID) -> list[str]:
    """Шаги, фактически выполненные, в порядке завершения."""
    rows = await db_session.scalars(
        select(StepRun.step_name)
        .where(
            StepRun.instance_id == instance_id,
            StepRun.status == StepRunStatus.COMPLETED,
            StepRun.compensated.is_(False),
        )
        .order_by(StepRun.finished_at.desc())
    )
    return list(rows)


async def find_step_run(
    db_session: AsyncSession, instance_id: uuid.UUID, step_name: str
) -> StepRun | None:
    """Последнее успешное выполнение шага.

    Поиск по факту выполнения, а не по предположению: если шага нет, компенсировать
    нечего, и это не ошибка.
    """
    return await db_session.scalar(
        select(StepRun)
        .where(
            StepRun.instance_id == instance_id,
            StepRun.step_name == step_name,
            StepRun.status == StepRunStatus.COMPLETED,
        )
        .order_by(StepRun.finished_at.desc())
        .limit(1)
    )


async def attempts_made(db_session: AsyncSession, step_run_id: uuid.UUID) -> int:
    """Сколько попыток компенсации по шагу уже было.

    Считается по журналу компенсаций, а не по памяти воркера: после его
    перезапуска счётчик обязан сохраниться.
    """
    from sqlalchemy import func

    return (
        await db_session.scalar(
            select(func.count())
            .select_from(CompensationLog)
            .where(CompensationLog.step_run_id == step_run_id)
        )
    ) or 0


async def plan(
    db_session: AsyncSession,
    instance_id: uuid.UUID,
    compensation_refs: dict[str, str | None],
    executed_steps: list[str] | None = None,
) -> list[CompensationItem]:
    """Строит план компенсации в обратном порядке выполнения.

    Порядок берётся из `replay` и передаётся явно: план и список выполненных
    шагов обязаны считаться по одному источнику (§3.1). Порядок из журнала при
    шагах из `step_runs` выродил бы отмену в «нечего компенсировать», хотя
    резерв уже занят.

    Шаги без компенсации в план не попадают — для них пишется
    `COMPENSATION_SKIPPED`. Уже компенсированные тоже: журнал хранит их
    навсегда, и второй проход отменил бы их повторно под новым ключом
    идемпотентности.
    """
    if executed_steps is None:
        # `completed_steps` уже развёрнут: сортировка по времени завершения
        # по убыванию. Журнал же даёт прямой порядок выполнения, и разворот
        # применяется к нему. Разворот ровно один, в любом из путей.
        order = await completed_steps(db_session, instance_id)
    else:
        order = list(reversed(executed_steps))

    items: list[CompensationItem] = []
    for step_name in order:
        compensation_ref = compensation_refs.get(step_name)
        if not compensation_ref:
            continue
        step_run = await find_step_run(db_session, instance_id, step_name)
        if step_run is not None and step_run.compensated:
            # Шаг откатан и лежит в `state.completed_steps`: план строим по
            # порядку из журнала, и повторный проход откатывал бы его снова —
            # уже под новым ключом идемпотентности.
            continue
        if step_run is None:
            # Шаг выполнен по журналу, но прогона нет: данных о нём нет, и
            # компенсировать нечего. Молча пропустить опаснее, чем упасть,
            # но это рассинхронизация, а не штатный путь.
            logger.warning(
                "compensation.no_step_run",
                extra={"step": step_name, "instance_id": str(instance_id)},
            )
            continue
        items.append(
            CompensationItem(
                step_run_id=step_run.id,
                step_name=step_name,
                compensation_ref=compensation_ref,
                attempts_made=await attempts_made(db_session, step_run.id),
            )
        )
    return items


async def pending_compensations(
    db_session: AsyncSession, instance_id: uuid.UUID
) -> list[CompensationLog]:
    """Незакрытые компенсации инстанса — для ручного разбора после сбоя."""
    rows = await db_session.scalars(
        select(CompensationLog)
        .where(
            CompensationLog.instance_id == instance_id,
            CompensationLog.status == CompensationStatus.FAILED,
        )
        .order_by(CompensationLog.started_at)
    )
    return list(rows)


async def start_attempt(
    db_session: AsyncSession,
    instance_id: uuid.UUID,
    item: CompensationItem,
    attempt: int,
) -> CompensationLog:
    """Открывает попытку компенсации и пишет событие журнала.

    Событие и запись лога — в одной транзакции: попытка без события была бы
    невидима при восстановлении.
    """
    log = CompensationLog(
        instance_id=instance_id,
        step_run_id=item.step_run_id,
        compensation_step=item.compensation_ref,
        attempt=attempt,
        status=CompensationStatus.RUNNING,
        started_at=datetime.now(UTC),
    )
    db_session.add(log)
    await db_session.flush()

    await append_event(
        db_session,
        instance_id,
        EventType.COMPENSATION_STARTED,
        {"for_step": item.step_name, "compensation": item.compensation_ref, "attempt": attempt},
    )
    return log


async def finish_attempt(
    db_session: AsyncSession,
    instance_id: uuid.UUID,
    log: CompensationLog,
    item: CompensationItem,
    succeeded: bool,
    error: str | None = None,
) -> CompensationOutcome:
    """Закрывает попытку и пишет соответствующее событие."""
    log.status = CompensationStatus.COMPLETED if succeeded else CompensationStatus.FAILED
    log.error = error
    log.finished_at = datetime.now(UTC)

    if succeeded:
        step_run = await db_session.get(StepRun, item.step_run_id)
        if step_run is not None:
            step_run.compensated = True
        await append_event(
            db_session,
            instance_id,
            EventType.COMPENSATION_COMPLETED,
            {"for_step": item.step_name, "attempt": log.attempt},
        )
    else:
        await append_event(
            db_session,
            instance_id,
            EventType.COMPENSATION_FAILED,
            {"for_step": item.step_name, "attempt": log.attempt, "error": error},
        )

    return CompensationOutcome(
        step_name=item.step_name,
        succeeded=succeeded,
        attempts_used=log.attempt,
        error=error,
    )


def operation_key(instance_id: uuid.UUID, item: CompensationItem) -> OperationKey:
    """Ключ идемпотентности для компенсации.

    Компенсация необратима, но повтор после сбоя обязан быть безопасным, и
    повтор обязан узнать свою отметку. Поэтому ключ строится по шагу, а не по
    номеру попытки: попытки — это попытки, а операция одна. С ключом, растущим
    вместе с попыткой, падение воркера между эффектом и отметкой привело бы к
    повторному возврату средств.
    """
    return OperationKey(
        instance_id=instance_id, step_name=f"compensate:{item.step_name}", attempt=0
    )


async def failed_count(db_session: AsyncSession, instance_id: uuid.UUID) -> int:
    """Сколько компенсаций провалилось — признак ручного разбора."""
    from sqlalchemy import func

    return (
        await db_session.scalar(
            select(func.count())
            .select_from(CompensationLog)
            .where(
                CompensationLog.instance_id == instance_id,
                CompensationLog.status == CompensationStatus.FAILED,
            )
        )
    ) or 0
