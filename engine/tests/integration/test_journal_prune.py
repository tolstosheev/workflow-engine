"""Уборка журнала у завершённых и архивных процессов (ADR-0019).

Проверяется правило отбора и то, что после уборки система не врёт. Самое
опасное здесь — не пропустить уборку лишнего, а убрать нужное: у процесса без
журнала разбор состояния невозможен, и интерфейс обязан сказать об этом прямо.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session_factory
from app.models.enums import EventType, InstanceStatus
from app.models.workflow import StepRun, WorkflowDefinition, WorkflowEvent, WorkflowInstance
from scripts.prune import prune_batch, prune_query

# Фикстуры переиспользуются как есть: оборачивать их в свои нельзя — pytest
# считает вызов фикстуры из фикстуры ошибкой, а не наследованием.
from tests.integration.test_instances_api import db_session as db_session

pytestmark = pytest.mark.integration

#: Срок, за который процесс ещё могут разобрать. Число проверяется в тесте:
#: произвольная константа в коде правятся молча и забывают, что значили.
PRUNE_DAYS = 30


async def _instance(
    db_session: AsyncSession,
    status: InstanceStatus,
    *,
    archived: bool | None,
    age_days: float,
) -> str:
    """Процесс с одним событием журнала и заданным возрастом."""
    flow = {"start": "a", "steps": {"a": {"action": "app.demo.handlers:validate_cart"}}}
    definition = WorkflowDefinition(name=f"prune-{uuid.uuid4().hex[:8]}", definition=flow)
    db_session.add(definition)
    await db_session.flush()
    row = WorkflowInstance(
        definition_id=definition.id,
        status=status,
        input={},
        variables={},
        archived_at=datetime.now(UTC) if archived else None,
    )
    db_session.add(row)
    await db_session.flush()
    await db_session.execute(
        text("UPDATE workflow_instances SET updated_at = now() - make_interval(days => :d)"),
        {"d": age_days},
    )
    await db_session.commit()

    # Событие пишется после коммита строки: журнал ссылается на инстанс, и
    # вставка в той же транзакции не увидела бы строки.
    async with get_session_factory()() as session:
        session.add(
            WorkflowEvent(
                instance_id=row.id,
                seq=1,
                type=EventType.INSTANCE_CREATED,
                payload={"start": "a"},
            )
        )
        await session.commit()
    return str(row.id)


async def _prunable(db_session: AsyncSession, **kwargs: Any) -> str:
    return await _instance(db_session, InstanceStatus.COMPLETED, **kwargs)


async def _count(instance_id: str, model: Any = WorkflowEvent) -> int:
    async with get_session_factory()() as session:
        return (
            await session.scalar(
                select(func.count())
                .select_from(model)
                .where(model.instance_id == uuid.UUID(instance_id))
            )
        ) or 0


async def _pruned_at(instance_id: str) -> datetime | None:
    async with get_session_factory()() as session:
        row = await session.get(WorkflowInstance, uuid.UUID(instance_id))
        return row.journal_pruned_at if row is not None else None


# --- Правило отбора ----------------------------------------------------------


async def test_live_process_is_never_pruned(db_session: AsyncSession) -> None:
    """Живой процесс неприкосновенен: его состояние выводится из журнала.

    Убрать события работающего процесса — значит сломать §3.1 тихо: воркер
    продолжит работу, но разбор перестанет объяснять, где он.
    """
    await _instance(db_session, InstanceStatus.RUNNING, archived=True, age_days=400)

    assert await prune_batch(batch=100, days=PRUNE_DAYS) == 0


async def test_paused_process_is_never_pruned(db_session: AsyncSession) -> None:
    # Пауза ждёт решения человека, откат — повтора. Спрятать их уборкой
    # значило бы спрятать незакрытый инцидент.
    await _instance(db_session, InstanceStatus.PAUSED, archived=True, age_days=400)
    await _instance(db_session, InstanceStatus.COMPENSATION_FAILED, archived=True, age_days=400)

    assert await prune_batch(batch=100, days=PRUNE_DAYS) == 0


async def test_archived_but_recent_is_kept(db_session: AsyncSession) -> None:
    instance_id = await _prunable(db_session, archived=True, age_days=2)

    assert await prune_batch(batch=100, days=PRUNE_DAYS) == 0
    assert await _count(instance_id) == 1


async def test_finished_but_not_archived_is_kept(db_session: AsyncSession) -> None:
    # Архив — это явное решение человека убрать процесс из работы. Без него
    # процесс может ещё понадобиться: недавно завершённый заказ разбирают чаще
    # давнего.
    instance_id = await _prunable(db_session, archived=False, age_days=400)

    assert await prune_batch(batch=100, days=PRUNE_DAYS) == 0
    assert await _count(instance_id) == 1


# --- Сама уборка -------------------------------------------------------------


async def test_old_archived_process_is_pruned(db_session: AsyncSession) -> None:
    instance_id = await _prunable(db_session, archived=True, age_days=40)

    assert await prune_batch(batch=100, days=PRUNE_DAYS) == 1
    assert await _count(instance_id) == 0


async def test_pruned_process_is_marked(db_session: AsyncSession) -> None:
    """Пометка обязательна: без неё пустой журнал читается как «событий не было».

    Такой процесс выглядел бы живым и незакрытым, и человек искал бы причину
    там, где её нет.
    """
    instance_id = await _prunable(db_session, archived=True, age_days=40)

    await prune_batch(batch=100, days=PRUNE_DAYS)

    assert await _pruned_at(instance_id) is not None


async def test_pruned_row_is_kept(db_session: AsyncSession) -> None:
    """Строка инстанса остаётся: уборка журнала не есть удаление процесса.

    Иначе уборка тихо ломала бы ADR-0017, где убирание из списка объявлено
    архивированием, а не удалением.
    """
    instance_id = await _prunable(db_session, archived=True, age_days=40)

    await prune_batch(batch=100, days=PRUNE_DAYS)

    async with get_session_factory()() as session:
        assert await session.get(WorkflowInstance, uuid.UUID(instance_id)) is not None


async def test_second_prune_does_nothing(db_session: AsyncSession) -> None:
    await _prunable(db_session, archived=True, age_days=40)

    assert await prune_batch(batch=100, days=PRUNE_DAYS) == 1
    assert await prune_batch(batch=100, days=PRUNE_DAYS) == 0


async def test_prune_respects_batch_size(db_session: AsyncSession) -> None:
    for _ in range(3):
        await _prunable(db_session, archived=True, age_days=40)

    assert await prune_batch(batch=2, days=PRUNE_DAYS) == 2


async def test_step_runs_go_with_the_journal(db_session: AsyncSession) -> None:
    """Прогоны шагов убираются вместе с журналом: они описывают тот же процесс.

    Оставшиеся прогоны без событий выглядели бы как процесс, который что-то
    делал, но без следа почему.
    """
    instance_id = await _prunable(db_session, archived=True, age_days=40)
    async with get_session_factory()() as session:
        session.add(
            StepRun(
                instance_id=uuid.UUID(instance_id),
                step_name="a",
                attempt=1,
                status="COMPLETED",
                input={},
            )
        )
        await session.commit()

    await prune_batch(batch=100, days=PRUNE_DAYS)

    assert await _count(instance_id, StepRun) == 0


# --- Защита в самой базе -----------------------------------------------------


async def test_plain_delete_is_still_forbidden(db_session: AsyncSession) -> None:
    """Обычный `DELETE` по журналу падает, даже если процесс старый и архивный.

    Служебный путь уборки включается настройкой сессии, а не отменой защиты:
    иначе «кто-то однажды удалил события» перестало бы быть ошибкой.
    """
    from sqlalchemy import delete as sql_delete

    from app.models.workflow import WorkflowEvent as Events

    instance_id = await _prunable(db_session, archived=True, age_days=40)
    async with get_session_factory()() as session:
        with pytest.raises(Exception, match="append-only"):
            await session.execute(
                sql_delete(Events).where(Events.instance_id == uuid.UUID(instance_id))
            )


async def test_flag_does_not_open_a_live_process(db_session: AsyncSession) -> None:
    """Даже с объявленной уборкой журнал живого процесса не удаляется.

    Проверяет базу, а не скрипт: триггер смотрит состояние и архивность сам,
    поэтому ошибка в отборе уборки не может стать удалением чужой истории.
    """
    from sqlalchemy import delete as sql_delete
    from sqlalchemy import text as sql_text

    from app.models.workflow import WorkflowEvent as Events

    instance_id = await _instance(db_session, InstanceStatus.RUNNING, archived=True, age_days=400)
    async with get_session_factory()() as session:
        await session.execute(sql_text("SET LOCAL app.journal_prune = 'on'"))
        with pytest.raises(Exception, match="append-only"):
            await session.execute(
                sql_delete(Events).where(Events.instance_id == uuid.UUID(instance_id))
            )


async def test_update_stays_forbidden_under_the_flag(db_session: AsyncSession) -> None:
    """`UPDATE` запрещён всегда: уборка удаляет, но не переписывает историю.

    Право переписать событие сделало бы журнал ненадёжным всерьёз: разбор
    перестал бы объяснять, что было на самом деле.
    """
    from sqlalchemy import text as sql_text

    from app.models.workflow import WorkflowEvent as Events

    instance_id = await _prunable(db_session, archived=True, age_days=40)
    async with get_session_factory()() as session:
        await session.execute(sql_text("SET LOCAL app.journal_prune = 'on'"))
        with pytest.raises(Exception, match="append-only"):
            await session.execute(
                sql_text("UPDATE workflow_events SET payload = '{}' WHERE instance_id = :id"),
                {"id": uuid.UUID(instance_id)},
            )
        assert await _count(instance_id, Events) == 1


# --- Что видит вызывающий ----------------------------------------------------


async def test_state_of_pruned_process_declares_it(db_session: AsyncSession) -> None:
    """Разбор убранного журнала не выдаётся за состояние процесса.

    Разбор нуля событий даёт `PENDING`, и ответ выглядел бы как испорченный
    процесс: страница показывала бы «ждёт» завершённому заказу. Ответ обязан
    сказать, что журнала нет.
    """
    from app.services.instances import rebuild_state

    instance_id = await _prunable(db_session, archived=True, age_days=40)
    await prune_batch(batch=100, days=PRUNE_DAYS)

    state = await rebuild_state(db_session, uuid.UUID(instance_id))

    assert state["journal_pruned_at"] is not None
    # Состояние берётся из колонки: разбирать нечего, а показывать «ждёт» —
    # это враньё, которое человек принял бы за зависший процесс.
    assert state["status"] == InstanceStatus.COMPLETED
    assert state["is_terminal"] is True


async def test_state_of_live_process_has_no_prune_mark(db_session: AsyncSession) -> None:
    from app.services.instances import rebuild_state

    instance_id = await _instance(db_session, InstanceStatus.RUNNING, archived=False, age_days=1)

    state = await rebuild_state(db_session, uuid.UUID(instance_id))

    # Отметка об уборке обязана отсутствовать там, где уборки не было: иначе
    # интерфейс объявил бы процесс разбираемым там, где разбор работает.
    assert state["journal_pruned_at"] is None


async def test_prune_survives_a_compensation_log(db_session: AsyncSession) -> None:
    """Уборка идёт по порядку внешних ключей, а не по списку моделей.

    `compensation_log` ссылается на `step_runs`, и удаление прогонов первыми
    роняет уборку. На тестовых процессах без отката этого не видно: строк
    компенсации нет, а на стенде процесс с компенсацией есть всегда.
    """
    from app.models.enums import CompensationStatus
    from app.models.workflow import CompensationLog

    instance_id = await _prunable(db_session, archived=True, age_days=40)
    async with get_session_factory()() as session:
        run = StepRun(
            instance_id=uuid.UUID(instance_id),
            step_name="a",
            attempt=1,
            status="COMPLETED",
            input={},
        )
        session.add(run)
        await session.flush()
        session.add(
            CompensationLog(
                instance_id=uuid.UUID(instance_id),
                step_run_id=run.id,
                compensation_step="app.demo.handlers:release_inventory",
                attempt=1,
                status=CompensationStatus.COMPLETED,
            )
        )
        await session.commit()

    assert await prune_batch(batch=100, days=PRUNE_DAYS) == 1
    assert await _count(instance_id, CompensationLog) == 0
    assert await _count(instance_id, StepRun) == 0


# --- Формулировка отбора -----------------------------------------------------


def test_prune_query_states_the_rule() -> None:
    """Отбор — это утверждение о том, что уборка имеет право удалить.

    Проверка читает условие текстом: «терминальный, архивный, старше отсечки,
    ещё не убран». Расширение круга жертв обязано быть заметным здесь.
    """
    compiled = str(prune_query(datetime.now(UTC))).lower()

    assert "archived_at is not null" in compiled
    assert "journal_pruned_at is null" in compiled
    assert "updated_at" in compiled
    # `compensating` означало бы уборку процесса посреди отката: его план
    # строится по журналу, и без событий откат стал бы пустым.
    assert "compensating" not in compiled


async def test_cutoff_moves_with_the_window(db_session: AsyncSession) -> None:
    """Окно влияет на результат, а не декларируется в тексте.

    Процесс ровно посередине окна убирается при коротком сроке и остаётся при
    длинном — иначе число в команде было бы просто числом.
    """
    instance_id = await _prunable(db_session, archived=True, age_days=10)

    assert await prune_batch(batch=100, days=5) == 1

    other = await _prunable(db_session, archived=True, age_days=10)
    assert other != instance_id
    assert await prune_batch(batch=100, days=30) == 0
