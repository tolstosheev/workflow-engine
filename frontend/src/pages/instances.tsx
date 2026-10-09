import { Link } from 'react-router-dom'

import { WorkerLeases, StreamStatus } from '@/components/WorkerLeases'
import { Button, Card, EmptyState, ErrorState, StatusBadge } from '@/components/ui'
import { useInstancesFeed } from '@/hooks/useInstancesFeed'
import { formatDateTime, statusLabel, statusTone } from '@/lib/status'
import { useInstancesStore } from '@/store/instances'
import type { InstanceStatus } from '@/types/api'

const FILTERS: Array<{ value: InstanceStatus | 'ALL'; label: string }> = [
  { value: 'ALL', label: 'Все' },
  { value: 'RUNNING', label: 'Выполняются' },
  { value: 'COMPENSATING', label: 'Откат' },
  { value: 'COMPENSATION_FAILED', label: 'Откат не удался' },
  { value: 'COMPLETED', label: 'Завершённые' },
]

export default function InstancesPage() {
  const { items, all, loading, error, connected, lastEventAt, load } = useInstancesFeed()
  const statusFilter = useInstancesStore((state) => state.statusFilter)
  const setStatusFilter = useInstancesStore((state) => state.setStatusFilter)

  return (
    <section className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Инстансы workflow</h1>
        <StreamStatus connected={connected} lastEventAt={lastEventAt} />
      </div>

      <div className="flex flex-wrap gap-2">
        {FILTERS.map((filter) => (
          <Button
            key={filter.value}
            variant={statusFilter === filter.value ? 'primary' : 'secondary'}
            onClick={() => setStatusFilter(filter.value)}
          >
            {filter.label}
          </Button>
        ))}
      </div>

      {error && <ErrorState message={error} onRetry={() => void load()} />}

      <WorkerLeases instances={all} />

      <Card>
        {loading && items.length === 0 ? (
          <EmptyState message="Загрузка инстансов…" />
        ) : items.length === 0 ? (
          <EmptyState message="Инстансов пока нет. Запустите процесс на странице определений." />
        ) : (
          <table className="w-full text-left text-sm">
            <thead className="text-xs uppercase tracking-wide text-white/40">
              <tr>
                <th className="pb-2">Процесс</th>
                <th className="pb-2">Статус</th>
                <th className="pb-2">Текущий шаг</th>
                <th className="pb-2">Воркер</th>
                <th className="pb-2">Создан</th>
              </tr>
            </thead>
            <tbody>
              {items.map((instance) => (
                <tr key={instance.id} className="border-t border-surface-border/60">
                  <td className="py-2">
                    <Link
                      to={`/instances/${instance.id}`}
                      className="text-accent hover:underline"
                    >
                      {instance.definition.name}
                    </Link>
                    <span className="ml-2 text-xs text-white/40">v{instance.definition.version}</span>
                  </td>
                  <td className="py-2">
                    <StatusBadge
                      label={statusLabel(instance.status)}
                      toneClass={statusTone(instance.status)}
                    />
                  </td>
                  <td className="py-2 text-white/70">{instance.current_step ?? '—'}</td>
                  <td className="py-2 text-white/50">{instance.lease_owner ?? '—'}</td>
                  <td className="py-2 text-white/50">{formatDateTime(instance.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </section>
  )
}