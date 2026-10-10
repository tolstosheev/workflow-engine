"""Индексы под горячие запросы движка.

Каждый индекс добавлен под конкретный запрос, а не «на всякий случай»:

- `workflow_events(created_at)` — поток SSE сортирует события по времени и
  берёт последние 200. Индексы `(instance_id, created_at)` и
  `(type, created_at)` этому не помогают: у запроса нет ведущей колонки.
  Замер на 60 000 событий: без индекса — последовательное чтение и сортировка
  (3489 буферов), с индексом — Index Scan без сортировки (204 буфера).
- `timers(instance_id, step_name)` — `planned_for` вызывается на каждом цикле
  воркера для шага с ожиданием.
- `compensation_log(step_run_id)` — уникальный индекс покрывает только
  незавершённые компенсации (`finished_at IS NULL`), а план отката читает все.
- `workflow_instances(definition_id)` — внешний ключ без индекса; по нему
  фильтрует список инстансов.

Индексы создаются обычным `CREATE INDEX`, а не `CONCURRENTLY`: миграция идёт
в транзакции, а таблицы в этом проекте невелики. Для боевой эксплуатации
индексы на больших таблицах создают вручную с `CONCURRENTLY` вне транзакции.

Revision ID: c3e5a7d24f18
Revises: b7e4c1a9d206
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "c3e5a7d24f18"
down_revision: str | None = "b7e4c1a9d206"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_workflow_events_created", "workflow_events", ["created_at"])
    op.create_index("ix_timers_instance_step", "timers", ["instance_id", "step_name"])
    op.create_index("ix_compensation_log_step_run", "compensation_log", ["step_run_id"])
    op.create_index(
        "ix_workflow_instances_definition", "workflow_instances", ["definition_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_workflow_instances_definition", table_name="workflow_instances")
    op.drop_index("ix_compensation_log_step_run", table_name="compensation_log")
    op.drop_index("ix_timers_instance_step", table_name="timers")
    op.drop_index("ix_workflow_events_created", table_name="workflow_events")