/**
 * Раскладка темпа по минутам.
 *
 * Проверяется то, что иначе тихо врёт: пустая минута остаётся нулём, неразбираемая
 * дата не превращается в эпоху, а процесс, запущенный полчаса назад, не
 * попадает в последние десять минут. Полосы, посчитанные неверно, выглядят
 * правдоподобно — поэтому сверка идёт с числами, а не «нарисуется».
 */

import { describe, expect, it } from 'vitest'

import { barHeight, bucketByMinute, minuteLabel, peakOf } from '@/lib/rate'
import type { WorkflowInstance } from '@/types/api'

function instance(over: Partial<WorkflowInstance> = {}): WorkflowInstance {
  return {
    id: 'i-1',
    definition: { id: 'd-1', name: 'order_fulfillment', version: 1 },
    status: 'COMPLETED',
    current_step: null,
    input: {},
    variables: {},
    error: null,
    lease_owner: null,
    lease_expires_at: null,
    heartbeat_at: null,
    archived_at: null,
    journal_pruned_at: null,
    created_at: '2026-03-01T12:10:30Z',
    updated_at: '2026-03-01T12:11:05Z',
    ...over,
  }
}

const NOW = Date.parse('2026-03-01T12:12:00Z')

describe('темп по минутам', () => {
  it('даёт ровно столько вёдер, сколько минут запрошено', () => {
    const buckets = bucketByMinute([], NOW, 10)
    expect(buckets).toHaveLength(10)
  })

  it('разносит запуск и завершение по своим минутам', () => {
    const buckets = bucketByMinute([instance()], NOW, 3)
    // Окно: 12:09, 12:10, 12:11. Запуск в 12:10, завершение в 12:11.
    expect(buckets.map((bucket) => bucket.started)).toEqual([0, 1, 0])
    expect(buckets.map((bucket) => bucket.finished)).toEqual([0, 0, 1])
  })

  it('не приписывает старый процесс последним минутам', () => {
    const buckets = bucketByMinute([instance({ created_at: '2026-03-01T09:00:00Z' })], NOW, 5)
    expect(buckets.every((bucket) => bucket.started === 0)).toBe(true)
  })

  it('оставляет пустую минуту нулём, а не пропуском', () => {
    // Пропуск на графике читался бы как «минуты не было», а на самом деле в ней
    // ничего не завершилось.
    const buckets = bucketByMinute([instance({ updated_at: '2026-03-01T12:09:30Z' })], NOW, 3)
    expect(buckets.map((bucket) => bucket.finished)).toEqual([1, 0, 0])
  })

  it('не считает незавершённый процесс завершённым', () => {
    const buckets = bucketByMinute([instance({ status: 'RUNNING' })], NOW, 3)
    expect(buckets.every((bucket) => bucket.finished === 0)).toBe(true)
  })

  it('отбрасывает неразбираемую дату, а не считает её эпохой', () => {
    const buckets = bucketByMinute([instance({ created_at: 'мусор' })], NOW, 3)
    expect(buckets.every((bucket) => bucket.started === 0)).toBe(true)
  })

  it('считает архивные процессы наравне с видимыми', () => {
    // Темп работы стенда за прошедший час не должен зависеть от того, нажал ли
    // человек кнопку уборки.
    const buckets = bucketByMinute(
      [instance({ archived_at: '2026-03-01T12:11:00Z' })],
      NOW,
      3,
    )
    expect(buckets.reduce((sum, bucket) => sum + bucket.started, 0)).toBe(1)
  })

  it('не делит на ноль, когда всё пусто', () => {
    expect(barHeight(0, 0)).toBe(0)
    expect(peakOf([], 'started')).toBe(0)
  })

  it('рисует полосу пропорционально пику', () => {
    expect(barHeight(5, 10)).toBe(50)
    expect(barHeight(10, 10)).toBe(100)
  })

  it('подписывает ведро двумя цифрами', () => {
    expect(minuteLabel(Date.parse('2026-03-01T12:07:00Z'))).toMatch(/^\d{2}:\d{2}$/)
  })
})