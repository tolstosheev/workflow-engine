/**
 * Подписи состояний и форматирование времени.
 *
 * Здесь нет ничего сложного, и именно поэтому регрессии проходили незамеченными:
 * подпись для состояния, которого нет в справочнике, тихо превращалась в
 * «undefined», а незавершённый шаг показывал длительность, которой нет.
 */

import { describe, expect, it } from 'vitest'

import {
  STATUS_LABELS,
  eventLabel,
  formatDateTime,
  formatDuration,
  statusLabel,
  statusTone,
  stepLabel,
  stepTone,
} from '@/lib/status'
import type { EventType, InstanceStatus, StepRunStatus } from '@/types/api'

describe('состояние процесса', () => {
  it('подписывает каждое состояние, которое движок может записать', () => {
    const statuses: InstanceStatus[] = [
      'PENDING',
      'RUNNING',
      'WAITING',
      'CANCELLING',
      'COMPENSATING',
      'COMPENSATION_FAILED',
      'COMPLETED',
      'COMPENSATED',
      'CANCELLED',
      'FAILED',
    ]

    for (const status of statuses) {
      expect(statusLabel(status)).not.toBeUndefined()
      expect(statusLabel(status).length).toBeGreaterThan(0)
    }
  })

  it('различает отмену и откат', () => {
    // Подписи «Отменён» и «Компенсирован» — единственное, чем пользователь
    // отличает добровольную остановку от сбоя с компенсацией.
    expect(statusLabel('CANCELLED')).not.toBe(statusLabel('COMPENSATED'))
    expect(statusLabel('FAILED')).not.toBe(statusLabel('COMPENSATING'))
  })

  it('не оставляет состояние без тона', () => {
    const statuses = Object.keys(STATUS_LABELS) as InstanceStatus[]

    for (const status of statuses) {
      expect(statusTone(status)).toMatch(/bg-|text-/)
    }
  })

  it('помечает провал и неудачный откат как опасные', () => {
    expect(statusTone('FAILED')).toBe(statusTone('COMPENSATION_FAILED'))
    expect(statusTone('COMPLETED')).toBe(statusTone('COMPENSATED'))
  })
})

describe('подписи событий', () => {
  it('подписывает каждое событие, которое движок пишет в журнал', () => {
    const types: EventType[] = [
      'INSTANCE_CREATED',
      'STEP_STARTED',
      'STEP_COMPLETED',
      'STEP_FAILED',
      'COMPENSATION_STARTED',
      'COMPENSATION_COMPLETED',
      'COMPENSATION_FAILED',
      'INSTANCE_COMPLETED',
      'INSTANCE_COMPENSATED',
      'INSTANCE_FAILED',
      'INSTANCE_CANCEL_REQUESTED',
      'INSTANCE_CANCELLED',
      'LOOP_EXHAUSTED',
    ]

    for (const type of types) {
      expect(eventLabel(type).length).toBeGreaterThan(0)
    }
  })

  it('отдаёт само имя события, если подписи нет', () => {
    // Новое событие в движке не должно превращаться в «undefined» в журнале.
    expect(eventLabel('STEP_SKIPPED' as EventType)).toBe('STEP_SKIPPED')
  })
})

describe('подпись шага', () => {
  it('подписывает каждое состояние выполнения шага, а не отдаёт его имя', () => {
    // Сырое `COMPLETED` в русском интерфейсе выдаёт движок, а человек читает
    // подпись: «выполнен» и «провал» говорят больше, чем имя колонки в БД.
    for (const status of ['COMPLETED', 'FAILED', 'COMPENSATED', 'RUNNING'] as StepRunStatus[]) {
      expect(stepLabel(status)).not.toBe(status)
    }
  })

  it('подписывает все состояния выполнения шага, какие движок записывает', () => {
    for (const status of ['RUNNING', 'COMPLETED', 'FAILED', 'COMPENSATED'] as StepRunStatus[]) {
      expect(stepLabel(status)).toBeTruthy()
    }
  })

  it('отдаёт само значение, если состояния нет в словаре', () => {
    // Лента приходит свободным текстом, и состояние из более новой версии
    // движка должно быть видно, а не пропасть в пустое место.
    expect(stepLabel('ЧТО-ТО НОВОЕ')).toBe('ЧТО-ТО НОВОЕ')
  })
})

describe('тон шага', () => {
  it('различает выполнение, провал и компенсацию', () => {
    expect(stepTone('COMPLETED')).not.toBe(stepTone('FAILED'))
    expect(stepTone('COMPENSATED')).toBe(stepTone('COMPLETED'))
    expect(stepTone('FAILED' as StepRunStatus)).toMatch(/text-danger/)
  })
})

describe('время', () => {
  it('показывает прочерк вместо пустой даты', () => {
    expect(formatDateTime(null)).toBe('—')
    expect(formatDateTime(undefined)).toBe('—')
  })

  it('подставляет прочерк вместо Invalid Date', () => {
    // Неразбираная дата не должна попадать в журнал: «Invalid Date»
    // выглядит как сбой интерфейса, а не как отсутствие данных.
    expect(formatDateTime('не дата')).toBe('—')
  })

  it('показывает незавершённый шаг как выполняющийся', () => {
    // Длительность незавершённого шага вычислять нельзя: правый конец пуст,
    // и разность уехала бы в отрицательные миллисекунды.
    expect(formatDuration('2026-01-01T10:00:00Z', null)).toBe('выполняется')
  })

  it('переходит от миллисекунд к минутам', () => {
    const from = '2026-01-01T10:00:00Z'

    expect(formatDuration(from, '2026-01-01T10:00:00.250Z')).toBe('250 мс')
    expect(formatDuration(from, '2026-01-01T10:00:01.500Z')).toBe('1.5 с')
    expect(formatDuration(from, '2026-01-01T10:02:00Z')).toBe('2 мин')
  })
})
