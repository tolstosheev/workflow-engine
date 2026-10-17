/**
 * Лента времени процесса: показывает пробелы, а не наложения.
 *
 * Шаг занимает десятки миллисекунд, а процесс живёт минуты — между шагами он
 * ждёт таймер, повтор или решение человека. Таблица длительностей эту картину
 * скрывает: в ней всё выглядит одинаково быстрым.
 */


import { dutyRatio, layoutSteps, type LaneRow } from '@/lib/timeline'
import { formatDuration, stepLabel } from '@/lib/status'
import type { StepRun, StepRunStatus } from '@/types/api'

const BAR_CLASSES: Record<StepRunStatus, string> = {
  PENDING: 'bg-white/20',
  RUNNING: 'bg-accent',
  COMPLETED: 'bg-success',
  FAILED: 'bg-danger',
  COMPENSATED: 'bg-warning',
}

export function StepTimeline({
  steps,
  titles,
  now,
}: {
  steps: StepRun[]
  /** Описание шага из схемы: подпись, а имя уходит в подсказку. */
  titles?: Record<string, string>
  now?: number
}) {
  const layout = layoutSteps(steps, now)
  if (!layout) return null

  const share = dutyRatio(layout)
  const running = layout.rows.find((row) => row.running)
  const titleOf = (name: string): string => titles?.[name] ?? name

  return (
    <section className="rounded-lg border border-surface-border bg-surface-raised p-4">
      <header className="mb-3">
        <h2 className="text-sm font-semibold text-white">Время жизни процесса</h2>
        <p className="mt-1 text-xs text-white/50">
          Полоса — время шага, пустое место между полосами — ожидание, повтор или решение человека.
          Процесс живёт {formatDuration('1970-01-01T00:00:00Z', new Date(layout.totalMs).toISOString())}, из
          него в шагах {percent(share)}, остальное — ожидание.
        </p>
        {running && (
          // Подпись исполняемого шага стоит над лентой, а не только на полосе:
          // полоса растёт на глазах, и без подписи непонятно, что именно
          // сейчас происходит с процессом.
          <p className="mt-2 flex items-center gap-2 text-xs text-accent">
            <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-accent" />
            Сейчас исполняется: {titleOf(running.stepName)}
            {running.attempt > 1 ? `, попытка ${running.attempt}` : ''}
          </p>
        )}
      </header>

      <ol className="space-y-1">
        {layout.rows.map((row) => (
          <Row key={row.key} row={row} title={titleOf(row.stepName)} />
        ))}
      </ol>
    </section>
  )
}

function Row({ row, title }: { row: LaneRow; title: string }) {
  return (
    <li className="flex items-center gap-2 text-xs">
      {/* Имя остаётся в подсказке: длинное описание в колонке фиксированной
          ширины обрезалось бы, а потерять имя шага на ленте нельзя. */}
      <span className="w-36 shrink-0 truncate text-white/70" title={row.stepName}>
        {title}
        {row.attempt > 1 ? ` · попытка ${row.attempt}` : ''}
      </span>

      <span className="relative h-4 flex-1 overflow-hidden rounded bg-surface">
        <span
          className={`absolute top-0 h-full ${BAR_CLASSES[row.status]} ${
            row.running ? 'animate-pulse' : ''
          }`}
          style={{ left: `${row.offset * 100}%`, width: `${row.width * 100}%` }}
        />
      </span>

      <span className="w-16 shrink-0 text-right text-white/50">{millis(row.durationMs)}</span>
      <span className="w-20 shrink-0 text-right text-white/30">
        {row.gapMs > 0 ? `+${millis(row.gapMs)}` : ''}
      </span>
      <span className="w-24 shrink-0 text-white/40">
        {row.compensated ? 'компенсирован' : stepLabel(row.status)}
      </span>
    </li>
  )
}

function percent(ratio: number): string {
  const value = ratio * 100
  if (value === 0) return '0 %'
  if (value < 0.1) return '< 0.1 %'
  return `${value.toFixed(1)} %`
}

function millis(value: number): string {
  if (value < 1000) return `${Math.round(value)} мс`
  if (value < 60_000) return `${(value / 1000).toFixed(1)} с`
  return `${Math.round(value / 60_000)} мин`
}
