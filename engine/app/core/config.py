"""Конфигурация приложения, читаемая из переменных окружения (`.env`)."""

from __future__ import annotations

from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "development"
    debug: bool = True
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    cors_origins: list[str] = ["http://localhost:5173"]

    database_url: str = "postgresql+asyncpg://workflow:workflow@localhost:5433/workflow_engine"
    sync_database_url: str = "postgresql+asyncpg://workflow:workflow@localhost:5433/workflow_engine"
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_echo: bool = False

    lease_ttl_seconds: int = 30
    heartbeat_interval_seconds: int = 5
    worker_max_steps_per_cycle: int = 100
    worker_poll_interval_seconds: float = 1.0
    step_max_attempts: int = 3
    sse_heartbeat_seconds: int = 15

    log_level: str = "INFO"
    log_json: bool = False

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        # В compose CORS_ORIGINS приходит строкой через запятую, а не списком.
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @field_validator("heartbeat_interval_seconds")
    @classmethod
    def _heartbeat_less_than_ttl(cls, value: int, info: object) -> int:
        # Heartbeat заметно чаще TTL: иначе lease протухает на живом воркере
        # и другой воркер перехватит инстанс посреди исполнения шага (§3.3).
        data = getattr(info, "data", {})
        ttl = data.get("lease_ttl_seconds")
        if isinstance(ttl, int) and value >= ttl:
            raise ValueError("HEARTBEAT_INTERVAL_SECONDS должен быть меньше LEASE_TTL_SECONDS")
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    # Кэш обязателен: иначе окружение перечитывается на каждый запрос.
    # В тестах — через `get_settings.cache_clear()`.
    return Settings()
