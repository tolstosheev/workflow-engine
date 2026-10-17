"""Пакетный захват инстансов (ADR-0023).

Захват одной штуки остаётся и продолжает проверяться `test_lease.py`. Здесь
инварианты пачки: сколько можно взять, по одному лицу, с возвратом, и что
потерянная аренда в одном не рушит остальное.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine import lease
from app.models.enums import InstanceStatus
from app.models.workflow import WorkflowDefinition, WorkflowInstance

# Фикстуры переиспользуются как есть: оборачивать их в свои нельзя — pytest
# считает вызов фикстуры из фикстуры ошибкой, а не наследованием.
from tests.integration.test_instances_api import db_session as db_session  # noqa: F401

pytestmark = pytest.mark.integration


async def _seed(db_session: AsyncSession, count: int, status: InstanceStatus) -> list[str]:
    """`count` процессов в статусе, готовом к захвату."""
    flow = {"start": "a", "steps": {"a": {"action": "app.demo.handlers:validate_cart"}}}
    definition = WorkflowDefinition(name=f"batch-{uuid.uuid4().hex[:8]}", definition=flow)
    db_session.add(definition)
    await db_session.flush()

    ids: list[str] = []
    for _ in range(count):
        row = WorkflowInstance(
            definition_id=definition.id,
            status=status,
            input={},
            variables={},
            created_at=datetime.now(UTC),
        )
        db_session.add(row)
        await db_session.flush()
        ids.append(str(row.id))
    await db_session.commit()
    return ids


async def test_batch_takes_up_to_limit(db_session: AsyncSession) -> None:
    await _seed(db_session, 3, InstanceStatus.PENDING)

    claimed = await lease.claim_batch(db_session, "w1", limit=3)

    assert len(claimed) == 3


async def test_batch_respects_limit(db_session: AsyncSession) -> None:
    # Больше инстансов, чем помещается в пачку: лишние должны остаться для
    # следующего цикла, а не висеть под чужой арендой.
    await _seed(db_session, 5, InstanceStatus.PENDING)

    claimed = await lease.claim_batch(db_session, "w1", limit=2)

    assert len(claimed) == 2


async def test_empty_queue_returns_nothing(db_session: AsyncSession) -> None:
    assert await lease.claim_batch(db_session, "w1", limit=4) == []


async def test_every_instance_gets_its_lease(db_session: AsyncSession) -> None:
    ids = await _seed(db_session, 2, InstanceStatus.PENDING)

    claimed = await lease.claim_batch(db_session, "w1", limit=2)

    assert {str(item.instance_id) for item in claimed} == set(ids)


async def test_second_worker_does_not_take_the_batch(db_session: AsyncSession) -> None:
    """`SKIP LOCKED` работает и на пачке: захваченное не отдаётся.

    Пакет без этого свойства означал бы, что два воркера исполняют один шаг
    дважды, и вся идемпотентность держалась бы на ключе операции.
    """
    await _seed(db_session, 2, InstanceStatus.PENDING)

    first = await lease.claim_batch(db_session, "w1", limit=2)
    second = await lease.claim_batch(db_session, "w2", limit=2)

    assert len(first) == 2
    assert second == []


async def test_release_frees_one_instance_of_the_batch(db_session: AsyncSession) -> None:
    """Аренда освобождается по одному, а не пачкой.

    Иначе освобождение последнего сняло бы аренду и с несделанными, и они
    висели бы до истечения TTL.
    """
    await _seed(db_session, 3, InstanceStatus.PENDING)
    claimed = await lease.claim_batch(db_session, "w1", limit=3)
    await db_session.commit()

    await lease.release(db_session, claimed[0].instance_id, "w1")
    await db_session.commit()

    taken = await lease.claim_batch(db_session, "w2", limit=3)

    # Взят должен быть именно освобождённый, а не «первый по дате»: у процессов
    # одной пачки даты совпадают, и проверка по позиции проверяла бы сортировку.
    assert [str(item.instance_id) for item in taken] == [str(claimed[0].instance_id)]


async def test_sleeping_instance_is_not_taken(db_session: AsyncSession) -> None:
    """Спящий инстанс в пачку не попадает: его разбудит таймер.

    Иначе воркер брал бы один и тот же спящий инстанс, пока тот не проснётся,
    и вся очередь стояла бы за ним.
    """
    await _seed(db_session, 1, InstanceStatus.WAITING)
    await _seed(db_session, 1, InstanceStatus.PENDING)

    claimed = await lease.claim_batch(db_session, "w1", limit=4)

    assert len(claimed) == 1
