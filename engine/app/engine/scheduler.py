"""Durable-таймеры ожидания.

Ожидание — объект в БД, а не `asyncio.sleep`: иначе рестарт воркера терял бы
все паузы и процесс застревал бы в `WAITING` навсегда.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.journal import append_event
from app.models.enums import EventType
from app.models.workflow import Timer


def _now() -> datetime:
    return datetime.now(UTC)


async def schedule(
    db_session: AsyncSession,
    instance_id: uuid.UUID,
    step_name: str,
    delay_seconds: float,
    payload: dict | None = None,
) -> Timer:
    """Создаёт ожидание и пишет событие журнала.

    Таймер и событие — в одной транзакции: таймер без события не был бы виден
    в replay, и инстанс остался бы в `RUNNING` вместо `WAITING`.
    """
    due_at = _now() + timedelta(seconds=delay_seconds)
    timer = Timer(
        instance_id=instance_id, step_name=step_name, due_at=due_at, payload=payload or {}
    )
    db_session.add(timer)
    await db_session.flush()

    await append_event(
        db_session,
        instance_id,
        EventType.TIMER_SCHEDULED,
        {"step": step_name, "due_at": due_at.isoformat(), "timer_id": str(timer.id)},
    )
    return timer


async def claim_due(db_session: AsyncSession, batch_size: int = 10) -> list[Timer]:
    """Отмечает сработавшие таймеры и возвращает их.

    `SKIP LOCKED` позволяет нескольким воркерам обрабатывать разные таймеры
    одновременно, не конфликтуя за один и тот же.
    """
    now = _now()
    due = (
        await db_session.scalars(
            select(Timer.id)
            .where(Timer.fired.is_(False), Timer.due_at <= now)
            .order_by(Timer.due_at)
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
    ).all()

    claimed: list[Timer] = []
    for timer_id in due:
        result = await db_session.execute(
            update(Timer)
            .where(Timer.id == timer_id, Timer.fired.is_(False))
            .values(fired=True)
            .returning(Timer.id, Timer.instance_id, Timer.step_name)
        )
        row = result.first()
        if row is None:  # pragma: no cover - гонка с другим воркером
            continue
        claimed.append(Timer(id=row[0], instance_id=row[1], step_name=row[2], fired=True))
    await db_session.flush()
    return claimed


async def notify_fired(db_session: AsyncSession, timer: Timer) -> None:
    """Пишет `TIMER_FIRED` и возвращает инстанс в `RUNNING` через replay."""
    await append_event(
        db_session, timer.instance_id, EventType.TIMER_FIRED, {"step": timer.step_name}
    )


async def pending_count(db_session: AsyncSession, instance_id: uuid.UUID) -> int:
    """Сколько ожиданий ещё не сработало — для диагностики зависшего инстанса."""
    from sqlalchemy import func

    return (
        await db_session.scalar(
            select(func.count())
            .select_from(Timer)
            .where(Timer.instance_id == instance_id, Timer.fired.is_(False))
        )
    ) or 0


async def cancel_for(db_session: AsyncSession, instance_id: uuid.UUID) -> int:
    """Снимает ожидания инстанса.

    Вызывается при отмене и при начале отката: ждать таймер уже бессмысленно,
    а неотменённый таймер разбудил бы процесс после компенсации.
    """
    result = await db_session.execute(
        update(Timer)
        .where(Timer.instance_id == instance_id, Timer.fired.is_(False))
        .values(fired=True)
        .returning(Timer.id)
    )
    return len(result.all())
