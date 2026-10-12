import { useCallback, useEffect, useRef, useState } from 'react'
import { useParams } from 'react-router-dom'

import { instances as instancesApi, definitions as definitionsApi } from '@/api/client'
import { useEventStream } from '@/api/stream'
import { DefinitionGraph } from '@/components/DefinitionGraph'
import { RollbackLane, TimerCountdown } from '@/components/RollbackLane'
import { Button, Card, EmptyState, ErrorState, StatusBadge } from '@/components/ui'
import { useAbortable } from '@/hooks/useAbortable'
import { usePolling } from '@/hooks/usePolling'
import { pollingIntervalMs } from '@/lib/polling'
import { eventLabel, formatDuration, formatDateTime, statusLabel, statusTone, stepTone } from '@/lib/status'
import type { InstanceState, StepRun, TimelineEntry, WorkflowDefinition, WorkflowInstance } from '@/types/api'

export default function InstancePage() {
  const { id } = useParams<{ id: string }>()
  const [instance, setInstance] = useState<WorkflowInstance | null>(null)
  const [state, setState] = useState<InstanceState | null>(null)
  const [steps, setSteps] = useState<StepRun[]>([])
  const [timeline, setTimeline] = useState<TimelineEntry[]>([])
  const [definition, setDefinition] = useState<WorkflowDefinition | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [connected, setConnected] = useState(false)
  // Определение перечитывается только при смене инстанса: это единственная
  // строка без изменений, и запрос на каждый цикл повторялся бы десятки раз.
  const loadedDefinition = useRef<string | null>(null)

  // Текущий запрос: без ссылки на него нечем отменить предыдущий.
  // Текущий запрос: без ссылки на него нечем отменить предыдущий.
  const { next: beginRequest, abort } = useAbortable()

  const load = useCallback(async () => {
    if (!id) return
    // Ответ предыдущей загрузки мог прийти позже текущего и откатить экран на
    // шаг назад, а после ухода со страницы он писал бы в состояние уже не
    // существующего компонента. Оба запроса отменяются одним сигналом.
    const controller = beginRequest()
    try {
      const [one, rebuilt, runs, flow] = await Promise.all([
        instancesApi.get(id, controller.signal),
        instancesApi.state(id, controller.signal),
        instancesApi.steps(id, controller.signal),
        instancesApi.timeline(id, controller.signal),
      ])
      if (controller.signal.aborted) return
      setInstance(one)
      setState(rebuilt)
      setSteps(runs.items)
      setTimeline(flow.items)
      setError(null)

      // Определение — единственная строка без изменений. Запрашивать его на
      // каждый цикл значило бы лишний запрос, который на длинном процессе
      // повторялся бы десятки раз.
      if (loadedDefinition.current !== one.definition.id) {
        try {
          const loaded = await definitionsApi.get(one.definition.id)
          if (controller.signal.aborted) return
          setDefinition(loaded)
          loadedDefinition.current = one.definition.id
        } catch {
          setDefinition(null)
        }
      }
    } catch (cause) {
      // Отменённый запрос — не ошибка: он отменён намеренно, следующий
      // запустится по своему расписанию.
      if (controller.signal.aborted) return
      setError(cause instanceof Error ? cause.message : 'Не удалось загрузить инстанс')
    }
  }, [beginRequest, id])

  // Страховка на потерянные события: поток не гарантирует доставку, а журнал
  // на сервере остаётся полным. Завершённый процесс не опрашивается вовсе,
  // а при живом потоке интервал редкий — события и так приносят изменения.
  usePolling(load, pollingIntervalMs(instance?.status ?? null, connected))
  useEffect(() => abort, [abort])
  // Приведение экрана запускает запрос, а не каждое событие: шаги идут пачками,
  // и без общего счётчика страница запрашивала бы данные на каждый шаг.
  // Сборка затем, но последнее из любого события.
  const refreshAt = useRef(0)
  const scheduled = useRef<number | null>(null)

  useEventStream({
    // Состояние потока определяет частоту опроса: пока поток жив, события
    // приносят изменения сами и подстраховка может быть редкой.
    onStatusChange: setConnected,
    onEvent: (event) => {
      if (event.instance_id !== id) return
      const now = Date.now()
      if (now - refreshAt.current < 400) return
      refreshAt.current = now
      if (scheduled.current !== null) return
      scheduled.current = window.setTimeout(() => {
        scheduled.current = null
        void load()
      }, 400)
    },
  })

  useEffect(
    () => () => {
      if (scheduled.current !== null) window.clearTimeout(scheduled.current)
    },
    [],
  )

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
              <Row
                label="Ожидание"
                value={
                  instance.status === 'WAITING' ? (
                    <TimerCountdown timeline={timeline} waiting />
                  ) : (
                    '—'
                  )
                }
              />
              <Row
                label="Цикл"
                value={
                  Object.entries(state.step_iterations ?? {})
                    .filter(([, count]) => count > 1)
                    .map(([name, count]) => `${name}: ${count}`)
                    .join(', ') || '—'
                }
              />
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

      {definition && (
        <Card title="Граф шагов">
          <DefinitionGraph definition={definition.definition} steps={steps} instance={state} />
        </Card>
      )}

      <RollbackLane instance={state} steps={steps} />

      <Card title="Шаги">
        {steps.length === 0 ? (
          <EmptyState message="Шаги ещё не выполнялись" />
        ) : (
          <table aria-label="Прогоны шагов" className="w-full text-left text-sm">
            <thead className="text-xs uppercase tracking-wide text-white/40">
              <tr>
                <th scope="col" className="pb-2">Шаг</th>
                <th scope="col" className="pb-2">Попытка</th>
                <th scope="col" className="pb-2">Статус</th>
                <th scope="col" className="pb-2">Длительность</th>
                <th scope="col" className="pb-2">Компенсация</th>
                <th scope="col" className="pb-2">Ошибка</th>
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
              <li
                key={`${entry.kind}-${entry.seq ?? entry.step_name}-${index}`}
                className={`flex gap-3 text-sm ${entry.kind === 'step' ? 'pl-4' : ''}`}
              >
                <span className="w-24 shrink-0 text-white/40">
                  {formatDateTime(entry.created_at ?? entry.started_at)}
                </span>
                {entry.kind === 'event' ? (
                  <>
                    <span className="w-20 shrink-0 text-white/30">#{entry.seq}</span>
                    <span className="min-w-0">
                      <span className="block">{eventLabel((entry.type ?? 'INSTANCE_CREATED') as never)}</span>
                      {/* payload — то, чем журнал доказывает: без него видно
                          «шаг выполнен», но не видно, каким был переход. */}
                      <EventPayload payload={entry.payload} />
                    </span>
                  </>
                ) : (
                  <>
                    <span className="w-20 shrink-0 text-white/30">шаг</span>
                    <span className="min-w-0">
                      <span className="block">{entry.step_name}</span>
                      <span className="text-xs text-white/40">
                        {entry.status?.toLowerCase()}
                        {entry.compensated ? ', компенсация выполнена' : ''}
                      </span>
                    </span>
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

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-3">
      <dt className="text-white/50">{label}</dt>
      <dd className="text-right">{value}</dd>
    </div>
  )
}

/** Содержимое события: показывается по требованию, чтобы не шуметь. */
function EventPayload({ payload }: { payload: Record<string, unknown> | null | undefined }) {
  const [open, setOpen] = useState(false)
  const entries = Object.entries(payload ?? {})
  if (entries.length === 0) return null

  return (
    <>
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        className="mt-0.5 text-[11px] text-accent/80 hover:text-accent"
      >
        {open ? 'скрыть' : `данные события (${entries.length})`}
      </button>
      {open && (
        <pre className="mt-1 overflow-auto rounded bg-surface p-2 text-[11px] text-white/60">
          {JSON.stringify(payload, null, 2)}
        </pre>
      )}
    </>
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
      {instance.status === 'PAUSED' && (
        // Процесс стоит и ждёт именно этого решения: без кнопки снять паузу
        // можно было бы только из консоли.
        <Button variant="primary" onClick={() => void run(() => instancesApi.resume(instance.id), 'Не удалось продолжить процесс')}>
          Продолжить
        </Button>
      )}
      {instance.status === 'COMPENSATION_FAILED' && (
        <Button
          variant="primary"
          onClick={() => void run(() => instancesApi.retryCompensation(instance.id), 'Не удалось повторить откат')}
        >
          Повторить откат
        </Button>
      )}
      {!terminal && (
        // Отмена работает и из паузы: компенсировать выполненные шаги нужно
        // независимо от того, чем процесс занимался.
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
