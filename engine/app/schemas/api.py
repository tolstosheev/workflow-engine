"""Схемы чтения: определения и инстансы для API.

Отдельные от схем записи: формат ответа не должен совпадать с форматом
входа, иначе добавление поля в API сломает дефинишены, уже лежащие в БД.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import InstanceStatus
from app.schemas.definition import WorkflowDefinitionSchema


class DefinitionSummary(BaseModel):
    """Краткая карточка определения для списка."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    version: int
    is_active: bool
    steps: list[str]
    created_at: datetime


class DefinitionOut(BaseModel):
    """Полное определение."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    version: int
    is_active: bool
    definition: WorkflowDefinitionSchema
    created_at: datetime
    updated_at: datetime


class DefinitionListOut(BaseModel):
    items: list[DefinitionSummary]
    total: int
    limit: int
    offset: int


class ValidationIssue(BaseModel):
    """Одна проблема дефинишена с указанием места."""

    code: str
    message: str
    step: str | None = None


class ValidationResult(BaseModel):
    valid: bool
    errors: list[ValidationIssue] = Field(default_factory=list)
    warnings: list[ValidationIssue] = Field(default_factory=list)


class DefinitionRef(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str
    version: int


class InstanceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    definition: DefinitionRef
    status: InstanceStatus
    current_step: str | None
    input: dict[str, Any]
    variables: dict[str, Any]
    error: str | None
    lease_owner: str | None
    lease_expires_at: datetime | None
    created_at: datetime
    updated_at: datetime


class InstanceListOut(BaseModel):
    items: list[InstanceOut]
    total: int
    limit: int
    offset: int
