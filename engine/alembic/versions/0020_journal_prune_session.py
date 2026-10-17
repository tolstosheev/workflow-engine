"""Служебное удаление журнала по ADR-0019.

Журнал остаётся append-only: обычный `UPDATE` и `DELETE` по-прежнему
отклоняются. Триггер пропускает удаление только когда выполнены три условия
одновременно:

1. сессия объявила уборку явной настройкой `app.journal_prune`;
2. процесс, чей журнал удаляется, терминален;
3. тот же процесс архивлен — то есть человек убрал его из работы сам.

Второе и третье проверяет сама база, а не скрипт: ошибка в отборе уборки не
может удалить журнал живого процесса, даже если настройку кто-то выставит.
"""

from __future__ import annotations

from alembic import op

revision = "0020_journal_prune_session"
down_revision = "0019_journal_pruned"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION workflow_events_append_only() RETURNS trigger AS $$
        DECLARE
            owner_status TEXT;
            owner_archived TIMESTAMPTZ;
        BEGIN
            IF TG_OP = 'DELETE'
               AND current_setting('app.journal_prune', TRUE) = 'on' THEN
                SELECT i.status::TEXT, i.archived_at INTO owner_status, owner_archived
                FROM workflow_instances i
                WHERE i.id = OLD.instance_id;

                IF owner_status IS NOT NULL
                   -- COMPENSATION_FAILED и PAUSED ждут человека: их журнал
                   -- разбирают, и убирать его нельзя.
                   AND owner_status IN ('COMPLETED', 'COMPENSATED',
                                        'CANCELLED', 'FAILED')
                   AND owner_archived IS NOT NULL THEN
                    RETURN OLD;
                END IF;
            END IF;

            RAISE EXCEPTION
                'workflow_events is append-only: % запрещён (instance_id=%, seq=%)',
                TG_OP,
                OLD.instance_id,
                OLD.seq;
        END;
        $$ LANGUAGE plpgsql;
        """
    )


def downgrade() -> None:
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