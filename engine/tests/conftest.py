"""Общие фикстуры тестов."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from typing import Any

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
                "workers, workflow_instances, workflow_definitions RESTART IDENTITY CASCADE"
            )
        )
        await session.commit()
    yield


@pytest.fixture
async def inventory(db_engine: None) -> AsyncIterator[dict[str, Any]]:
    """Заполняет склад и отдаёт остаток по артикулам.

    Очистка базы удаляет и склад, а обработчики резервирования к нему
    обращаются. Без заполнения тесты падали бы с «товар не найден на складе» —
    и падали бы одинаково, не показывая, в чём причина.
    """
    from app.core.db import get_session_factory
    from app.models.workflow import DemoInventoryItem
    from scripts.definitions import DEMO_INVENTORY

    stock = {item["sku"]: item["available"] for item in DEMO_INVENTORY}

    async with get_session_factory()() as session:
        for row in DEMO_INVENTORY:
            session.add(DemoInventoryItem(sku=row["sku"], available=row["available"], reserved=0))
        await session.commit()

    yield stock


async def reserved_units(sku: str) -> int:
    """Сколько единиц товара сейчас занято резервом — для проверки отката."""
    from sqlalchemy import select

    from app.core.db import get_session_factory
    from app.models.workflow import DemoInventoryItem

    async with get_session_factory()() as session:
        # Поиск по `sku`: у таблицы два ключа, и `get()` взял бы идентификатор.
        row = await session.scalar(select(DemoInventoryItem).where(DemoInventoryItem.sku == sku))
        return int(row.reserved) if row is not None else -1
