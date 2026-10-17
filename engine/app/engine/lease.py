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

    Захватывается ровно один инстанс: пачка берётся `claim_batch`, и держит
    аренду только на тех, которые воркер действительно обрабатывает.
    """
    settings: Settings = get_settings()
    ttl = ttl_seconds if ttl_seconds is not None else settings.lease_ttl_seconds
    now = _now()

    candidates = (
        select(WorkflowInstance.id)
        .where(
            WorkflowInstance.status.in_(_ready_status_values()),
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


def _ready_status_values() -> list[str]:
    """Состояния, которые воркер забирает.

    Спящий инстанс воркером не обслуживается: его разбудит таймер. Если бы он
    попадал в выборку, воркер брал бы один и тот же спящий инстанс, пока тот не
    проснётся, и вся очередь за ним стояла бы. Пауза — по той же причине: она
    ждёт человека (ADR-0014), а не таймера.

    Список общий для `claim_next` и `claim_batch`: пачка не должна брать то,
    что одиночный захват брать не станет.
    """
    return sorted(
        value
        for value in _ACTIVE_STATUS_VALUES
        if value not in {str(InstanceStatus.WAITING), str(InstanceStatus.PAUSED)}
    )


async def claim_batch(
    db_session: AsyncSession,
    worker_id: str,
    limit: int,
    ttl_seconds: int | None = None,
) -> list[ClaimedInstance]:
    """Подбирает и захватывает до `limit` свободных инстансов (ADR-0023).

    Конструкция та же, что у `claim_next`: выбор и захват объединены в один
    `UPDATE` с `SKIP LOCKED`, иначе вторая транзакция держала бы блокировку на
    всех кандидатах и не увидела бы свободных.

    Аренда у всех одна по сроку, но освобождается по одному, сразу после
    обработки: пачка, взятая «в запас», висела бы до истечения TTL.

    Замер показал, что шаг занимает около 30 % времени цикла, а остальное —
    повторяющийся на каждом шаге захват и освобождение. Пакет сокращает число
    обращений к базе на шаг; размер пачки ограничен сверху, иначе хвост пачки
    рискует не успеть до истечения аренды.
    """
    if limit < 1:
        return []

    settings: Settings = get_settings()
    ttl = ttl_seconds if ttl_seconds is not None else settings.lease_ttl_seconds
    now = _now()

    candidates = (
        select(WorkflowInstance.id)
        .where(
            WorkflowInstance.status.in_(_ready_status_values()),
            _lease_free(now),
            _compensation_ready(now),
        )
        .order_by(WorkflowInstance.created_at)
        .limit(limit)
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

    return [
        ClaimedInstance(instance_id=row[0], definition_id=row[1], lease_owner=worker_id)
        for row in claimed.all()
    ]


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


async def current_owner(db_session: AsyncSession, instance_id: uuid.UUID) -> str | None:
    """Кто сейчас владеет арендой — для тестов и диагностики."""
    return await db_session.scalar(
        select(WorkflowInstance.lease_owner).where(WorkflowInstance.id == instance_id)
    )
