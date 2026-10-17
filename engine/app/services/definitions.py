"""Сервис работы с определениями workflow."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, DefinitionInvalidError, NotFoundError
from app.models.enums import InstanceStatus
from app.models.workflow import WorkflowDefinition
from app.schemas.api import ValidationResult
from app.schemas.definition import DefinitionCreate, DefinitionUpdate, WorkflowDefinitionSchema
from app.services.definition_validation import validate_definition


async def create_definition(
    db_session: AsyncSession, payload: DefinitionCreate
) -> WorkflowDefinition:
    """Создаёт определение после полной валидации.

    Проверка до записи: невалидный дефинишен в базе означает инстанс, который
    упадёт на середине исполнения.
    """
    result = validate_definition(payload.definition)
    if not result.valid:
        raise DefinitionInvalidError(_format_errors(result))

    existing = await db_session.scalar(
        select(WorkflowDefinition.id).where(
            WorkflowDefinition.name == payload.name,
            WorkflowDefinition.version == payload.version,
        )
    )
    if existing is not None:
        raise ConflictError(f"определение {payload.name!r} версии {payload.version} уже существует")

    definition = WorkflowDefinition(
        name=payload.name,
        version=payload.version,
        definition=payload.definition.model_dump(mode="json"),
        is_active=payload.is_active,
    )
    db_session.add(definition)
    await db_session.flush()
    return definition


async def get_definition(db_session: AsyncSession, definition_id: uuid.UUID) -> WorkflowDefinition:
    definition = await db_session.get(WorkflowDefinition, definition_id)
    if definition is None:
        raise NotFoundError(f"определение {definition_id} не найдено")
    return definition


async def list_definitions(
    db_session: AsyncSession,
    limit: int,
    offset: int,
    is_active: bool | None,
) -> tuple[list[WorkflowDefinition], int]:
    filters = []
    if is_active is not None:
        filters.append(WorkflowDefinition.is_active.is_(is_active))

    query = (
        select(WorkflowDefinition).where(*filters).order_by(WorkflowDefinition.created_at.desc())
    )
    rows = (await db_session.scalars(query.limit(limit).offset(offset))).all()

    total = await db_session.scalar(
        select(func.count()).select_from(WorkflowDefinition).where(*filters)
    )
    return list(rows), total or 0


async def update_definition(
    db_session: AsyncSession, definition_id: uuid.UUID, payload: DefinitionUpdate
) -> WorkflowDefinition:
    definition = await get_definition(db_session, definition_id)

    result = validate_definition(payload.definition)
    if not result.valid:
        raise DefinitionInvalidError(_format_errors(result))

    definition.definition = payload.definition.model_dump(mode="json")
    if payload.is_active is not None:
        definition.is_active = payload.is_active
    await db_session.flush()
    return definition


async def validate_only(db_session: AsyncSession, definition_id: uuid.UUID) -> ValidationResult:
    """Проверяет сохранённый дефинишен без изменений."""
    definition = await get_definition(db_session, definition_id)
    return validate_definition(WorkflowDefinitionSchema.model_validate(definition.definition))


async def set_active(
    db_session: AsyncSession, definition_id: uuid.UUID, is_active: bool
) -> WorkflowDefinition:
    definition = await get_definition(db_session, definition_id)
    definition.is_active = is_active
    await db_session.flush()
    return definition


async def deactivate_all_with_name(
    db_session: AsyncSession, name: str, except_id: uuid.UUID | None = None
) -> None:
    """Оставляет активной только одну версию определения.

    Две активные версии одного имени означают неоднозначность при запуске:
    инстанс должен ссылаться на конкретную версию, иначе пришлось бы
    разрешать её в прикладном коде.

    Объекты обновляются поштучно, а не одним UPDATE: массовый update несовместим
    с `eager_defaults` и оставил бы объекты с просроченными полями.
    """
    query = select(WorkflowDefinition).where(
        WorkflowDefinition.name == name,
        WorkflowDefinition.is_active.is_(True),
    )
    if except_id is not None:
        query = query.where(WorkflowDefinition.id != except_id)

    for definition in (await db_session.scalars(query)).all():
        definition.is_active = False
    await db_session.flush()


async def ensure_runnable(db_session: AsyncSession, definition_id: uuid.UUID) -> WorkflowDefinition:
    """Проверяет, что определение можно запустить."""
    definition = await get_definition(db_session, definition_id)
    if not definition.is_active:
        raise ConflictError(
            f"определение {definition.name!r} версии {definition.version} неактивно"
        )

    result = validate_definition(WorkflowDefinitionSchema.model_validate(definition.definition))
    if not result.valid:
        raise DefinitionInvalidError(_format_errors(result))
    return definition


def _format_errors(result: ValidationResult) -> str:
    """Собирает все ошибки в одно сообщение.

    Показывается только первая ошибка, пользователь будет править по кругу.
    """
    parts = [
        f"{issue.step}: {issue.message}" if issue.step else issue.message for issue in result.errors
    ]
    return "схема процесса не прошла проверку:\n- " + "\n- ".join(parts)


__all__ = [
    "InstanceStatus",
    "create_definition",
    "deactivate_all_with_name",
    "ensure_runnable",
    "get_definition",
    "list_definitions",
    "set_active",
    "update_definition",
    "validate_only",
]
