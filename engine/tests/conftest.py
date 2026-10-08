"""Общие фикстуры тестов."""

from __future__ import annotations

import os
from collections.abc import Iterator

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
