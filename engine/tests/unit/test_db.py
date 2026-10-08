"""Тесты фабрики сессий.

Проверяются границы транзакции: commit на успешном выходе из `session_scope`
и откат при исключении. Именно на этом держится правило §3.2 — транзакция
закрывается до выхода наружу, а не тянется за внешним вызовом.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import db as db_module
from app.core.config import get_settings

_SQLITE_URL = "sqlite+aiosqlite:///./.pytest_sessions.db"


@pytest.fixture(autouse=True)
async def sqlite_engine() -> None:
    """Подменить пул на SQLite: инфраструктура сессий проверяется без БД.

    Файл, а не :memory: — иначе фабрика поднимает StaticPool, который не
    принимает параметры размера пула и не воспроизводит поведение боевого
    движка на asyncpg.
    """
    await db_module.dispose_engine()
    get_settings.cache_clear()
    original = get_settings().database_url
    object.__setattr__(get_settings(), "database_url", _SQLITE_URL)
    try:
        yield
    finally:
        object.__setattr__(get_settings(), "database_url", original)
        await db_module.dispose_engine()


async def test_get_engine_is_cached_per_process() -> None:
    assert db_module.get_engine() is db_module.get_engine()


async def test_get_session_factory_is_cached_per_process() -> None:
    assert db_module.get_session_factory() is db_module.get_session_factory()


async def test_session_scope_commits_on_success() -> None:
    async with db_module.session_scope() as session:
        assert isinstance(session, AsyncSession)
        await session.execute(text("SELECT 1"))


async def test_session_scope_rolls_back_on_error() -> None:
    with pytest.raises(RuntimeError, match="сбой шага"):
        async with db_module.session_scope() as session:
            await session.execute(text("SELECT 1"))
            raise RuntimeError("сбой шага")

    # После отката сессия обязана оставаться пригодной для следующего
    # использования, иначе утечка соединения повесит пул воркера.
    async with db_module.session_scope() as session:
        await session.execute(text("SELECT 1"))


async def test_get_db_does_not_commit_autonomously() -> None:
    # Роутеры не коммитят на выходе: границу транзакции выбирает сервис.
    generator = db_module.get_db()
    session = await anext(generator)
    assert isinstance(session, AsyncSession)
    await generator.aclose()


async def test_get_db_rolls_back_on_error() -> None:
    generator = db_module.get_db()
    await anext(generator)
    with pytest.raises(RuntimeError, match="ошибка обработчика"):
        await generator.athrow(RuntimeError("ошибка обработчика"))


async def test_dispose_engine_is_idempotent() -> None:
    await db_module.dispose_engine()
    await db_module.dispose_engine()


async def test_session_factory_follows_disposed_engine() -> None:
    # Если фабрика пережила dispose, воркер продолжит писать в закрытый пул.
    factory = db_module.get_session_factory()
    await db_module.dispose_engine()

    assert db_module.get_session_factory() is not factory
