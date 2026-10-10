"""Интеграционные тесты схемы на реальном PostgreSQL.

Отдельно от модульных проверок: здесь интересует поведение самой базы —
триггеры, ограничения и частичные индексы, которые на SQLite не воспроизвести.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.workflow import (
    ProcessedOperation,
    WorkflowDefinition,
    WorkflowEvent,
    WorkflowInstance,
)

pytestmark = pytest.mark.integration


@pytest.fixture
async def db_session(db_engine: None) -> AsyncIterator[AsyncSession]:
    """Сессия к тестовой БД с откатом всех изменений.

    Откат вместо очистки таблиц: чужая транзакция в общей базе не должна
    исчезнуть из-под теста.
    """
    from app.core.db import get_session_factory

    session = get_session_factory()()
    transaction = await session.begin()
    try:
        yield session
    finally:
        # Откат выполняется всегда, но тест, ожидающий нарушения ограничения,
        # мог уже откатить транзакцию сам — повторный откат тогда падает.
        if transaction.is_active:
            await transaction.rollback()
        await session.close()


async def _new_instance(db_session: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
    definition = WorkflowDefinition(
        name=f"test-def-{uuid.uuid4().hex[:8]}", definition={"start": "a", "steps": {}}
    )
    db_session.add(definition)
    await db_session.flush()

    instance = WorkflowInstance(definition_id=definition.id, input={}, variables={})
    db_session.add(instance)
    await db_session.flush()
    return definition.id, instance.id


async def test_definition_and_instance_are_created(
    db_session: AsyncSession,
) -> None:
    definition_id, instance_id = await _new_instance(db_session)

    assert definition_id is not None
    assert instance_id is not None


async def test_workflow_events_reject_update(db_session: AsyncSession) -> None:
    # Приложение не обновляет журнал, но проверка на уровне базы гарантирует,
    # что этого не сделает и ни один сценарий, о котором никто не знает.
    _, instance_id = await _new_instance(db_session)
    db_session.add(WorkflowEvent(instance_id=instance_id, seq=0, type="INSTANCE_CREATED"))
    await db_session.flush()

    with pytest.raises(Exception, match="append-only"):
        await db_session.execute(
            text("UPDATE workflow_events SET type = 'HACKED' WHERE instance_id = :i"),
            {"i": instance_id},
        )
    await db_session.rollback()


async def test_workflow_events_reject_delete(db_session: AsyncSession) -> None:
    _, instance_id = await _new_instance(db_session)
    db_session.add(WorkflowEvent(instance_id=instance_id, seq=0, type="INSTANCE_CREATED"))
    await db_session.flush()

    with pytest.raises(Exception, match="append-only"):
        await db_session.execute(
            text("DELETE FROM workflow_events WHERE instance_id = :i"), {"i": instance_id}
        )
    await db_session.rollback()


async def test_events_seq_is_unique_per_instance(db_session: AsyncSession) -> None:
    # Второе событие с тем же seq отвергается базой: история инстанса не может
    # разветвиться.
    _, instance_id = await _new_instance(db_session)
    db_session.add(WorkflowEvent(instance_id=instance_id, seq=0, type="INSTANCE_CREATED"))
    await db_session.flush()

    db_session.add(WorkflowEvent(instance_id=instance_id, seq=0, type="INSTANCE_STARTED"))

    with pytest.raises(IntegrityError, match="uq_workflow_events_instance_seq"):
        await db_session.flush()
    await db_session.rollback()


async def test_processed_operations_reject_duplicate_key(
    db_session: AsyncSession,
) -> None:
    # Уникальный индекс — механизм идемпотентности: второй INSERT с тем же
    # op_key обязан быть отвергнут базой, а не надеждой обработчика.
    _, instance_id = await _new_instance(db_session)
    db_session.add(ProcessedOperation(instance_id=instance_id, op_key="i:a:1", step_name="a"))
    await db_session.flush()

    db_session.add(ProcessedOperation(instance_id=instance_id, op_key="i:a:1", step_name="a"))

    with pytest.raises(IntegrityError, match="uq_processed_operations_key"):
        await db_session.flush()
    await db_session.rollback()


async def test_processed_operations_on_conflict_do_nothing_is_noop(
    db_session: AsyncSession,
) -> None:
    _, instance_id = await _new_instance(db_session)
    await db_session.execute(
        text(
            "INSERT INTO processed_operations (instance_id, op_key, step_name) "
            "VALUES (:i, 'i:a:1', 'a') ON CONFLICT DO NOTHING"
        ),
        {"i": instance_id},
    )
    await db_session.execute(
        text(
            "INSERT INTO processed_operations (instance_id, op_key, step_name) "
            "VALUES (:i, 'i:a:1', 'a') ON CONFLICT DO NOTHING"
        ),
        {"i": instance_id},
    )

    count = await db_session.scalar(
        text("SELECT count(*) FROM processed_operations WHERE instance_id = :i"),
        {"i": instance_id},
    )
    assert count == 1
    await db_session.rollback()


async def test_lease_owner_cannot_be_stolen_by_other_worker(
    db_session: AsyncSession,
) -> None:
    # Подмена владельца аренды с одного непустого значения на другое означала бы,
    # что старый воркер сбросил прогресс нового.
    _, instance_id = await _new_instance(db_session)
    await db_session.execute(
        text("UPDATE workflow_instances SET lease_owner = 'w1' WHERE id = :i"),
        {"i": instance_id},
    )

    with pytest.raises(Exception, match="lease_owner"):
        await db_session.execute(
            text("UPDATE workflow_instances SET lease_owner = 'w2' WHERE id = :i"),
            {"i": instance_id},
        )
    await db_session.rollback()


async def test_lease_owner_can_be_released_to_null(db_session: AsyncSession) -> None:
    _, instance_id = await _new_instance(db_session)
    await db_session.execute(
        text("UPDATE workflow_instances SET lease_owner = 'w1' WHERE id = :i"),
        {"i": instance_id},
    )
    await db_session.execute(
        text("UPDATE workflow_instances SET lease_owner = NULL WHERE id = :i"),
        {"i": instance_id},
    )

    owner = await db_session.scalar(
        text("SELECT lease_owner FROM workflow_instances WHERE id = :i"), {"i": instance_id}
    )
    assert owner is None
    await db_session.rollback()


async def test_expired_lease_cannot_be_stolen(db_session: AsyncSession) -> None:
    # Захват по TTL должен проходить только при истёкшей аренде: живой lease
    # блокирует перехват, иначе шаг выполнился бы дважды.
    _, instance_id = await _new_instance(db_session)
    await db_session.execute(
        text(
            "UPDATE workflow_instances SET lease_owner = 'w1', "
            "lease_expires_at = now() + interval '30 seconds', heartbeat_at = now() "
            "WHERE id = :i"
        ),
        {"i": instance_id},
    )

    stolen = await db_session.execute(
        text(
            "UPDATE workflow_instances SET lease_owner = NULL WHERE id = :i "
            "AND (lease_expires_at IS NULL OR lease_expires_at < now()) RETURNING id"
        ),
        {"i": instance_id},
    )
    assert stolen.rowcount == 0

    await db_session.rollback()


async def test_expired_lease_can_be_taken_over(db_session: AsyncSession) -> None:
    _, instance_id = await _new_instance(db_session)
    await db_session.execute(
        text(
            "UPDATE workflow_instances SET lease_owner = NULL, "
            "lease_expires_at = now() - interval '1 second', heartbeat_at = now() "
            "WHERE id = :i"
        ),
        {"i": instance_id},
    )

    taken = await db_session.execute(
        text(
            "UPDATE workflow_instances SET lease_owner = 'w2', "
            "lease_expires_at = now() + interval '30 seconds' "
            "WHERE id = :i AND (lease_expires_at IS NULL OR lease_expires_at < now()) "
            "RETURNING id"
        ),
        {"i": instance_id},
    )
    assert taken.rowcount == 1
    await db_session.rollback()


async def test_skip_locked_returns_row_without_blocking(
    db_session: AsyncSession,
) -> None:
    # SKIP LOCKED — механизм распределённой очереди задач: конкурент не ждёт
    # занятой строки, а берёт следующую.
    _, instance_id = await _new_instance(db_session)
    await db_session.execute(
        text(
            "UPDATE workflow_instances SET status = 'RUNNING', lease_owner = 'w1', "
            "lease_expires_at = now() - interval '1 second' WHERE id = :i"
        ),
        {"i": instance_id},
    )

    rows = await db_session.execute(
        text(
            "SELECT id FROM workflow_instances "
            "WHERE status IN ('RUNNING','COMPENSATING') AND lease_expires_at < now() "
            "FOR UPDATE SKIP LOCKED"
        )
    )
    assert instance_id in {row[0] for row in rows}
    await db_session.rollback()


async def test_events_are_not_deleted_with_instance(db_session: AsyncSession) -> None:
    # ON DELETE RESTRICT: удаление инстанса уничтожило бы журнал, а вместе с
    # ним возможность восстановления.
    _, instance_id = await _new_instance(db_session)
    db_session.add(WorkflowEvent(instance_id=instance_id, seq=0, type="INSTANCE_CREATED"))
    await db_session.flush()

    with pytest.raises(Exception, match="workflow_instances"):
        await db_session.execute(
            text("DELETE FROM workflow_instances WHERE id = :i"), {"i": instance_id}
        )
    await db_session.rollback()


async def test_timestamps_are_stored_with_timezone(db_session: AsyncSession) -> None:
    # timestamptz, а не timestamp: без зоны время неоднозначно между воркерами.
    _, instance_id = await _new_instance(db_session)
    moment = datetime.now(UTC)

    await db_session.execute(
        text("UPDATE workflow_instances SET heartbeat_at = :t WHERE id = :i"),
        {"t": moment, "i": instance_id},
    )
    stored = await db_session.scalar(
        text("SELECT heartbeat_at FROM workflow_instances WHERE id = :i"), {"i": instance_id}
    )

    assert stored.tzinfo is not None
    assert abs((stored - moment).total_seconds()) < 60
    await db_session.rollback()


async def test_active_index_excludes_terminal_instances(
    db_session: AsyncSession,
) -> None:
    _, instance_id = await _new_instance(db_session)
    await db_session.execute(
        text("UPDATE workflow_instances SET status = 'COMPLETED' WHERE id = :i"),
        {"i": instance_id},
    )

    listed = await db_session.execute(
        text(
            "SELECT id FROM workflow_instances WHERE status IN "
            "('PENDING','RUNNING','WAITING','CANCELLING','COMPENSATING',"
            "'COMPENSATION_FAILED')"
        )
    )
    assert instance_id not in {row[0] for row in listed}
    await db_session.rollback()


async def test_step_seq_advances_under_row_lock(db_session: AsyncSession) -> None:
    # Выделение seq под FOR UPDATE даёт строгую монотонность без пропусков.
    _, instance_id = await _new_instance(db_session)
    last_seq = await db_session.scalar(
        text("SELECT coalesce(max(seq), -1) FROM workflow_events WHERE instance_id = :i"),
        {"i": instance_id},
    )

    for expected in range(last_seq + 1, last_seq + 4):
        await db_session.execute(
            text("SELECT id FROM workflow_instances WHERE id = :i FOR UPDATE"),
            {"i": instance_id},
        )
        current = await db_session.scalar(
            text("SELECT coalesce(max(seq), -1) FROM workflow_events WHERE instance_id = :i"),
            {"i": instance_id},
        )
        db_session.add(
            WorkflowEvent(instance_id=instance_id, seq=current + 1, type="INSTANCE_STARTED")
        )
        await db_session.flush()
        assert current + 1 == expected

    await db_session.rollback()


async def test_heartbeat_extends_lease_without_changing_owner(
    db_session: AsyncSession,
) -> None:
    _, instance_id = await _new_instance(db_session)
    await db_session.execute(
        text(
            "UPDATE workflow_instances SET lease_owner = 'w1', "
            "lease_expires_at = now() + interval '5 seconds' WHERE id = :i"
        ),
        {"i": instance_id},
    )
    before = await db_session.scalar(
        text("SELECT lease_expires_at FROM workflow_instances WHERE id = :i"),
        {"i": instance_id},
    )

    await db_session.execute(
        text(
            "UPDATE workflow_instances SET lease_expires_at = now() + interval '60 seconds', "
            "heartbeat_at = now() WHERE id = :i AND lease_owner = 'w1'"
        ),
        {"i": instance_id},
    )
    after = await db_session.scalar(
        text("SELECT lease_expires_at FROM workflow_instances WHERE id = :i"),
        {"i": instance_id},
    )
    owner = await db_session.scalar(
        text("SELECT lease_owner FROM workflow_instances WHERE id = :i"), {"i": instance_id}
    )

    assert owner == "w1"
    assert after - before > timedelta(seconds=30)
    await db_session.rollback()


# --- Индексы под горячие запросы ---------------------------------------------------


async def test_hot_path_indexes_exist(db_session: AsyncSession) -> None:
    """Индексы, которые обслуживают конкретные запросы движка.

    Список на каждый запрос — не проектирование по плану запроса, а
    если индекса нет — планирующий счётчик получисток. В настоящем
    объеме родсте обращение без индекса, а давес вариант чем пустой
    таблицы и регрестрация приведёт её снова к содержимой.
    """
    rows = await db_session.execute(
        text("SELECT indexname FROM pg_indexes WHERE schemaname = 'public'")
    )
    present = {row[0] for row in rows}

    assert {
        "ix_workflow_events_created",
        "ix_timers_instance_step",
        "ix_compensation_log_step_run",
        "ix_workflow_instances_definition",
    } <= present
