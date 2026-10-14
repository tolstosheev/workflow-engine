from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_db
from app.schemas.api import WorkerListOut, WorkerOut
from app.services import registry as service

router = APIRouter(prefix="/api/workers", tags=["workers"])


@router.get("", response_model=WorkerListOut)
async def list_workers(db_session: AsyncSession = Depends(get_db)) -> WorkerListOut:
    """Реестр воркеров (ADR-0018).

    Отдаёт и не отвечающих: упавший воркер не удаляется из реестра, иначе падение
    выглядело бы как отсутствие воркера, а вопрос был как раз в том, жив ли он.
    """
    settings = get_settings()
    fresh_for = service.stale_after(settings.worker_poll_interval_seconds)
    now = datetime.now(UTC)
    rows = await service.list_workers(db_session, fresh_for)
    return WorkerListOut(
        items=[
            WorkerOut(
                worker_id=row.worker_id,
                state=service.worker_state(row, now, fresh_for),
                started_at=row.started_at,
                heartbeat_at=row.heartbeat_at,
                stopped_at=row.stopped_at,
                cycles_completed=row.cycles_completed,
                steps_executed=row.steps_executed,
                lease_lost=row.lease_lost,
            )
            for row in rows
        ],
        fresh_for_seconds=settings.worker_poll_interval_seconds * 3,
    )
