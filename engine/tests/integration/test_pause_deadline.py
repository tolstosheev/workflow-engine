"""Срок ожидания человека и его истечение (ADR-0016).

Пауза без срока оставляла процесс висящим навсегда: `PAUSED` исключён из
подбора воркером, будить его было некому, резерв оставался занятым. Здесь
закрепляется поведение по шагам: срок планируется durable-объектом, истечение
отменяет процесс, а отсутствие срока ничего не меняет.

Отдельно проверяется, что срок паузы не путается с ожиданием шага на том же
шаге: у таймеров нет иного различия, кроме вида, и без него `planned_for`
принял бы одно ожидание за другое.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.journal import read_events
from app.models.enums import EventType, InstanceStatus
from app.models.workflow import Timer
from app.schemas import definition as definition_schema
from tests.integration.test_execution import (
    _create_instance,
    _run_cycles,
    _seed_journal,
    _status,
)
from tests.integration.test_execution import db_session as _db_session

db_session = _db_session

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("inventory")]


def _flow(**overrides: object) -> dict:
    """Тот же PAUSE_FLOW, но срок задаётся вызывающим."""
    from tests.integration.test_execution import PAUSE_FLOW

    flow = {
        key: dict(value) if isinstance(value, dict) else value for key, value in PAUSE_FLOW.items()
    }
    for name, value in overrides.items():
        flow["steps"]["approve_order"] = {**flow["steps"]["approve_order"], name: value}
    return flow


async def _timers(db_session: AsyncSession, instance_id: object) -> list[Timer]:
    rows = await db_session.scalars(select(Timer).where(Timer.instance_id == instance_id))
    return list(rows)


async def test_pause_without_a_deadline_lives_forever(
    db_session: AsyncSession,
) -> None:
    # Регрессия: без срока поведение прежнее. Если бы встроенное значение
    # появилось, отменённые вручную определения начали бы откатываться сами.
    flow = _flow()
    flow["steps"]["approve_order"].pop("pause_timeout_seconds", None)

    instance_id, _ = await _create_instance(db_session, flow)
    await _seed_journal(db_session, instance_id, flow)
    await db_session.commit()
    await _run_cycles("w1")

    assert await _status(db_session, instance_id) == InstanceStatus.PAUSED

    # Ни одного таймера-срока: будить паузу по-прежнему некому, и это пока
    # осознанный выбор автора определения.
    assert [timer for timer in await _timers(db_session, instance_id)] == []


async def test_pause_plans_a_deadline(db_session: AsyncSession) -> None:
    flow = _flow(pause_timeout_seconds=3600)

    instance_id, _ = await _create_instance(db_session, flow)
    await _seed_journal(db_session, instance_id, flow)
    await db_session.commit()
    await _run_cycles("w1")

    assert await _status(db_session, instance_id) == InstanceStatus.PAUSED

    timers = await _timers(db_session, instance_id)

    # Вид обязателен: на одном шаге могут стоять и ожидание, и срок, и без
    # различия `planned_for` принял бы одно за другое.
    assert [timer.kind for timer in timers] == ["pause_deadline"]


async def test_expiry_cancels_the_process(db_session: AsyncSession) -> None:
    flow = _flow(pause_timeout_seconds=3600)

    instance_id, _ = await _create_instance(db_session, flow)
    await _seed_journal(db_session, instance_id, flow)
    await db_session.commit()
    await _run_cycles("w1")
    assert await _status(db_session, instance_id) == InstanceStatus.PAUSED

    await _fire_pause_deadline(db_session, instance_id)
    await db_session.commit()
    await _run_cycles("w2")

    # Истечение отменяет, а не продолжает: действие шага необратимо, и «платим
    # без согласования» — не то, что означают ворота согласования.
    assert await _status(db_session, instance_id) in {
        InstanceStatus.COMPENSATED,
        InstanceStatus.CANCELLED,
    }
    types = [event.type for event in await read_events(db_session, instance_id)]
    assert EventType.INSTANCE_CANCEL_REQUESTED in types


async def test_expiry_survives_a_worker_restart(db_session: AsyncSession) -> None:
    # Срок — durable-объект, а не таймер в памяти (§3.6). Проверяется тем, что
    # срабатывание вызывается напрямую по объекту из БД, без всякого воркера.
    flow = _flow(pause_timeout_seconds=3600)

    instance_id, _ = await _create_instance(db_session, flow)
    await _seed_journal(db_session, instance_id, flow)
    await db_session.commit()
    await _run_cycles("w1")

    db_session.expire_all()
    timer = (await _timers(db_session, instance_id))[0]

    from app.engine import deadline

    assert await deadline.expire_pause_deadline(db_session, timer) is True


async def test_pause_wins_over_a_step_wait(db_session: AsyncSession) -> None:
    # Шаг может объявить и срок паузы, и ожидание. Планировать оба нельзя:
    # сработавший таймер ожидания разбудил бы процесс, а сработавший срок
    # отменил бы его — и исход зависел бы от того, кто выстрелит первым.
    # Пауза проверяется раньше ожидания, и таймер ожидания не заводится вовсе.
    flow = _flow(pause_timeout_seconds=3600)
    flow["steps"]["approve_order"]["wait_before_seconds"] = 5

    instance_id, _ = await _create_instance(db_session, flow)
    await _seed_journal(db_session, instance_id, flow)
    await db_session.commit()
    await _run_cycles("w1", count=3)

    kinds = [timer.kind for timer in await _timers(db_session, instance_id)]

    assert kinds == ["pause_deadline"]
    assert await _status(db_session, instance_id) == InstanceStatus.PAUSED


def test_deadline_needs_a_pause() -> None:
    # Срок без `wait_for: manual` означал бы ожидание, которого не будет:
    # шаг выполнится сразу, а таймер сработает вхолостую.
    with pytest.raises(ValidationError, match="wait_for: manual"):
        definition_schema.Step.model_validate(
            {"action": "app.demo.handlers:confirm_delivery", "pause_timeout_seconds": 60}
        )


def test_deadline_is_bounded() -> None:
    with pytest.raises(ValidationError):
        definition_schema.Step.model_validate(
            {
                "action": "app.demo.handlers:confirm_delivery",
                "wait_for": "manual",
                "pause_timeout_seconds": 10**9,
            }
        )


async def test_deadline_does_not_turn_a_pause_into_a_wait(
    db_session: AsyncSession,
) -> None:
    # Срок планируется таймером и пишет `TIMER_SCHEDULED` — тем же событием,
    # что и ожидание шага. Разбор без оглядки на вид события переводил паузу в
    # `WAITING`, и одна и та же страница называла одно состояние двумя словами:
    # шапка «Ждёт решения» и карточка «Ожидание» (§3.1).
    flow = _flow(pause_timeout_seconds=3600)

    instance_id, _ = await _create_instance(db_session, flow)
    await _seed_journal(db_session, instance_id, flow)
    await db_session.commit()
    await _run_cycles("w1")

    from app.engine.journal import read_events, replay

    state = replay(await read_events(db_session, instance_id))

    assert await _status(db_session, instance_id) == InstanceStatus.PAUSED
    assert state.status is InstanceStatus.PAUSED


async def _fire_pause_deadline(db_session: AsyncSession, instance_id: object) -> None:
    """Срабатывает срок так, как это сделал бы настоящий таймер."""
    from app.engine import deadline

    timer = [row for row in await _timers(db_session, instance_id) if row.kind == "pause_deadline"][
        0
    ]
    assert await deadline.expire_pause_deadline(db_session, timer) is True
