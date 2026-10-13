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
from app.models.enums import EventType, InstanceStatus
from app.models.workflow import Timer, WorkflowInstance

# Вид ожидания. Срок паузы и ожидание шага различаются видом: на одном шаге они
# могут стоять одновременно (ADR-0016).
STEP_WAIT = "step"
PAUSE_DEADLINE = "pause_deadline"


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
        instance_id=instance_id,
        step_name=step_name,
        kind=STEP_WAIT,
        due_at=due_at,
        payload=payload or {},
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
    """Забирает пачку сработавших таймеров, ещё не отмеченных как сгоревшие.

    Отметка «сгорел» ставится не здесь, а в `fire_due`: пока строка выбрана, но
    не отмечена, любой воркер может взять её повторно. Отметка и событие
    должны идти одной транзакцией с возвратом инстанса в работу — иначе падение
    между ними оставит инстанс спящим навсегда.

    `SKIP LOCKED` возвращает воркеру только свободные строки: без него вторая
    выборка ждала бы на блокировке первой, и ожидание перестало бы быть
    параллельным.
    """
    now = _now()
    return list(
        await db_session.scalars(
            select(Timer)
            .where(Timer.fired.is_(False), Timer.due_at <= now)
            .order_by(Timer.due_at)
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
    )


async def fire_due(db_session: AsyncSession, timer: Timer) -> bool:
    """Отмечает таймер сгоревшим и будит инстанс.

    Событие `TIMER_FIRED` и возврат статуса в `RUNNING` идут одной транзакцией
    с отметкой в самой строке таймера: инстанс спит, пока таймер не сгорит, и
    без единой записи просыпается только воркер, уже не работающий с этим
    инстансом (§3.6 AGENTS.md).

    `False` означает, что таймер кто-то уже отметил: повторная обработка
    второго воркера ничего не должна писать в журнал.
    """
    result = await db_session.execute(
        update(Timer)
        .where(Timer.id == timer.id, Timer.fired.is_(False))
        .values(fired=True)
        .returning(Timer.id)
    )
    if result.first() is None:
        return False

    await append_event(
        db_session, timer.instance_id, EventType.TIMER_FIRED, {"step": timer.step_name}
    )

    instance = await db_session.get(WorkflowInstance, timer.instance_id, with_for_update=True)
    if instance is not None:
        instance.status = InstanceStatus.RUNNING
    return True


async def planned_for(
    db_session: AsyncSession,
    instance_id: uuid.UUID,
    step_name: str,
    kind: str = STEP_WAIT,
) -> bool:
    """Планировалось ли ожидание для этого шага инстанса.

    Учитываются и сгоревшие таймеры: `wait_before_seconds` ждёт **один** раз,
    и после срабатывания таймера шаг обязан исполниться, а не завести новую
    паузу. Проверяется перед планированием ожидания — иначе воркер создавал бы
    таймер на каждом цикле и шаг не наступил бы никогда.

    Вид обязателен: на одном шаге могут стоять и ожидание, и срок паузы, и без
    различия вторая проверка приняла бы первую за себя, и шаг не наступил бы
    никогда (ADR-0016).
    """
    from sqlalchemy import func

    count = await db_session.scalar(
        select(func.count())
        .select_from(Timer)
        .where(
            Timer.instance_id == instance_id,
            Timer.step_name == step_name,
            Timer.kind == kind,
        )
    )
    return bool(count)


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
