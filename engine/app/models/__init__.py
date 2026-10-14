"""Реестр моделей.

Импортируется из `alembic/env.py`, поэтому все таблицы должны быть видны
здесь: пустой реестр означал бы пустую автогенерацию миграции.
"""

from __future__ import annotations

from app.models.base import Base, TimestampMixin
from app.models.chaos import ChaosRule
from app.models.enums import (
    ACTIVE_STATUSES,
    TERMINAL_STATUSES,
    CompensationStatus,
    EventType,
    InstanceStatus,
    StepRunStatus,
)
from app.models.registry import Worker
from app.models.workflow import (
    CompensationLog,
    DemoInventoryItem,
    DemoPayment,
    DemoShipment,
    ProcessedOperation,
    StepRun,
    Timer,
    WorkflowDefinition,
    WorkflowEvent,
    WorkflowInstance,
)

__all__ = [
    "ACTIVE_STATUSES",
    "TERMINAL_STATUSES",
    "Base",
    "ChaosRule",
    "CompensationLog",
    "CompensationStatus",
    "DemoInventoryItem",
    "DemoPayment",
    "DemoShipment",
    "EventType",
    "InstanceStatus",
    "ProcessedOperation",
    "StepRun",
    "StepRunStatus",
    "Timer",
    "TimestampMixin",
    "Worker",
    "WorkflowDefinition",
    "WorkflowEvent",
    "WorkflowInstance",
]
