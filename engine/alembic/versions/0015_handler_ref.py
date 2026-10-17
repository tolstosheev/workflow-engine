"""Инъекция отказа адресуется обработчиком компенсации (ADR-0015).

**Revision ID**: 0015_handler_ref
**Revises**: предыдущая ревизия схемы
**Create Date**: 2026-10-13

Столбец пустой у всех существующих правил: правило с адресом обработчика
ломает возврат, правило без него — прямой шаг, как и раньше.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0015_handler_ref"
down_revision = "7d4a1c92b8e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "chaos_rules",
        sa.Column("handler_ref", sa.String(length=256), nullable=True),
    )
    # Индекс обязателен: поиск идёт по точному значению на каждой попытке
    # компенсации, а правил может быть сколько угодно и для всех инстансов.
    op.create_index(
        op.f("ix_chaos_rules_handler_ref"), "chaos_rules", ["handler_ref"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_chaos_rules_handler_ref"), table_name="chaos_rules")
    op.drop_column("chaos_rules", "handler_ref")
