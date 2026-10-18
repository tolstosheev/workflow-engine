"""Точка входа HTTP-сервиса (API)."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from app.core.db import dispose_engine, get_session_factory
from app.core.errors import AppError
from app.core.logging import configure_logging
from app.routers.definitions import router as definitions_router
from app.routers.demo import router as demo_router
from app.routers.instances import router as instances_router
from app.routers.registry import router as registry_router
from app.routers.service import router as service_router
from app.routers.stream import router as stream_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    # Проверка соединения при старте: воркер без БД должен падать сразу,
    # а не на первом же цикле опроса.
    async with get_session_factory()() as session:
        from sqlalchemy import text

        await session.execute(text("SELECT 1"))
    logger.info("api.started", extra={"env": get_settings().app_env})
    try:
        yield
    finally:
        await dispose_engine()
        logger.info("api.stopped")


def api_version() -> str:
    """Версия из метаданных пакета, а без пакета — из `pyproject.toml`.

    Обязаться со списка пакетов, а не копировать: версия в документации расходилась на
    пол-версии, и в `package.json` она отставала бы на два релиза.

    Запасной путь нужен потому, что образ Docker ставит зависимости поимённо и
    копирует исходники, но сам проект не устанавливает: в контейнере метаданных
    нет, и стенд отдавал `0.0.0` при любой версии, то есть релиз был помечен, а
    на `/docs` значилось другое. Файл читается тот же, что объявляет версию, —
    второго источника истины не появляется.
    """
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("workflow-engine")
    except PackageNotFoundError:
        return _version_from_pyproject()


def _version_from_pyproject() -> str:
    """Версия из `pyproject.toml` рядом с пакетом.

    Отсутствие файла — не повод отдавать ноль: ноль в поле версии читается как
    «сборка без номера», и отличить его от настоящей версии нельзя.
    """
    import tomllib
    from pathlib import Path

    # `app/main.py` → `engine/app` → `engine`, где и лежит `pyproject.toml`.
    # В образе тот же путь: исходники копируются в `/srv` вместе с файлом.
    path = Path(__file__).resolve().parents[1] / "pyproject.toml"
    if not path.exists():
        logger.warning("version.pyproject_missing", extra={"path": str(path)})
        return "unknown"
    return str(tomllib.loads(path.read_text(encoding="utf-8"))["project"]["version"])


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(
        title="Workflow Engine",
        version=api_version(),
        description=(
            "Распределённый движок выполнения workflow с поддержкой долгих "
            "транзакций и компенсаций (Saga)."
        ),
        lifespan=lifespan,
    )

    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @application.exception_handler(AppError)
    async def _app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})

    @application.get("/health", tags=["service"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    application.include_router(demo_router)
    application.include_router(definitions_router)
    application.include_router(instances_router)
    application.include_router(registry_router)
    application.include_router(stream_router)
    application.include_router(service_router)

    return application


app = create_app()
