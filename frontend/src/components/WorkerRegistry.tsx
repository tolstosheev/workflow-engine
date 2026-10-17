/**
 * Реестр исполнителей (ADR-0018).
 *
 * Отвечает на два вопроса: сколько исполнителей в системе и кто из них молчит.
 * Второе важнее: раньше исполнитель был виден только пока держал процесс, то
 * есть пока делал что-то, и зависший выглядел так же, как отсутствующий.
 *
 * Заголовок называет то, что в списке. Записи старше суток скрыты за
 * переключателем вместе со своим числом — они не удалены, но и в решении не
 * участвуют.
 */


import { useCallback, useEffect, useState } from 'react'

import { workers as workersApi } from '@/api/client'
import { Button, ErrorState, StatusBadge } from '@/components/ui'
import { formatDateTime } from '@/lib/status'
import type { WorkerRecord, WorkerState } from '@/types/api'

const STATE: Record<WorkerState, { label: string; tone: string }> = {
  active: { label: 'Отвечает', tone: 'bg-success/15 text-success border-success/40' },
  stale: { label: 'Не отвечает', tone: 'bg-warning/15 text-warning border-warning/40' },
  stopped: { label: 'Остановлен', tone: 'bg-white/10 text-white/60 border-white/20' },
}

/** Сколько секунд прошло. Мусорное время даёт null, а не ноль — см. lib/time. */
function ageSeconds(iso: string): number | null {
  const millis = Date.parse(iso)
  if (Number.isNaN(millis)) return null
  return Math.max(0, Math.round((Date.now() - millis) / 1000))
}

function age(iso: string): string {
  const seconds = ageSeconds(iso)
  if (seconds === null) return '—'
  if (seconds < 60) return `${seconds} с назад`
  if (seconds < 3600) return `${Math.round(seconds / 60)} мин назад`
  return `${Math.round(seconds / 3600)} ч назад`
}

const POLL_MS = 5000

export function WorkerRegistry() {
  const [items, setItems] = useState<WorkerRecord[] | null>(null)
  const [freshFor, setFreshFor] = useState(0)
  const [departedTotal, setDepartedTotal] = useState(0)
  const [showDeparted, setShowDeparted] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      // Параметр уходит всегда: иначе переключатель менял бы выдачу, а сервер
      // молчал бы о скрытых строках, и число на панели расходилось бы с её
      // содержимым.
      const page = await workersApi.list({ departed: showDeparted })
      setItems(page.items)
      setFreshFor(page.fresh_for_seconds)
      setDepartedTotal(page.departed_total ?? 0)
      setError(null)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Реестр исполнителей не прочитан')
    }
  }, [showDeparted])

  useEffect(() => {
    void load()
    // Опрос непрерывный: реестр живёт тем, что сердце воркера перестало или
    // снова идёт, а без обновления «не отвечает» застыло бы навсегда.
    const timer = window.setInterval(() => void load(), POLL_MS)
    return () => window.clearInterval(timer)
  }, [load])

  const answering = items?.filter((worker) => worker.state === 'active').length ?? 0
  const silent = items?.filter((worker) => worker.state === 'stale').length ?? 0
  const stopped = items?.filter((worker) => worker.state === 'stopped').length ?? 0

  return (
    <section className="rounded-lg border border-surface-border bg-surface-raised p-4">
      <header className="mb-3 flex items-center justify-between gap-3">
        <h2 className="text-sm font-semibold text-white">Исполнители</h2>
        <span className="text-xs text-white/40">
          {items === null
            ? '…'
            : `${answering} отвечают${silent ? ` · ${silent} молчат` : ''}${
                stopped ? ` · ${stopped} остановлены` : ''
              }`}
        </span>
      </header>

      {error && <ErrorState message={error} />}

      {departedTotal > 0 && (
        <Button
          aria-pressed={showDeparted}
          onClick={() => setShowDeparted((value) => !value)}
        >
          {showDeparted
            ? 'Скрыть завершённых'
            : `Показать завершённых (${departedTotal})`}
        </Button>
      )}

      {items !== null && items.length === 0 && (
        <p className="mt-3 text-sm text-white/50">
          {departedTotal > 0
            ? 'Сейчас в системе нет ни одного исполнителя; завершённые скрыты за переключателем'
            : 'Исполнителей нет ни одного'}
        </p>
      )}

      {items !== null && items.length > 0 && (
        <ul className="mt-3 space-y-2">
          {items.map((worker) => (
            <li key={worker.worker_id} className="flex flex-wrap items-center gap-2 text-xs">
              <span className="font-mono text-white/80">{worker.worker_id}</span>
              <StatusBadge label={STATE[worker.state].label} toneClass={STATE[worker.state].tone} />
              <span className="text-white/40">активность {age(worker.heartbeat_at)}</span>
              <span className="text-white/40">циклов {worker.cycles_completed}</span>
              <span className="text-white/40">шагов {worker.steps_executed}</span>
              {worker.lease_lost > 0 && (
                <span className="text-warning" title="Циклов, оборвавшихся потерей закрепления">
                  потерянных закреплений {worker.lease_lost}
                </span>
              )}
              {worker.stopped_at && (
                <span className="text-white/40">остановлен {formatDateTime(worker.stopped_at)}</span>
              )}
            </li>
          ))}
        </ul>
      )}

      <p className="mt-3 text-[11px] text-white/30">
        {freshFor > 0
          ? `Молчащим считается исполнитель, который не подтвердил активность дольше ${freshFor} с. Упавший не удаляется: отсутствие сигнала — не отсутствие исполнителя.`
          : 'Упавший исполнитель не удаляется из реестра: отсутствие сигнала — не отсутствие исполнителя.'}
      </p>
    </section>
  )
}