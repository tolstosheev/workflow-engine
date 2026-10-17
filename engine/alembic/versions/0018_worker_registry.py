"""Реестр воркеров (ADR-0018)."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0018_worker_registry"
down_revision = "0017_instance_archived"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workers",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("worker_id", sa.String(length=128), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cycles_completed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("steps_executed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lease_lost", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workers")),
        sa.UniqueConstraint("worker_id", name=op.f("uq_workers_worker_id")),
    )
    # Список открывают целиком и упорядочивают по свежести сердца.
    op.create_index("ix_workers_heartbeat_at", "workers", ["heartbeat_at"])


def downgrade() -> None:
    op.drop_index("ix_workers_heartbeat_at", table_name="workers")
    op.drop_table("workers")
