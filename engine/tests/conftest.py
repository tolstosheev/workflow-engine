"""Общие фикстуры тестов."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator

import pytest

from app.core.config import get_settings


def pytest_configure(config: pytest.Config) -> None:
    # Окружение выставляется до первого обращения к Settings: pydantic-settings
    # читает переменные один раз при создании объекта.
    os.environ.setdefault("APP_ENV", "test")
    os.environ.setdefault("DEBUG", "false")


@pytest.fixture(autouse=True)
def reset_settings_cache() -> Iterator[None]:
    """Сбрасывать кэш настроек, иначе результат теста зависит от порядка выполнения."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
async def db_engine() -> AsyncIterator[None]:
    """Поднимает движок на время одного async-теста.

    pytest-asyncio закрывает event loop после каждого теста, а пул соединений
    asyncpg привязан к своему loop. Движок уровня модуля пережил бы первый тест
    и начал отдавать «Event loop is closed» на всех остальных, поэтому он
    создаётся и закрывается вместе с тестом.
    """
    from app.core.db import dispose_engine, get_engine

    get_engine()
    try:
        yield
    finally:
        await dispose_engine()


@pytest.fixture(autouse=True)
async def clean_database(request: pytest.FixtureRequest, db_engine: None) -> AsyncIterator[None]:
    """Очищает таблицы перед каждым интеграционным и chaos-тестом.

    Откат в фикстуре `db_session` не спасает: часть тестов коммитит намеренно
    (HTTP-слой, конкуренция воркеров), и их данные оставались бы в базе, ломая
    выборки следующих тестов. `alembic_version` не трогается — иначе миграции
    пришлось бы применять заново.
    """
    needs_db = any(
        request.node.get_closest_marker(name) is not None for name in ("integration", "chaos")
    )
    if not needs_db:
        yield
        return

    from sqlalchemy import text

    from app.core.db import get_session_factory

    async with get_session_factory()() as session:
        await session.execute(
            text(
                "TRUNCATE TABLE compensation_log, step_runs, timers, processed_operations, "
                "workflow_events, chaos_rules, demo_payments, demo_shipments, demo_inventory, "
                "workflow_instances, workflow_definitions RESTART IDENTITY CASCADE"
            )
        )
        await session.commit()
    yield
