/**
 * Закреплённые процессы: кто что держит и когда последний раз подтверждал
 * активность.
 *
 * После смерти исполнителя владелец меняется сам, без блокировок, которые
 * пришлось бы снимать руками.
 */


import { StatusBadge } from '@/components/ui'
import { collectLeases } from '@/lib/leases'
import { formatDateTime } from '@/lib/status'
import type { WorkflowInstance } from '@/types/api'

export function WorkerLeases({ instances }: { instances: WorkflowInstance[] }) {
  const leases = collectLeases(instances)
  const free = instances.filter((instance) => !instance.lease_owner).length

  return (
    <section className="rounded-lg border border-surface-border bg-surface-raised p-4">
      <header className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-white">Закреплённые процессы</h2>
        <span className="text-xs text-white/40">
          {leases.length} закреплено · {free} свободно
        </span>
      </header>

      {leases.length === 0 ? (
        <p className="text-sm text-white/50">Никто не держит процессы — исполнители простаивают</p>
      ) : (
        <ul className="space-y-2">
          {leases.map((lease) => (
            <li key={lease.owner} className="flex flex-wrap items-center gap-2 text-xs">
              <span className="font-mono text-white/80">{lease.owner}</span>
              <span className="text-white/40">{lease.instances.length} инст.</span>
              <span className="text-white/40">
                активность {lease.lastHeartbeat ? formatDateTime(lease.lastHeartbeat) : '—'}
              </span>
              <span className="flex flex-wrap gap-1">
                {lease.instances.map((instance) => (
                  <span
                    key={instance.id}
                    title={`${instance.current_step ?? 'без шага'} · создан ${formatDateTime(instance.created_at)}`}
                    className="rounded border border-surface-border px-1.5 py-0.5 font-mono text-[10px] text-white/60"
                  >
                    {instance.current_step ?? '—'}
                  </span>
                ))}
              </span>
            </li>
          ))}
        </ul>
      )}

      <p className="mt-3 text-[11px] text-white/30">
        Здесь только те, кто держит процесс. Исполнитель, которому нечего делать, ничего не
        не закрепляет и в списке не появится — за ним стоит панель «Исполнители» ниже.
      </p>
    </section>
  )
}

/** Статус соединения с потоком событий и время последнего события. */
export function StreamStatus({
  connected,
  lastEventAt,
}: {
  connected: boolean
  lastEventAt: string | null
}) {
  return (
    <div className="flex items-center gap-2 text-xs">
      <StatusBadge
        label={connected ? 'Поток событий подключён' : 'Поток событий недоступен'}
        toneClass={
          connected
            ? 'bg-success/15 text-success border-success/40'
            : 'bg-warning/15 text-warning border-warning/40'
        }
      />
      {lastEventAt && (
        <span className="text-white/40">последнее событие {formatDateTime(lastEventAt)}</span>
      )}
    </div>
  )
}