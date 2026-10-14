/**
 * Лента времени процесса.
 *
 * Показывает не наложения — один инстанс держит один воркер, и шаги внутри него
 * не перекрываются. Показывает она **пробелы**, и в этом весь смысл: шаг
 * занимает двадцать миллисекунд, а процесс живёт минуты, потому что между
 * шагами ждёт таймер, повтор или решение человека. Таблица длительностей такую
 * картину скрывала: в ней все шаги выглядят одинаково быстрыми, и процесс
 * кажется мгновенным.
 *
 * Доля времени, проведённого в шагах, вынесена в подпись: «0.1 % от времени
 * процесса» — это и есть утверждение о durable-исполнении в одной строке.
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

export function StepTimeline({ steps, now }: { steps: StepRun[]; now?: number }) {
  const layout = layoutSteps(steps, now)
  if (!layout) return null

  const share = dutyRatio(layout)

  return (
    <section className="rounded-lg border border-surface-border bg-surface-raised p-4">
      <header className="mb-3">
        <h2 className="text-sm font-semibold text-white">Время жизни процесса</h2>
        <p className="mt-1 text-xs text-white/50">
          Полоса — время шага, пустое место между полосами — ожидание, повтор или решение человека.
          Процесс живёт {formatDuration('1970-01-01T00:00:00Z', new Date(layout.totalMs).toISOString())}, из
          него в шагах {percent(share)}, остальное — ожидание.
        </p>
      </header>

      <ol className="space-y-1">
        {layout.rows.map((row) => (
          <Row key={row.key} row={row} />
        ))}
      </ol>
    </section>
  )
}

function Row({ row }: { row: LaneRow }) {
  return (
    <li className="flex items-center gap-2 text-xs">
      <span className="w-36 shrink-0 truncate font-mono text-white/70" title={row.stepName}>
        {row.stepName}
        {row.attempt > 1 ? ` · попытка ${row.attempt}` : ''}
      </span>

      <span className="relative h-4 flex-1 overflow-hidden rounded bg-surface">
        <span
          className={`absolute top-0 h-full ${BAR_CLASSES[row.status]}`}
          style={{ left: `${row.offset * 100}%`, width: `${row.width * 100}%` }}
        />
      </span>

      <span className="w-16 shrink-0 text-right text-white/50">{millis(row.durationMs)}</span>
      <span className="w-20 shrink-0 text-right text-white/30">
        {row.gapMs > 0 ? `+${millis(row.gapMs)}` : ''}
      </span>
      <span className="w-24 shrink-0 text-white/40">
        {row.compensated ? 'откатан' : stepLabel(row.status)}
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
