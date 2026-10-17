/**
 * Раскладка выполнения шагов по времени.
 *
 * Лента показывает **пробелы**, а не наложения: один процесс держит один
 * исполнитель (§3.3 `AGENTS.md`), поэтому шаги внутри него не перекрываются,
 * а паузы и повторы — это и есть время бизнес-транзакции.
 *
 * Масштаб вынесен отдельно от раскладки: два процесса кладутся на один
 * отрезок, иначе рядом стоящие ленты показывали бы несравнимые шкалы.
 */


import type { StepRun, StepRunStatus } from '@/types/api'

export interface LaneRow {
  key: string
  stepName: string
  attempt: number
  status: StepRunStatus
  compensated: boolean
  /** Начало и конец шага в миллисекундах от начала эпохи. */
  startMs: number
  endMs: number
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
  /** Длина отрезка от первого старта до последнего финиша. */
  totalMs: number
  /** Суммарное время, занятое шагами. */
  inStepsMs: number
  /** Время между шагами: ожидания, повторы, решения человека. */
  idleMs: number
}

export interface TimeScale {
  from: number
  to: number
  span: number
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
 * Раскладывает прогоны по отрезку.
 *
 * `now` передаётся снаружи, а не берётся внутри: иначе раскладку нельзя было бы
 * проверить — время в тесте шло бы своим ходом и совпадение означало бы удачу.
 * С незакрытым прогоном (`finished_at` пуст) полоса идёт до `now`.
 *
 * Явный `scale` кладёт прогоны на чужой отрезок — так два процесса получают
 * общую шкалу и сравниваются честно.
 */
export function layoutSteps(
  steps: StepRun[],
  now: number = Date.now(),
  scale?: TimeScale,
): StepLayout | null {
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

  const own = scale ?? bounds(timed.map((item) => [item.start, item.end] as const))
  const from = own.from
  const span = Math.max(own.span, 1)

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
      startMs: item.start,
      endMs: item.end,
      offset: (item.start - from) / span,
      width: Math.max(duration / span, MIN_WIDTH),
      durationMs: duration,
      gapMs: gap,
      running: item.running,
    }
  })

  const inStepsMs = rows.reduce((sum, row) => sum + row.durationMs, 0)
  const from0 = ordered[0].start
  const to = Math.max(...rows.map((row) => row.endMs))

  return {
    rows,
    totalMs: to - from0,
    inStepsMs,
    idleMs: Math.max(to - from0 - inStepsMs, 0),
  }
}

function bounds(points: ReadonlyArray<readonly [number, number]>): TimeScale {
  const from = Math.min(...points.map(([start]) => start))
  const to = Math.max(...points.map(([, end]) => end))
  return { from, to, span: Math.max(to - from, 1) }
}

/**
 * Общий отрезок для нескольких наборов прогонов.
 *
 * Нужен для сравнения: у каждого процесса своя шкала, и без общего отрезка
 * короткий процесс растянулся бы на всю ширину и выглядел бы равным длинному.
 */
export function sharedScale(sets: StepRun[][], now: number = Date.now()): TimeScale | null {
  const points: Array<readonly [number, number]> = []
  for (const steps of sets) {
    for (const run of steps) {
      const start = toMillis(run.started_at)
      if (start === null) continue
      const finished = toMillis(run.finished_at)
      points.push([start, finished === null ? now : finished])
    }
  }
  if (points.length === 0) return null
  return bounds(points)
}

/** Доля времени, проведённого в шагах. */
export function dutyRatio(layout: StepLayout): number {
  return layout.totalMs === 0 ? 0 : layout.inStepsMs / layout.totalMs
}
