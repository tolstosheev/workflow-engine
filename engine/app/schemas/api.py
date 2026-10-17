"""Схемы чтения: определения и инстансы для API.

Отдельные от схем записи: формат ответа не должен совпадать с форматом
входа, иначе добавление поля в API сломает дефинишены, уже лежащие в БД.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import EventType, InstanceStatus, StepRunStatus
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

    id: uuid.UUID
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
    # Отдельно от срока аренды: по нему видно, что воркер жив, даже если
    # аренда формально истекла в момент запроса.
    heartbeat_at: datetime | None
    # Отметка архива (ADR-0017). Процесс убран из списка, но не удалён: журнал
    # цел, а страница процесса открывается.
    archived_at: datetime | None
    # Убран ли журнал (ADR-0019). Разбор состояния для такого процесса
    # невозможен, и страница обязана сказать об этом прямо: пустая лента без
    # пометки читалась бы как «событий не было».
    journal_pruned_at: datetime | None
    created_at: datetime
    updated_at: datetime


class InstanceListOut(BaseModel):
    items: list[InstanceOut]
    total: int
    limit: int
    offset: int
    # Сколько всего архивных: без этого числа кнопка уборки не знала бы,
    # что именно она убирает.
    archived_total: int = 0


class InstanceCreate(BaseModel):
    """Тело запроса запуска инстанса."""

    model_config = ConfigDict(extra="forbid")

    definition_id: uuid.UUID
    input: dict[str, Any] = Field(default_factory=dict)


class InstanceCreated(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    definition: DefinitionRef
    status: InstanceStatus
    input: dict[str, Any]
    created_at: datetime


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    seq: int
    type: EventType
    payload: dict[str, Any]
    created_at: datetime


class EventListOut(BaseModel):
    items: list[EventOut]
    total: int


class StepRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    step_name: str
    attempt: int
    status: StepRunStatus
    output: dict[str, Any] | None
    error: str | None
    compensated: bool
    started_at: datetime
    finished_at: datetime | None


class StepListOut(BaseModel):
    items: list[StepRunOut]


class TimelineEntry(BaseModel):
    kind: Literal["step", "event"]
    step_name: str | None = None
    attempt: int | None = None
    status: str | None = None
    error: str | None = None
    compensated: bool | None = None
    seq: int | None = None
    type: str | None = None
    payload: dict[str, Any] | None = None
    created_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class TimelineOut(BaseModel):
    items: list[TimelineEntry]


class InstanceStateOut(BaseModel):
    """Состояние, восстановленное из журнала."""

    status: InstanceStatus
    current_step: str | None
    variables: dict[str, Any]
    completed_steps: list[str]
    pending_compensations: list[str]
    is_terminal: bool
    # Сколько раз шаг успешно завершился: интерфейсу нужен для показа
    # «проход 2 из 3» у цикла.
    step_iterations: dict[str, int] = Field(default_factory=dict)
    # Убран ли журнал (ADR-0019). Тогда состояние выше — это колонка
    # инстанса, а не разбор: разбирать нечего, и объявлять об этом обязан
    # сам ответ.
    journal_pruned_at: datetime | None = None


class ForceCompleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=3, max_length=500)


class ActionResult(BaseModel):
    status: InstanceStatus
    message: str


class WorkerOut(BaseModel):
    """Строка реестра воркеров (ADR-0018)."""

    model_config = ConfigDict(from_attributes=True)

    worker_id: str
    # `active` — сердце свежее, `stale` — молчит, `stopped` — остановлен штатно.
    state: Literal["active", "stale", "stopped"]
    started_at: datetime
    heartbeat_at: datetime
    stopped_at: datetime | None
    cycles_completed: int
    steps_executed: int
    lease_lost: int


class WorkerListOut(BaseModel):
    items: list[WorkerOut]
    # Через сколько секунд молчания воркер объявляется не отвечающим. Отдаётся
    # вместе со списком, иначе подпись «не отвечает» была бы загадкой.
    fresh_for_seconds: float
    # Сколько записей скрыто за переключателем «показать завершённых». Без
    # числа переключатель выглядел бы пустым, а на деле за ним были строки.
    departed_total: int = 0


class ArchiveRequest(BaseModel):
    """Тело запроса уборки завершённых процессов."""

    model_config = ConfigDict(extra="forbid")

    # Только процессы, созданные раньше срока. Без срока база растёт, а
    # удалять по возрасту мы сознательно не умеем (ADR-0017).
    before: datetime | None = None


class ArchiveResult(BaseModel):
    archived: int
    message: str


# --- Инъекция отказов ------------------------------------------------------


class ChaosStepRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instance_id: uuid.UUID | None = None
    step: str | None = None
    mode: Literal["once", "always", "timeout"] = "always"
    error: str = "сбой шага (неисправность)"


class ChaosCompensationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instance_id: uuid.UUID | None = None
    # Ссылка на обработчик обязательна: пустая означала бы «сломать любое
    # возмещение», а это не демонстрация сбоя, а порча стенда целиком.
    handler: str = Field(min_length=3, max_length=256, pattern=r"^[\w.]+:[\w]+$")
    mode: Literal["once", "always"] = "always"
    error: str = "сбой компенсации (неисправность)"


class ChaosServiceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service: Literal["inventory", "payment", "shipment"]


class ChaosLeaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instance_id: uuid.UUID
    duration_seconds: int = Field(default=40, ge=1, le=600)


class ChaosLeaseStop(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instance_id: uuid.UUID


class ChaosResult(BaseModel):
    active: list[str]
    message: str
