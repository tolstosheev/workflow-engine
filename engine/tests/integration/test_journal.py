"""Тесты журнала событий и replay.

Replay — чистая функция, поэтому часть проверок не нуждается в БД: важно
проверить детерминированность и корректность переходов, а не SQL.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.journal import (
    InstanceState,
    RecordedEvent,
    append_event,
    read_events,
    replay,
    steps_to_compensate,
)
from app.models.enums import EventType, InstanceStatus
from app.models.workflow import WorkflowDefinition, WorkflowEvent, WorkflowInstance

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


async def _new_instance(db_session: AsyncSession, start: str = "first") -> uuid.UUID:
    definition = WorkflowDefinition(
        name=f"journal-{uuid.uuid4().hex[:8]}",
        definition={
            "start": start,
            "steps": {start: {"action": "app.demo.handlers:validate_cart"}},
        },
    )
    db_session.add(definition)
    await db_session.flush()

    instance = WorkflowInstance(definition_id=definition.id, input={}, variables={})
    db_session.add(instance)
    await db_session.flush()
    return instance.id


def _event(seq: int, event_type: EventType, **payload: Any) -> RecordedEvent:
    return RecordedEvent(seq=seq, type=event_type, payload=payload, created_at=datetime.now(UTC))


# --- Replay: чистая логика -------------------------------------------------


def test_empty_journal_gives_pending_state() -> None:
    state = replay([])

    assert state.status is InstanceStatus.PENDING
    assert state.current_step is None
    assert state.variables == {}


def test_replay_is_deterministic() -> None:
    events = [
        _event(0, EventType.INSTANCE_CREATED, start="a"),
        _event(1, EventType.STEP_STARTED, step="a"),
        _event(2, EventType.STEP_COMPLETED, step="a", output={"x": 1}, next="b"),
        _event(3, EventType.STEP_STARTED, step="b"),
    ]

    assert replay(events) == replay(events)
    assert replay(events) == replay(list(events))


def test_created_event_sets_start_step() -> None:
    state = replay([_event(0, EventType.INSTANCE_CREATED, start="a")])

    assert state.current_step == "a"


def test_step_completed_merges_output_into_variables() -> None:
    # Выход шага становится переменной инстанса: следующий шаг получает данные
    # через variables, а не через возвращаемое значение вызывающего кода.
    state = replay(
        [
            _event(0, EventType.INSTANCE_CREATED, start="a"),
            _event(1, EventType.STEP_COMPLETED, step="a", output={"total": 10}, next="b"),
        ]
    )

    assert state.variables == {"total": 10}
    assert state.current_step == "b"
    assert state.completed_steps == ["a"]


def test_step_completed_without_output_keeps_variables() -> None:
    state = replay(
        [
            _event(0, EventType.STEP_COMPLETED, step="a", output={"total": 1}),
            _event(1, EventType.STEP_COMPLETED, step="b"),
        ]
    )

    assert state.variables == {"total": 1}


def test_completed_step_is_not_recorded_twice() -> None:
    # Повтор шага после ретрая не должен удваивать запись: иначе компенсация
    # выполнит его дважды.
    state = replay(
        [
            _event(0, EventType.STEP_COMPLETED, step="a"),
            _event(1, EventType.STEP_COMPLETED, step="a"),
        ]
    )

    assert state.completed_steps == ["a"]


def test_iteration_counter_grows_on_every_completion() -> None:
    # Счётчик проходов растёт, пока список выполненных шагов остаётся
    # уникальным: циклу нужен счётчик, компенсации — порядок.
    state = replay(
        [
            _event(0, EventType.STEP_COMPLETED, step="a"),
            _event(1, EventType.STEP_COMPLETED, step="a"),
            _event(2, EventType.STEP_COMPLETED, step="a"),
        ]
    )

    assert state.step_iterations == {"a": 3}
    assert state.completed_steps == ["a"]


def test_iteration_counter_keeps_steps_apart() -> None:
    state = replay(
        [
            _event(0, EventType.STEP_COMPLETED, step="a"),
            _event(1, EventType.STEP_COMPLETED, step="b"),
            _event(2, EventType.STEP_COMPLETED, step="a"),
        ]
    )

    assert state.step_iterations == {"a": 2, "b": 1}


def test_failed_step_does_not_count_as_iteration() -> None:
    # Ошибочный повтор не завершает шаг, поэтому проходом он не считается:
    # иначе предел цикла расходовался бы на неудачные попытки.
    state = replay(
        [
            _event(0, EventType.STEP_COMPLETED, step="a"),
            _event(1, EventType.STEP_FAILED, step="a", terminal=False),
        ]
    )

    assert state.step_iterations == {"a": 1}


def test_loop_exhausted_moves_to_compensation() -> None:
    # Предел цикла исчерпан — процесс откатывается, а не «успешно»
    # останавливается: цель цикла не достигнута.
    state = replay(
        [
            _event(0, EventType.STEP_COMPLETED, step="a"),
            _event(1, EventType.STEP_COMPLETED, step="a"),
            _event(2, EventType.LOOP_EXHAUSTED, step="a", iterations=2, max_iterations=2),
        ]
    )

    assert state.status is InstanceStatus.COMPENSATING
    assert state.failed_step == "a"
    # Шаг выполнен, значит его компенсировать: иначе откат был бы неполным.
    assert state.completed_steps == ["a"]


def test_failed_step_is_recorded() -> None:
    state = replay([_event(0, EventType.STEP_FAILED, step="charge", error="timeout")])

    assert state.failed_step == "charge"


def test_timer_scheduled_moves_to_waiting() -> None:
    state = replay([_event(0, EventType.TIMER_SCHEDULED, step="later")])

    assert state.status is InstanceStatus.WAITING


def test_timer_fired_returns_to_running() -> None:
    state = replay(
        [
            _event(0, EventType.TIMER_SCHEDULED, step="later"),
            _event(1, EventType.TIMER_FIRED, step="later"),
        ]
    )

    assert state.status is InstanceStatus.RUNNING
    assert state.current_step == "later"


def test_compensation_started_switches_mode() -> None:
    state = replay([_event(0, EventType.COMPENSATION_STARTED, for_step="reserve")])

    assert state.status is InstanceStatus.COMPENSATING
    assert state.compensation_for == "reserve"


def test_compensation_failed_is_visible_state() -> None:
    # COMPENSATION_FAILED — не тихий сбой, а состояние для ручного разбора.
    state = replay([_event(0, EventType.COMPENSATION_FAILED, error="сервис недоступен")])

    assert state.status is InstanceStatus.COMPENSATION_FAILED


def test_retry_scheduled_returns_to_running() -> None:
    state = replay(
        [
            _event(0, EventType.STEP_FAILED, step="a"),
            _event(1, EventType.STEP_RETRY_SCHEDULED, step="a", attempt=2),
        ]
    )

    assert state.status is InstanceStatus.RUNNING
    assert state.current_step == "a"


def test_cancel_requested_is_not_terminal() -> None:
    # Запрос отмены — не завершение: выполненные шаги ещё не откачены.
    # Терминальный статус здесь оставлял бы резерв товара висеть навсегда.
    state = replay(
        [
            _event(0, EventType.STEP_STARTED, step="a"),
            _event(1, EventType.INSTANCE_CANCEL_REQUESTED),
        ]
    )

    assert state.status is InstanceStatus.CANCELLING
    assert state.current_step == "a"
    assert not state.is_terminal


def test_cancel_requested_does_not_hide_completed_steps() -> None:
    # Пошаговый откат строится по выполненным шагам, и отмена их не отменяет:
    # именно их и надо вернуть.
    state = replay(
        [
            _event(0, EventType.STEP_STARTED, step="a"),
            _event(1, EventType.STEP_COMPLETED, step="a", next="b"),
            _event(2, EventType.INSTANCE_CANCEL_REQUESTED),
        ]
    )

    assert state.completed_steps == ["a"]


def test_terminal_events_clear_current_step() -> None:
    for event_type, expected in [
        (EventType.INSTANCE_COMPLETED, InstanceStatus.COMPLETED),
        (EventType.INSTANCE_COMPENSATED, InstanceStatus.COMPENSATED),
        (EventType.INSTANCE_CANCELLED, InstanceStatus.CANCELLED),
    ]:
        state = replay(
            [
                _event(0, EventType.STEP_STARTED, step="a"),
                _event(1, event_type),
            ]
        )
        assert state.status is expected
        assert state.current_step is None
        assert state.is_terminal


def test_unknown_event_type_is_ignored() -> None:
    # Событие из более новой версии движка не должно останавливать разбор:
    # старый воркер обязан продолжить работу с журналом, а не упасть на
    # каждом чтении состояния. Известные типы перечислены в разборе явно,
    # поэтому опечатка в enum невозможна, а потерянных событий не будет —
    # их по-прежнему видно в самом журнале.
    broken = RecordedEvent(
        seq=0,
        type="SOMETHING_NEW",  # type: ignore[arg-type]
        payload={},
        created_at=datetime.now(UTC),
    )
    known = _event(1, EventType.STEP_STARTED, step="a")

    state = replay([broken, known])

    assert state.status is InstanceStatus.RUNNING
    assert state.current_step == "a"


def test_steps_to_compensate_reverses_execution_order() -> None:
    state = replay(
        [
            _event(0, EventType.STEP_COMPLETED, step="a", next="b"),
            _event(1, EventType.STEP_COMPLETED, step="b", next="c"),
            _event(2, EventType.STEP_COMPLETED, step="c"),
        ]
    )

    assert steps_to_compensate(state) == ["c", "b", "a"]


def test_steps_to_compensate_skips_incomplete_steps() -> None:
    state = replay(
        [
            _event(0, EventType.STEP_COMPLETED, step="a"),
            _event(1, EventType.STEP_STARTED, step="b"),
            _event(2, EventType.STEP_FAILED, step="b"),
        ]
    )

    assert steps_to_compensate(state) == ["a"]


def test_fresh_state_has_no_steps_to_compensate() -> None:
    assert steps_to_compensate(InstanceState()) == []


# --- Запись в БД -----------------------------------------------------------


async def test_append_assigns_sequential_seq(db_session: AsyncSession) -> None:
    instance_id = await _new_instance(db_session)

    first = await append_event(db_session, instance_id, EventType.INSTANCE_CREATED, {"start": "a"})
    second = await append_event(db_session, instance_id, EventType.STEP_STARTED, {"step": "a"})

    assert first.seq == 0
    assert second.seq == 1


async def test_events_are_readable_in_order(db_session: AsyncSession) -> None:
    instance_id = await _new_instance(db_session)
    await append_event(db_session, instance_id, EventType.INSTANCE_CREATED, {"start": "a"})
    await append_event(
        db_session, instance_id, EventType.STEP_COMPLETED, {"step": "a", "next": "b"}
    )

    events = await read_events(db_session, instance_id)

    assert [e.seq for e in events] == [0, 1]
    assert [e.type for e in events] == [EventType.INSTANCE_CREATED, EventType.STEP_COMPLETED]


async def test_read_events_supports_offset(db_session: AsyncSession) -> None:
    instance_id = await _new_instance(db_session)
    for seq in range(3):
        await append_event(db_session, instance_id, EventType.STEP_STARTED, {"step": f"s{seq}"})

    events = await read_events(db_session, instance_id, from_seq=2)

    assert [e.seq for e in events] == [2]


async def test_replay_from_database_matches_journal(db_session: AsyncSession) -> None:
    # Путь восстановления после падения: прочитать журнал и построить состояние.
    instance_id = await _new_instance(db_session)
    await append_event(db_session, instance_id, EventType.INSTANCE_CREATED, {"start": "a"})
    await append_event(
        db_session,
        instance_id,
        EventType.STEP_COMPLETED,
        {"step": "a", "output": {"n": 1}, "next": "b"},
    )

    state = replay(await read_events(db_session, instance_id))

    assert state.current_step == "b"
    assert state.variables == {"n": 1}
    assert state.completed_steps == ["a"]


async def test_payload_is_stored_as_jsonb(db_session: AsyncSession) -> None:
    instance_id = await _new_instance(db_session)
    await append_event(
        db_session,
        instance_id,
        EventType.STEP_COMPLETED,
        {"step": "a", "output": {"items": [1, 2]}},
    )

    stored = await db_session.scalar(
        select(WorkflowEvent.payload).where(WorkflowEvent.instance_id == instance_id)
    )

    assert stored == {"step": "a", "output": {"items": [1, 2]}}


async def test_events_of_other_instances_are_not_visible(db_session: AsyncSession) -> None:
    # Журнал инстанса не должен содержать чужих событий: подмена на rеplay
    # дала бы инстансу состояние чужого процесса.
    first = await _new_instance(db_session)
    second = await _new_instance(db_session)
    await append_event(db_session, first, EventType.INSTANCE_CREATED, {"start": "a"})

    assert len(await read_events(db_session, second)) == 0


async def test_append_is_rolled_back_with_transaction(db_session: AsyncSession) -> None:
    # Событие и изменение состояния обязаны попадать в базу вместе: событие без
    # изменения или изменение без события ломают восстановление.
    instance_id = await _new_instance(db_session)
    await append_event(db_session, instance_id, EventType.INSTANCE_CREATED, {"start": "a"})
    await db_session.rollback()

    assert await read_events(db_session, instance_id) == []


def test_cancel_requested_marks_origin_of_compensation() -> None:
    """Журнал различает, почему начался откат."""
    events = [
        _event(1, EventType.STEP_FAILED, step="a", terminal=True),
    ]
    assert replay(events).cancel_requested is False

    events.append(_event(2, EventType.INSTANCE_CANCEL_REQUESTED))
    assert replay(events).cancel_requested is True


def test_failure_after_cancel_clears_origin() -> None:
    """Повторная отмена не должна записываться в журнале
    после предыдущей отмены."""
    events = [
        _event(1, EventType.INSTANCE_CANCEL_REQUESTED),
        _event(2, EventType.STEP_FAILED, step="a", terminal=True),
    ]
    assert replay(events).cancel_requested is False


def test_instance_failed_event_marks_terminal_failure() -> None:
    events = [
        _event(1, EventType.STEP_STARTED, step="a"),
        _event(2, EventType.INSTANCE_FAILED),
    ]

    state = replay(events)

    assert state.status is InstanceStatus.FAILED
    assert state.current_step is None
    assert state.is_terminal
