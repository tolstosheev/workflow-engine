"""Тесты Saga и durable-таймеров на реальной БД."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine import saga, scheduler
from app.engine.journal import read_events, replay
from app.models.enums import CompensationStatus, EventType, InstanceStatus, StepRunStatus
from app.models.workflow import (
    CompensationLog,
    StepRun,
    Timer,
    WorkflowDefinition,
    WorkflowInstance,
)

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
        name=f"saga-{uuid.uuid4().hex[:8]}",
        definition={"start": "a", "steps": {"a": {"action": "app.demo.handlers:validate_cart"}}},
    )
    db_session.add(definition)
    await db_session.flush()

    instance = WorkflowInstance(definition_id=definition.id, input={}, variables={})
    db_session.add(instance)
    await db_session.flush()
    return instance.id


async def _completed_step(
    db_session: AsyncSession, instance_id: uuid.UUID, step_name: str
) -> uuid.UUID:
    """Записывает успешное выполнение шага с возрастающим временем завершения."""
    existing = await db_session.scalar(
        select(func_count()).where(
            StepRun.step_name == step_name, StepRun.instance_id == instance_id
        )
    )
    step_run = StepRun(
        instance_id=instance_id,
        step_name=step_name,
        attempt=1,
        status=StepRunStatus.COMPLETED,
        finished_at=datetime.now(UTC) + timedelta(seconds=existing or 0),
    )
    db_session.add(step_run)
    await db_session.flush()
    return step_run.id


async def _completed_event(
    db_session: AsyncSession, instance_id: uuid.UUID, step_name: str
) -> None:
    """Записывает `STEP_COMPLETED` в журнал — источник истины о выполнении."""
    from app.engine.journal import append_event

    await append_event(db_session, instance_id, EventType.STEP_COMPLETED, {"step": step_name})


def func_count() -> object:
    from sqlalchemy import func

    return func.count()


async def _failed_step(db_session: AsyncSession, instance_id: uuid.UUID, step_name: str) -> None:
    """Шаг с провалом не компенсируется: эффекта не было."""
    db_session.add(
        StepRun(
            instance_id=instance_id,
            step_name=step_name,
            attempt=1,
            status=StepRunStatus.FAILED,
            finished_at=datetime.now(UTC),
        )
    )
    await db_session.flush()


# --- Выбор шагов к компенсации ---------------------------------------------


async def test_completed_steps_are_returned(db_session: AsyncSession) -> None:
    instance_id = await _new_instance(db_session)
    await _completed_step(db_session, instance_id, "a")

    assert await saga.completed_steps(db_session, instance_id) == ["a"]


async def test_failed_steps_are_not_compensated(db_session: AsyncSession) -> None:
    # Шаг, упавший на первой попытке, эффекта не оказал — отменять нечего.
    instance_id = await _new_instance(db_session)
    await _failed_step(db_session, instance_id, "charge")

    assert await saga.completed_steps(db_session, instance_id) == []


async def test_compensation_plan_is_reverse_order(db_session: AsyncSession) -> None:
    instance_id = await _new_instance(db_session)
    await _completed_step(db_session, instance_id, "validate")
    await _completed_step(db_session, instance_id, "reserve")
    await _completed_step(db_session, instance_id, "charge")

    plan = await saga.plan(
        db_session,
        instance_id,
        {
            "validate": "app.demo.handlers:undo_validate",
            "reserve": "app.demo.handlers:release_inventory",
            "charge": "app.demo.handlers:refund_payment",
        },
    )

    assert [item.step_name for item in plan] == ["charge", "reserve", "validate"]


async def test_plan_from_journal_matches_plan_from_step_runs(db_session: AsyncSession) -> None:
    """Порядок из журнала и из step_runs должен совпадать.

    План отката строится по журналу: колонка инстанса отстаёт на шаг,
    завершившийся в текущей итерации, и отмена, пришедшая в ту же итерацию,
    вырвалась бы в пустой план при занятом резерве.
    """
    instance_id = await _new_instance(db_session)
    for name in ("validate", "reserve", "charge"):
        await _completed_step(db_session, instance_id, name)
        await _completed_event(db_session, instance_id, name)

    refs = {
        "validate": "app.demo.handlers:undo_validate",
        "reserve": "app.demo.handlers:release_inventory",
        "charge": "app.demo.handlers:refund_payment",
    }
    from_runs = await saga.plan(db_session, instance_id, refs)
    from_journal = await saga.plan(
        db_session,
        instance_id,
        refs,
        executed_steps=["validate", "reserve", "charge"],
    )

    assert [item.step_name for item in from_journal] == [item.step_name for item in from_runs]


async def test_plan_from_journal_includes_step_absent_in_step_runs(
    db_session: AsyncSession,
) -> None:
    """Шаг, завершившийся в текущей итерации, обязан попасть в откат.

    Это и есть регрессия: прогон шага пишется в одной транзакции, а вызов
    отката — в следующей, и между ними шаг успевает исчезнуть из выборки по
    колонке.
    """
    instance_id = await _new_instance(db_session)
    await _completed_event(db_session, instance_id, "reserve")

    plan = await saga.plan(
        db_session,
        instance_id,
        {"reserve": "app.demo.handlers:release_inventory"},
        executed_steps=["reserve"],
    )

    # Без прогона компенсировать нечего, но шаг не должен исчезнуть молча:
    # решение принимает воркер, а не валидатор.
    assert plan == []
    assert await saga.completed_steps(db_session, instance_id) == []


async def test_steps_without_compensation_are_skipped(db_session: AsyncSession) -> None:
    instance_id = await _new_instance(db_session)
    await _completed_step(db_session, instance_id, "reserve")
    await _completed_step(db_session, instance_id, "validate")

    plan = await saga.plan(
        db_session,
        instance_id,
        {"reserve": "app.demo.handlers:release_inventory", "validate": None},
    )

    assert [item.step_name for item in plan] == ["reserve"]


async def test_plan_is_empty_when_nothing_completed(db_session: AsyncSession) -> None:
    instance_id = await _new_instance(db_session)

    assert await saga.plan(db_session, instance_id, {}) == []


async def test_compensated_steps_leave_the_plan(db_session: AsyncSession) -> None:
    # Повторный откат уже откатанного привёл бы к двойному возврату.
    instance_id = await _new_instance(db_session)
    step_run_id = await _completed_step(db_session, instance_id, "reserve")
    step_run = await db_session.get(StepRun, step_run_id)
    assert step_run is not None
    step_run.compensated = True
    await db_session.flush()

    plan = await saga.plan(
        db_session, instance_id, {"reserve": "app.demo.handlers:release_inventory"}
    )

    assert plan == []


async def test_plan_covers_every_execution_of_a_looped_step(db_session: AsyncSession) -> None:
    """Шаг, выполненный дважды, откатывается дважды.

    План строился по имени шага и брал «последнее выполнение»: обе итерации
    цикла получали один прогон, а вторая компенсация приходила под занятым
    ключом и не отменяла ничего. На стенде это оставляло резерв прохода на
    складе навсегда.
    """
    instance_id = await _new_instance(db_session)
    await _completed_step(db_session, instance_id, "reserve")
    await _completed_step(db_session, instance_id, "reserve")

    plan = await saga.plan(
        db_session,
        instance_id,
        {"reserve": "app.demo.handlers:release_inventory"},
        executed_steps=["reserve", "reserve"],
    )

    assert len(plan) == 2
    assert len({item.step_run_id for item in plan}) == 2


async def test_plan_pairs_loop_passes_with_their_own_runs(db_session: AsyncSession) -> None:
    """Обратный порядок откатывает свежий проход первым.

    Порядок берётся из журнала, а прогон — по факту выполнения; связать их иначе
    нечем, и «первым откатывать последний проход» — единственное чтение, при
    котором обратный порядок вообще имеет смысл.
    """
    instance_id = await _new_instance(db_session)
    earlier = await _completed_step(db_session, instance_id, "reserve")
    later = await _completed_step(db_session, instance_id, "reserve")

    plan = await saga.plan(
        db_session,
        instance_id,
        {"reserve": "app.demo.handlers:release_inventory"},
        executed_steps=["reserve", "reserve"],
    )

    assert [item.step_run_id for item in plan] == [later, earlier]


# --- Попытки компенсации ----------------------------------------------------


async def test_attempt_start_writes_event(db_session: AsyncSession) -> None:
    instance_id = await _new_instance(db_session)
    await _completed_step(db_session, instance_id, "reserve")
    item = (
        await saga.plan(db_session, instance_id, {"reserve": "app.demo.handlers:release_inventory"})
    )[0]

    log = await saga.start_attempt(db_session, instance_id, item, attempt=1)

    assert log.status == CompensationStatus.RUNNING
    events = await read_events(db_session, instance_id)
    assert EventType.COMPENSATION_STARTED in [e.type for e in events]


async def test_interrupted_attempt_does_not_block_the_next_one(
    db_session: AsyncSession,
) -> None:
    """Попытка, оставшаяся незакрытой, не запирает откат навсегда.

    Регрессия из аудита: исполнитель умирает или падает между вызовом
    обработчика и закрытием попытки, и запись остаётся RUNNING. Частичный
    уникальный индекс допускает одну незакрытую запись на прогон, поэтому
    следующая попытка падала бы на нарушении уникальности — процесс навсегда
    оставался бы в компенсации, а список ручного разбора был бы пустым.
    """
    instance_id = await _new_instance(db_session)
    step_run_id = await _completed_step(db_session, instance_id, "reserve")
    item = (
        await saga.plan(db_session, instance_id, {"reserve": "app.demo.handlers:release_inventory"})
    )[0]
    interrupted = await saga.start_attempt(db_session, instance_id, item, attempt=1)
    # Исполнитель пропал: попытка не закрыта — ни времени, ни статуса.
    assert interrupted.finished_at is None
    assert interrupted.status == CompensationStatus.RUNNING

    retry = await saga.start_attempt(
        db_session, instance_id, item, attempt=item.attempts_made + 1
    )

    # Прерванная попытка обязана быть закрыта и видна как провал: иначе на
    # экране не будет ничего, за что можно взяться.
    assert interrupted.status == CompensationStatus.FAILED
    assert interrupted.finished_at is not None
    assert interrupted.error is not None
    assert retry.id != interrupted.id
    assert retry.step_run_id == step_run_id
    pending = await saga.pending_compensations(db_session, instance_id)
    assert [p.error for p in pending] == [interrupted.error]


async def test_interrupted_attempt_is_reflected_in_the_journal(
    db_session: AsyncSession,
) -> None:
    """Прерванная попытка пишется в журнал, а не остаётся в базе молча."""
    instance_id = await _new_instance(db_session)
    await _completed_step(db_session, instance_id, "reserve")
    item = (
        await saga.plan(db_session, instance_id, {"reserve": "app.demo.handlers:release_inventory"})
    )[0]
    await saga.start_attempt(db_session, instance_id, item, attempt=1)

    await saga.start_attempt(db_session, instance_id, item, attempt=2)

    events = await read_events(db_session, instance_id)
    failures = [e for e in events if e.type == EventType.COMPENSATION_FAILED]
    assert len(failures) == 1
    # Провал виден в разборе журнала, а не в колонке: закрытая провалом
    # попытка пишет `COMPENSATION_FAILED`, и следующая попытка возвращает
    # процесс в откат — читается это по журналу (§3.1), и по нему же видно,
    # что попытка была прервана, а не провалена обработчиком.
    replayed = replay(events)
    assert replayed.compensation_for == "reserve"
    errors = [e.payload.get("error") for e in failures]
    assert errors and all("прервана" in str(error) for error in errors)


async def test_successful_attempt_marks_step_compensated(db_session: AsyncSession) -> None:
    instance_id = await _new_instance(db_session)
    step_run_id = await _completed_step(db_session, instance_id, "reserve")
    item = (
        await saga.plan(db_session, instance_id, {"reserve": "app.demo.handlers:release_inventory"})
    )[0]
    log = await saga.start_attempt(db_session, instance_id, item, attempt=1)

    outcome = await saga.finish_attempt(db_session, instance_id, log, item, succeeded=True)

    assert outcome.succeeded is True
    assert log.status == CompensationStatus.COMPLETED
    assert log.finished_at is not None
    # Откатанный шаг помечен: второй проход плана обязан его пропустить, иначе
    # компенсация вернула бы средства дважды.
    step_run = await db_session.get(StepRun, step_run_id)
    assert step_run is not None
    assert step_run.compensated is True


async def test_failed_attempt_is_visible_for_manual_review(db_session: AsyncSession) -> None:
    # Провал компенсации — не тихий сбой: он обязан остаться в состоянии,
    # которое видно и требует разбора.
    instance_id = await _new_instance(db_session)
    await _completed_step(db_session, instance_id, "reserve")
    item = (
        await saga.plan(db_session, instance_id, {"reserve": "app.demo.handlers:release_inventory"})
    )[0]
    log = await saga.start_attempt(db_session, instance_id, item, attempt=1)

    outcome = await saga.finish_attempt(
        db_session, instance_id, log, item, succeeded=False, error="сервис недоступен"
    )

    assert outcome.succeeded is False
    assert log.status == CompensationStatus.FAILED
    assert await saga.failed_count(db_session, instance_id) == 1
    pending = await saga.pending_compensations(db_session, instance_id)
    assert [p.error for p in pending] == ["сервис недоступен"]


async def test_failure_event_carries_error(db_session: AsyncSession) -> None:
    instance_id = await _new_instance(db_session)
    await _completed_step(db_session, instance_id, "reserve")
    item = (
        await saga.plan(db_session, instance_id, {"reserve": "app.demo.handlers:release_inventory"})
    )[0]
    log = await saga.start_attempt(db_session, instance_id, item, attempt=1)
    await saga.finish_attempt(db_session, instance_id, log, item, False, "таймаут")

    state = replay(await read_events(db_session, instance_id))

    assert state.status.value == "COMPENSATION_FAILED"


async def test_attempts_counted_from_log(db_session: AsyncSession) -> None:
    # Счётчик обязан переживать перезапуск воркера, поэтому берётся из БД.
    instance_id = await _new_instance(db_session)
    step_run_id = await _completed_step(db_session, instance_id, "reserve")
    item = (
        await saga.plan(db_session, instance_id, {"reserve": "app.demo.handlers:release_inventory"})
    )[0]

    assert item.attempts_made == 0
    await saga.start_attempt(db_session, instance_id, item, attempt=1)

    assert await saga.attempts_made(db_session, step_run_id) == 1


async def test_operation_key_is_namespaced_by_compensation(db_session: AsyncSession) -> None:
    # Ключ компенсации не должен совпадать с ключом основного шага: иначе
    # откат был бы признан уже применённым. И он не зависит от номера попытки:
    # компенсация — одна операция, и повтор обязан узнать свою отметку.
    instance_id = await _new_instance(db_session)
    step_run_id = await _completed_step(db_session, instance_id, "reserve")
    item = (
        await saga.plan(db_session, instance_id, {"reserve": "app.demo.handlers:release_inventory"})
    )[0]

    key = saga.operation_key(instance_id, item)

    assert key.step_name.startswith("compensate:reserve")
    assert str(key) == f"{instance_id}:compensate:reserve:{step_run_id}:0"


async def test_operation_key_label_fits_the_column(db_session: AsyncSession) -> None:
    """Подпись в колонке — имя шага, а не ключ.

    Ключ компенсации содержит `compensate:` и идентификатор прогона, и на длинном
    имени шага он переставал помещаться в `processed_operations.step_name`
    (`varchar(128)`): запись падала не на уникальности, а на длине, и это
    исключение уходило из отката. Идентичность остаётся в `op_key`.
    """
    instance_id = await _new_instance(db_session)
    long_name = "s" + "t" * 119
    await _completed_step(db_session, instance_id, long_name)
    item = (
        await saga.plan(db_session, instance_id, {long_name: "app.demo.handlers:release_inventory"})
    )[0]

    key = saga.operation_key(instance_id, item)

    # Ключ длиннее колонки — и это нормально: он лежит в `op_key` шириной 256.
    assert len(str(key)) > 128
    assert len(key.label_name) <= 128
    assert key.label_name == long_name


async def test_operation_key_distinguishes_passes_of_a_loop(
    db_session: AsyncSession,
) -> None:
    """Два прохода одного шага — две операции с разными ключами.

    По одному имени шага вторая компенсация пришла бы под занятым ключом,
    объявила бы «уже применено» и оставила резерв прохода на складе.
    """
    instance_id = await _new_instance(db_session)
    await _completed_step(db_session, instance_id, "reserve")
    await _completed_step(db_session, instance_id, "reserve")
    items = await saga.plan(
        db_session,
        instance_id,
        {"reserve": "app.demo.handlers:release_inventory"},
        executed_steps=["reserve", "reserve"],
    )

    keys = [str(saga.operation_key(instance_id, item)) for item in items]

    assert len(set(keys)) == 2


# --- Durable-таймеры ---------------------------------------------------------


async def test_timer_is_created_with_due_date(db_session: AsyncSession) -> None:
    instance_id = await _new_instance(db_session)

    timer = await scheduler.schedule(db_session, instance_id, "confirm", 60)

    assert timer.due_at > datetime.now(UTC)
    assert timer.fired is False


async def test_scheduling_writes_event(db_session: AsyncSession) -> None:
    # Таймер без события не виден в replay, и инстанс остался бы RUNNING
    # вместо WAITING.
    instance_id = await _new_instance(db_session)

    await scheduler.schedule(db_session, instance_id, "confirm", 60)

    events = await read_events(db_session, instance_id)
    assert events[-1].type == EventType.TIMER_SCHEDULED


async def test_replay_shows_waiting_state(db_session: AsyncSession) -> None:
    instance_id = await _new_instance(db_session)
    await scheduler.schedule(db_session, instance_id, "confirm", 60)

    state = replay(await read_events(db_session, instance_id))

    assert state.status.value == "WAITING"


async def test_future_timer_is_not_claimed(db_session: AsyncSession) -> None:
    instance_id = await _new_instance(db_session)
    await scheduler.schedule(db_session, instance_id, "confirm", 3600)

    assert await scheduler.claim_due(db_session) == []


async def test_due_timer_is_claimed(db_session: AsyncSession) -> None:
    instance_id = await _new_instance(db_session)
    timer = await scheduler.schedule(db_session, instance_id, "confirm", 0)
    timer.due_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.flush()

    claimed = await scheduler.claim_due(db_session)

    assert [t.id for t in claimed] == [timer.id]
    # Подбор не помечает таймер: отметка идёт в fire_due вместе с событием,
    # иначе падение между ними оставило бы инстанс спящим навсегда.
    assert claimed[0].fired is False


async def test_timer_is_fired_only_once(db_session: AsyncSession) -> None:
    # Второй воркер не должен сработать тот же таймер: он уже обработан.
    instance_id = await _new_instance(db_session)
    timer = await scheduler.schedule(db_session, instance_id, "confirm", 0)
    timer.due_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.flush()

    claimed = (await scheduler.claim_due(db_session))[0]
    assert await scheduler.fire_due(db_session, claimed) is True
    assert await scheduler.fire_due(db_session, claimed) is False


async def test_fired_timer_wakes_instance(db_session: AsyncSession) -> None:
    # Статус в колонке и в replay обязан стать RUNNING: без этого воркер не
    # подберёт инстанс и шаг после ожидания не выполнится.
    instance_id = await _new_instance(db_session)
    timer = await scheduler.schedule(db_session, instance_id, "confirm", 0)
    timer.due_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.flush()
    claimed = (await scheduler.claim_due(db_session))[0]

    await scheduler.fire_due(db_session, claimed)

    state = replay(await read_events(db_session, instance_id))
    assert state.status.value == "RUNNING"
    assert state.current_step == "confirm"

    row = await db_session.get(WorkflowInstance, instance_id)
    assert row is not None
    assert row.status == InstanceStatus.RUNNING


async def test_fire_due_is_skipped_when_already_fired(db_session: AsyncSession) -> None:
    # Гонка двух воркеров: выигрывает один, второй не пишет событие вторично.
    instance_id = await _new_instance(db_session)
    timer = await scheduler.schedule(db_session, instance_id, "confirm", 0)
    timer.due_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.flush()
    claimed = (await scheduler.claim_due(db_session))[0]

    assert await scheduler.fire_due(db_session, claimed) is True
    await scheduler.fire_due(db_session, claimed)

    events = await read_events(db_session, instance_id)
    assert [e.type for e in events].count(EventType.TIMER_FIRED) == 1


async def test_planned_for_sees_fired_timer(db_session: AsyncSession) -> None:
    # `wait_before_seconds` ждёт один раз: после срабатывания таймера новый
    # ожидатель создаваться не должен, иначе шаг не наступил бы никогда.
    instance_id = await _new_instance(db_session)
    timer = await scheduler.schedule(db_session, instance_id, "confirm", 0)
    timer.due_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.flush()

    assert await scheduler.planned_for(db_session, instance_id, "confirm") is True
    assert await scheduler.planned_for(db_session, instance_id, "other") is False

    await scheduler.fire_due(db_session, (await scheduler.claim_due(db_session))[0])

    assert await scheduler.planned_for(db_session, instance_id, "confirm") is True


async def test_pending_count_tracks_unfired(db_session: AsyncSession) -> None:
    instance_id = await _new_instance(db_session)
    await scheduler.schedule(db_session, instance_id, "confirm", 3600)

    assert await scheduler.pending_count(db_session, instance_id) == 1


async def test_cancel_marks_timers_fired(db_session: AsyncSession) -> None:
    # При откате ждать таймер бессмысленно, а неотменённый таймер разбудил бы
    # процесс уже после компенсации.
    instance_id = await _new_instance(db_session)
    await scheduler.schedule(db_session, instance_id, "confirm", 3600)

    assert await scheduler.cancel_for(db_session, instance_id) == 1
    assert await scheduler.pending_count(db_session, instance_id) == 0


async def test_cancel_is_idempotent(db_session: AsyncSession) -> None:
    instance_id = await _new_instance(db_session)
    await scheduler.schedule(db_session, instance_id, "confirm", 3600)
    await scheduler.cancel_for(db_session, instance_id)

    assert await scheduler.cancel_for(db_session, instance_id) == 0


async def test_compensation_log_has_single_active_attempt(db_session: AsyncSession) -> None:
    # Частичный индекс не даёт открыть вторую параллельную компенсацию одного
    # шага: две отмены резерва — это двойной возврат.
    from sqlalchemy.exc import IntegrityError

    instance_id = await _new_instance(db_session)
    step_run_id = await _completed_step(db_session, instance_id, "reserve")
    item = (
        await saga.plan(db_session, instance_id, {"reserve": "app.demo.handlers:release_inventory"})
    )[0]
    await saga.start_attempt(db_session, instance_id, item, attempt=1)

    db_session.add(
        CompensationLog(
            instance_id=instance_id,
            step_run_id=step_run_id,
            compensation_step=item.compensation_ref,
            attempt=2,
            status=CompensationStatus.RUNNING,
        )
    )

    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


async def test_timer_rows_are_scoped_to_instance(db_session: AsyncSession) -> None:
    first = await _new_instance(db_session)
    second = await _new_instance(db_session)
    await scheduler.schedule(db_session, first, "confirm", 3600)

    assert await scheduler.pending_count(db_session, second) == 0
    rows = await db_session.scalars(select(Timer).where(Timer.instance_id == second))
    assert rows.all() == []
