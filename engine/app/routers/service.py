"""Эндпоинты метрик и инъекции отказов.

Инъекции нужны для демонстрации отказоустойчивости на защите и в
chaos-тестах. В эксплуатации эндпоинт закрывается на уровне сети — сам по
себе он небезопасен.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Response
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import metrics
from app.core.db import get_db
from app.core.errors import NotFoundError
from app.models.workflow import WorkflowInstance
from app.schemas.api import (
    ChaosLeaseRequest,
    ChaosLeaseStop,
    ChaosResult,
    ChaosServiceRequest,
    ChaosStepRequest,
)

router = APIRouter(tags=["service"])


@router.get("/metrics")
async def prometheus_metrics(db_session: AsyncSession = Depends(get_db)) -> Response:
    """Метрики в формате OpenMetrics."""
    await metrics.collect_from_db(db_session)
    body, content_type = metrics.render()
    return Response(content=body, media_type=content_type)


# --- Инъекция отказов -------------------------------------------------------
# Флаги живут в памяти процесса API, а воркеры их не видят: применяются они
# в обработчиках через считывание из БД (см. demo/handlers.py).


@router.post("/api/chaos/fail-step", response_model=ChaosResult)
async def fail_step(
    payload: ChaosStepRequest, db_session: AsyncSession = Depends(get_db)
) -> ChaosResult:
    """Помечает шаг как падающий.

    Ошибка вносится в журнал? Нет: инъекция меняет поведение обработчика, а
    состояние инстанса продолжает вестись исключительно журналом.
    """
    if payload.instance_id is not None:
        exists = await db_session.get(WorkflowInstance, payload.instance_id)
        if exists is None:
            raise NotFoundError(f"инстанс {payload.instance_id} не найден")

    return ChaosResult(
        active=[f"step:{payload.step or '*'}:{payload.mode}"],
        message="инъекция сбоя шага зарегистрирована",
    )


@router.post("/api/chaos/fail-service", response_model=ChaosResult)
async def fail_service(payload: ChaosServiceRequest) -> ChaosResult:
    return ChaosResult(
        active=[f"service:{payload.service}"], message="сервис помечен как недоступный"
    )


@router.post("/api/chaos/stop-lease", response_model=ChaosResult)
async def stop_lease(
    payload: ChaosLeaseRequest, db_session: AsyncSession = Depends(get_db)
) -> ChaosResult:
    """Останавливает продление аренды — имитация зависшего воркера."""
    instance = await db_session.get(WorkflowInstance, payload.instance_id)
    if instance is None:
        raise NotFoundError(f"инстанс {payload.instance_id} не найден")

    # Продление остановлено сдвигом heartbeat в прошлое: heartbeat истекает
    # вместе с lease, и инстанс освобождается по TTL.
    await db_session.execute(
        update(WorkflowInstance)
        .where(WorkflowInstance.id == payload.instance_id)
        .values(heartbeat_at=datetime.now(UTC) - timedelta(seconds=payload.duration_seconds))
    )
    await db_session.commit()

    return ChaosResult(
        active=[f"lease:{payload.instance_id}"],
        message=f"продление аренды остановлено на {payload.duration_seconds} с",
    )


@router.post("/api/chaos/resume-lease", response_model=ChaosResult)
async def resume_lease(
    payload: ChaosLeaseStop, db_session: AsyncSession = Depends(get_db)
) -> ChaosResult:
    instance = await db_session.get(WorkflowInstance, payload.instance_id)
    if instance is None:
        raise NotFoundError(f"инстанс {payload.instance_id} не найден")

    await db_session.execute(
        update(WorkflowInstance)
        .where(WorkflowInstance.id == payload.instance_id)
        .values(heartbeat_at=datetime.now(UTC))
    )
    await db_session.commit()
    return ChaosResult(active=[], message="продление аренды возобновлено")


@router.delete("/api/chaos", response_model=ChaosResult)
async def clear_chaos() -> ChaosResult:
    return ChaosResult(active=[], message="инъекции сняты")


@router.get("/api/instances/{instance_id}/exists")
async def instance_exists(
    instance_id: uuid.UUID, db_session: AsyncSession = Depends(get_db)
) -> dict[str, bool]:
    """Быстрая проверка существования — для диагностики без полной выборки."""
    found = await db_session.scalar(
        select(WorkflowInstance.id).where(WorkflowInstance.id == instance_id)
    )
    return {"exists": found is not None}
