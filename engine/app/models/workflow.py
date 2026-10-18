"""Таблицы ядра: инстансы, журнал событий, шаги, таймеры, компенсации."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import UUID_PK_DEFAULT, Base, TimestampMixin
from app.models.enums import CompensationStatus, InstanceStatus, StepRunStatus


class WorkflowDefinition(Base, TimestampMixin):
    """Дефинишены хранятся данными, а не кодом: новый процесс не требует
    изменения движка."""

    __tablename__ = "workflow_definitions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=UUID_PK_DEFAULT
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    definition: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        # Одна версия на имя: две версии одного имени означали бы неоднозначность
        # при запуске, и её пришлось бы разрешать в прикладном коде.
        Index("uq_workflow_definitions_name_version", "name", "version", unique=True),
        CheckConstraint("version >= 1", name="version_positive"),
    )

    instances: Mapped[list[WorkflowInstance]] = relationship(back_populates="definition")


class WorkflowInstance(Base, TimestampMixin):
    """Экземпляр исполнения дефинишена.

    `status` и `variables` — производные от журнала событий: изменяются только
    вместе с записью события в одной транзакции (§3.1 AGENTS.md).
    """

    __tablename__ = "workflow_instances"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=UUID_PK_DEFAULT
    )
    definition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workflow_definitions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=InstanceStatus.PENDING,
        server_default=InstanceStatus.PENDING,
    )
    input: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    variables: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    current_step: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Три колонки lease — механизм взаимноисключающего исполнения (§3.3).
    # heartbeat_at отделён от lease_expires_at: по нему видно, что воркер
    # жив, даже если аренда формально истекает в момент запроса.
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Раннее время следующей попытки отката. Провал компенсации означает, что
    # внешний сервис недоступен: без паузы воркер бил бы по нему раз в секунду
    # на каждый процесс, и отказ лечил бы сам себя лавиной повторов.
    compensation_not_before: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Отметка «убран из списка» (ADR-0017). Это не удаление: журнал остаётся
    # целым, а процесс по-прежнему открывается. Архивировать можно только
    # терминальные — незакрытый инцидент из вида убирать нельзя.
    # Момент уборки журнала (ADR-0019). Разбор событий после него невозможен,
    # поэтому пометка обязана быть в самой строке: интерфейс и разбор инцидента
    # читают её, а не догадываются по пустому списку событий.
    journal_pruned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    definition: Mapped[WorkflowDefinition] = relationship(back_populates="instances")
    events: Mapped[list[WorkflowEvent]] = relationship(
        back_populates="instance",
        # Журнал не грузится вместе с инстансом: у долгого процесса он
        # достигает тысяч строк, а нужен он по прямому запросу.
        lazy="raise",
    )

    __table_args__ = (
        Index("ix_workflow_instances_lease", "lease_expires_at"),
        # Частичный индекс: опрос воркера смотрит только активные инстансы,
        # а накопленные терминальные не должны попадать в его выборку.
        Index(
            "ix_workflow_instances_active",
            "lease_expires_at",
            postgresql_where="status IN ('PENDING','RUNNING','WAITING','CANCELLING','COMPENSATING','COMPENSATION_FAILED')",
        ),
        Index("ix_workflow_instances_status_created", "status", "created_at"),
        # Фильтр по определению — внешний ключ, а внешние ключи индекса не
        # получают: без него отбор по определению сканировал бы таблицу.
        Index("ix_workflow_instances_definition", "definition_id"),
    )


class WorkflowEvent(Base):
    """Append-only журнал — единственный источник истины об инстансе.

    `UPDATE` и `DELETE` запрещены: событие — факт о произошедшем, исправить
    факт нельзя, можно записать следующее.
    """

    __tablename__ = "workflow_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=UUID_PK_DEFAULT
    )
    instance_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workflow_instances.id", ondelete="RESTRICT"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    type: Mapped[str] = mapped_column(String(48), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    instance: Mapped[WorkflowInstance] = relationship(back_populates="events")

    __table_args__ = (
        # Монотонность без пропусков: конкурентная запись с тем же seq
        # откатывается, а не создаёт вторую ветку истории.
        Index("uq_workflow_events_instance_seq", "instance_id", "seq", unique=True),
        Index("ix_workflow_events_instance_created", "instance_id", "created_at"),
        Index("ix_workflow_events_type_created", "type", "created_at"),
        # Поток SSE сортирует события по времени, а индексы по инстансу и по
        # типу начинаются не с него: без такого индекса планировщик читает всю
        # таблицу и сортирует её на стороне СУБД.
        Index("ix_workflow_events_created", "created_at"),
        CheckConstraint("seq >= 0", name="seq_non_negative"),
    )


class StepRun(Base):
    """Инженерные детали исполнения шага: попытки, тайминги, результат.

    Журнал отвечает на вопрос «что произошло», эта таблица — «сколько заняло
    и с каким результатом».
    """

    __tablename__ = "step_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=UUID_PK_DEFAULT
    )
    instance_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workflow_instances.id", ondelete="RESTRICT"), nullable=False
    )
    step_name: Mapped[str] = mapped_column(String(128), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=StepRunStatus.PENDING,
        server_default=StepRunStatus.PENDING,
    )
    input: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    output: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    compensated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    compensation: Mapped[CompensationLog | None] = relationship(back_populates="step_run")

    __table_args__ = (
        CheckConstraint("attempt >= 1", name="attempt_positive"),
        Index("ix_step_runs_instance_step", "instance_id", "step_name"),
        # Обслуживает выборку шагов к компенсации в обратном порядке.
        Index("ix_step_runs_instance_status_finished", "instance_id", "status", "finished_at"),
    )


class Timer(Base):
    """Durable-ожидание: переживает рестарт воркера, в отличие от sleep."""

    __tablename__ = "timers"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=UUID_PK_DEFAULT
    )
    instance_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workflow_instances.id", ondelete="RESTRICT"), nullable=False
    )
    step_name: Mapped[str] = mapped_column(String(128), nullable=False)
    # Вид ожидания. На одном шаге могут стоять и ожидание, и срок паузы, и без
    # различия `planned_for` принял бы одно за другое: шаг не наступил бы
    # никогда, а срок паузы отменил бы не тот процесс (ADR-0016).
    kind: Mapped[str] = mapped_column(
        String(32), nullable=False, default="step", server_default="step"
    )
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    fired: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_timers_due", "fired", "due_at"),
        # `planned_for` ищет по инстансу и шагу на каждом цикле воркера, а не
        # по сроку ожидания: без индекса на этой паре воркер сканировал бы
        # всю таблицу таймеров.
        Index("ix_timers_instance_step", "instance_id", "step_name"),
    )


class ProcessedOperation(Base):
    """Учёт применённых операций — механизм идемпотентности (§3.4).

    `UNIQUE (instance_id, op_key)` делает повторное применение эффекта
    физически невозможным, но требует от обработчика проверки ключа *до*
    действия.
    """

    __tablename__ = "processed_operations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=UUID_PK_DEFAULT
    )
    instance_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workflow_instances.id", ondelete="RESTRICT"), nullable=False
    )
    op_key: Mapped[str] = mapped_column(String(256), nullable=False)
    step_name: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (Index("uq_processed_operations_key", "instance_id", "op_key", unique=True),)


class CompensationLog(Base):
    """Попытки компенсаций отдельно от шагов: компенсация — самостоятельная
    операция со своей попыткой, временем и результатом."""

    __tablename__ = "compensation_log"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=UUID_PK_DEFAULT
    )
    instance_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workflow_instances.id", ondelete="RESTRICT"), nullable=False
    )
    step_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("step_runs.id", ondelete="RESTRICT"), nullable=False
    )
    compensation_step: Mapped[str] = mapped_column(String(256), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=CompensationStatus.RUNNING,
        server_default=CompensationStatus.RUNNING,
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    step_run: Mapped[StepRun] = relationship(back_populates="compensation")

    __table_args__ = (
        CheckConstraint("attempt >= 1", name="attempt_positive"),
        Index("ix_compensation_log_instance_status", "instance_id", "status"),
        # Разрешён не более одной активной компенсации на выполнение шага:
        # две параллельные отмены одного резерва — это двойной возврат.
        Index(
            "uq_compensation_log_step_run",
            "step_run_id",
            unique=True,
            postgresql_where="finished_at IS NULL",
        ),
        # Индекс без условия: партиальный уникальный действовал бы только на
        # невыполненных запусках, а план отката ищет прогоны по всем записям.
        Index("ix_compensation_log_step_run", "step_run_id"),
    )


class DemoInventoryItem(Base, TimestampMixin):
    """Остаток товара на складе — предметная область, не часть движка."""

    __tablename__ = "demo_inventory"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=UUID_PK_DEFAULT
    )
    sku: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    available: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reserved: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


__table_args__ = (
    CheckConstraint("available >= 0", name="available_non_negative"),
    CheckConstraint("reserved >= 0", name="reserved_non_negative"),
    # Резерв не может превысить остаток: заказ больше, чем есть на
    # складе, обязан падать. Без этого ограничения резервирование
    # неограниченного количества проходило бы незаметно, и проверка
    # остатка в обработчике была бы единственной защитой.
    CheckConstraint("reserved <= available", name="reserved_within_available"),
)


class DemoPayment(Base, TimestampMixin):
    """Платёж по инстансу. Сумма — `Numeric`: двоичная плавающая точка
    накапливает ошибку там, где сравнение с нулём должно быть точным."""

    __tablename__ = "demo_payments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=UUID_PK_DEFAULT
    )
    instance_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workflow_instances.id", ondelete="RESTRICT"), nullable=False
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="HELD")
    refunded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_demo_payments_instance", "instance_id"),)


class DemoShipment(Base, TimestampMixin):
    """Отправление по инстансу."""

    __tablename__ = "demo_shipments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=UUID_PK_DEFAULT
    )
    instance_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workflow_instances.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="CREATED")
    tracking_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_demo_shipments_instance", "instance_id"),)
