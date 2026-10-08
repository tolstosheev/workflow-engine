"""Тесты реестра моделей.

Модуль существует ради единой точки импорта: autogenerate Alembic собирает
метаданные из `Base.metadata`, и пустой реестр означал бы пустую миграцию.
"""

from __future__ import annotations

from app.models import Base, TimestampMixin
from app.models.base import NAMING_CONVENTION


def test_base_is_exported_from_registry() -> None:
    from app.models.base import Base as BaseFromModule

    assert Base is BaseFromModule


def test_metadata_has_naming_convention() -> None:
    # Без конвенции autogenerate переименовывает ограничения при каждом
    # регенерировании схемы, и diff миграций становится нечитаемым.
    assert Base.metadata.naming_convention == NAMING_CONVENTION


def test_timestamp_mixin_declares_created_and_updated() -> None:
    assert "created_at" in TimestampMixin.__annotations__
    assert "updated_at" in TimestampMixin.__annotations__
