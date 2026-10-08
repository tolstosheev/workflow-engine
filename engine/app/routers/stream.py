"""Поток живых обновлений (SSE).

Фронтенд получает события по мере их появления в журнале. Источник — тот же
журнал, что и у остального чтения: отдельный канал событий означал бы второе
место, где состояние может разойтись с журналом.

Курсор — время последнего прочитанного события. `seq` уникален только внутри
инстанса и для глобального потока не годится.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import session_scope
from app.models.enums import EventType
from app.models.workflow import WorkflowEvent

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["stream"])

# На экране интересуют только смена статуса, шаги и компенсации. LEASE_EXPIRED
# не меняет картину и только увеличивает поток.
NOTIFIABLE = frozenset(
    {
        EventType.INSTANCE_CREATED,
        EventType.INSTANCE_COMPLETED,
        EventType.INSTANCE_COMPENSATED,
        EventType.INSTANCE_CANCELLED,
        EventType.STEP_COMPLETED,
        EventType.STEP_FAILED,
        EventType.COMPENSATION_STARTED,
        EventType.COMPENSATION_COMPLETED,
        EventType.COMPENSATION_FAILED,
    }
)

# Начало эпохи воркера: события старше не показываются новому подключению.
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _format(event_type: EventType, payload: dict) -> str:
    data = json.dumps(payload, ensure_ascii=False, default=str)
    return f"event: {event_type.value}\ndata: {data}\n\n"


async def _read_after(cursor: datetime) -> list[tuple[datetime, EventType, dict, str]]:
    async with session_scope() as session:
        rows = (
            await session.scalars(
                select(WorkflowEvent)
                .where(WorkflowEvent.created_at > cursor)
                .order_by(WorkflowEvent.created_at)
                .limit(200)
            )
        ).all()
    # instance_id берётся из колонки события, а не из payload: в payload его
    # нет, и клиент получал бы пустой идентификатор инстанса.
    return [
        (row.created_at, EventType(row.type), row.payload, str(row.instance_id)) for row in rows
    ]


async def event_stream() -> AsyncIterator[str]:
    """Отдаёт SSE-события по мере появления новых записей в журнале.

    События при переподключении не повторяются и не догружаются: клиент
    запрашивает их через `GET /instances/{id}/events` с известным `from_seq`.
    """
    settings = get_settings()
    cursor = _EPOCH

    while True:
        try:
            batch = await _read_after(cursor)
        except Exception:
            # Обрыв связи с БД не должен убивать поток: клиент переподключится
            # сам, а heartbeat продолжит держать соединение живым.
            logger.warning("stream.db_unavailable")
            batch = []

        if batch:
            for created_at, event_type, payload, instance_id in batch:
                cursor = max(cursor, created_at)
                if event_type in NOTIFIABLE:
                    yield _format(
                        event_type,
                        {
                            "instance_id": instance_id,
                            "type": event_type.value,
                            "payload": payload,
                            "created_at": created_at.isoformat(),
                        },
                    )
            continue

        # Пустой опрос: время для heartbeat. Он же ограничивает ожидание цикла,
        # чтобы отмена соединения обрабатывалась быстро.
        await asyncio.sleep(settings.sse_heartbeat_seconds)
        yield ": heartbeat\n\n"


@router.get("/stream")
async def stream() -> StreamingResponse:
    """SSE-поток событий всех инстансов."""
    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            # Буферизация прокси иначе соберёт все события и отдал пачкой,
            # а интерфейс перестанет быть живым.
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
