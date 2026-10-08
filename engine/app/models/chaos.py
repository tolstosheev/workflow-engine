"""Инъекция отказов: хранение правил и их применение.

Правила живут в БД, а не в памяти процесса: API и воркер — разные процессы,
и правило, зарегистрированное через HTTP, обязано быть видно исполнителю шага.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.elements import ColumnElement

from app.models.base import UUID_PK_DEFAULT, Base
from app.models.workflow import WorkflowInstance

MODES = ("once", "always", "timeout")


class ChaosRule(Base):
    """Правило сбоя для шага.

    Пустые поля означают «любой»: `instance_id` без значения действует на все
    инстансы, `step_name` — на все шаги инстанса.
    """

    __tablename__ = "chaos_rules"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_PK_DEFAULT.type, primary_key=True, default=uuid.uuid4, server_default=UUID_PK_DEFAULT
    )
    instance_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_PK_DEFAULT.type, nullable=True, index=True
    )
    step_name: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    error: Mapped[str] = mapped_column(Text, nullable=False, default="сбой шага (инъекция)")
    used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def label(self) -> str:
        """Человекочитаемое имя правила для ответа API."""
        scope = str(self.instance_id) if self.instance_id else "*"
        step = self.step_name or "*"
        return f"{scope}:{step}:{self.mode}"

    def matches(self, instance_id: uuid.UUID, step_name: str) -> bool:
        """Подходит ли правило к конкретному шагу инстанса."""
        if self.instance_id is not None and self.instance_id != instance_id:
            return False
        return self.step_name is None or self.step_name == step_name


async def add_rule(
    db_session: AsyncSession,
    instance_id: uuid.UUID | None,
    step_name: str | None,
    mode: str,
    error: str,
) -> ChaosRule:
    if mode not in MODES:
        raise ValueError(f"неизвестный режим инъекции: {mode}")

    rule = ChaosRule(instance_id=instance_id, step_name=step_name, mode=mode, error=error)
    db_session.add(rule)
    await db_session.flush()
    return rule


def _scope(
    instance_id: uuid.UUID, step_name: str
) -> tuple[ColumnElement[bool], ColumnElement[bool]]:
    """Правило подходит, если его инстанс и шаг — заданные или любые."""
    return (
        or_(ChaosRule.instance_id.is_(None), ChaosRule.instance_id == instance_id),
        or_(ChaosRule.step_name.is_(None), ChaosRule.step_name == step_name),
    )


async def find_rule(
    db_session: AsyncSession, instance_id: uuid.UUID, step_name: str
) -> ChaosRule | None:
    """Ищет действующее правило для шага.

    Правило в режиме `once` помечается израсходованным сразу: иначе «сломать
    один раз» ломало бы все последующие шаги.
    """
    instance_filter, step_filter = _scope(instance_id, step_name)

    persistent = (
        await db_session.scalars(
            select(ChaosRule)
            .where(instance_filter, step_filter, ChaosRule.mode != "once")
            .order_by(ChaosRule.created_at)
            .limit(1)
        )
    ).all()
    if persistent:
        return persistent[0]

    once = (
        await db_session.scalars(
            select(ChaosRule)
            .where(instance_filter, step_filter, ChaosRule.mode == "once")
            .order_by(ChaosRule.created_at)
            .limit(1)
        )
    ).all()
    if once:
        once[0].used += 1
        await db_session.flush()
        return once[0]

    return None


async def clear_rules(db_session: AsyncSession) -> int:
    rows = list((await db_session.scalars(select(ChaosRule))).all())
    for rule in rows:
        await db_session.delete(rule)
    await db_session.flush()
    return len(rows)


async def list_rules(db_session: AsyncSession) -> list[ChaosRule]:
    return list((await db_session.scalars(select(ChaosRule).order_by(ChaosRule.created_at))).all())


async def existing_instance(db_session: AsyncSession, instance_id: uuid.UUID) -> bool:
    found = await db_session.get(WorkflowInstance, instance_id)
    return found is not None
