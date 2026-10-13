"""Вид ожидания: срок паузы отличается от ожидания шага (ADR-0016).

Столбец пуст у всех существующих таймеров: они ожидают шаг, как и раньше.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0016_timer_kind"
down_revision = "0015_handler_ref"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Значение по умолчанию обязательно: иначе существующие строки получили бы
    # NULL и перестали бы читаться как ожидания шага.
    op.add_column(
        "timers",
        sa.Column("kind", sa.String(length=32), nullable=False, server_default="step"),
    )


def downgrade() -> None:
    op.drop_column("timers", "kind")
