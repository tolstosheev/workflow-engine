import { useCallback, useEffect } from 'react'

import { useEventStream } from '@/api/stream'
import { useAbortable } from '@/hooks/useAbortable'
import { usePolling } from '@/hooks/usePolling'
import { pollingIntervalMs } from '@/lib/polling'
import { useInstancesStore } from '@/store/instances'
import type { InstanceStatus } from '@/types/api'

/**
 * Живая лента инстансов: события потока плюс подстраховка опросом.
 *
 * Пока поток жив, события приносят изменения сами и опрос остаётся редким.
 * Когда поток молчит, он становится единственным источником, и частота
 * возвращается прежней.
 *
 * Из стора берутся только нужные поля: подписка на весь стор перерисовывала
 * бы страницу при каждой записи `loading` и `connected`.
 *
 * Фильтр по статусу приходит снаружи, а не берётся из стора: он живёт в
 * адресе, и адрес — единственное место, где он переживает перезагрузку.
 */
export function useInstancesFeed(statusFilter: InstanceStatus | 'ALL') {
  const items = useInstancesStore((state) => state.items)
  const loading = useInstancesStore((state) => state.loading)
  const error = useInstancesStore((state) => state.error)
  const connected = useInstancesStore((state) => state.connected)
  const lastEventAt = useInstancesStore((state) => state.lastEventAt)
  const archivedTotal = useInstancesStore((state) => state.archivedTotal)
  const handleEvent = useInstancesStore((state) => state.handleEvent)
  const setConnected = useInstancesStore((state) => state.setConnected)
  const loadList = useInstancesStore((state) => state.load)

  useEventStream({ onEvent: handleEvent, onStatusChange: setConnected })

  // Текущий запрос: без ссылки на него нечем отменить предыдущий, а он
  // нужен, чтобы уходящая со страницы вкладка не держала соединения.
  const { next: beginRequest, abort } = useAbortable()

  const reload = useCallback(async () => {
    await loadList(beginRequest().signal)
  }, [beginRequest, loadList])

  usePolling(reload, pollingIntervalMs(null, connected))
  useEffect(() => abort, [abort])

  const visible =
    statusFilter === 'ALL' ? items : items.filter((item) => item.status === statusFilter)

  return {
    items: visible,
    all: items,
    loading,
    error,
    connected,
    lastEventAt,
    archivedTotal,
    load: reload,
  }
}