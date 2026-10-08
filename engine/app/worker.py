"""Воркер-процесс: подбирает готовые инстансы и исполняет их шаги.

Ядро наполняется в Фазе 2; на текущем этапе — каркас цикла опроса с lease,
чтобы состав контейнера в `docker-compose.yml` был определён уже сейчас.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import socket
import uuid

from app.core.config import get_settings
from app.core.db import dispose_engine, session_scope
from app.core.logging import configure_logging

logger = logging.getLogger(__name__)


def _install_stop_handlers(stop_event: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        # add_signal_handler недоступен на Windows и вне главного потока:
        # без него SIGTERM завершит процесс минуя finally, не закрыв пул.
        with contextlib.suppress(NotImplementedError, AttributeError, ValueError):
            loop.add_signal_handler(sig, stop_event.set)


async def run_worker(stop_event: asyncio.Event | None = None) -> None:
    """Цикл опроса до получения сигнала остановки.

    `stop_event` внедряется, чтобы тест мог остановить цикл без отправки
    сигнала всему процессу.
    """
    settings = get_settings()
    worker_id = f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"

    if stop_event is None:
        stop_event = asyncio.Event()
        _install_stop_handlers(stop_event)

    logger.info(
        "worker.started",
        extra={
            "worker_id": worker_id,
            "lease_ttl_seconds": settings.lease_ttl_seconds,
            "pid": os.getpid(),
        },
    )

    try:
        while not stop_event.is_set():
            # wait_for вместо sleep: событие останавливает цикл немедленно,
            # а не по истечении интервала опроса.
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    stop_event.wait(), timeout=settings.worker_poll_interval_seconds
                )
    finally:
        await dispose_engine()
        logger.info("worker.stopped", extra={"worker_id": worker_id})


async def main() -> None:
    configure_logging()
    async with session_scope() as session:
        from sqlalchemy import text

        await session.execute(text("SELECT 1"))
    await run_worker()


if __name__ == "__main__":
    asyncio.run(main())
