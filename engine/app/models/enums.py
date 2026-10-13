"""Состояния инстансов, шагов и компенсаций.

Значения хранятся строками, а не PostgreSQL `enum`: добавление состояния
не должно требовать миграции, блокирующей таблицу, иначе к каждому новому
переходу придётся ехать в продакшен отдельным релизом.

Соответствие состояний описано в `docs/ENGINE.md` §9.
"""

from __future__ import annotations

from enum import StrEnum

# Вид ожидания: ожидание шага или срок ожидания человека.
#
# Живёт здесь, а не в планировщике, потому что читает его и разбор журнала,
# а планировщик импортирует журнал: оттуда константа была бы недостижима без
# циклического импорта. Строка попадает и в событие `TIMER_SCHEDULED`, по
# которому разбор отличает одно ожидание от другого (ADR-0016).
STEP_WAIT = "step"
PAUSE_DEADLINE = "pause_deadline"


class InstanceStatus(StrEnum):
    """Состояние инстанса workflow."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    # Процесс встал и ждёт решения человека: активен, но не берётся в работу,
    # пока не придёт команда возобновления (ADR-0014).
    PAUSED = "PAUSED"
    CANCELLING = "CANCELLING"
    COMPENSATING = "COMPENSATING"
    COMPENSATION_FAILED = "COMPENSATION_FAILED"
    COMPLETED = "COMPLETED"
    COMPENSATED = "COMPENSATED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


ACTIVE_STATUSES: frozenset[InstanceStatus] = frozenset(
    {
        InstanceStatus.PENDING,
        InstanceStatus.RUNNING,
        InstanceStatus.WAITING,
        InstanceStatus.PAUSED,
        InstanceStatus.CANCELLING,
        InstanceStatus.COMPENSATING,
        InstanceStatus.COMPENSATION_FAILED,
    }
)

TERMINAL_STATUSES: frozenset[InstanceStatus] = frozenset(
    {
        InstanceStatus.COMPLETED,
        InstanceStatus.COMPENSATED,
        InstanceStatus.CANCELLED,
        InstanceStatus.FAILED,
    }
)


class StepRunStatus(StrEnum):
    """Состояние одного выполнения шага."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    COMPENSATED = "COMPENSATED"


class CompensationStatus(StrEnum):
    """Состояние попытки компенсации."""

    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class EventType(StrEnum):
    """Типы событий журнала.

    Перечень закрыт: реплей строит состояние по нему, и неизвестный тип
    означает, что читающий код не знает о существующем событии.
    """

    INSTANCE_CREATED = "INSTANCE_CREATED"
    # Процесс встал на паузу и ждёт человека. Отдельное событие, а не флаг:
    # состояние обязано выводиться из журнала, иначе подхват воркера после
    # перезапуска дал бы другое поведение, чем до него (ADR-0014).
    INSTANCE_PAUSED = "INSTANCE_PAUSED"
    INSTANCE_RESUMED = "INSTANCE_RESUMED"
    STEP_STARTED = "STEP_STARTED"
    STEP_COMPLETED = "STEP_COMPLETED"
    STEP_FAILED = "STEP_FAILED"
    STEP_RETRY_SCHEDULED = "STEP_RETRY_SCHEDULED"
    # Число проходов цикла исчерпано: шаг выполнен, но повторять его больше
    # нельзя. Отдельное событие потому, что шаг-то успешен — «провалить» его
    # значило бы записать в журнал ложь, а молча остановиться значило бы
    # выдать неуспех за успех.
    LOOP_EXHAUSTED = "LOOP_EXHAUSTED"
    TIMER_SCHEDULED = "TIMER_SCHEDULED"
    TIMER_FIRED = "TIMER_FIRED"
    COMPENSATION_STARTED = "COMPENSATION_STARTED"
    COMPENSATION_COMPLETED = "COMPENSATION_COMPLETED"
    COMPENSATION_FAILED = "COMPENSATION_FAILED"
    INSTANCE_COMPLETED = "INSTANCE_COMPLETED"
    INSTANCE_COMPENSATED = "INSTANCE_COMPENSATED"
    # Отказ без компенсации: упал первый шаг, откатывать нечего.
    # Раньше такое завершение писало INSTANCE_COMPENSATED, и журнал утверждал
    # об откате, которого не было.
    INSTANCE_FAILED = "INSTANCE_FAILED"
    # Запрос отмены — не завершение. Отдельное событие потому, что
    # INSTANCE_CANCELLED терминален: при его трактовке как запроса отмены
    # процесс завершался, минуя откат выполненных шагов.
    INSTANCE_CANCEL_REQUESTED = "INSTANCE_CANCEL_REQUESTED"
    INSTANCE_CANCELLED = "INSTANCE_CANCELLED"
