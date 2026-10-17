"""Архив завершённых процессов (ADR-0017).

Колонка отмечает процесс как убранный из списка. Удаления нет: события
журнала остаются, и состояние по-прежнему выводится их повтором.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0017_instance_archived"
down_revision = "0016_timer_kind"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workflow_instances",
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Частичный индекс по неархивным: список и опрос воркера ходят по этой
    # колонке, а архивные строки в выборку попадать не должны.
    op.create_index(
        "ix_workflow_instances_not_archived",
        "workflow_instances",
        ["created_at"],
        postgresql_where=sa.text("archived_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_workflow_instances_not_archived", table_name="workflow_instances")
    op.drop_column("workflow_instances", "archived_at")
