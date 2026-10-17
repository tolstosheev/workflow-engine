"""Инъекция роняет компенсацию, а не прямой шаг.

Это и есть недостающая половина панели инъекций: Saga обещает, что откат может
провалиться, и до сих пор нечем было этого показать. Проверяется не только то,
что правило срабатывает, но и то, что прямой путь оно не задевает, — иначе
«сломать возврат» оказалось бы тем же самым, что «сломать шаг».
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine import lease
from app.models import chaos
from app.models.enums import EventType, InstanceStatus
from tests.integration.test_execution import (
    CANCEL_FLOW,
    _create_instance,
    _patch_resolver,
    _request_cancel,
    _run_cycles,
    _seed_journal,
    _status,
)

# Склад наполняется: без резерва откатывать нечего, и процесс завершился бы
# отменой без компенсаций вместо проверки её сбоя.
pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("inventory")]

DEMO = "app.demo.handlers"
RELEASE_REF = f"{DEMO}:release_inventory"


@pytest.fixture
async def db_session(db_engine: None):
    from app.core.db import get_session_factory

    session = get_session_factory()()
    transaction = await session.begin()
    try:
        yield session
    finally:
        if transaction.is_active:
            await transaction.rollback()
        await session.close()


async def test_injection_breaks_the_compensation_not_the_step(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.demo.handlers as demo

    instance_id, _ = await _create_instance(db_session, CANCEL_FLOW)
    await _seed_journal(db_session, instance_id, CANCEL_FLOW)
    await db_session.commit()

    # Прямой шаг проходит: правило адресовано откату, и шаг до отката ещё не
    # выполнен — он выполнится, когда процесс дойдёт до компенсации.
    await _run_cycles("w1")
    await _request_cancel(db_session, instance_id)
    await db_session.commit()

    await chaos.add_rule_for_handler(
        db_session, None, RELEASE_REF, "always", "сбой компенсации (инъекция)"
    )
    await db_session.commit()

    _patch_resolver(monkeypatch, demo)

    await _run_cycles("w2")

    # Провал компенсации — это COMPENSATION_FAILED: процесс остался с
    # частично применённым эффектом, и это видно в интерфейсе.
    assert await _status(db_session, instance_id) == InstanceStatus.COMPENSATION_FAILED

    from app.engine.journal import read_events

    events = await read_events(db_session, instance_id)
    assert EventType.COMPENSATION_FAILED in [event.type for event in events]


async def test_step_rule_does_not_touch_the_compensation(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.demo.handlers as demo

    instance_id, _ = await _create_instance(db_session, CANCEL_FLOW)
    await _seed_journal(db_session, instance_id, CANCEL_FLOW)
    await db_session.commit()
    await _run_cycles("w1")
    await _request_cancel(db_session, instance_id)
    await db_session.commit()

    # Правило по имени шага остаётся адресованным шагу: откат оно не роняет,
    # иначе адресация по обработчику была бы не нужна.
    await chaos.add_rule(db_session, None, "reserve_inventory", "always", "сбой")
    await db_session.commit()

    _patch_resolver(monkeypatch, demo)

    await _run_cycles("w2")

    assert await _status(db_session, instance_id) == InstanceStatus.COMPENSATED


async def test_manual_retry_clears_the_injection_backoff(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.demo.handlers as demo
    from app.services import instances as instance_service

    instance_id, _ = await _create_instance(db_session, CANCEL_FLOW)
    await _seed_journal(db_session, instance_id, CANCEL_FLOW)
    await db_session.commit()
    await _run_cycles("w1")
    await _request_cancel(db_session, instance_id)
    await db_session.commit()

    await chaos.add_rule_for_handler(
        db_session, instance_id, RELEASE_REF, "once", "сбой компенсации (инъекция)"
    )
    await db_session.commit()

    _patch_resolver(monkeypatch, demo)

    await _run_cycles("w2")
    assert await _status(db_session, instance_id) == InstanceStatus.COMPENSATION_FAILED

    # Повтор снимает накопленную паузу, иначе кнопка «повторить откат» молча
    # не работала бы: процесс ждал бы следующего подходящего момента.
    await instance_service.retry_compensation(db_session, instance_id)
    await db_session.commit()

    from app.core.db import session_scope

    async with session_scope() as session:
        await lease.release(session, instance_id, "w3")
        await session.commit()

    await _run_cycles("w3")

    # Правило «один раз» израсходовано, и откат проходит.
    assert await _status(db_session, instance_id) == InstanceStatus.COMPENSATED
