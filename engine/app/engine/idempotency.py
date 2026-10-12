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
    except IntegrityError as exc:
        # Гонка: другой воркер успел зарегистрировать тот же ключ. Откат
        # нужен, чтобы транзакция осталась пригодной.
        # Но нарушение внешнего ключа — это не гонка: так был бы отказ из-за
        # неверных данных, и проглатывать его как гонку нельзя.
        if not _is_unique_violation(exc):
            raise
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


async def apply_once_by_key(
    db_session: AsyncSession,
    instance_id: uuid.UUID,
    op_key: str,
    step_name: str,
    effect: Effect,
) -> bool:
    """Проверяет и применяет операцию по строковому ключу, в одной транзакции.

    Существует рядом с `apply_once` для объекта-ключа и делает то же, но по
    строке. Нужна обработчикам: у них есть `ctx.operation_id`, и разбирать его
    обратно в UUID и собирать `OperationKey` заново значило бы продублировать
    формат ключа в прикладном коде — с расхождением при первом же изменении
    формата.

    Ключевое отличие от проверки и отметки по отдельности: проверка, эффект и
    отметка здесь **в одной транзакции**. Проверка в движке, а отметка после
    эффекта оставляли окно, в котором падение воркера приводило к повторному
    применению: эффект записан, отметка — нет. Для обработчика без побочных
    эффектов это безобидно; для обработчика, который списывает средства, —
    двойное списание.

    `False` — операция уже была применена: это штатный повтор, а не ошибка.
    """
    found = await db_session.scalar(
        select(ProcessedOperation.id).where(
            ProcessedOperation.instance_id == instance_id,
            ProcessedOperation.op_key == op_key,
        )
    )
    if found is not None:
        return False

    await effect()
    db_session.add(ProcessedOperation(instance_id=instance_id, op_key=op_key, step_name=step_name))
    try:
        await db_session.flush()
    except IntegrityError as exc:
        # Гонкой считается только нарушение уникальности ключа: тот же ключ
        # успел зарегистрироваться. Нарушение внешнего ключа — уже не гонка, а
        # неверное состояние данных, и молчать о нём нельзя: откат тихо
        # отменил бы применённый эффект, а вызывающий решил бы, что всё в
        # порядке.
        if not _is_unique_violation(exc):
            raise
        # Откат нужен, чтобы транзакция осталась пригодной.
        await db_session.rollback()
        return False
    return True


def _is_unique_violation(exc: IntegrityError) -> bool:
    """Нарушено ли именно уникальное ограничение.

    Проверка по имени ограничения, а не по типу исключения: нарушение внешнего
    ключа даёт тот же `IntegrityError`, и принять его за гонку значило бы
    проглотить ошибку данных.
    """
    orig = getattr(exc, "orig", None)
    constraint = getattr(getattr(orig, "diag", None), "constraint_name", None)
    if constraint is not None:
        return constraint == "uq_processed_operations_key"
    text = str(orig)
    return "uq_processed_operations_key" in text or "duplicate key" in text.lower()


async def list_applied(db_session: AsyncSession, instance_id: uuid.UUID) -> list[str]:
    """Список применённых операций инстанса — для диагностики и отладки."""
    rows = await db_session.scalars(
        select(ProcessedOperation.op_key)
        .where(ProcessedOperation.instance_id == instance_id)
        .order_by(ProcessedOperation.created_at)
    )
    return list(rows)
