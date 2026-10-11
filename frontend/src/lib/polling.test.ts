/**
 * Когда странице пора опрашивать API.
 *
 * Тест на чистой функции, а не на таймерах компонента: правило важно само по
 * себе, а проверять его через фальшивые часы значит тестировать Vitest.
 */

import { describe, expect, it } from 'vitest'

import { isTerminal, pollingIntervalMs } from '@/lib/polling'
import type { InstanceStatus } from '@/types/api'

const TERMINAL_STATUSES: InstanceStatus[] = ['COMPLETED', 'COMPENSATED', 'CANCELLED', 'FAILED']

const ACTIVE_STATUSES: InstanceStatus[] = [
  'PENDING',
  'RUNNING',
  'WAITING',
  'CANCELLING',
  'COMPENSATING',
  'COMPENSATION_FAILED',
]

describe('терминальные состояния', () => {
  it('охватывает все состояния, после которых процесс не меняется', () => {
    for (const status of TERMINAL_STATUSES) {
      expect(isTerminal(status)).toBe(true)
    }
  })

  it('не считает терминальными те, что ещё выполняются или откатываются', () => {
    // Откат идёт после отказа: процесс в COMPENSATION_FAILED ещё меняется.
    for (const status of ACTIVE_STATUSES) {
      expect(isTerminal(status)).toBe(false)
    }
  })
})

describe('интервал опроса', () => {
  it('не опрашивает терминальный процесс вовсе', () => {
    // Завершённый процесс больше не меняется: опрос был бы вечной нагрузкой
    // на API без единой новой строки на экране.
    for (const status of TERMINAL_STATUSES) {
      expect(pollingIntervalMs(status, true)).toBe(0)
      expect(pollingIntervalMs(status, false)).toBe(0)
    }
  })

  it('при живом потоке опрашивает редко', () => {
    // События уже приносят изменения: опрос остаётся страховкой на случай,
    // когда событие потерялось.
    expect(pollingIntervalMs('RUNNING', true)).toBeGreaterThanOrEqual(10_000)
  })

  it('при оборванном потоке опрашивает часто', () => {
    // Пока SSE молчит, остаётся единственный источник изменений.
    const withStream = pollingIntervalMs('RUNNING', true)
    const withoutStream = pollingIntervalMs('RUNNING', false)

    expect(withoutStream).toBeLessThan(withStream)
    expect(withoutStream).toBeLessThanOrEqual(3_000)
  })

  it('до первого ответа считает поток оборванным', () => {
    // Статус ещё неизвестен: осторожный интервал лучше медленного обновления.
    expect(pollingIntervalMs(null, false)).toBeLessThanOrEqual(3_000)
    expect(pollingIntervalMs(null, true)).toBeGreaterThan(3_000)
  })
})