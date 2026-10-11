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

    # Строка, а не список: pydantic-settings разбирает сложные поля как JSON
    # до вызова валидаторов, поэтому CORS_ORIGINS="http://a,http://b" из
    # docker-compose упал бы на разборе, не дойдя до field_validator.
    cors_origins: str = "http://localhost:5173"

    database_url: str = "postgresql+asyncpg://workflow:workflow@localhost:5434/workflow_engine"
    sync_database_url: str = "postgresql+psycopg://workflow:workflow@localhost:5434/workflow_engine"
    # Пул рассчитывается на процесс, а не на сервис: compose поднимает API и два
    # воркера, и при 10 + 20 на каждый три процесса забиравали бы трить из 100
    # слот. Один из воркеров обрабатывает по одному инстансу за циклам,
    # поэтому больше места идёт для всех одновременных воркеров.
    db_pool_size: int = 5
    db_max_overflow: int = 5
    # Не дефолт SQLAlchemy (30 с) совпадаёт с LEASE_TTL_SECONDS: воркер, заблочивший
    # соединением, птанут аренду ровно в момент истечения аренды.
    db_pool_timeout: int = 5
    db_pool_recycle: int = 1800
    db_echo: bool = False

    lease_ttl_seconds: int = 30
    heartbeat_interval_seconds: int = 5
    worker_max_steps_per_cycle: int = 100
    worker_poll_interval_seconds: float = 1.0
    step_max_attempts: int = 3
    sse_heartbeat_seconds: int = 15
    # Опрос журнала строики событий: латентность обязана дожжна событием отдаваться
    # быть на всю время heartbeat. Раньше идёт веком раз в секунду.
    sse_poll_seconds: float = 1.0

    log_level: str = "INFO"
    log_json: bool = False

    @property
    def cors_origin_list(self) -> list[str]:
        """Origins для CORS: строка через запятую разбирается в список."""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

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
