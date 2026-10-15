"""Пометка убранного журнала (ADR-0019).

Колонка означает, что события процесса удалены уборкой: разбор по журналу для
такого процесса невозможен, и интерфейс обязан сказать об этом прямо, а не
показать пустую ленту как будто событий не было.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0019_journal_pruned"
down_revision = "0018_worker_registry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workflow_instances",
        sa.Column("journal_pruned_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Индекс по уборке: команда выбирает именно такие строки, и без него она
    # сканировала бы все архивные процессы подряд.
    op.create_index(
        "ix_workflow_instances_journal_pruned",
        "workflow_instances",
        ["journal_pruned_at"],
        postgresql_where=sa.text("journal_pruned_at IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_workflow_instances_journal_pruned", table_name="workflow_instances")
    op.drop_column("workflow_instances", "journal_pruned_at")