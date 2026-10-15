"""Уборка журнала у завершённых и архивных процессов (ADR-0019).

Журнал растёт бесконечно: каждый процесс оставляет после себя события, и они
никуда не исчезают. Разбор состояния выводится из журнала (§3.1), поэтому
удалять события живого процесса нельзя — и нельзя бездумно: интерфейс умеет
сверять разбор с колонкой и объявляет расхождение дефектом. Для убранного
журнала такое расхождение выдуманное, поэтому уборка ставит пометку
`journal_pruned_at`, а интерфейс её читает.

Уборка идёт порциями и отдельной командой, а не внутри цикла воркера: цикл
воркера живёт инвариантами одного инстанса (§3.3), и архивная уборка в него не
влезет без отдельного решения.

Запуск: `python -m scripts.prune` (или `make prune`).
"""

from __future__ import annotations

import argparse
import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import Select, delete, func, select, text, update

from app.core.db import dispose_engine, session_scope
from app.models.enums import InstanceStatus
from app.models.workflow import (
    CompensationLog,
    ProcessedOperation,
    StepRun,
    Timer,
    WorkflowEvent,
    WorkflowInstance,
)

#: Состояния, журнал которых убирается.
#:
#: `PAUSED` ждёт решения человека, `COMPENSATION_FAILED` — повтора отката.
#: Оба ждут человека, и убрать их историю значило бы спрятать незакрытый
#: инцидент (ADR-0017): процесс перестал бы быть разбираемым ровно тогда,
#: когда разбирать его придётся.
PRUNABLE: tuple[InstanceStatus, ...] = (
    InstanceStatus.COMPLETED,
    InstanceStatus.COMPENSATED,
    InstanceStatus.CANCELLED,
    InstanceStatus.FAILED,
)


def prune_query(cutoff: datetime) -> Select[uuid.UUID]:
    """Отбор под уборку: терминальный, архивный и старше отсечки.

    Отдельной функцией, а не внутри команды: правило — это утверждение о том,
    что именно уборка имеет право удалить, и его должен проверять тест.
    """
    return select(WorkflowInstance.id).where(
        WorkflowInstance.status.in_(PRUNABLE),
        WorkflowInstance.archived_at.is_not(None),
        WorkflowInstance.updated_at < cutoff,
        WorkflowInstance.journal_pruned_at.is_(None),
    )


async def collect(batch: int, days: int) -> tuple[list, datetime]:
    async with session_scope() as session:
        cutoff = datetime.now(UTC) - timedelta(days=days)
        ids = list(
            await session.scalars(
                prune_query(cutoff).order_by(WorkflowInstance.updated_at).limit(batch)
            )
        )
        return ids, cutoff


async def prune_batch(batch: int, days: int) -> int:
    """Убирает одну порцию и возвращает число убранных процессов."""
    ids, cutoff = await collect(batch, days)
    if not ids:
        return 0

    async with session_scope() as session:
        # Объявление уборки действует только внутри этой транзакции: `SET LOCAL`
        # снимается вместе с ней, и следующий случайный `DELETE` по журналу
        # снова упрётся в триггер. Настройка ставится после проверки — сама по
        # себе она ничего не разрешает, триггер смотрит ещё и на процесс.
        await session.execute(text("SET LOCAL app.journal_prune = 'on'"))

        # Порядок задан внешними ключами, а не удобством: `compensation_log`
        # ссылается на `step_runs`, и удаление прогонов первыми роняет уборку
        # на живом процессе с откатом. Найдено на стенде, а не в тестах: у
        # тестовых процессов не было строк компенсации.
        for model in (CompensationLog, StepRun, Timer, ProcessedOperation, WorkflowEvent):
            await session.execute(delete(model).where(model.instance_id.in_(ids)))
        await session.execute(
            update(WorkflowInstance)
            .where(WorkflowInstance.id.in_(ids))
            .values(journal_pruned_at=datetime.now(UTC))
        )
    return len(ids)


async def prune_all(batch: int, days: int) -> tuple[int, int]:
    """Убирает порциями до конца, возвращает (процессов, порций)."""
    total = 0
    rounds = 0
    while True:
        done = await prune_batch(batch, days)
        if done == 0:
            return total, rounds
        total += done
        rounds += 1


async def count_prunable(cutoff: datetime) -> int:
    """Сколько процессов попадёт под уборку."""
    async with session_scope() as session:
        return (
            await session.scalar(select(func.count()).select_from(prune_query(cutoff).subquery()))
        ) or 0


async def count_pruned() -> int:
    """Сколько журналов убрано ранее."""
    async with session_scope() as session:
        return (
            await session.scalar(
                select(func.count())
                .select_from(WorkflowInstance)
                .where(WorkflowInstance.journal_pruned_at.is_not(None))
            )
        ) or 0


async def main_async(days: int, batch: int, dry_run: bool) -> int:
    pending = await count_prunable(datetime.now(UTC) - timedelta(days=days))
    already = await count_pruned()

    print(f"под уборку попадает процессов: {pending or 0}")
    print(f"журнал убран у процессов ранее: {already or 0}")

    if dry_run:
        # Сухой прогон — обычный режим: уборка необратима, и человек вправе
        # сначала увидеть цифру.
        print("сухой прогон: ничего не удалено")
        return 0

    if not pending:
        print("убирать нечего")
        return 0

    total, rounds = await prune_all(batch, days)
    print(f"убрано журналов: {total} за {rounds} порций")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Уборка журнала старых архивных процессов")
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="сколько дней должен быть старше процесс (по умолчанию 30)",
    )
    parser.add_argument(
        "--batch",
        type=int,
        default=200,
        help="сколько процессов убирать за раз (по умолчанию 200)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="действительно удалить; без этого флага — только показать, сколько",
    )
    args = parser.parse_args()

    async def run() -> int:
        try:
            return await main_async(args.days, args.batch, not args.apply)
        finally:
            await dispose_engine()

    return asyncio.run(run())


if __name__ == "__main__":
    raise SystemExit(main())
