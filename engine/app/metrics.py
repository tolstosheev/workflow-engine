"""Метрики Prometheus.

Gauge'ы читаются из БД при каждом сборе, а не ведутся в памяти: после
перезапуска воркера счётчики обнулились бы, тогда как реальная картина по
инстансам и шагам сохранилась.
"""

from __future__ import annotations

import os
from typing import Literal

from prometheus_client import CONTENT_TYPE_LATEST as _PROM_CONTENT_TYPE
from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    multiprocess,
)
from prometheus_client.registry import Collector
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import CompensationStatus, InstanceStatus, StepRunStatus
from app.models.workflow import CompensationLog, StepRun, WorkflowDefinition, WorkflowInstance

# Отдельный реестр: глобальный содержал бы метрики дублирующих приложений
# в одном процессе тестов.
REGISTRY = CollectorRegistry()

# Воркер и API — разные процессы, и записанное воркером оставалось бы в самом
# воркере: единственная точка экспорта — `/metrics` в API. Поэтому при заданном
# `PROMETHEUS_MULTIPROC_DIR` значения пишутся в общий каталог, а отдача
# собирает их из файлов (ADR-0012).
#
# Собирать файлы в этот же реестр нельзя: тогда отдача выводила бы и файлы, и
# собственные значения процесса API, и одна метрика выходила бы двумя рядами.
IS_MULTIPROCESS = bool(os.getenv("PROMETHEUS_MULTIPROC_DIR"))

# Метрики, пересчитываемые из БД. Все процессы считают одно и то же по общей
# базе, поэтому в multiprocess-режиме режим по умолчанию не годится: gauge
# разворачивается в серии с меткой `pid`, и запрос
# `workflow_instances{status="RUNNING"}` перестаёт работать — приходит только
# один ряд из нескольких.
_DB_GAUGE_MODE: Literal["mostrecent"] = "mostrecent"

INSTANCES_BY_STATUS = Gauge(
    "workflow_instances",
    "Процессы по состояниям",
    ["status"],
    registry=REGISTRY,
    multiprocess_mode=_DB_GAUGE_MODE,
)

INSTANCES_TOTAL = Counter(
    "workflow_instances_created_total",
    "Создано процессов",
    registry=REGISTRY,
)

STEPS_BY_RESULT = Gauge(
    "workflow_steps",
    "Исполнения шагов по результату",
    ["step", "result"],
    registry=REGISTRY,
    multiprocess_mode=_DB_GAUGE_MODE,
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
    multiprocess_mode=_DB_GAUGE_MODE,
)

LEASE_ACQUIRED = Counter(
    "workflow_lease_acquired_total",
    "Успешные захваты закрепления",
    registry=REGISTRY,
)

LEASE_LOST = Counter(
    "workflow_lease_lost_total",
    "Потерянные закрепления: процесс перехвачен другим исполнителем",
    registry=REGISTRY,
)

#: Ряды, которые уже публиковались хотя бы раз. Gauge без нового значения
#: показывает прежнее, поэтому исчезнувшие ряды обнуляются по этому списку.
_PUBLISHED_STEPS: set[str] = set()
_PUBLISHED_COMPENSATIONS: set[str] = set()


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

    # Шаги объявлены в схемах, а не в журнале: их имена известны и тогда, когда ни
    # один прогон не состоялся. Иначе метрика начинает с нуля на каждом пустом
    # стенде, а имя из схемы остаётся опубликованным в общем файле с прежним
    # значением — и таблица показывает прогоны, которых не было.
    steps_by_definition, compensations_by_definition = await _declared_names(db_session)
    for step_name in _with_previous(_step_names(steps), _PUBLISHED_STEPS) | steps_by_definition:
        STEPS_BY_RESULT.labels(step=step_name, result="success").set(
            steps.get((step_name, "success"), 0)
        )
        STEPS_BY_RESULT.labels(step=step_name, result="failure").set(
            steps.get((step_name, "failure"), 0)
        )
    _PUBLISHED_STEPS.clear()
    _PUBLISHED_STEPS.update(_step_names(steps))

    compensation_rows = await db_session.execute(
        select(CompensationLog.compensation_step, CompensationLog.status, func.count()).group_by(
            CompensationLog.compensation_step, CompensationLog.status
        )
    )
    compensations: dict[tuple[str, str], int] = {}
    for step_name, compensation_status, count in compensation_rows.all():
        compensations[(step_name, _compensation_result(compensation_status))] = count

    for step_name in (
        _with_previous(_step_names(compensations), _PUBLISHED_COMPENSATIONS)
        | compensations_by_definition
    ):
        COMPENSATIONS_BY_RESULT.labels(step=step_name, result="success").set(
            compensations.get((step_name, "success"), 0)
        )
        COMPENSATIONS_BY_RESULT.labels(step=step_name, result="failure").set(
            compensations.get((step_name, "failure"), 0)
        )
    _PUBLISHED_COMPENSATIONS.clear()
    _PUBLISHED_COMPENSATIONS.update(_step_names(compensations))


async def _declared_names(db_session: AsyncSession) -> tuple[set[str], set[str]]:
    """Имена шагов и обработчиков компенсации из всех схем.

    Читается JSON, а не отдельная таблица: имена живут внутри схемы, и их
    отдельная копия разошлась бы с ней при первой же правке определения.
    """
    rows = await db_session.execute(select(WorkflowDefinition.definition))
    steps: set[str] = set()
    compensations: set[str] = set()
    for (definition,) in rows.all():
        declared = definition.get("steps") if isinstance(definition, dict) else None
        if not isinstance(declared, dict):
            continue
        steps.update(str(name) for name in declared)
        for step in declared.values():
            handler = step.get("compensation") if isinstance(step, dict) else None
            if isinstance(handler, str) and handler:
                compensations.add(handler)
    return steps, compensations


def _with_previous(current: list[str], previous: set[str]) -> set[str]:
    """Имена, для которых значение обязано быть выставлено: нынешние и прошлые.

    Gauge, которому не выставили значение, не показывает ноль — он продолжает
    показывать последнее записанное. Пустая `step_runs` не вызывает ни одного
    `set`, и ряд про выполнявшиеся шаги остаётся жить после того, как шагов не
    осталось: на стенде страница состояния показывала «14 успешных списаний» при
    таблице без единой строки. Хуже нулей это потому, что выглядит как работающая
    система.

    Состояние живёт в процессе, а пересчитывает из БД только API — единственный,
    кто отдаёт `/metrics`.
    """
    return set(current) | previous


def _step_names(rows: dict[tuple[str, str], int]) -> list[str]:
    return sorted({step_name for step_name, _ in rows})


def _step_result(status: str) -> str:
    return "success" if status == StepRunStatus.COMPLETED else "failure"


def _compensation_result(status: str) -> str:
    return "success" if status == CompensationStatus.COMPLETED else "failure"


def _collector() -> Collector:
    """Отдаёт ли данные для одного из файлов общего каталога."""
    if not IS_MULTIPROCESS:
        return REGISTRY
    return multiprocess.MultiProcessCollector(CollectorRegistry())


def render() -> tuple[bytes, str]:
    """Текст метрик в экспозиционном формате Prometheus.

    Формат и заголовок должны совпадать: `generate_latest` пишет классический
    текст (`text/plain; version=0.0.4`), а не OpenMetrics. Раньше объявлялся
    заголовок верный для OpenMetrics при теле в формате Prometheus, и строгий
    скрайпер отверг бы весь ответ целиком.
    """
    return generate_latest(_collector()), _PROM_CONTENT_TYPE
