/**
 * Темп: сколько процессов проходит за единицу времени.
 *
 * Отдельная функция без React и без DOM — как `instanceList`: правило «что
 * показать» легко проверить тестом и легко сломать незаметно. Считается по
 * факту, а не по ожиданию: пустая минута остаётся нулём, и полосы разной
 * высоты сравнимы между собой.
 *
 * Чего график не делает: не заменяет замер `scripts/load.py`. Он показывает
 * темп стенда прямо сейчас, где замер — средняя скорость под нагрузкой.
 */

import type { InstanceStatus, WorkflowInstance } from '@/types/api'

export interface RateBucket {
  /** Начало минуты в миллисекундах. */
  start: number
  started: number
  /** Процессы, дошедшие до терминального состояния. */
  finished: number
}

const TERMINAL = new Set<InstanceStatus>([
  'COMPLETED',
  'COMPENSATED',
  'CANCELLED',
  'FAILED',
  'COMPENSATION_FAILED',
])

/** Миллисекунды в дате либо `null`: неразбираемое время не должно становиться эпохой. */
function millis(value: string): number | null {
  const parsed = Date.parse(value)
  return Number.isNaN(parsed) ? null : parsed
}

/** Начало той же минуты: округление вниз, чтобы ведра не «плавали». */
function minuteStart(at: number): number {
  return Math.floor(at / 60_000) * 60_000
}

/**
 * Раскладывает процессы по минутам последних `minutes` минут.
 *
 * Архивные процессы учитываются наравне с видимыми: темп работы стенда за
 * прошедший час не должен зависеть от того, нажал ли человек кнопку уборки.
 * Поэтому список сюда приходит с `archived=all`.
 *
 * Текущая минута в окно не входит: она ещё не истекла, и полоса за неё всегда
 * была бы ниже соседних — график показывал бы не темп, а момент своего просмотра.
 * Все вёдра полные и потому сравнимы между собой.
 */
export function bucketByMinute(
  instances: WorkflowInstance[],
  now: number,
  minutes: number,
): RateBucket[] {
  const current = minuteStart(now)
  const first = current - minutes * 60_000
  const buckets = new Map<number, RateBucket>()
  for (let index = 0; index < minutes; index += 1) {
    const start = first + index * 60_000
    buckets.set(start, { start, started: 0, finished: 0 })
  }

  for (const instance of instances) {
    const created = millis(instance.created_at)
    if (created !== null) {
      const key = minuteStart(created)
      const bucket = buckets.get(key)
      if (bucket) bucket.started += 1
    }
    // Время завершения — это время последнего изменения: журнал пишет его и при
    // успехе, и при откате, а отдельной отметки окончания у процесса нет.
    if (TERMINAL.has(instance.status)) {
      const finished = millis(instance.updated_at)
      if (finished !== null) {
        const key = minuteStart(finished)
        const bucket = buckets.get(key)
        if (bucket) bucket.finished += 1
      }
    }
  }

  return [...buckets.values()]
}

/** Высота полосы в процентах от самой высокой. Ноль делить нельзя. */
export function barHeight(value: number, max: number): number {
  if (max <= 0) return 0
  return Math.round((value / max) * 100)
}

export function peakOf(buckets: RateBucket[], field: 'started' | 'finished'): number {
  return buckets.reduce((max, bucket) => Math.max(max, bucket[field]), 0)
}

/** `HH:MM` для подписи ведра — по местному времени читателя. */
export function minuteLabel(start: number): string {
  return new Date(start).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })
}