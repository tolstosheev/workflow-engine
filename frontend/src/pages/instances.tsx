import { useEffect, useMemo, useState } from 'react'

import { Link, useSearchParams } from 'react-router-dom'

import { instances as instancesApi } from '@/api/client'
import { WorkerLeases, StreamStatus } from '@/components/WorkerLeases'
import { WorkerRegistry } from '@/components/WorkerRegistry'
import { Button, Card, EmptyState, ErrorState, StatusBadge } from '@/components/ui'
import { useInstancesFeed } from '@/hooks/useInstancesFeed'
import {
  INSTANCE_FILTERS,
  countByFilter,
  countRunning,
  filterInstances,
  readFilterId,
  selectInstances,
} from '@/lib/instanceList'
import { formatDateTime, statusLabel, statusTone } from '@/lib/status'
import type { WorkflowInstance } from '@/types/api'

export default function InstancesPage() {
  // Состояние списка живёт в адресе, а не в полях страницы: без него фильтр и
  // порядок терялись при перезагрузке, ссылка была негодной, а кнопка «назад»
  // возвращала на список, который человек уже отфильтровал.
  const [params, setParams] = useSearchParams()
  const filterId = readFilterId(params.get('filter'))
  const query = params.get('q') ?? ''
  const newestFirst = params.get('sort') !== 'old'

  function patch(next: Record<string, string>): void {
    const merged = new URLSearchParams(params)
    for (const [key, value] of Object.entries(next)) merged.set(key, value)
    // Пустые и равные умолчанию значения убираются, а не пишутся: `?q=&filter=all`
    // — это шум в ссылке, и две ссылки на одно состояние разъезжаются.
    for (const key of [...merged.keys()]) {
      const value = merged.get(key)
      if (value === '' || (key === 'filter' && value === 'all')) merged.delete(key)
    }
    setParams(merged, { replace: true })
  }

  const showArchive = params.get('archive') === '1'
  const { items, all, loading, error, connected, lastEventAt, load, archivedTotal } =
    useInstancesFeed()

  // Архивный список грузится отдельно от основного: стор держит только живые
  // процессы, и мешать ему архивные строки значило бы заставить его помнить,
  // в каком режиме открыт.
  const [archiveItems, setArchiveItems] = useState<WorkflowInstance[]>([])
  const [tidyError, setTidyError] = useState<string | null>(null)
  const [tidyNote, setTidyNote] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!showArchive) return
    let cancelled = false
    void (async () => {
      try {
        const page = await instancesApi.list({ limit: 100, archived: 'archived' })
        if (!cancelled) setArchiveItems(page.items)
      } catch (cause) {
        if (!cancelled) {
          setTidyError(cause instanceof Error ? cause.message : 'Архив не прочитан')
        }
      }
    })()
    return () => {
      cancelled = true
    }
  }, [showArchive])

  // Уборка архивирует, а не удаляет: журнал процесса остаётся целым, и кнопка
  // так и называется. Слово «удалить» обещало бы больше, чем делает движок.
  const runTidy = async () => {
    setBusy(true)
    setTidyError(null)
    setTidyNote(null)
    try {
      const result = await instancesApi.archive()
      setTidyNote(result.message)
      await load()
    } catch (cause) {
      setTidyError(cause instanceof Error ? cause.message : 'Уборка не выполнена')
    } finally {
      setBusy(false)
    }
  }

  const restore = async (id: string) => {
    setBusy(true)
    setTidyError(null)
    try {
      await instancesApi.unarchive(id)
      const page = await instancesApi.list({ limit: 100, archived: 'archived' })
      setArchiveItems(page.items)
      await load()
    } catch (cause) {
      setTidyError(cause instanceof Error ? cause.message : 'Не удалось вернуть процесс')
    } finally {
      setBusy(false)
    }
  }

  // Поиск и сортировка применяются к уже полученному списку: это то, что
  // видно сейчас, без запроса на каждый ввод. Отбор и порядок считаются в
  // чистой функции: правило проверяется тестом, а страница — отрисовать.
  const visible = useMemo(
    () => selectInstances(filterInstances(items, filterId), { search: query, newestFirst }),
    [items, filterId, query, newestFirst],
  )
  // Числа на вкладках: пустая вкладка без числа выглядит поломкой, хотя
  // означает всего лишь «сейчас таких нет».
  const counts = useMemo(() => countByFilter(items), [items])
  const runningCount = useMemo(() => countRunning(visible), [visible])

  return (
    <section className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Инстансы workflow</h1>
        <StreamStatus connected={connected} lastEventAt={lastEventAt} />
      </div>

      <div className="flex flex-wrap items-center gap-2">
        {INSTANCE_FILTERS.map((filter) => (
          <Button
            key={filter.id}
            variant={filterId === filter.id ? 'primary' : 'secondary'}
            aria-pressed={filterId === filter.id}
            onClick={() => patch({ filter: filter.id })}
          >
            {filter.label}
            {counts[filter.id] > 0 && ` (${counts[filter.id]})`}
          </Button>
        ))}

        <input
          id="instance-search"
          aria-label="Поиск по имени процесса"
          value={query}
          onChange={(event) => patch({ q: event.target.value })}
          placeholder="Имя процесса"
          className="ml-auto w-56 rounded-md border border-surface-border bg-surface px-3 py-1.5 text-sm"
        />
        <Button onClick={() => patch({ sort: newestFirst ? 'old' : '' })}>
          {newestFirst ? 'Сначала новые' : 'Сначала старые'}
        </Button>
        <Button aria-pressed={showArchive} onClick={() => patch({ archive: showArchive ? '' : '1' })}>
          {showArchive ? 'Скрыть архив' : `Архив${archivedTotal ? ` (${archivedTotal})` : ''}`}
        </Button>
        {!showArchive && (
          <Button disabled={busy} onClick={() => void runTidy()}>
            {busy ? 'Убираем…' : 'Убрать завершённые'}
          </Button>
        )}
      </div>

      {tidyNote && (
        <p aria-live="polite" className="text-xs text-white/40">
          {tidyNote}. Из списка они убраны, но не удалены: журнал цел, а открыть процесс можно через
          переключатель «Архив».
        </p>
      )}
      {tidyError && <ErrorState message={tidyError} />}

      {showArchive && (
        <Card title="Архив">
          {archiveItems.length === 0 ? (
            <EmptyState message="В архиве пусто" />
          ) : (
            <ul className="space-y-1 text-sm">
              {archiveItems.map((instance) => (
                <li key={instance.id} className="flex items-center gap-2">
                  <Link to={`/instances/${instance.id}`} className="text-accent hover:underline">
                    {instance.definition.name}
                  </Link>
                  <StatusBadge
                    label={statusLabel(instance.status)}
                    toneClass={statusTone(instance.status)}
                  />
                  <span className="text-xs text-white/40">{formatDateTime(instance.created_at)}</span>
                  <Button
                    className="ml-auto"
                    disabled={busy}
                    onClick={() => void restore(instance.id)}
                  >
                    Вернуть в список
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </Card>
      )}

      {query.trim() && (
        <p aria-live="polite" className="text-xs text-white/40">
          По запросу «{query.trim()}» найдено {visible.length}, выполняется {runningCount}
        </p>
      )}

      {error && <ErrorState message={error} onRetry={() => void load()} />}

      <WorkerLeases instances={all} />

      <WorkerRegistry />

      <Card>
        {loading && items.length === 0 ? (
          <EmptyState message="Загрузка инстансов…" />
        ) : items.length === 0 ? (
          <EmptyState message="Инстансов пока нет. Запустите процесс на странице определений." />
        ) : visible.length === 0 ? (
          <EmptyState message={`По запросу «${query.trim()}» ничего не найдено`} />
        ) : (
          <table aria-label="Инстансы workflow" className="w-full text-left text-sm">
            <thead className="text-xs uppercase tracking-wide text-white/40">
              <tr>
                <th scope="col" className="pb-2">Процесс</th>
                <th scope="col" className="pb-2">Статус</th>
                <th scope="col" className="pb-2">Текущий шаг</th>
                <th scope="col" className="pb-2">Воркер</th>
                <th scope="col" className="pb-2">Создан</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((instance) => (
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