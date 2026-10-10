import { useEffect } from 'react'

import { useEventStream } from '@/api/stream'
import { useInstancesStore } from '@/store/instances'

/**
 * Держит список инстансов свежим.
 *
 * События SSE обновляют список точечно, а перечитывание по таймеру страхует от
 * пропусков: поток не гарантирует доставку, а пропущенное событие не вернётся.
 */
export function useInstancesFeed() {
  const { items, loading, error, connected, lastEventAt, load, handleEvent, setConnected, statusFilter } =
    useInstancesStore()

  useEventStream({ onEvent: handleEvent, onStatusChange: setConnected })

  useEffect(() => {
    void load()
    // Список перечитывается независимо от потока: раз в две секунды это
    // дёшево, а расхождение с журналом после обрыва не накапливается.
    const timer = setInterval(() => void load(), 2000)
    return () => clearInterval(timer)
  }, [load])

  const visible =
    statusFilter === 'ALL' ? items : items.filter((item) => item.status === statusFilter)

  return { items: visible, all: items, loading, error, connected, lastEventAt, load }
}