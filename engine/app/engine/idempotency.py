"""Идемпотентность исполнения шага.

Доставка at-least-once: повтор шага после падения воркера — норма, а не
исключение. Exactly-once не существует, поэтому «ровно один раз»
имитируется учётом применённых операций.

Правило, которое держит весь механизм: проверка ключа идёт **до** действия.
Обратный порядок не защищает ничего — к моменту проверки дубль уже применён.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.workflow import ProcessedOperation

# Эффект шага: асинхронная функция, применяющая изменения.
Effect = Callable[[], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class OperationKey:
    """Ключ операции: однозначно определяет одну попытку одного шага."""

    instance_id: uuid.UUID
    step_name: str
    attempt: int

    def __str__(self) -> str:
        # Формат передаётся наружу во внешний сервис, поэтому он должен быть
        # стабильным: смена формата сделает все прошлые ключи нераспознаваемыми.
        return f"{self.instance_id}:{self.step_name}:{self.attempt}"


def make_key(instance_id: uuid.UUID, step_name: str, attempt: int = 1) -> OperationKey:
    return OperationKey(instance_id=instance_id, step_name=step_name, attempt=attempt)


async def already_applied(db_session: AsyncSession, key: OperationKey) -> bool:
    """Проверяет, применялась ли операция.

    Вызывается обработчиком **до** действия. Повтор шага после падения воркера
    обязан вернуть «да» и завершиться без повторного вызова.
    """
    found = await db_session.scalar(
        select(ProcessedOperation.id).where(
            ProcessedOperation.instance_id == key.instance_id,
            ProcessedOperation.op_key == str(key),
        )
    )
    return found is not None


async def mark_applied(db_session: AsyncSession, key: OperationKey) -> bool:
    """Регистрирует применённую операцию. `False` — уже была применена.

    Вставка идёт в той же транзакции, что и эффект шага: операция, отмеченная
    без эффекта, потеряла бы работу, а эффект без отметки — повторился бы.
    """
    db_session.add(
        ProcessedOperation(instance_id=key.instance_id, op_key=str(key), step_name=key.step_name)
    )
    try:
        await db_session.flush()
    except IntegrityError:
        # Гонка: другой воркер успел зарегистрировать тот же ключ. Откат
        # нужен, чтобы транзакция осталась пригодной.
        await db_session.rollback()
        return False
    return True


async def apply_once(db_session: AsyncSession, key: OperationKey, effect: Effect) -> bool:
    """Выполняет эффект, если операция ещё не применялась.

    `effect` — асинхронная функция, применяющая изменения. Возвращает `False`,
    если операция уже была применена: это штатный повтор, а не ошибка.
    """
    if await already_applied(db_session, key):
        return False
    await effect()
    return await mark_applied(db_session, key)


async def list_applied(db_session: AsyncSession, instance_id: uuid.UUID) -> list[str]:
    """Список применённых операций инстанса — для диагностики и отладки."""
    rows = await db_session.scalars(
        select(ProcessedOperation.op_key)
        .where(ProcessedOperation.instance_id == instance_id)
        .order_by(ProcessedOperation.created_at)
    )
    return list(rows)
