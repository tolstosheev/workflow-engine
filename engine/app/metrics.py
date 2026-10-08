"""Метрики Prometheus.

Gauge'ы читаются из БД при каждом сборе, а не ведутся в памяти: после
перезапуска воркера счётчики обнулились бы, тогда как реальная картина по
инстансам и шагам сохранилась.
"""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest
from prometheus_client.openmetrics.exposition import CONTENT_TYPE_LATEST as _OM_CONTENT_TYPE
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import CompensationStatus, InstanceStatus, StepRunStatus
from app.models.workflow import CompensationLog, StepRun, WorkflowInstance

# Отдельный реестр: глобальный содержал бы метрики дублирующих приложений
# в одном процессе тестов.
REGISTRY = CollectorRegistry()

INSTANCES_BY_STATUS = Gauge(
    "workflow_instances",
    "Инстансы по состояниям",
    ["status"],
    registry=REGISTRY,
)

INSTANCES_TOTAL = Counter(
    "workflow_instances_created_total",
    "Создано инстансов",
    registry=REGISTRY,
)

STEPS_BY_RESULT = Gauge(
    "workflow_steps",
    "Исполнения шагов по результату",
    ["step", "result"],
    registry=REGISTRY,
)

STEP_DURATION = Histogram(
    "workflow_step_duration_seconds",
    "Длительность шагов",
    ["step"],
    buckets=(0.01, 0.05, 0.1, 0.5, 1, 5, 10, 30, 60, 300),
    registry=REGISTRY,
)

COMPENSATIONS_BY_RESULT = Gauge(
    "workflow_compensations",
    "Попытки компенсаций по результату",
    ["step", "result"],
    registry=REGISTRY,
)

LEASE_ACQUIRED = Counter(
    "workflow_lease_acquired_total",
    "Успешные захваты аренды",
    registry=REGISTRY,
)

LEASE_LOST = Counter(
    "workflow_lease_lost_total",
    "Потерянные аренды: инстанс перехвачен другим воркером",
    registry=REGISTRY,
)


async def collect_from_db(db_session: AsyncSession) -> None:
    """Обновляет метрики состояния из БД."""
    status_rows = await db_session.execute(
        select(WorkflowInstance.status, func.count()).group_by(WorkflowInstance.status)
    )
    counts = dict(status_rows.all())

    # Статусы с нулём тоже публикуются: иначе на графике статус просто исчезает
    # и рост падений выглядит как «инстансы пропали».
    for status_name in [str(status) for status in InstanceStatus]:
        INSTANCES_BY_STATUS.labels(status=status_name).set(counts.get(status_name, 0))

    step_rows = await db_session.execute(
        select(StepRun.step_name, StepRun.status, func.count()).group_by(
            StepRun.step_name, StepRun.status
        )
    )
    steps: dict[tuple[str, str], int] = {}
    for step_name, step_status, count in step_rows.all():
        steps[(step_name, _step_result(step_status))] = count

    for step_name in _step_names(steps):
        STEPS_BY_RESULT.labels(step=step_name, result="success").set(
            steps.get((step_name, "success"), 0)
        )
        STEPS_BY_RESULT.labels(step=step_name, result="failure").set(
            steps.get((step_name, "failure"), 0)
        )

    compensation_rows = await db_session.execute(
        select(CompensationLog.compensation_step, CompensationLog.status, func.count()).group_by(
            CompensationLog.compensation_step, CompensationLog.status
        )
    )
    compensations: dict[tuple[str, str], int] = {}
    for step_name, compensation_status, count in compensation_rows.all():
        compensations[(step_name, _compensation_result(compensation_status))] = count

    for step_name in _step_names(compensations):
        COMPENSATIONS_BY_RESULT.labels(step=step_name, result="success").set(
            compensations.get((step_name, "success"), 0)
        )
        COMPENSATIONS_BY_RESULT.labels(step=step_name, result="failure").set(
            compensations.get((step_name, "failure"), 0)
        )


def _step_names(rows: dict[tuple[str, str], int]) -> list[str]:
    return sorted({step_name for step_name, _ in rows})


def _step_result(status: str) -> str:
    return "success" if status == StepRunStatus.COMPLETED else "failure"


def _compensation_result(status: str) -> str:
    return "success" if status == CompensationStatus.COMPLETED else "failure"


def render() -> tuple[bytes, str]:
    """Текст метрик в формате OpenMetrics."""
    return generate_latest(REGISTRY), _OM_CONTENT_TYPE
