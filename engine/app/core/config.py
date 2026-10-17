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
    # воркера, и при 10 + 20 на каждый три процесса забрали бы тридцать слотов
    # из сотни. Воркер держит по соединению на цикл, поэтому больше места
    # идёт на всё число работающих воркеров.
    db_pool_size: int = 5
    db_max_overflow: int = 5
    # Не дефолт SQLAlchemy (30 с), а LEASE_TTL_SECONDS: соединение, взятое в
    # момент ожидания, могло бы протухнуть ровно тогда, когда истекает аренда.
    db_pool_timeout: int = 5
    db_pool_recycle: int = 1800
    db_echo: bool = False

    lease_ttl_seconds: int = 30
    heartbeat_interval_seconds: int = 5
    worker_max_steps_per_cycle: int = 100
    worker_poll_interval_seconds: float = 1.0
    # Сколько инстансов воркер берёт за один цикл (ADR-0023).
    #
    # По умолчанию 1, и это не осторожность, а результат замера: пачка из
    # четырёх не дала выигрыша ни на одном воркере (ниже на 7 %), ни на четырёх
    # (выше на 5 % при разбросе 8–12 % между прогонами). Разница лежит внутри
    # шума, а значит обещать её нельзя. Механизм оставлен и покрыт тестами:
    # поднять пачку — одна настройка, и решать это должен тот, кто сможет
    # измерить на своей машине.
    worker_claim_batch: int = 1
    step_max_attempts: int = 3
    # Пауза перед повтором отката. Провал компенсации означает недоступность
    # внешнего сервиса, и повтор без задержки бьёт по нему раз в секунду на
    # каждый процесс. Задержка растёт по попыткам и ограничена потолком.
    # Корни модулей, из которых разрешено брать обработчики: определение
    # сохраняется через HTTP, и без границы строка вида `os:system` вызвала бы
    # любой импортируемый код (ADR-0013).
    handler_module_allowlist: str = "app"
    compensation_retry_base_seconds: float = 5.0
    compensation_retry_max_seconds: float = 300.0
    sse_heartbeat_seconds: int = 15
    # Опрос журнала и отдача событий идут раздельно: пока событий нет, ждать
    # остаётся только heartbeat — целиком в его интервал. Поэтому опрос идёт
    # заметно чаще, а не наравне с ним.
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
