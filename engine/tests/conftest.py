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
