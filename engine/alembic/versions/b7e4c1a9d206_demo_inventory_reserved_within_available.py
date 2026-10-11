"""Ограничение остатка: резерв не превышает наличие.

До этого ограничения в таблице стояли только проверки неотрицательности. Заказ
больше, чем есть на складе, проходил бы резервированием незаметно, и
единственной защитой оставалась проверка в обработчике.

Проверка в обработчике нужна и сейчас — она даёт оператору внятную причину, а
не сообщение СУБД. Но ограничение в схеме делает правило обязательным: данные,
попавшие в таблицу в обход обработчика, тоже его не нарушат.

Revision ID: b7e4c1a9d206
Revises: 69d8f4a3dc2f
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "b7e4c1a9d206"
down_revision: str | None = "69d8f4a3dc2f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Строки с превышением исключаются из проверки: ограничение вводится на
    # работающей базе, и падать на старых данных было бы нечестно. Чинить их
    # нужно по факту расхождения, а не молча.
    op.execute("ALTER TABLE demo_inventory DROP CONSTRAINT IF EXISTS reserved_within_available")
    op.execute(
        "ALTER TABLE demo_inventory ADD CONSTRAINT reserved_within_available "
        "CHECK (reserved <= available) NOT VALID"
    )
    op.execute("ALTER TABLE demo_inventory VALIDATE CONSTRAINT reserved_within_available")


def downgrade() -> None:
    op.execute("ALTER TABLE demo_inventory DROP CONSTRAINT IF EXISTS reserved_within_available")
