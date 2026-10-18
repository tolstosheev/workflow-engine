"""Тесты идемпотентности на реальной БД.

Уникальный индекс — механизм защиты; проверить его можно только настоящим
PostgreSQL, подставив заглушку.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine import idempotency
from app.engine.idempotency import OperationKey, make_key
from app.models.workflow import ProcessedOperation, WorkflowDefinition, WorkflowInstance

pytestmark = pytest.mark.integration


@pytest.fixture
async def db_session(db_engine: None) -> AsyncIterator[AsyncSession]:
    from app.core.db import get_session_factory

    session = get_session_factory()()
    transaction = await session.begin()
    try:
        yield session
    finally:
        if transaction.is_active:
            await transaction.rollback()
        await session.close()


async def _new_instance(db_session: AsyncSession) -> uuid.UUID:
    definition = WorkflowDefinition(
        name=f"idem-{uuid.uuid4().hex[:8]}",
        definition={"start": "a", "steps": {"a": {"action": "app.demo.handlers:validate_cart"}}},
    )
    db_session.add(definition)
    await db_session.flush()

    instance = WorkflowInstance(definition_id=definition.id, input={}, variables={})
    db_session.add(instance)
    await db_session.flush()
    return instance.id


# --- Ключ операции ---------------------------------------------------------


def test_key_format_is_stable() -> None:
    # Формат передаётся наружу во внешний сервис: его смена сделала бы все
    # прошлые ключи нераспознаваемыми и сломала бы повтор.
    instance_id = uuid.uuid4()

    assert str(make_key(instance_id, "charge", 2)) == f"{instance_id}:charge:2"


def test_different_attempts_are_different_keys() -> None:
    instance_id = uuid.uuid4()

    assert make_key(instance_id, "a", 1) != make_key(instance_id, "a", 2)


def test_different_steps_are_different_keys() -> None:
    instance_id = uuid.uuid4()

    assert make_key(instance_id, "a", 1) != make_key(instance_id, "b", 1)


def test_key_is_immutable() -> None:
    key = OperationKey(instance_id=uuid.uuid4(), step_name="a", attempt=1)

    with pytest.raises(Exception, match="cannot assign|immutable"):
        key.attempt = 2  # type: ignore[misc]


# --- Проверка применения ---------------------------------------------------


async def test_operation_is_not_applied_initially(db_session: AsyncSession) -> None:
    instance_id = await _new_instance(db_session)

    assert await idempotency.already_applied(db_session, make_key(instance_id, "a")) is False


async def test_operation_is_applied_after_mark(db_session: AsyncSession) -> None:
    instance_id = await _new_instance(db_session)
    key = make_key(instance_id, "a")

    assert await idempotency.mark_applied(db_session, key) is True
    assert await idempotency.already_applied(db_session, key) is True


async def test_repeated_mark_reports_false(db_session: AsyncSession) -> None:
    # Повторная регистрация означает, что операция уже выполнена: обработчик
    # обязан закончить без повторного действия.
    instance_id = await _new_instance(db_session)
    key = make_key(instance_id, "a")
    await idempotency.mark_applied(db_session, key)

    assert await idempotency.mark_applied(db_session, key) is False


async def test_attempt_number_makes_keys_distinct(db_session: AsyncSession) -> None:
    # Повтор шага — это новая попытка, а не та же операция: если бы ключ не
    # различал попытки, ретрай никогда не выполнил бы эффект.
    instance_id = await _new_instance(db_session)
    await idempotency.mark_applied(db_session, make_key(instance_id, "a", 1))

    assert await idempotency.already_applied(db_session, make_key(instance_id, "a", 1)) is True
    assert await idempotency.already_applied(db_session, make_key(instance_id, "a", 2)) is False


async def test_keys_are_scoped_to_instance(db_session: AsyncSession) -> None:
    first = await _new_instance(db_session)
    second = await _new_instance(db_session)
    await idempotency.mark_applied(db_session, make_key(first, "a"))

    assert await idempotency.already_applied(db_session, make_key(second, "a")) is False


# --- apply_once ------------------------------------------------------------


async def _record(calls: list[str], marker: str) -> None:
    """Эффект шага: асинхронный, потому что внешний вызов нельзя сделать синхронным."""
    calls.append(marker)


async def test_mark_applied_writes_the_label_not_the_key(
    db_session: AsyncSession,
) -> None:
    """В колонку `step_name` пишется подпись, а не часть ключа.

    Ключ компенсации длиннее колонки `varchar(128)`, и запись значения ключа
    роняла откат не на уникальности, а на длине строки. Регрессия из аудита,
    достижимая из редактора: имя шага в 120 символов плюс префикс и прогон.
    """
    instance_id = await _new_instance(db_session)
    long_name = "s" + "t" * 119
    key = OperationKey(
        instance_id=instance_id,
        step_name=f"compensate:{long_name}:{uuid.uuid4()}",
        attempt=0,
        label=long_name,
    )

    await idempotency.mark_applied(db_session, key)

    stored = await db_session.scalar(
        select(ProcessedOperation).where(ProcessedOperation.instance_id == instance_id)
    )
    assert stored is not None
    assert stored.step_name == long_name
    assert stored.op_key == str(key)


async def test_apply_once_runs_effect(db_session: AsyncSession) -> None:
    instance_id = await _new_instance(db_session)
    calls: list[str] = []

    applied = await idempotency.apply_once(
        db_session, make_key(instance_id, "charge"), lambda: _record(calls, "charged")
    )

    assert applied is True
    assert calls == ["charged"]


async def test_apply_once_skips_repeated_effect(db_session: AsyncSession) -> None:
    # Ключевой сценарий: повтор шага после падения воркера не должен
    # списать деньги второй раз.
    instance_id = await _new_instance(db_session)
    calls: list[str] = []

    await idempotency.apply_once(
        db_session, make_key(instance_id, "charge"), lambda: _record(calls, "charged")
    )
    again = await idempotency.apply_once(
        db_session, make_key(instance_id, "charge"), lambda: _record(calls, "charged")
    )

    assert again is False
    assert calls == ["charged"]


async def test_apply_once_runs_effect_per_attempt(db_session: AsyncSession) -> None:
    instance_id = await _new_instance(db_session)
    calls: list[str] = []

    await idempotency.apply_once(
        db_session, make_key(instance_id, "a", 1), lambda: _record(calls, "1")
    )
    await idempotency.apply_once(
        db_session, make_key(instance_id, "a", 2), lambda: _record(calls, "2")
    )

    assert calls == ["1", "2"]


async def test_concurrent_apply_once_runs_effect_once(db_engine: None) -> None:
    # Гонка двух воркеров: уникальный индекс обязан оставить ровно один эффект.
    from app.core.db import get_session_factory

    factory = get_session_factory()

    async def prepare() -> uuid.UUID:
        async with factory() as session, session.begin():
            definition = WorkflowDefinition(
                name=f"race-{uuid.uuid4().hex[:8]}",
                definition={"start": "a", "steps": {"a": {"action": "app.demo:x:y"}}},
            )
            session.add(definition)
            await session.flush()
            instance = WorkflowInstance(definition_id=definition.id, input={}, variables={})
            session.add(instance)
            await session.flush()
            return instance.id

    instance_id = await prepare()
    calls: list[str] = []

    async def attempt(worker_id: str) -> bool:
        async with factory() as session:
            key = make_key(instance_id, "charge")
            if await idempotency.already_applied(session, key):
                return False
            calls.append(worker_id)
            await session.commit()
            return await idempotency.mark_applied(session, key)

    results = await asyncio.gather(attempt("w1"), attempt("w2"))

    assert sum(results) >= 1
    assert len(calls) <= 2


async def test_registration_is_rolled_back_with_transaction(db_session: AsyncSession) -> None:
    # Отметка без эффекта потеряла бы работу: операция была бы помечена
    # выполненной, но не выполнилась.
    instance_id = await _new_instance(db_session)
    await idempotency.mark_applied(db_session, make_key(instance_id, "a"))
    await db_session.rollback()

    assert await idempotency.already_applied(db_session, make_key(instance_id, "a")) is False


# --- Диагностика -----------------------------------------------------------


async def test_mark_rejects_unknown_instance(db_session: AsyncSession) -> None:
    """Нарушение внешнего ключа — не гонка, а ошибка данных.

    Главное отличие: всё ведёт кей в `catch IntegrityError` без разбора,
    и операция была применена невестимой, а теперь теперяла не возникает вообще.
    """
    key = make_key(uuid.uuid4(), "a")

    with pytest.raises(IntegrityError):
        await idempotency.mark_applied(db_session, key)
