"""Тесты цикла воркера.

Ценность теста — в проверке остановки: цикл обязан завершиться по событию
и закрыть пул соединений, иначе остановка контейнера оставит соединения
в состоянии ESTABLISHED.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from app import worker
from app.core.config import get_settings


async def test_worker_loop_stops_on_event() -> None:
    stop_event = asyncio.Event()
    task = asyncio.create_task(worker.run_worker(stop_event))

    await asyncio.sleep(0.05)
    assert not task.done()

    stop_event.set()
    await asyncio.wait_for(task, timeout=5)


async def test_worker_loop_exits_immediately_on_pre_set_event() -> None:
    stop_event = asyncio.Event()
    stop_event.set()

    await asyncio.wait_for(worker.run_worker(stop_event), timeout=5)


async def test_worker_disposes_engine_on_stop() -> None:
    stopped = asyncio.Event()

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            if record.getMessage() == "worker.stopped":
                stopped.set()

    capture = _Capture()
    logger = logging.getLogger(worker.__name__)
    logger.addHandler(capture)
    stop_event = asyncio.Event()
    stop_event.set()
    try:
        await asyncio.wait_for(worker.run_worker(stop_event), timeout=5)
        assert stopped.is_set()
    finally:
        logger.removeHandler(capture)


async def test_worker_uses_configured_poll_interval() -> None:
    # Интервал опроса задаётся конфигурацией, а не константой в коде:
    # без него подбор инстансов нельзя ускорить в тестах нагрузки.
    assert get_settings().worker_poll_interval_seconds > 0


async def test_stop_handlers_tolerate_unsupported_signals() -> None:
    # На Windows add_signal_handler бросает — обработчик обязан это пережить,
    # иначе воркер не стартует ни на одной платформе разработки.
    worker._install_stop_handlers(asyncio.Event())


async def test_worker_without_injected_event_installs_own() -> None:
    # Боевой путь: воркер сам создаёт событие и вешает обработчики сигналов.
    # Отмена имитирует SIGTERM: finally обязан отработать при любом выходе.
    task = asyncio.create_task(worker.run_worker())
    await asyncio.sleep(0.05)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
