/**
 * Подписка на поток событий.
 *
 * `EventSource` переподключается сам и передаёт в `Last-Event-ID` последнее
 * событие, поэтому разрыв не приводит к потере. Пока соединение живо,
 * события — источник изменений, а опрос остаётся страховкой на молчание.
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

/**
 * Типы событий, на которые подписан поток.
 *
 * Список обязан совпадать с `NOTIFIABLE` в
 * `engine/app/routers/stream.py`: событие, которого нет в потоке, экран не
 * увидит, а его обработчик в редьюсере останется недостижимым. Расхождение
 * ловит `engine/tests/unit/test_stream_contract.py`.
 */
export const SUBSCRIBED_EVENT_TYPES: StreamEvent['type'][] = [
  'INSTANCE_CREATED',
  'INSTANCE_PAUSED',
  'INSTANCE_RESUMED',
  'INSTANCE_COMPLETED',
  'INSTANCE_COMPENSATED',
  'INSTANCE_FAILED',
  'INSTANCE_CANCEL_REQUESTED',
  'INSTANCE_CANCELLED',
  'LOOP_EXHAUSTED',
  'STEP_STARTED',
  'STEP_COMPLETED',
  'STEP_FAILED',
  'TIMER_SCHEDULED',
  'TIMER_FIRED',
  'COMPENSATION_STARTED',
  'COMPENSATION_COMPLETED',
  'COMPENSATION_FAILED',
]

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

    for (const type of SUBSCRIBED_EVENT_TYPES) {
      source.addEventListener(type, forward as EventListener)
    }

    source.onopen = () => statusRef.current?.(true)
    source.onerror = () => statusRef.current?.(false)

    return () => {
      source.close()
    }
  }, [])
}