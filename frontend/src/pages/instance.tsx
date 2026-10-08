import { useCallback, useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'

import { instances as instancesApi } from '@/api/client'
import { Button, Card, EmptyState, ErrorState, StatusBadge } from '@/components/ui'
import { eventLabel, formatDuration, formatDateTime, statusLabel, statusTone, stepTone } from '@/lib/status'
import type { InstanceState, StepRun, TimelineEntry, WorkflowInstance } from '@/types/api'

export default function InstancePage() {
  const { id } = useParams<{ id: string }>()
  const [instance, setInstance] = useState<WorkflowInstance | null>(null)
  const [state, setState] = useState<InstanceState | null>(null)
  const [steps, setSteps] = useState<StepRun[]>([])
  const [timeline, setTimeline] = useState<TimelineEntry[]>([])
  const [error, setError] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)

  const load = useCallback(async () => {
    if (!id) return
    try {
      const [one, rebuilt, runs, flow] = await Promise.all([
        instancesApi.get(id),
        instancesApi.state(id),
        instancesApi.steps(id),
        instancesApi.timeline(id),
      ])
      setInstance(one)
      setState(rebuilt)
      setSteps(runs.items)
      setTimeline(flow.items)
      setError(null)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Не удалось загрузить инстанс')
    }
  }, [id])

  useEffect(() => {
    void load()
    // Перечитывание страхует от пропущенных событий SSE: поток не гарантирует
    // доставку, а журнал на сервере остаётся полным.
    const timer = setInterval(() => void load(), 1500)
    return () => clearInterval(timer)
  }, [load])

  if (!id) return <EmptyState message="Идентификатор инстанса не указан" />
  if (error && !instance) return <ErrorState message={error} onRetry={() => void load()} />
  if (!instance) return <EmptyState message="Загрузка…" />

  return (
    <section className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">{instance.definition.name}</h1>
          <p className="text-xs text-white/40">
            v{instance.definition.version} · {instance.id}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <StatusBadge label={statusLabel(instance.status)} toneClass={statusTone(instance.status)} />
          <Actions instance={instance} onDone={load} onError={setActionError} />
        </div>
      </div>

      {actionError && <ErrorState message={actionError} />}

      {error && <ErrorState message={error} onRetry={() => void load()} />}

      <div className="grid gap-4 lg:grid-cols-3">
        <Card title="Состояние по журналу" className="lg:col-span-1">
          {state ? (
            <dl className="space-y-2 text-sm">
              <Row label="Статус" value={statusLabel(state.status)} />
              <Row label="Текущий шаг" value={state.current_step ?? '—'} />
              <Row label="Выполнено" value={state.completed_steps.join(' → ') || '—'} />
              <Row label="К откату" value={state.pending_compensations.join(' → ') || '—'} />
              <Row label="Аренда" value={instance.lease_owner ?? 'свободна'} />
              <Row label="Создан" value={formatDateTime(instance.created_at)} />
            </dl>
          ) : (
            <EmptyState message="Нет данных" />
          )}
        </Card>

        <Card title="Переменные инстанса" className="lg:col-span-2">
          {Object.keys(instance.variables).length === 0 ? (
            <EmptyState message="Переменные появятся после первого шага" />
          ) : (
            <pre className="max-h-64 overflow-auto rounded-md bg-surface p-3 text-xs">
              {JSON.stringify(instance.variables, null, 2)}
            </pre>
          )}
        </Card>
      </div>

      <Card title="Шаги">
        {steps.length === 0 ? (
          <EmptyState message="Шаги ещё не выполнялись" />
        ) : (
          <table className="w-full text-left text-sm">
            <thead className="text-xs uppercase tracking-wide text-white/40">
              <tr>
                <th className="pb-2">Шаг</th>
                <th className="pb-2">Попытка</th>
                <th className="pb-2">Статус</th>
                <th className="pb-2">Длительность</th>
                <th className="pb-2">Компенсация</th>
                <th className="pb-2">Ошибка</th>
              </tr>
            </thead>
            <tbody>
              {steps.map((run) => (
                <tr key={run.id} className="border-t border-surface-border/60">
                  <td className="py-2">{run.step_name}</td>
                  <td className="py-2 text-white/50">{run.attempt}</td>
                  <td className="py-2">
                    <StatusBadge label={run.status} toneClass={stepTone(run.status)} />
                  </td>
                  <td className="py-2 text-white/60">{formatDuration(run.started_at, run.finished_at)}</td>
                  <td className="py-2 text-white/50">{run.compensated ? 'выполнена' : '—'}</td>
                  <td className="py-2 text-danger/80">{run.error ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      <Card title="Журнал событий">
        {timeline.length === 0 ? (
          <EmptyState message="Событий пока нет" />
        ) : (
          <ol className="space-y-2">
            {timeline.map((entry, index) => (
              <li key={`${entry.kind}-${entry.seq ?? entry.step_name}-${index}`} className="flex gap-3 text-sm">
                <span className="w-24 shrink-0 text-white/40">
                  {formatDateTime(entry.created_at ?? entry.started_at)}
                </span>
                {entry.kind === 'event' ? (
                  <>
                    <span className="w-20 text-white/30">#{entry.seq}</span>
                    <span>{eventLabel((entry.type ?? 'INSTANCE_CREATED') as never)}</span>
                  </>
                ) : (
                  <>
                    <span className="w-20 text-white/30">шаг</span>
                    <span>{entry.step_name}</span>
                  </>
                )}
              </li>
            ))}
          </ol>
        )}
      </Card>
    </section>
  )
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-3">
      <dt className="text-white/50">{label}</dt>
      <dd className="text-right">{value}</dd>
    </div>
  )
}

interface ActionsProps {
  instance: WorkflowInstance
  onDone: () => void
  onError: (message: string) => void
}

/** Действия доступны не во всех состояниях: отменять завершённый процесс бессмысленно. */
function Actions({ instance, onDone, onError }: ActionsProps) {
  const terminal = ['COMPLETED', 'COMPENSATED', 'CANCELLED', 'FAILED'].includes(instance.status)

  const run = async (action: () => Promise<unknown>, fallback: string) => {
    try {
      await action()
      onError('')
      onDone()
    } catch (cause) {
      onError(cause instanceof Error ? cause.message : fallback)
    }
  }

  return (
    <>
      {instance.status === 'COMPENSATION_FAILED' && (
        <Button
          variant="primary"
          onClick={() => void run(() => instancesApi.retryCompensation(instance.id), 'Не удалось повторить откат')}
        >
          Повторить откат
        </Button>
      )}
      {!terminal && (
        <Button onClick={() => void run(() => instancesApi.cancel(instance.id), 'Не удалось отменить')}>
          Отменить
        </Button>
      )}
      {!terminal && (
        <Button
          variant="danger"
          onClick={() =>
            void run(
              () => instancesApi.forceComplete(instance.id, 'завершено оператором из интерфейса'),
              'Не удалось завершить принудительно',
            )
          }
        >
          Завершить без компенсаций
        </Button>
      )}
    </>
  )
}