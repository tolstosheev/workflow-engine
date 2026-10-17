"""Реестр воркеров (ADR-0018).

Воркер сам объявляет о себе: без отдельной сущности простаивающий воркер не
оставляет следов, потому что аренды у него нет, а вопрос «работает ли он» идёт
до `docker compose ps` — то есть мимо системы.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import UUID_PK_DEFAULT, Base


class Worker(Base):
    """Запись о работающем или упавшем воркере."""

    __tablename__ = "workers"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=UUID_PK_DEFAULT
    )
    # Идентификатор из логов и из колонки аренды: тот же самый, иначе строки
    # реестра не сопоставить с тем, что видно на странице инстанса.
    worker_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Заполняется только при штатной остановке. Упавший воркер остаётся в
    # реестре со stale-heartbeat'ом: отсутствие сигнала не означает отсутствия
    # объекта — тот же приём, что и с арендой (§3.3).
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cycles_completed: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    steps_executed: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    lease_lost: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    __table_args__ = (
        # Список открывают целиком и он упорядочен по свежести сердца: по
        # worker_id сортировать нельзя, индекс по нему уникальный и не про обход.
        Index("ix_workers_heartbeat_at", "heartbeat_at"),
    )
