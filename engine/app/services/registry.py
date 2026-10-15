"""Реестр воркеров: кто поднялся, кто отвечает, кто молчит (ADR-0018).

Состояние воркера не выводится из журнала — воркер не часть бизнес-процесса,
и его появление не меняет состояние инстанса. Здесь он и есть источник истины
о самом себе.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.registry import Worker

#: Состояния строки реестра. `stale` — не «умер», а «молчит»: упавший воркер
#: остаётся в реестре, иначе падение выглядело бы как отсутствие.
ACTIVE = "active"
STALE = "stale"
STOPPED = "stopped"


def stale_after(poll_interval_seconds: float) -> timedelta:
    """Граница свежести сердца.

    Тройной интервал опроса: короче — воркер, задержавшийся на один цикл, объявлялся
    бы мёртвым; длиннее — зависший числился бы живым.
    """
    return timedelta(seconds=poll_interval_seconds * 3)


#: Сколько живёт запись реестра в выдаче по умолчанию.
#:
#: Идентификатор воркера включает случайную часть, а имя узла у контейнера
#: меняется при каждом пересоздании, поэтому каждое переподключение добавляет
#: строку. Записи старше суток по умолчанию не отдаются — они никуда не удаляются
#: (ADR-0018), а лишь скрыты за переключателем вместе со своим числом.
DEPARTED_AFTER = timedelta(hours=24)


def departed_before(now: datetime | None = None) -> datetime:
    """Момент, раньше которого запись считается ушедшей надолго."""
    return (now or datetime.now(UTC)) - DEPARTED_AFTER


async def register(session: AsyncSession, worker_id: str) -> None:
    """Вписывает воркера в реестр или обновляет его строку.

    `ON CONFLICT` нужен для перезапуска с тем же идентификатором: простой
    `INSERT` ронял бы воркер на второй попытке подъёма, а это как раз тот
    случай, когда система обязана подняться.
    """
    now = datetime.now(UTC)
    await session.execute(
        insert(Worker)
        .values(worker_id=worker_id, started_at=now, heartbeat_at=now)
        .on_conflict_do_update(
            index_elements=[Worker.worker_id],
            set_={
                "started_at": now,
                "heartbeat_at": now,
                "stopped_at": None,
                "cycles_completed": 0,
                "steps_executed": 0,
                "lease_lost": 0,
            },
        )
    )


async def heartbeat(
    session: AsyncSession, worker_id: str, cycles: int = 0, steps: int = 0, lease_lost: int = 0
) -> None:
    """Продлевает сердце воркера и дописывает счётчики.

    Счётчики прибавляются, а не присваиваются: воркер единственный, кто их
    пишет, но при перезапуске с тем же идентификатором они обнуляются — иначе
    счётчик нового воркера начинался бы с числа шагов предыдущего.
    """
    await session.execute(
        update(Worker)
        .where(Worker.worker_id == worker_id)
        .values(
            heartbeat_at=datetime.now(UTC),
            cycles_completed=Worker.cycles_completed + cycles,
            steps_executed=Worker.steps_executed + steps,
            lease_lost=Worker.lease_lost + lease_lost,
        )
    )


async def mark_stopped(session: AsyncSession, worker_id: str) -> None:
    """Отмечает воркера остановленным.

    Упавший воркер сюда не попадает: у него нет последнего кода, и его строка
    остаётся с устаревшим сердцем. Это различие и читается как «остановлен» и
    «не отвечает», и стирать его нельзя.
    """
    await session.execute(
        update(Worker).where(Worker.worker_id == worker_id).values(stopped_at=datetime.now(UTC))
    )


async def list_workers(
    session: AsyncSession, fresh_for: timedelta, include_departed: bool = False
) -> list[Worker]:
    """Строки реестра, свежие сверху.

    Сортировка по сердцу, а не по идентификатору: список открывают, когда что-то
    пошло не так, и первым там нужен тот, кто молчит дольше всех.

    Ушедшие надолго по умолчанию не отдаются. Данные остаются в базе — реестр
    ничего не удаляет (ADR-0018), — но панель показывает то, о чём в ней можно
    принять решение, а не список всех, кто когда-либо запускался.
    """
    statement = select(Worker)
    if not include_departed:
        statement = statement.where(Worker.heartbeat_at >= departed_before())
    return list(await session.scalars(statement.order_by(Worker.heartbeat_at.desc())))


async def count_departed(session: AsyncSession) -> int:
    """Сколько записей скрыто за переключателем."""
    return (
        await session.scalar(
            select(func.count()).select_from(Worker).where(Worker.heartbeat_at < departed_before())
        )
    ) or 0


def worker_state(worker: Worker, now: datetime, fresh_for: timedelta) -> str:
    """Состояние строки реестра, выведенное читателем."""
    if worker.stopped_at is not None:
        return STOPPED
    return ACTIVE if now - worker.heartbeat_at <= fresh_for else STALE
