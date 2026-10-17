"""Пауза перед повтором отката.

Провал компенсации означает, что внешний сервис недоступен: склад, платёжная
система или то, что стоит за конкретным обработчиком. Без паузы воркер брал бы
такой процесс каждый цикл, то есть раз в секунду на каждый воркер, и лавина
повторов делала бы недоступность хуже, чем она есть.

Пауза хранится в строке инстанса, а не в памяти воркера: перезапуск процесса
не должен сбрасывать ожидание. `claim_next` не отдаёт процесс до срока, поэтому
очередь не встаёт и остальные инстансы обрабатываются как обычно.

Повтор остаётся бесконечным: §3.5 требует, чтобы компенсация была в очереди
до успеха, а решение об отказе от процесса принимает оператор.

Revision ID: 7d4a1c92b8e1
Revises: c3e5a7d24f18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "7d4a1c92b8e1"
down_revision: str | None = "c3e5a7d24f18"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workflow_instances",
        sa.Column(
            "compensation_not_before",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    # Выборка идёт по статусу и сроку отката вместе: без индекса процесс с
    # паузой просматривался бы целиком на каждом цикле каждого воркера.
    op.create_index(
        "ix_workflow_instances_compensation_ready",
        "workflow_instances",
        ["status", "compensation_not_before"],
    )


def downgrade() -> None:
    op.drop_index("ix_workflow_instances_compensation_ready", table_name="workflow_instances")
    op.drop_column("workflow_instances", "compensation_not_before")
