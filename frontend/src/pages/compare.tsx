/**
 * Сравнение двух процессов на одном отрезке времени.
 *
 * Ответ на вопрос «во что обходится сбой», который из двух таблиц длительностей
 * получить нельзя. На раздельных шкалах процесс, прошедший за полсекунды, и
 * процесс, провисевший в ожидании сорок секунд, выглядели бы одинаково
 * быстрыми. На общем отрезке видно, что второй почти всё время ждал, и что
 * работа — это единицы процентов его жизни в обоих случаях.
 *
 * Процессы выбираются и живут в адресе: сравнение — это то, что показывают
 * комиссии, и ссылка должна переносить выбор, как и всё остальное на странице
 * списка.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'

import { Card, EmptyState, ErrorState } from '@/components/ui'
import { useAbortable } from '@/hooks/useAbortable'
import { instances as instancesApi } from '@/api/client'
import { sharedScale } from '@/lib/timeline'
import { statusLabel, statusTone } from '@/lib/status'
import { StatusBadge } from '@/components/ui'
import type { StepRun, WorkflowInstance } from '@/types/api'

interface Side {
  instance: WorkflowInstance
  steps: StepRun[]
}

export function ComparePage() {
  const [params, setParams] = useSearchParams()
  const left = params.get('a') ?? ''
  const right = params.get('b') ?? ''

  const [choices, setChoices] = useState<WorkflowInstance[]>([])
  const [sides, setSides] = useState<{ left: Side | null; right: Side | null }>({
    left: null,
    right: null,
  })
  const [error, setError] = useState<string | null>(null)
  // Зависимости — на сами `next`, а не на объекты, которые хук возвращает:
  // объект новый на каждом рендере, и с ним в зависимостях страница грузила
  // бы процессы по кругу. `next` — это useCallback с пустым списком, он стабилен.
  const { next: nextLeft } = useAbortable()
  const { next: nextRight } = useAbortable()

  useEffect(() => {
    let cancelled = false
    void (async () => {
      try {
        const page = await instancesApi.list({ limit: 50 })
        if (!cancelled) setChoices(page.items)
      } catch (cause) {
        if (!cancelled) {
          setError(cause instanceof Error ? cause.message : 'Не удалось получить список процессов')
        }
      }
    })()
    return () => {
      cancelled = true
    }
  }, [])

  const loadSide = useCallback(
    async (id: string, which: 'left' | 'right') => {
      if (!id) {
        setSides((current) => ({ ...current, [which]: null }))
        return
      }
      // Контроллер на каждую сторону свой: смена выбора справа не должна
      // отменять уже идущую загрузку слева.
      const controller = which === 'left' ? nextLeft() : nextRight()
      try {
        const [instance, steps] = await Promise.all([
          instancesApi.get(id, controller.signal),
          instancesApi.steps(id, controller.signal),
        ])
        if (controller.signal.aborted) return
        setSides((current) => ({ ...current, [which]: { instance, steps: steps.items } }))
      } catch (cause) {
        if (controller.signal.aborted) return
        setError(cause instanceof Error ? cause.message : 'Не удалось прочитать процесс')
      }
    },
    [nextLeft, nextRight],
  )

  useEffect(() => {
    void loadSide(left, 'left')
  }, [left, loadSide])

  useEffect(() => {
    void loadSide(right, 'right')
  }, [right, loadSide])

  const scale = useMemo(
    () =>
      sharedScale([sides.left?.steps ?? [], sides.right?.steps ?? []], Date.now()),
    [sides],
  )

  const pick = (which: 'a' | 'b', id: string) => {
    const next = new URLSearchParams(params)
    if (id) next.set(which, id)
    else next.delete(which)
    setParams(next, { replace: true })
  }

  const both = sides.left && sides.right

  return (
    <section className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold">Сравнение процессов</h1>
        <p className="mt-1 text-sm text-white/60">
          Два процесса на одном отрезке времени. Сравнивать длительности по раздельным шкалам
          бессмысленно: короткий процесс растянулся бы на всю ширину и выглядел бы равным длинному.
        </p>
      </div>

      {error && <ErrorState message={error} />}

      <Card title="Что сравниваем">
        <div className="grid gap-3 sm:grid-cols-2">
          <Select label="Первый процесс" value={left} choices={choices} onPick={(id) => pick('a', id)} />
          <Select label="Второй процесс" value={right} choices={choices} onPick={(id) => pick('b', id)} />
        </div>
      </Card>

      {!both && (
        <EmptyState message="Выберите два процесса — по умолчанию ничего не сравнивается" />
      )}

      {both && scale && (
        <div className="space-y-3">
          <SideCard title="Первый процесс" side={sides.left!} scale={scale} />
          <SideCard title="Второй процесс" side={sides.right!} scale={scale} />
        </div>
      )}
    </section>
  )
}

function Select({
  label,
  value,
  choices,
  onPick,
}: {
  label: string
  value: string
  choices: WorkflowInstance[]
  onPick: (id: string) => void
}) {
  return (
    <label className="block text-xs">
      <span className="mb-1 block uppercase tracking-wide text-white/40">{label}</span>
      <select
        aria-label={label}
        value={value}
        onChange={(event) => onPick(event.target.value)}
        className="w-full rounded-md border border-surface-border bg-surface px-3 py-1.5 font-mono text-sm"
      >
        <option value="">— не выбран —</option>
        {choices.map((item) => (
          <option key={item.id} value={item.id}>
            {item.definition.name} · {statusLabel(item.status)} · {item.id.slice(0, 8)}
          </option>
        ))}
      </select>
    </label>
  )
}

function SideCard({ title, side, scale }: { title: string; side: Side; scale: NonNullable<ReturnType<typeof sharedScale>> }) {
  const { instance, steps } = side
  const ordered = steps.filter((run) => run.started_at).sort((a, b) => a.started_at.localeCompare(b.started_at))
  const from = ordered[0]?.started_at ?? null
  const to = ordered[ordered.length - 1]?.finished_at ?? ordered[ordered.length - 1]?.started_at ?? null

  return (
    <Card title={title}>
      <div className="mb-2 flex flex-wrap items-center gap-2 text-xs">
        <StatusBadge label={statusLabel(instance.status)} toneClass={statusTone(instance.status)} />
        <span className="font-mono text-white/60">{instance.id.slice(0, 8)}</span>
        <span className="text-white/40">
          {from && to ? `${formatSpan(new Date(from).getTime(), new Date(to).getTime())} жизни` : 'нет прогонов'}
        </span>
      </div>

      <ol className="space-y-1">
        {ordered.map((run) => {
          const start = new Date(run.started_at).getTime()
          const end = run.finished_at ? new Date(run.finished_at).getTime() : Date.now()
          const offset = ((start - scale.from) / scale.span) * 100
          const width = Math.max(((end - start) / scale.span) * 100, 0.4)

          return (
            <li key={run.id} className="flex items-center gap-2 text-xs">
              <span className="w-32 shrink-0 truncate font-mono text-white/70">{run.step_name}</span>
              <span className="relative h-3 flex-1 overflow-hidden rounded bg-surface">
                <span
                  className={`absolute top-0 h-full ${
                    run.status === 'FAILED' ? 'bg-danger' : run.status === 'RUNNING' ? 'bg-accent' : 'bg-success'
                  }`}
                  style={{ left: `${offset}%`, width: `${width}%` }}
                />
              </span>
              <span className="w-16 shrink-0 text-right text-white/40">
                {Math.round(end - start)} мс
              </span>
            </li>
          )
        })}
      </ol>
    </Card>
  )
}

function formatSpan(from: number, to: number): string {
  const millis = to - from
  if (millis < 1000) return `${millis} мс`
  if (millis < 60_000) return `${(millis / 1000).toFixed(1)} с`
  return `${Math.round(millis / 60_000)} мин`
}
