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
from app.handlers.registry import is_handler_importable
from app.models import chaos
from app.models.workflow import WorkflowInstance
from app.schemas.api import (
    ChaosCompensationRequest,
    ChaosLeaseRequest,
    ChaosLeaseStop,
    ChaosResult,
    ChaosServiceRequest,
    ChaosStepRequest,
)

router = APIRouter(tags=["service"])


@router.get("/metrics")
async def prometheus_metrics(db_session: AsyncSession = Depends(get_db)) -> Response:
    """Метрики в экспозиционном формате Prometheus."""
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

    Правило сохраняется в БД: воркер работает в другом процессе и должен
    увидеть инъекцию, зарегистрированную через HTTP.
    """
    if payload.instance_id is not None and not await chaos.existing_instance(
        db_session, payload.instance_id
    ):
        raise NotFoundError(f"процесс {payload.instance_id} не найден")

    await chaos.add_rule(db_session, payload.instance_id, payload.step, payload.mode, payload.error)
    await db_session.commit()
    return ChaosResult(
        active=[f"step:{payload.step or '*'}:{payload.mode}"],
        message="неисправность шага зарегистрирована",
    )


@router.post("/api/chaos/fail-service", response_model=ChaosResult)
async def fail_service(
    payload: ChaosServiceRequest, db_session: AsyncSession = Depends(get_db)
) -> ChaosResult:
    """Помечает сервис предметной области недоступным.

    Шаг сервиса определяется по имени шага: обработчики демо-области живут в
    `demo/handlers.py`, и отдельный реестр имён вводил бы второе место, где
    можно ошибиться.
    """
    service_steps = {
        "inventory": "reserve_inventory",
        "payment": "charge_payment",
        "shipment": "create_shipment",
    }
    step_name = service_steps[payload.service]
    await chaos.add_rule(db_session, None, step_name, "always", f"{payload.service} недоступен")
    await db_session.commit()
    return ChaosResult(active=[f"service:{payload.service}"], message="сервис помечен недоступным")


@router.post("/api/chaos/fail-compensation", response_model=ChaosResult)
async def fail_compensation(
    payload: ChaosCompensationRequest, db_session: AsyncSession = Depends(get_db)
) -> ChaosResult:
    """Помечает обработчик компенсации отказывающим (ADR-0015).

    Правило адресовано обработчику отката, а не шагу: шаг уже выполнен, и
    правило по его имени сработало бы на прямом шаге, а не на возврате.
    """
    if payload.instance_id is not None and not await chaos.existing_instance(
        db_session, payload.instance_id
    ):
        raise NotFoundError(f"процесс не найден: {payload.instance_id}")

    # Обработчик резолвится сразу: правило, которое никогда не сработает,
    # выглядит на панели как работающая инъекция, и оператор будет искать,
    # почему откат не падает.
    if not is_handler_importable(payload.handler):
        raise NotFoundError(f"обработчик не найден: {payload.handler}")

    await chaos.add_rule_for_handler(
        db_session, payload.instance_id, payload.handler, payload.mode, payload.error
    )
    await db_session.commit()
    return ChaosResult(
        active=[f"compensation:{payload.handler}:{payload.mode}"],
        message="неисправность компенсации зарегистрирована",
    )


@router.get("/api/chaos", response_model=ChaosResult)
async def list_chaos(db_session: AsyncSession = Depends(get_db)) -> ChaosResult:
    """Активные инъекции — чтобы панель в интерфейсе показывала их состояние."""
    rules = await chaos.list_rules(db_session)
    return ChaosResult(
        active=[rule.label() for rule in rules],
        message=f"активных неисправностей: {len(rules)}",
    )


@router.post("/api/chaos/stop-lease", response_model=ChaosResult)
async def stop_lease(
    payload: ChaosLeaseRequest, db_session: AsyncSession = Depends(get_db)
) -> ChaosResult:
    """Останавливает продление аренды — имитация зависшего воркера."""
    instance = await db_session.get(WorkflowInstance, payload.instance_id)
    if instance is None:
        raise NotFoundError(f"процесс {payload.instance_id} не найден")

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
        message=f"продление закрепления остановлено на {payload.duration_seconds} с",
    )


@router.post("/api/chaos/resume-lease", response_model=ChaosResult)
async def resume_lease(
    payload: ChaosLeaseStop, db_session: AsyncSession = Depends(get_db)
) -> ChaosResult:
    instance = await db_session.get(WorkflowInstance, payload.instance_id)
    if instance is None:
        raise NotFoundError(f"процесс {payload.instance_id} не найден")

    # Продление отключают с намерением сдвинуть heartbeat в прошлое:
    # инстанс проверен, и без такой проверки сдвигать было бы вслепую.
    await db_session.execute(
        update(WorkflowInstance)
        .where(WorkflowInstance.id == payload.instance_id)
        .values(heartbeat_at=datetime.now(UTC))
    )
    await db_session.commit()
    return ChaosResult(active=[], message="продление закрепления возобновлено")


@router.delete("/api/chaos", response_model=ChaosResult)
async def clear_chaos(db_session: AsyncSession = Depends(get_db)) -> ChaosResult:
    removed = await chaos.clear_rules(db_session)
    await db_session.commit()
    return ChaosResult(active=[], message=f"снято неисправностей: {removed}")


@router.get("/api/instances/{instance_id}/exists")
async def instance_exists(
    instance_id: uuid.UUID, db_session: AsyncSession = Depends(get_db)
) -> dict[str, bool]:
    """Быстрая проверка существования — для диагностики без полной выборки."""
    found = await db_session.scalar(
        select(WorkflowInstance.id).where(WorkflowInstance.id == instance_id)
    )
    return {"exists": found is not None}
