"""Lease: взаимноисключающее исполнение инстанса при воркерах, которые могут умереть.

Координация построена на трёх колонках и атомарном `UPDATE` с условием на
истёкшую аренду. Никаких advisory-блокировок: удержание блокировки между
шагами — это утечка, которая вешает инстанс навсегда (§3.3 AGENTS.md).

Цена решения — время обнаружения смерти воркера равно TTL: система не может
отличить «медленный воркер» от «мёртвого» иначе, чем по молчанию.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.core.config import Settings, get_settings
from app.models.enums import ACTIVE_STATUSES, InstanceStatus
from app.models.workflow import WorkflowInstance


@dataclass(frozen=True, slots=True)
class ClaimedInstance:
    """Инстанс, за который воркер получил аренду."""

    instance_id: uuid.UUID
    definition_id: uuid.UUID
    lease_owner: str


# Хранится строкой: статус — TEXT с CHECK, а не enum, и psycopg отдаёт str.
_ACTIVE_STATUS_VALUES = sorted(str(status) for status in ACTIVE_STATUSES)


def _now() -> datetime:
    return datetime.now(UTC)


def _lease_free(now: datetime) -> ColumnElement[bool]:
    """Инстанс свободен, если аренда не выдана или истекла."""
    return (WorkflowInstance.lease_expires_at.is_(None)) | (WorkflowInstance.lease_expires_at < now)


def _compensation_ready(now: datetime) -> ColumnElement[bool]:
    """Пора ли брать откат, который уже откладывали.

    Пауза нужна после провала компенсации: она означает, что внешний сервис
    недоступен, и повтор без задержки бьёт по нему раз в секунду.
    """
    return (WorkflowInstance.compensation_not_before.is_(None)) | (
        WorkflowInstance.compensation_not_before <= now
    )


async def _update_lease(
    db_session: AsyncSession,
    instance_id: uuid.UUID,
    values: dict[str, Any],
    conditions: list[ColumnElement[bool]],
) -> bool:
    """Условный UPDATE с признаком «строка затронута».

    Именно `RETURNING` определяет успех: `rowcount` у `UPDATE ... RETURNING`
    недоступен, а условие в `WHERE` и есть проверка.
    """
    result = await db_session.execute(
        update(WorkflowInstance)
        .where(WorkflowInstance.id == instance_id, *conditions)
        .values(**values)
        .returning(WorkflowInstance.id)
    )
    return result.first() is not None


async def acquire(
    db_session: AsyncSession,
    worker_id: str,
    instance_id: uuid.UUID,
    ttl_seconds: int | None = None,
) -> bool:
    """Пытается захватить инстанс. `False` — аренда занята живым воркером.

    Атомарность обеспечивает сам `UPDATE` с условием, а не проверка до него:
    между SELECT и UPDATE другой воркер успел бы занять инстанс.
    """
    settings: Settings = get_settings()
    ttl = ttl_seconds if ttl_seconds is not None else settings.lease_ttl_seconds
    now = _now()

    return await _update_lease(
        db_session,
        instance_id,
        {
            "lease_owner": worker_id,
            "lease_expires_at": now + timedelta(seconds=ttl),
            "heartbeat_at": now,
        },
        [_lease_free(now)],
    )


async def claim_next(
    db_session: AsyncSession,
    worker_id: str,
    ttl_seconds: int | None = None,
) -> ClaimedInstance | None:
    """Подбирает один свободный инстанс и захватывает его.

    Выбор и захват объединены в один `UPDATE`. Раздельный `SELECT ... FOR
    UPDATE SKIP LOCKED` с последующим `UPDATE` удерживал бы блокировку на всех
    кандидатах, и второй воркер не увидел бы ничего, даже если инстансов
    было больше одного.

    Захватывается ровно один инстанс: захват пачки означал бы, что воркер
    держит аренду на инстансах, которые не обрабатывает, и они будут висеть до
    истечения TTL.
    """
    settings: Settings = get_settings()
    ttl = ttl_seconds if ttl_seconds is not None else settings.lease_ttl_seconds
    now = _now()

    # Спящий инстанс воркером не обслуживается: его разбудит таймер. Если бы он
    # попадал в выборку, воркер брал бы один и тот же спящий инстанс, пока тот
    # не проснётся, и вся очередь за ним стояла бы.
    ready_values = sorted(
        value for value in _ACTIVE_STATUS_VALUES if value != str(InstanceStatus.WAITING)
    )

    candidates = (
        select(WorkflowInstance.id)
        .where(
            WorkflowInstance.status.in_(ready_values),
            _lease_free(now),
            _compensation_ready(now),
        )
        .order_by(WorkflowInstance.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    claimed = await db_session.execute(
        update(WorkflowInstance)
        .where(WorkflowInstance.id.in_(candidates))
        .values(
            lease_owner=worker_id,
            lease_expires_at=now + timedelta(seconds=ttl),
            heartbeat_at=now,
        )
        .returning(WorkflowInstance.id, WorkflowInstance.definition_id)
    )

    row = claimed.first()
    if row is None:
        return None

    return ClaimedInstance(instance_id=row[0], definition_id=row[1], lease_owner=worker_id)


async def heartbeat(db_session: AsyncSession, instance_id: uuid.UUID, worker_id: str) -> bool:
    """Продлевает аренду. `False` — lease потерян, воркер обязан прекратить работу.

    Продлежение нужно только на длинных шагах: на коротких воркер заканчивает
    шаг быстрее TTL, и heartbeat ничего не меняет.
    """
    settings = get_settings()
    now = _now()

    return await _update_lease(
        db_session,
        instance_id,
        {
            "heartbeat_at": now,
            "lease_expires_at": now + timedelta(seconds=settings.lease_ttl_seconds),
        },
        [WorkflowInstance.lease_owner == worker_id],
    )


async def release(db_session: AsyncSession, instance_id: uuid.UUID, worker_id: str) -> bool:
    """Освобождает аренду.

    Условие по владельцу обязательно: если lease уже перехвачен, старый воркер
    не должен сбросить прогресс нового.
    """
    return await _update_lease(
        db_session,
        instance_id,
        {"lease_owner": None, "lease_expires_at": None},
        [WorkflowInstance.lease_owner == worker_id],
    )


async def find_expired(db_session: AsyncSession, batch_size: int = 10) -> list[uuid.UUID]:
    """Инстансы с истёкшей арендой — кандидаты на перехват другим воркером."""
    rows = await db_session.scalars(
        select(WorkflowInstance.id)
        .where(
            WorkflowInstance.status.in_(_ACTIVE_STATUS_VALUES),
            WorkflowInstance.lease_expires_at.is_not(None),
            WorkflowInstance.lease_expires_at < _now(),
        )
        .order_by(WorkflowInstance.lease_expires_at)
        .limit(batch_size)
    )
    return list(rows)


async def current_owner(db_session: AsyncSession, instance_id: uuid.UUID) -> str | None:
    """Кто сейчас владеет арендой — для тестов и диагностики."""
    return await db_session.scalar(
        select(WorkflowInstance.lease_owner).where(WorkflowInstance.id == instance_id)
    )


def is_active(status: InstanceStatus) -> bool:
    return status in ACTIVE_STATUSES
