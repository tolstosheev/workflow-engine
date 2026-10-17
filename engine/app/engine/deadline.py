"""Срок ожидания человека и его истечение (ADR-0016).

Срок паузы — тот же durable-таймер, что и ожидание шага: новая таблица ради одного
поля означала бы второй механизм там, где хватает одного. Различает их вид
`kind`, без которого `planned_for` принял бы одно ожидание за другое на шаге, где
стоят оба.

Истечение отменяет процесс, а не продолжает его: действие шага необратимо, и
«оплатить без согласования» — не то, что означают ворота согласования.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.journal import append_event
from app.models.enums import PAUSE_DEADLINE, EventType, InstanceStatus
from app.models.workflow import Timer, WorkflowInstance


async def schedule_deadline(
    db_session: AsyncSession,
    instance_id: uuid.UUID,
    step_name: str,
    seconds: float,
) -> Timer:
    """Планирует срок паузы и пишет событие журнала.

    Событие — то же `TIMER_SCHEDULED`, что и у ожидания шага: различать их в
    разборе незачем, а второй тип события означал бы второе место, где надо
    вспомнить про этот таймер. Вид виден в строке таймера и в подписи события.
    """
    due_at = datetime.now(UTC) + timedelta(seconds=seconds)
    timer = Timer(
        instance_id=instance_id,
        step_name=step_name,
        kind=PAUSE_DEADLINE,
        due_at=due_at,
        payload={"purpose": PAUSE_DEADLINE},
    )
    db_session.add(timer)
    await db_session.flush()

    await append_event(
        db_session,
        instance_id,
        EventType.TIMER_SCHEDULED,
        {
            "step": step_name,
            "due_at": due_at.isoformat(),
            "timer_id": str(timer.id),
            "kind": PAUSE_DEADLINE,
        },
    )
    return timer


async def expire_pause_deadline(db_session: AsyncSession, timer: Timer) -> bool:
    """Срок истёк: отменяет процесс, уводя его в откат.

    `False` означает, что срок кто-то уже отметил: повторная обработка была бы
    второй отменой одного и того же процесса.
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
        db_session,
        timer.instance_id,
        EventType.INSTANCE_CANCEL_REQUESTED,
        {"step": timer.step_name, "reason": "pause_deadline_expired"},
    )

    instance = await db_session.get(WorkflowInstance, timer.instance_id, with_for_update=True)
    if instance is not None:
        # `CANCELLING`, а не терминальный статус: компенсации выполняет воркер,
        # и именно он приведёт процесс к `COMPENSATED` либо `CANCELLED`.
        instance.status = InstanceStatus.CANCELLING
    return True


async def deadline_planned(
    db_session: AsyncSession, instance_id: uuid.UUID, step_name: str
) -> bool:
    """Планировался ли уже срок паузы для этого шага.

    Отдельно от `planned_for`: пауза входит и выходит многократно по кругу
    (шаг в цикле), и без проверки каждый вход заводил бы новый срок, который
    отменил бы уже отменённый процесс.
    """
    found = await db_session.scalar(
        select(Timer.id).where(
            Timer.instance_id == instance_id,
            Timer.step_name == step_name,
            Timer.kind == PAUSE_DEADLINE,
        )
    )
    return found is not None
