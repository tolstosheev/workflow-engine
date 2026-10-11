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

from fastapi import APIRouter, Request
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
        EventType.INSTANCE_CANCEL_REQUESTED,
        EventType.INSTANCE_CANCELLED,
        EventType.INSTANCE_FAILED,
        EventType.STEP_COMPLETED,
        EventType.STEP_FAILED,
        EventType.COMPENSATION_STARTED,
        EventType.COMPENSATION_COMPLETED,
        EventType.COMPENSATION_FAILED,
        # Истеранение цикла выводит инстанс в откат. Без этого события в
        # потоке статус в списке до пересбора бы дождалось влет у статус
        # RUNNING и лишь запускал отката, который не виден.
        EventType.LOOP_EXHAUSTED,
    }
)

# Курсор потока — момент времени последнего события. Новое подключение
# получает только новые события а не всю историю: раньше увидит тысячи
# полученных кадров.
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _format(event_type: EventType, payload: dict, created_at: datetime) -> str:
    data = json.dumps(payload, ensure_ascii=False, default=str)
    # Идентификатор SSE позволяет браузеру передать последнее
    # событие в Last-Event-ID. Без него повторное подключение начиналось бы с
    # самого начала с эпохи и прогоняла всю журнал событий через все клиенты.
    return f"id: {created_at.isoformat()}\nevent: {event_type.value}\ndata: {data}\n\n"


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


async def event_stream(last_event_id: str | None) -> AsyncIterator[str]:
    """Отдаёт события по мере появления новых записей в журнале.

    Курсор приходит от подключения, а не всю историю. Раньше был цикл, который прогонял всю историю за одного
    подключения и делал общение за три секунды.

    `Last-Event-ID` присылает браузер EventSource сам и возвращает потом к адресу идентификатора
    последнего события. Раньше каждое подключение шло всю выборку
    журнала и прогонял обрабатывать его повторно.
    """
    settings = get_settings()
    cursor = _parse_cursor(last_event_id) or datetime.now(UTC)

    while True:
        try:
            batch = await _read_after(cursor)
        except Exception:
            # Обрыв связи с БД не должен убивать поток: клиент переподключится
            # сам, а heartbeat продолжит держать соединение живым.
            logger.exception("stream.db_unavailable")
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
                        created_at,
                    )
            # Затрус без паузы: при большом отставшемя батьях не должно пропускать раз в секунду.
            await asyncio.sleep(0)
            continue

        # Пустой опрос — это интервал ожидания, а не heartbeat. Ожидание
        # следующих событий должно быть коротким, иначе интерфейс
        # отставал бы на весь heartbeat (15 с) и показывал бы жив даже 15 с.
        await asyncio.sleep(settings.sse_poll_seconds)
        if not batch:
            yield ": heartbeat\n\n"


def _parse_cursor(last_event_id: str | None) -> datetime | None:
    """Заголвок `Last-Event-ID` в время.

    Браузер присылает там что отдаёт, но только всё. Неверный вывод
    должен возвращать только новые события так идёт с параллельного соединения.
    """
    if not last_event_id:
        return None
    try:
        parsed = datetime.fromisoformat(last_event_id)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


@router.get("/stream")
async def stream(request: Request) -> StreamingResponse:
    """SSE-поток событий всех инстансов."""
    return StreamingResponse(
        event_stream(request.headers.get("last-event-id")),
        media_type="text/event-stream",
        headers={
            # Буферизация прокси иначе собрет все события и отдалт
            # пачкой, а интерфейс перестанет быть живым.
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
