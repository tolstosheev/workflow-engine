/**
 * Порядок компенсаций: фактический, а не предполагаемый.
 *
 * Важное различие этого подхода: список к откату берётся из того, что шаги
 * действительно выполнились, а не из структуры определения. Определение не
 * знает, какие шаги успели выполниться до сбоя, и рисовать по нему откат
 * значило бы показывать то, чего не было.
 *
 * Порядок обратный: зависимости между шагами обратимы только так.
 */

import { STATUS_LABELS } from '@/lib/status'
import { useEffect, useState } from 'react'
import type { InstanceState, StepRun, TimelineEntry } from '@/types/api'

interface RollbackLaneProps {
  instance: InstanceState | null
  steps: StepRun[]
}

type Marker = 'compensated' | 'planned' | 'kept'

const MARKER_STYLES: Record<Marker, string> = {
  compensated: 'bg-warning/20 text-warning border-warning/40',
  planned: 'bg-surface-border text-white/60 border-surface-border',
  kept: 'bg-surface-border/40 text-white/40 border-transparent',
}

export function RollbackLane({ instance, steps }: RollbackLaneProps) {
  const done = instance?.completed_steps ?? []
  if (!instance || done.length === 0) return null

  const pending = instance.pending_compensations
  const rollingBack = instance.status === 'COMPENSATING' || instance.status === 'CANCELLING'
  const compensatedOf = (name: string) => steps.some((run) => run.step_name === name && run.compensated)

  return (
    <section className="rounded-lg border border-surface-border bg-surface-raised p-4">
      <header className="mb-3">
        <h2 className="text-sm font-semibold text-white">Порядок отката</h2>
        <p className="mt-1 text-xs text-white/50">
          Список к откату взят из выполненных шагов, а не из определения: определение не знает, что
          успело выполниться. Откат идёт в обратном порядке — иначе зависимости между шагами не
          обратимы.
        </p>
      </header>

      <p className="mb-3 text-xs text-white/40">
        {rollingBack
          ? `Сейчас: ${STATUS_LABELS[instance.status]}`
          : 'Откат запускается при сбое шага, отмене процесса или нехватке проходов цикла'}
      </p>

      <ol className="space-y-1.5">
        {done.map((name, index) => {
          const already = compensatedOf(name)
          const planned = pending.includes(name)
          const marker: Marker = already ? 'compensated' : planned ? 'planned' : 'kept'
          const failed = steps.some((run) => run.step_name === name && run.status === 'FAILED')

          return (
            <li key={name} className="flex items-center gap-2 text-xs">
              <span className="w-5 shrink-0 text-right text-white/30">{done.length - index}</span>
              <span
                className={`w-24 shrink-0 rounded border px-2 py-0.5 text-center ${MARKER_STYLES[marker]}`}
              >
                {already ? 'откатан' : planned ? 'откат' : 'остаётся'}
              </span>
              <span className="font-mono text-white/80">{name}</span>
              {failed && <span className="text-danger/70">упал, компенсации нет</span>}
            </li>
          )
        })}
      </ol>
    </section>
  )
}

/**
 * Обратный отсчёт до срабатывания ожидания.
 *
 * Без него инстанс в статусе «Ожидание» выглядит одинаково — жив он или
 * забыт, а это противоположные ситуации. Данные берутся из события
 * TIMER_SCHEDULED: таймер лежит в базе, поэтому и срок известен точно.
 */
export function TimerCountdown({ timeline, waiting }: { timeline: TimelineEntry[]; waiting: boolean }) {
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    if (!waiting) return
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [waiting])

  if (!waiting) return null

  const scheduled = timeline.find(
    (entry) => entry.kind === 'event' && entry.type === 'TIMER_SCHEDULED',
  )
  const fired = timeline.some((entry) => entry.kind === 'event' && entry.type === 'TIMER_FIRED')

  if (fired) {
    return <p className="mt-1 text-xs text-white/50">Ожидание отбыло, шаг исполняется</p>
  }

  const dueAt = scheduled?.payload?.due_at
  if (typeof dueAt !== 'string') {
    return <p className="mt-1 text-xs text-white/50">Ожидание создано, срок из журнала неизвестен</p>
  }

  const remaining = new Date(dueAt).getTime() - now
  if (remaining <= 0) {
    return <p className="mt-1 text-xs text-warning">Ожидание истекло, воркер поднимет инстанс на ближайшем цикле</p>
  }

  return (
    <p className="mt-1 text-xs text-white/50">
      Сработает через {formatRemaining(remaining)}
      {scheduled?.step_name ? ` · шаг ${scheduled.step_name}` : ''}
    </p>
  )
}

function formatRemaining(millis: number): string {
  const seconds = Math.round(millis / 1000)
  if (seconds < 60) return `${seconds} с`
  if (seconds < 3600) return `${Math.floor(seconds / 60)} мин ${seconds % 60} с`
  return `${Math.floor(seconds / 3600)} ч ${Math.floor((seconds % 3600) / 60)} мин`
}