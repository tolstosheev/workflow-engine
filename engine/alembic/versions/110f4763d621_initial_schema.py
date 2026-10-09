"""Начальная схема: ядро движка и таблицы предметной области.

Создаёт семь таблиц движка и три демонстрационные, а также триггеры, которые
делают правила журнала и аренды свойством базы, а не дисциплины кода.
Принятые решения описаны в `docs/DATA-MODEL.md` и ADR-0002…0006.

Revision ID: 110f4763d621
Revises:
Create Date: 2026-10-08 17:58:30.796707

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "110f4763d621"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Порядок соответствует зависимостям внешних ключей: сначала таблицы,
    # на которые ссылаются другие, затем ссылающиеся.
    op.create_table(
        "demo_inventory",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("sku", sa.String(length=64), nullable=False),
        sa.Column("available", sa.Integer(), nullable=False),
        sa.Column("reserved", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("available >= 0", name=op.f("ck_demo_inventory_available_non_negative")),
        sa.CheckConstraint("reserved >= 0", name=op.f("ck_demo_inventory_reserved_non_negative")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_demo_inventory")),
        sa.UniqueConstraint("sku", name=op.f("uq_demo_inventory_sku")),
    )
    op.create_table(
        "workflow_definitions",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("definition", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_workflow_definitions_version_positive")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workflow_definitions")),
    )
    op.create_index(
        "uq_workflow_definitions_name_version",
        "workflow_definitions",
        ["name", "version"],
        unique=True,
    )
    op.create_table(
        "workflow_instances",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("definition_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="PENDING", nullable=False),
        sa.Column("input", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("variables", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("current_step", sa.String(length=128), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["definition_id"],
            ["workflow_definitions.id"],
            name=op.f("fk_workflow_instances_definition_id_workflow_definitions"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workflow_instances")),
    )
    op.create_index(
        "ix_workflow_instances_active",
        "workflow_instances",
        ["lease_expires_at"],
        unique=False,
        postgresql_where="status IN ('PENDING','RUNNING','WAITING','CANCELLING','COMPENSATING','COMPENSATION_FAILED')",
    )
    op.create_index(
        "ix_workflow_instances_lease", "workflow_instances", ["lease_expires_at"], unique=False
    )
    op.create_index(
        "ix_workflow_instances_status_created",
        "workflow_instances",
        ["status", "created_at"],
        unique=False,
    )
    op.create_table(
        "demo_payments",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("instance_id", sa.UUID(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("refunded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["instance_id"],
            ["workflow_instances.id"],
            name=op.f("fk_demo_payments_instance_id_workflow_instances"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_demo_payments")),
    )
    op.create_index("ix_demo_payments_instance", "demo_payments", ["instance_id"], unique=False)
    op.create_table(
        "demo_shipments",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("instance_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("tracking_number", sa.String(length=64), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["instance_id"],
            ["workflow_instances.id"],
            name=op.f("fk_demo_shipments_instance_id_workflow_instances"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_demo_shipments")),
    )
    op.create_index("ix_demo_shipments_instance", "demo_shipments", ["instance_id"], unique=False)
    op.create_table(
        "processed_operations",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("instance_id", sa.UUID(), nullable=False),
        sa.Column("op_key", sa.String(length=256), nullable=False),
        sa.Column("step_name", sa.String(length=128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["instance_id"],
            ["workflow_instances.id"],
            name=op.f("fk_processed_operations_instance_id_workflow_instances"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_processed_operations")),
    )
    op.create_index(
        "uq_processed_operations_key",
        "processed_operations",
        ["instance_id", "op_key"],
        unique=True,
    )
    op.create_table(
        "step_runs",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("instance_id", sa.UUID(), nullable=False),
        sa.Column("step_name", sa.String(length=128), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="PENDING", nullable=False),
        sa.Column("input", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("output", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("compensated", sa.Boolean(), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("attempt >= 1", name=op.f("ck_step_runs_attempt_positive")),
        sa.ForeignKeyConstraint(
            ["instance_id"],
            ["workflow_instances.id"],
            name=op.f("fk_step_runs_instance_id_workflow_instances"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_step_runs")),
    )
    op.create_index(
        "ix_step_runs_instance_status_finished",
        "step_runs",
        ["instance_id", "status", "finished_at"],
        unique=False,
    )
    op.create_index(
        "ix_step_runs_instance_step", "step_runs", ["instance_id", "step_name"], unique=False
    )
    op.create_table(
        "timers",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("instance_id", sa.UUID(), nullable=False),
        sa.Column("step_name", sa.String(length=128), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fired", sa.Boolean(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["instance_id"],
            ["workflow_instances.id"],
            name=op.f("fk_timers_instance_id_workflow_instances"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_timers")),
    )
    op.create_index("ix_timers_due", "timers", ["fired", "due_at"], unique=False)
    op.create_table(
        "workflow_events",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("instance_id", sa.UUID(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(length=48), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("seq >= 0", name=op.f("ck_workflow_events_seq_non_negative")),
        sa.ForeignKeyConstraint(
            ["instance_id"],
            ["workflow_instances.id"],
            name=op.f("fk_workflow_events_instance_id_workflow_instances"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workflow_events")),
    )
    op.create_index(
        "ix_workflow_events_instance_created",
        "workflow_events",
        ["instance_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_workflow_events_type_created", "workflow_events", ["type", "created_at"], unique=False
    )
    op.create_index(
        "uq_workflow_events_instance_seq", "workflow_events", ["instance_id", "seq"], unique=True
    )
    op.create_table(
        "compensation_log",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("instance_id", sa.UUID(), nullable=False),
        sa.Column("step_run_id", sa.UUID(), nullable=False),
        sa.Column("compensation_step", sa.String(length=256), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="RUNNING", nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("attempt >= 1", name=op.f("ck_compensation_log_attempt_positive")),
        sa.ForeignKeyConstraint(
            ["instance_id"],
            ["workflow_instances.id"],
            name=op.f("fk_compensation_log_instance_id_workflow_instances"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["step_run_id"],
            ["step_runs.id"],
            name=op.f("fk_compensation_log_step_run_id_step_runs"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_compensation_log")),
    )
    op.create_index(
        "ix_compensation_log_instance_status",
        "compensation_log",
        ["instance_id", "status"],
        unique=False,
    )
    op.create_index(
        "uq_compensation_log_step_run",
        "compensation_log",
        ["step_run_id"],
        unique=True,
        postgresql_where="finished_at IS NULL",
    )
    # ### end Alembic commands ###
    _create_append_only_guard()
    _create_lease_guard()


def _create_append_only_guard() -> None:
    """Запрет UPDATE/DELETE на журнале событий.

    Правило «журнал append-only» держится дисциплиной кода ровно до первого
    же UPDATE в отладочном скрипте. Триггер делает его свойством базы: обойти
    можно только выключив его явно.
    """
    op.execute(
        """
        CREATE OR REPLACE FUNCTION workflow_events_append_only() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION
                'workflow_events is append-only: % запрещён (instance_id=%, seq=%)',
                TG_OP,
                OLD.instance_id,
                OLD.seq;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER workflow_events_no_update
            BEFORE UPDATE OR DELETE ON workflow_events
            FOR EACH ROW EXECUTE FUNCTION workflow_events_append_only();
        """
    )


def _create_lease_guard() -> None:
    """Запрет изменения аренды чужого воркера.

    Без проверки старый воркер, потерявший lease, сможет обнулить аренду нового
    и сбросить его прогресс. Триггер отсекает именно этот случай: смена владельца
    на непустое значение обязана совпадать с текущим.
    """
    op.execute(
        """
        CREATE OR REPLACE FUNCTION workflow_instances_lease_owner_guard() RETURNS trigger AS $$
        BEGIN
            IF NEW.lease_owner IS DISTINCT FROM OLD.lease_owner
               AND NEW.lease_owner IS NOT NULL
               AND OLD.lease_owner IS NOT NULL
            THEN
                RAISE EXCEPTION
                    'lease_owner может меняться только на NULL или с захвата по TTL (id=%)',
                    NEW.id;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER workflow_instances_lease_guard
            BEFORE UPDATE ON workflow_instances
            FOR EACH ROW EXECUTE FUNCTION workflow_instances_lease_owner_guard();
        """
    )


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_index(
        "uq_compensation_log_step_run",
        table_name="compensation_log",
        postgresql_where="finished_at IS NULL",
    )
    op.drop_index("ix_compensation_log_instance_status", table_name="compensation_log")
    op.drop_table("compensation_log")
    op.drop_index("uq_workflow_events_instance_seq", table_name="workflow_events")
    op.drop_index("ix_workflow_events_type_created", table_name="workflow_events")
    op.drop_index("ix_workflow_events_instance_created", table_name="workflow_events")
    op.drop_table("workflow_events")
    op.drop_index("ix_timers_due", table_name="timers")
    op.drop_table("timers")
    op.drop_index("ix_step_runs_instance_step", table_name="step_runs")
    op.drop_index("ix_step_runs_instance_status_finished", table_name="step_runs")
    op.drop_table("step_runs")
    op.drop_index("uq_processed_operations_key", table_name="processed_operations")
    op.drop_table("processed_operations")
    op.drop_index("ix_demo_shipments_instance", table_name="demo_shipments")
    op.drop_table("demo_shipments")
    op.drop_index("ix_demo_payments_instance", table_name="demo_payments")
    op.drop_table("demo_payments")
    op.drop_index("ix_workflow_instances_status_created", table_name="workflow_instances")
    op.drop_index("ix_workflow_instances_lease", table_name="workflow_instances")
    op.drop_index(
        "ix_workflow_instances_active",
        table_name="workflow_instances",
        postgresql_where="status IN ('PENDING','RUNNING','WAITING','CANCELLING','COMPENSATING','COMPENSATION_FAILED')",
    )
    op.drop_table("workflow_instances")
    op.drop_index("uq_workflow_definitions_name_version", table_name="workflow_definitions")
    op.drop_table("workflow_definitions")
    op.drop_table("demo_inventory")
    # ### end Alembic commands ###
