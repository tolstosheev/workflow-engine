"""Отмена из паузы обязана быть видна в разборе журнала (ADR-0016).

Тайм-аут паузы работает ровно этим путём: он пишет `INSTANCE_CANCEL_REQUESTED`
процессу в `PAUSED`. Поэтому здесь закрепляется инвариант, на который он опирается:
колонка статуса и разбор журнала дают одно и то же (§3.1), а отменённый процесс
доходит до конца, а не ждёт возобновления, которого не будет.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.journal import read_events, replay
from app.models.enums import InstanceStatus
from app.services import instances as instance_service
from tests.integration.test_execution import (
    PAUSE_FLOW,
    _create_instance,
    _run_cycles,
    _seed_journal,
    _status,
)

# Фикстура объявлена в соседнем файле, а не в общем conftest: переиспользование
# важнее, чем вторая копия, которая потом разъедется с первой. Pytest находит
# фикстуру по имени в модуле, поэтому её надо положить в это имя обратно.
from tests.integration.test_execution import db_session as _db_session

db_session = _db_session

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("inventory")]


async def test_cancel_from_pause_is_visible_in_replay(
    db_session: AsyncSession,
) -> None:
    instance_id, _ = await _create_instance(db_session, PAUSE_FLOW)
    await _seed_journal(db_session, instance_id, PAUSE_FLOW)
    await db_session.commit()
    await _run_cycles("w1")
    assert await _status(db_session, instance_id) == InstanceStatus.PAUSED

    await instance_service.cancel_instance(db_session, instance_id)
    await db_session.commit()

    # Эндпоинт `/state` отдаёт разбор журнала и назван «то, что увидит воркер
    # после подхвата». Расхождение с колонкой означало бы, что экран показывает
    # «Продолжить» процессу, который уже откатывается.
    assert await _status(db_session, instance_id) == InstanceStatus.CANCELLING

    state = replay(await read_events(db_session, instance_id))

    assert state.status is InstanceStatus.CANCELLING
    assert state.cancel_requested is True


async def test_cancelled_pause_does_not_wait_for_a_resume(
    db_session: AsyncSession,
) -> None:
    instance_id, _ = await _create_instance(db_session, PAUSE_FLOW)
    await _seed_journal(db_session, instance_id, PAUSE_FLOW)
    await db_session.commit()
    await _run_cycles("w1")

    await instance_service.cancel_instance(db_session, instance_id)
    await db_session.commit()
    await _run_cycles("w2")

    # Отменённый из паузы процесс обязан дойти до конца. Разбор, считающий его
    # спящим, оставил бы резерв занятым навсегда.
    assert await _status(db_session, instance_id) in {
        InstanceStatus.COMPENSATED,
        InstanceStatus.CANCELLED,
    }
