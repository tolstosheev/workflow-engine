"""REST-эндпоинты инстансов workflow."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.errors import NotFoundError
from app.models.enums import InstanceStatus
from app.models.workflow import WorkflowDefinition, WorkflowInstance
from app.schemas.api import (
    ActionResult,
    DefinitionRef,
    EventListOut,
    EventOut,
    ForceCompleteRequest,
    InstanceCreate,
    InstanceCreated,
    InstanceListOut,
    InstanceOut,
    InstanceStateOut,
    StepListOut,
    StepRunOut,
    TimelineEntry,
    TimelineOut,
)
from app.services import instances as service

router = APIRouter(prefix="/api/instances", tags=["instances"])


async def _definition_of(
    db_session: AsyncSession, instance: WorkflowInstance
) -> WorkflowDefinition:
    definition = await db_session.get(WorkflowDefinition, instance.definition_id)
    if definition is None:  # pragma: no cover - внешний ключ защищён БД
        raise NotFoundError("определение инстанса отсутствует")
    return definition


def _to_out(instance: WorkflowInstance, definition: WorkflowDefinition) -> InstanceOut:
    return InstanceOut(
        id=instance.id,
        definition=DefinitionRef(
            id=definition.id, name=definition.name, version=definition.version
        ),
        status=InstanceStatus(instance.status),
        current_step=instance.current_step,
        input=instance.input,
        variables=instance.variables,
        error=instance.error,
        lease_owner=instance.lease_owner,
        lease_expires_at=instance.lease_expires_at,
        heartbeat_at=instance.heartbeat_at,
        created_at=instance.created_at,
        updated_at=instance.updated_at,
    )


@router.post("", response_model=InstanceCreated, status_code=status.HTTP_201_CREATED)
async def create_instance(
    payload: InstanceCreate, db_session: AsyncSession = Depends(get_db)
) -> InstanceCreated:
    instance = await service.create_instance(db_session, payload.definition_id, payload.input)
    await db_session.commit()

    definition = await _definition_of(db_session, instance)
    return InstanceCreated(
        id=instance.id,
        definition=DefinitionRef(
            id=definition.id, name=definition.name, version=definition.version
        ),
        status=InstanceStatus(instance.status),
        input=instance.input,
        created_at=instance.created_at,
    )


@router.get("", response_model=InstanceListOut)
async def list_instances(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    status_filter: str | None = Query(default=None, alias="status"),
    definition_id: uuid.UUID | None = None,
    db_session: AsyncSession = Depends(get_db),
) -> InstanceListOut:
    rows, total = await service.list_instances(
        db_session, limit, offset, status_filter, definition_id
    )
    return InstanceListOut(
        items=[_to_out(instance, definition) for instance, definition in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{instance_id}", response_model=InstanceOut)
async def get_instance(
    instance_id: uuid.UUID, db_session: AsyncSession = Depends(get_db)
) -> InstanceOut:
    instance = await service.get_instance(db_session, instance_id)
    definition = await _definition_of(db_session, instance)
    return _to_out(instance, definition)


@router.get("/{instance_id}/events", response_model=EventListOut)
async def get_events(
    instance_id: uuid.UUID,
    from_seq: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=1000),
    db_session: AsyncSession = Depends(get_db),
) -> EventListOut:
    events, total = await service.get_events(db_session, instance_id, from_seq, limit)
    return EventListOut(
        items=[
            EventOut(seq=e.seq, type=e.type, payload=e.payload, created_at=e.created_at)
            for e in events
        ],
        total=total,
    )


@router.get("/{instance_id}/steps", response_model=StepListOut)
async def get_steps(
    instance_id: uuid.UUID, db_session: AsyncSession = Depends(get_db)
) -> StepListOut:
    runs = await service.get_steps(db_session, instance_id)
    return StepListOut(items=[StepRunOut.model_validate(run) for run in runs])


@router.get("/{instance_id}/timeline", response_model=TimelineOut)
async def get_timeline(
    instance_id: uuid.UUID,
    limit: int = Query(default=500, ge=1, le=2000),
    db_session: AsyncSession = Depends(get_db),
) -> TimelineOut:
    entries = await service.get_timeline(db_session, instance_id, limit)
    return TimelineOut(items=[TimelineEntry(**entry) for entry in entries])


@router.get("/{instance_id}/state", response_model=InstanceStateOut)
async def get_state(
    instance_id: uuid.UUID, db_session: AsyncSession = Depends(get_db)
) -> InstanceStateOut:
    """Состояние по журналу — то, что увидит воркер после подхвата."""
    state = await service.rebuild_state(db_session, instance_id)
    return InstanceStateOut.model_validate(state)


@router.post("/{instance_id}/cancel", response_model=ActionResult)
async def cancel_instance(
    instance_id: uuid.UUID, db_session: AsyncSession = Depends(get_db)
) -> ActionResult:
    instance = await service.cancel_instance(db_session, instance_id)
    await db_session.commit()
    # CANCELLING, а не терминальный статус: откат выполняет воркер. Если
    # компенсировать нечего, процесс позже придёт в CANCELLED.
    return ActionResult(
        status=InstanceStatus(instance.status), message="отмена принята, выполняется откат"
    )


@router.post("/{instance_id}/complete", response_model=ActionResult)
async def force_complete(
    instance_id: uuid.UUID,
    payload: ForceCompleteRequest,
    db_session: AsyncSession = Depends(get_db),
) -> ActionResult:
    instance = await service.force_complete(db_session, instance_id, payload.reason)
    await db_session.commit()
    return ActionResult(
        status=InstanceStatus(instance.status), message="инстанс завершён без компенсаций"
    )


@router.post("/{instance_id}/compensation/retry", response_model=ActionResult)
async def retry_compensation(
    instance_id: uuid.UUID, db_session: AsyncSession = Depends(get_db)
) -> ActionResult:
    instance = await service.retry_compensation(db_session, instance_id)
    await db_session.commit()
    return ActionResult(status=InstanceStatus(instance.status), message="откат поставлен на повтор")
