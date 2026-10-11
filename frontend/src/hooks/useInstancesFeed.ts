import { useCallback, useEffect, useRef } from 'react'

import { useEventStream } from '@/api/stream'
import { pollingIntervalMs } from '@/lib/polling'
import { useInstancesStore } from '@/store/instances'

/**
 * Живая лента инстансов: события потока плюс подстраховка опросом.
 *
 * Пока поток жив, события приносят изменения сами и опрос остаётся редким.
 * Когда поток молчит, он становится единственным источником, и частота
 * возвращается прежней.
 */
export function useInstancesFeed() {
  const {
    items,
    loading,
    error,
    connected,
    lastEventAt,
    load,
    handleEvent,
    setConnected,
    statusFilter,
  } = useInstancesStore()

  useEventStream({ onEvent: handleEvent, onStatusChange: setConnected })

  // Текущий запрос: без ссылки на него нечем отменить предыдущий, а он
  // нужен, чтобы уходящая со страницы вкладка не держала соединения.
  const inflight = useRef<AbortController | null>(null)

  const reload = useCallback(async () => {
    const controller = new AbortController()
    inflight.current?.abort()
    inflight.current = controller
    try {
      await load(controller.signal)
    } finally {
      if (inflight.current === controller) inflight.current = null
    }
  }, [load])

  useEffect(() => {
    void reload()
    const delay = pollingIntervalMs(null, connected)
    const timer = window.setInterval(() => void reload(), delay)
    return () => {
      window.clearInterval(timer)
      inflight.current?.abort()
    }
  }, [reload, connected])

  const visible =
    statusFilter === 'ALL' ? items : items.filter((item) => item.status === statusFilter)

  return { items: visible, all: items, loading, error, connected, lastEventAt, load: reload }
}