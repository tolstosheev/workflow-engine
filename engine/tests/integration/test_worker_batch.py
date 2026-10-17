"""Пакетный цикл воркера (ADR-0023).

Захват пачки экономит обращения к базе, но меняет вид отказа: раньше цикл
работал с одним инстансом, теперь — с несколькими. Проверяется главное: пачка
обрабатывается целиком, а один испорченный процесс не срывает остальные.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import worker
from app.core.config import get_settings
from app.engine.journal import append_event
from app.models.enums import EventType, InstanceStatus
from app.models.workflow import StepRun, WorkflowDefinition, WorkflowInstance

# Фикстуры переиспользуются как есть: оборачивать их в свои нельзя — pytest
# считает вызов фикстуры из фикстуры ошибкой, а не наследованием.
from tests.integration.test_instances_api import db_session as db_session  # noqa: F401

pytestmark = pytest.mark.integration

FLOW = {
    "start": "reserve_inventory",
    "steps": {
        "reserve_inventory": {
            "action": "app.demo.handlers:reserve_inventory",
            "compensation": "app.demo.handlers:release_inventory",
            "next": "confirm",
        },
        "confirm": {"action": "app.demo.handlers:confirm_delivery"},
    },
}


async def _pending(
    db_session: AsyncSession,
    count: int,
    *,
    input: dict | None = None,
) -> list[uuid.UUID]:
    definition = WorkflowDefinition(name=f"batch-{uuid.uuid4().hex[:8]}", definition=FLOW)
    db_session.add(definition)
    await db_session.flush()

    ids: list[uuid.UUID] = []
    for _ in range(count):
        row = WorkflowInstance(
            definition_id=definition.id,
            status=InstanceStatus.PENDING,
            input=input or {"items": [{"sku": "SKU-000", "qty": 1}], "amount": "10.00"},
            variables={},
            created_at=datetime.now(UTC),
        )
        db_session.add(row)
        await db_session.flush()
        await append_event(
            db_session,
            row.id,
            EventType.INSTANCE_CREATED,
            {"start": definition.definition["start"]},
        )
        ids.append(row.id)
    await db_session.commit()
    return ids


async def _step_count(db_session: AsyncSession, instance_id: uuid.UUID) -> int:
    return (
        await db_session.scalar(
            select(func.count()).select_from(StepRun).where(StepRun.instance_id == instance_id)
        )
    ) or 0


async def _run_batch_cycles(worker_id: str, count: int = 8) -> int:
    cache: dict[uuid.UUID, object] = {}
    handled = 0
    for _ in range(count):
        result = await worker.run_batch_cycle(worker_id, cache)  # type: ignore[arg-type]
        if not result.handled:
            break
        handled += 1
    return handled


async def test_batch_runs_every_instance(db_session: AsyncSession) -> None:
    ids = await _pending(db_session, 3)

    await _run_batch_cycles("w1")

    # Каждый инстанс получил шаг: пачка не «съела» очередь, обработав первый.
    for instance_id in ids:
        assert await _step_count(db_session, instance_id) >= 1


async def test_batch_size_is_bounded(db_session: AsyncSession) -> None:
    """Пачка не превышает разумного: хвост стареет, пока воркер занят первым."""

    batch = get_settings().worker_claim_batch

    assert 1 <= batch <= 8, f"размер пачки {batch} не защищает хвост аренды"


async def test_batch_is_off_by_default() -> None:
    """По умолчанию пачка выключена: замер не показал выигрыша (ADR-0023).

    Число 4 давало −7 % на одном воркере и +5 % при разбросе 8–12 % на
    четырёх. Разница лежит внутри шума, и объявлять выигрышем нечего. Чтобы
    значение не увели тихо, здесь стоит проверка.
    """

    assert get_settings().worker_claim_batch == 1


async def test_one_broken_instance_does_not_stop_the_batch(db_session: AsyncSession) -> None:
    """Инстанс, который падает постоянно, не должен блокировать пачку.

    Раньше каждый цикл брал один инстанс, и такой процесс просто занимал цикл.
    В пачке он занимал бы её целиком, и очередь стояла бы до конца.
    """
    healthy = await _pending(db_session, 3)
    # Товара нет на складе: шаг падает постоянно, процесс уходит в отказ.
    broken = await _pending(db_session, 1, input={"items": [{"sku": "SKU-НЕТ", "qty": 1}]})

    await _run_batch_cycles("w1")

    for instance_id in healthy:
        assert await _step_count(db_session, instance_id) >= 1
    # Сломанный процесс обязан быть виден как разобранный, а не исчезнувший.
    assert await _step_count(db_session, broken[0]) >= 1


async def test_empty_queue_is_not_handled(db_session: AsyncSession) -> None:
    result = await worker.run_batch_cycle("w1", {})  # type: ignore[arg-type]

    # «Ничего не взял» и «взял и обработал» — разные вещи: цикл воркера
    # останавливается по первому, иначе он крутился бы вечно.
    assert result.handled is False
