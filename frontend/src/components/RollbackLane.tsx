/**
 * Порядок компенсаций: фактический, а не предполагаемый.
 *
 * Список берётся из того, что шаги действительно выполнились, а не из схемы:
 * схема не знает, какие шаги успели выполниться до сбоя. Порядок обратный —
 * зависимости между шагами обратимы только так.
 */


import { STATUS_LABELS } from '@/lib/status'
import { useEffect, useState } from 'react'
import type { InstanceState, StepRun, TimelineEntry } from '@/types/api'

interface RollbackLaneProps {
  instance: InstanceState | null
  steps: StepRun[]
  /** Описание шага из схемы; без него полоса показывает имена. */
  titles?: Record<string, string>
}

type Marker = 'compensated' | 'planned' | 'kept'

const MARKER_STYLES: Record<Marker, string> = {
  compensated: 'bg-warning/20 text-warning border-warning/40',
  planned: 'bg-surface-border text-white/60 border-surface-border',
  kept: 'bg-surface-border/40 text-white/40 border-transparent',
}

export function RollbackLane({ instance, steps, titles }: RollbackLaneProps) {
  const done = instance?.completed_steps ?? []
  if (!instance || done.length === 0) return null

  const pending = instance.pending_compensations
  const rollingBack = instance.status === 'COMPENSATING' || instance.status === 'CANCELLING'
  const compensatedOf = (name: string) => steps.some((run) => run.step_name === name && run.compensated)

  return (
    <section className="rounded-lg border border-surface-border bg-surface-raised p-4">
      <header className="mb-3">
        <h2 className="text-sm font-semibold text-white">Порядок компенсации</h2>
        <p className="mt-1 text-xs text-white/50">
          Список компенсаций взят из выполненных шагов, а не из схемы: схема не знает, что
          успело выполниться. Компенсация идёт в обратном порядке — иначе зависимости между шагами
          обратимы.
        </p>
      </header>

      <p className="mb-3 text-xs text-white/40">
        {rollingBack
          ? `Сейчас: ${STATUS_LABELS[instance.status]}`
          : 'Компенсация запускается при сбое шага, отмене процесса или нехватке проходов цикла'}
      </p>

      <ol className="space-y-1.5">
        {/* Список показывается в том порядке, в котором откат пойдёт, а не в том,
            в котором шаги выполнялись. Порядок выполнения приходилось читать
            снизу вверх: нумерация была правильной, а строки шли прямые, и
            верхняя строка — первый выполненный — читалась как первый к откату. */}
        {[...done].reverse().map((name, index) => {
          const already = compensatedOf(name)
          const planned = pending.includes(name)
          const marker: Marker = already ? 'compensated' : planned ? 'planned' : 'kept'
          const failed = steps.some((run) => run.step_name === name && run.status === 'FAILED')

          return (
            <li key={name} className="flex items-center gap-2 text-xs">
              <span className="w-5 shrink-0 text-right text-white/30">{index + 1}</span>
              <span
                className={`w-24 shrink-0 rounded border px-2 py-0.5 text-center ${MARKER_STYLES[marker]}`}
              >
                {already ? 'компенсирован' : planned ? 'компенсация' : 'остаётся'}
              </span>
              <span className="font-mono text-white/80">{titles?.[name] ?? name}</span>
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
export function TimerCountdown({
  timeline,
  waiting,
  kind = 'step',
}: {
  timeline: TimelineEntry[]
  waiting: boolean
  /** Какого ожидания касается отсчёт: шага или срока паузы (ADR-0016). */
  kind?: 'step' | 'pause_deadline'
}) {
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    if (!waiting) return
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [waiting])

  if (!waiting) return null

  // Отсчёт берёт своё ожидание, а не первое попавшееся: в журнале процесса
  // их может быть несколько, и срок уже отбывшего ожидания шага показывал бы
  // неверное время до отмены.
  const scheduled = timeline.find(
    (entry) =>
      entry.kind === 'event' &&
      entry.type === 'TIMER_SCHEDULED' &&
      (entry.payload?.kind ?? 'step') === kind,
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
    return (
      <p className="mt-1 text-xs text-warning">
        {kind === 'pause_deadline'
          ? 'Срок ожидания истёк, процесс отменится и уйдёт в компенсацию'
          : 'Ожидание истекло, исполнитель поднимет процесс на ближайшем цикле'}
      </p>
    )
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