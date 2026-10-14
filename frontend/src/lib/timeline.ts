/**
 * Раскладка прогона шагов по времени.
 *
 * Главное, что показывает лента, — не наложения: один инстанс держит один
 * воркер (§3.3 AGENTS.md), поэтому шаги внутри него не перекрываются. Показывает
 * она **пробелы** между шагами, а они и есть главный тезис проекта: бизнес-
 * транзакция живёт минуты, а на работу уходит по двадцать миллисекунд за раз,
 * и всё это время умещается в короткие транзакции.
 */

import type { StepRun, StepRunStatus } from '@/types/api'

export interface LaneRow {
  key: string
  stepName: string
  attempt: number
  status: StepRunStatus
  compensated: boolean
  /** Начало полосы в долях отрезка, 0..1. */
  offset: number
  /** Длина полосы в долях отрезка, 0..1. */
  width: number
  /** Длительность шага в миллисекундах. */
  durationMs: number
  /** Разрыв после шага до следующего, миллисекунды. */
  gapMs: number
  /** Шаг ещё идёт: полоса достраивается до текущего момента. */
  running: boolean
}

export interface StepLayout {
  rows: LaneRow[]
  /** Длина всего отрезка от первого старта до последнего финиша. */
  totalMs: number
  /** Суммарное время, занятое шагами. */
  inStepsMs: number
  /** Время между шагами: ожидания, повторы, решения человека. */
  idleMs: number
}

/** Полоса уже этой длины всё равно была бы тоньше пикселя. */
const MIN_WIDTH = 0.004

/**
 * Момент времени или `null`, если его нет.
 *
 * `0` на месте мусора был бы тихой подменой: строка «мусор» читалась бы как
 * 1970 год, прогон попадал в раздачу и тянул за собой весь отрезок времени.
 */
function toMillis(value: string | null | undefined): number | null {
  if (typeof value !== 'string' || value === '') return null
  const time = new Date(value).getTime()
  return Number.isNaN(time) ? null : time
}

/**
 * Раскладывает прогоны по отрезку от первого старта до последнего финиша.
 *
 * `now` передаётся снаружи, а не берётся внутри: иначе раскладку нельзя было бы
 * проверить — время в тесте шло бы своим ходом и совпадение означало бы удачу.
 * С незакрытым прогоном (`finished_at` пуст) полоса идёт до `now`.
 */
export function layoutSteps(steps: StepRun[], now: number = Date.now()): StepLayout | null {
  const timed = steps
    .map((run) => {
      const start = toMillis(run.started_at)
      if (start === null) return null
      const finished = toMillis(run.finished_at)
      const end = finished === null ? now : finished
      return { run, start, end, running: finished === null }
    })
    // Прогон, который закончился раньше начала, или разобрать не удалось,
    // в раздачу не попадает: натянуть его времями было бы выдумкой.
    .filter((item): item is NonNullable<typeof item> => item !== null && item.end >= item.start)

  if (timed.length === 0) return null

  const first = Math.min(...timed.map((item) => item.start))
  const last = Math.max(...timed.map((item) => item.end))
  const total = Math.max(last - first, 1)

  const ordered = [...timed].sort((a, b) => a.start - b.start)

  const rows = ordered.map((item, index) => {
    const next = ordered[index + 1]
    const duration = item.end - item.start
    const gap = next === undefined ? 0 : Math.max(next.start - item.end, 0)

    return {
      key: item.run.id,
      stepName: item.run.step_name,
      attempt: item.run.attempt,
      status: item.run.status,
      compensated: item.run.compensated,
      offset: (item.start - first) / total,
      width: Math.max(duration / total, MIN_WIDTH),
      durationMs: duration,
      gapMs: gap,
      running: item.running,
    }
  })

  const inStepsMs = rows.reduce((sum, row) => sum + row.durationMs, 0)

  return {
    rows,
    totalMs: last - first,
    inStepsMs,
    idleMs: Math.max(last - first - inStepsMs, 0),
  }
}

/** Доля времени, проведённого в шагах. */
export function dutyRatio(layout: StepLayout): number {
  return layout.totalMs === 0 ? 0 : layout.inStepsMs / layout.totalMs
}
