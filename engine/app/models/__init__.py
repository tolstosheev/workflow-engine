"""Реестр моделей.

Импортируется из `alembic/env.py` неявно, через `app.models.base`; этот модуль
гарантирует, что все таблицы зарегистрированы в `Base.metadata` до autogenerate.
"""

from __future__ import annotations

from app.models.base import Base, TimestampMixin

__all__ = ["Base", "TimestampMixin"]
