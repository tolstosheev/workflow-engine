"""REST-эндпоинты определений workflow."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.models.workflow import WorkflowDefinition
from app.schemas.api import DefinitionListOut, DefinitionOut, DefinitionSummary, ValidationResult
from app.schemas.definition import DefinitionCreate, DefinitionUpdate
from app.services import definitions as service

router = APIRouter(prefix="/api/definitions", tags=["definitions"])


def _to_summary(definition: WorkflowDefinition) -> DefinitionSummary:
    # definition отдаётся из БД как JSONB, поэтому список шагов берётся из
    # словаря: Pydantic здесь не помогает, поле не описано моделью.
    steps = list(definition.definition.get("steps", {}).keys())
    return DefinitionSummary(
        id=definition.id,
        name=definition.name,
        version=definition.version,
        is_active=definition.is_active,
        steps=steps,
        created_at=definition.created_at,
    )


@router.get("", response_model=DefinitionListOut)
async def list_definitions(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    is_active: bool | None = None,
    db_session: AsyncSession = Depends(get_db),
) -> DefinitionListOut:
    rows, total = await service.list_definitions(db_session, limit, offset, is_active)
    return DefinitionListOut(
        items=[_to_summary(row) for row in rows], total=total, limit=limit, offset=offset
    )


@router.post("", response_model=DefinitionOut, status_code=status.HTTP_201_CREATED)
async def create_definition(
    payload: DefinitionCreate, db_session: AsyncSession = Depends(get_db)
) -> DefinitionOut:
    definition = await service.create_definition(db_session, payload)
    await db_session.commit()
    return DefinitionOut.model_validate(definition)


@router.get("/{definition_id}", response_model=DefinitionOut)
async def get_definition(
    definition_id: uuid.UUID, db_session: AsyncSession = Depends(get_db)
) -> DefinitionOut:
    definition = await service.get_definition(db_session, definition_id)
    return DefinitionOut.model_validate(definition)


@router.put("/{definition_id}", response_model=DefinitionOut)
async def update_definition(
    definition_id: uuid.UUID,
    payload: DefinitionUpdate,
    db_session: AsyncSession = Depends(get_db),
) -> DefinitionOut:
    definition = await service.update_definition(db_session, definition_id, payload)
    await db_session.commit()
    return DefinitionOut.model_validate(definition)


@router.post("/{definition_id}/validate", response_model=ValidationResult)
async def validate_definition(
    definition_id: uuid.UUID, db_session: AsyncSession = Depends(get_db)
) -> ValidationResult:
    """Проверка без сохранения — для предпросмотра в редакторе."""
    return await service.validate_only(db_session, definition_id)


@router.post("/{definition_id}/activate", response_model=DefinitionOut)
async def activate_definition(
    definition_id: uuid.UUID, db_session: AsyncSession = Depends(get_db)
) -> DefinitionOut:
    definition = await service.get_definition(db_session, definition_id)
    await service.deactivate_all_with_name(db_session, definition.name, except_id=definition.id)
    definition = await service.set_active(db_session, definition.id, True)
    await db_session.commit()
    return DefinitionOut.model_validate(definition)


@router.post("/{definition_id}/deactivate", response_model=DefinitionOut)
async def deactivate_definition(
    definition_id: uuid.UUID, db_session: AsyncSession = Depends(get_db)
) -> DefinitionOut:
    definition = await service.set_active(db_session, definition_id, False)
    await db_session.commit()
    return DefinitionOut.model_validate(definition)
