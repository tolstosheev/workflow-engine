"""Уточнение защиты аренды: перехват истёкшей аренды разрешён.

Первая версия триггера запрещала смену `lease_owner` на любое новое непустое
значение. Это ломало восстановление: перехват брошенного инстанса — это и есть
смена владельца, и триггер отвергал её наравне с потерей прогресса.

Ограничение должно бить по опасному случаю — смене владельца при живой аренде,
то есть по попытке старого воркера продолжать работу поверх нового. Перехват
истёкшей аренды — законная операция, и блокировать её нельзя.

Revision ID: 4f2c8a91b7de
Revises: 110f4763d621
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "4f2c8a91b7de"
down_revision: str | None = "110f4763d621"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION workflow_instances_lease_owner_guard() RETURNS trigger AS $$
        BEGIN
            IF NEW.lease_owner IS DISTINCT FROM OLD.lease_owner
               AND NEW.lease_owner IS NOT NULL
               AND OLD.lease_owner IS NOT NULL
               AND (OLD.lease_expires_at IS NULL OR OLD.lease_expires_at >= now())
            THEN
                -- Кавычки внутри сообщения не ставятся: они конфликтуют
                -- с экранированием в SQL-теле функции.
                RAISE EXCEPTION
                    'lease ещё жив (владелец %, новый %), смена возможна только по TTL (id=%)',
                    OLD.lease_owner,
                    NEW.lease_owner,
                    NEW.id;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )


def downgrade() -> None:
    # Возврат к первой, более строгой версии: она отвергала и перехват по TTL.
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
