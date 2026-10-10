/**
 * Подписка на поток событий.
 *
 * `EventSource` переподключается сам, но после обрыва он начинает с текущего
 * момента: пропущенные события догружаются запросом журнала по `fromSeq`.
 */

import { useEffect, useRef } from 'react'

import type { StreamEvent } from '@/types/api'

const BASE_URL = (import.meta.env.VITE_API_URL ?? '').replace(/\/$/, '')

export interface StreamOptions {
  /** Вызывается на каждом событии потока. */
  onEvent: (event: StreamEvent) => void
  /** Отмечает разрыв соединения, чтобы интерфейс показал «нет связи». */
  onStatusChange?: (connected: boolean) => void
}

export function useEventStream({ onEvent, onStatusChange }: StreamOptions): void {
  // Колбэк хранится в ref, чтобы переподключение не зависело от его
  // идентичности: смена обработчика не должна рвать подписку.
  const handlerRef = useRef(onEvent)
  handlerRef.current = onEvent

  const statusRef = useRef(onStatusChange)
  statusRef.current = onStatusChange

  useEffect(() => {
    const source = new EventSource(`${BASE_URL}/api/stream`)

    const forward = (raw: MessageEvent) => {
      try {
        handlerRef.current(JSON.parse(raw.data) as StreamEvent)
      } catch {
        // Повреждённый кадр не должен рвать поток: следующий придёт вовремя.
      }
    }

    // Список должен совпадать с бекендовым `NOTIFIABLE`
    // в `engine/app/routers/stream.py`. Расхождение двух списков обязательно
    // содержать: событие, раньше превращались было без подписки в потоке.
    const types: StreamEvent['type'][] = [
      'INSTANCE_CREATED',
      'INSTANCE_COMPLETED',
      'INSTANCE_COMPENSATED',
      'INSTANCE_FAILED',
      'INSTANCE_CANCEL_REQUESTED',
      'INSTANCE_CANCELLED',
      'LOOP_EXHAUSTED',
      'STEP_COMPLETED',
      'STEP_FAILED',
      'COMPENSATION_STARTED',
      'COMPENSATION_COMPLETED',
      'COMPENSATION_FAILED',
    ]
    for (const type of types) {
      source.addEventListener(type, forward as EventListener)
    }

    source.onopen = () => statusRef.current?.(true)
    source.onerror = () => statusRef.current?.(false)

    return () => {
      source.close()
    }
  }, [])
}