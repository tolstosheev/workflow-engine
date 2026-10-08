"""Базовый декларативный класс моделей."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, MetaData, func, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Единое имя конвенции делает автогенерацию Alembic детерминированной:
# без неё ограничения переименовываются при каждом regenerate.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


# Идентификатор генерирует и ORM, и база: journal.py пишет события сырым
# INSERT ради одной транзакции с изменением состояния, а у такого INSERT нет
# Python-объекта, который подставил бы default.
UUID_PK_DEFAULT = text("gen_random_uuid()")


class TimestampMixin:
    # Значение по умолчанию задаётся и на Python, и на сервере: Python-side
    # нужен, чтобы ORM знал время сразу после INSERT и не пытался читать
    # его лениво из синхронного кода; server_default — для сырых вставок в
    # journal.py, у которых нет Python-объекта.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )
