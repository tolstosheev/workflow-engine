"""Тесты чтения настроек из окружения."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings, get_settings


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Убрать переменные окружения: иначе тест зависит от запуска контейнера."""
    for name in (
        "APP_ENV",
        "DEBUG",
        "CORS_ORIGINS",
        "LEASE_TTL_SECONDS",
        "HEARTBEAT_INTERVAL_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)


def test_defaults_are_local_development() -> None:
    settings = Settings(_env_file=None)

    assert settings.app_env == "development"
    assert settings.debug is True
    assert settings.cors_origin_list == ["http://localhost:5173"]


def test_cors_origins_accept_comma_separated_string() -> None:
    # В docker-compose origins приходят строкой через запятую: без разбора
    # CORS перестаёт работать в контейнерах.
    settings = Settings(_env_file=None, cors_origins="http://a.test, http://b.test")

    assert settings.cors_origin_list == ["http://a.test", "http://b.test"]


def test_cors_origins_drops_empty_items() -> None:
    settings = Settings(_env_file=None, cors_origins="http://a.test,,")

    assert settings.cors_origin_list == ["http://a.test"]


def test_cors_origins_empty_string_yields_no_origins() -> None:
    settings = Settings(_env_file=None, cors_origins="")

    assert settings.cors_origin_list == []


def test_cors_origins_are_read_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "http://front.test,http://admin.test")

    assert Settings(_env_file=None).cors_origin_list == ["http://front.test", "http://admin.test"]


def test_heartbeat_must_be_shorter_than_lease_ttl() -> None:
    # Heartbeat не чаще TTL означает простухание lease на живом воркере
    # и двойное исполнение шага двумя воркерами.
    with pytest.raises(ValidationError, match="HEARTBEAT_INTERVAL_SECONDS"):
        Settings(_env_file=None, lease_ttl_seconds=10, heartbeat_interval_seconds=10)


def test_heartbeat_equal_to_ttl_is_rejected() -> None:
    with pytest.raises(ValidationError, match="HEARTBEAT_INTERVAL_SECONDS"):
        Settings(_env_file=None, lease_ttl_seconds=10, heartbeat_interval_seconds=30)


def test_heartbeat_shorter_than_ttl_is_accepted() -> None:
    settings = Settings(_env_file=None, lease_ttl_seconds=10, heartbeat_interval_seconds=5)

    assert settings.heartbeat_interval_seconds == 5


def test_env_file_is_ignored_when_missing() -> None:
    # Отсутствующий .env не должен ронять приложение: конфигурация приходит
    # из переменных окружения контейнера.
    settings = Settings(_env_file="does-not-exist.env")

    assert settings.app_env == "development"


def test_get_settings_is_cached() -> None:
    assert get_settings() is get_settings()
