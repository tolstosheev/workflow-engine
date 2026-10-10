/** Состояние интерфейса: живые обновления и кэш последних данных. */

import { create } from 'zustand'

import { instances as instancesApi } from '@/api/client'
import type { InstanceStatus, StreamEvent, WorkflowInstance } from '@/types/api'

const REFRESH_INTERVAL_MS = 2000

interface InstancesState {
  items: WorkflowInstance[]
  loading: boolean
  error: string | null
  connected: boolean
  lastEventAt: string | null
  statusFilter: InstanceStatus | 'ALL'
  load: () => Promise<void>
  setStatusFilter: (status: InstanceStatus | 'ALL') => void
  handleEvent: (event: StreamEvent) => void
  setConnected: (connected: boolean) => void
}

export const useInstancesStore = create<InstancesState>((set, get) => ({
  items: [],
  loading: false,
  error: null,
  connected: false,
  lastEventAt: null,
  statusFilter: 'ALL',

  load: async () => {
    set({ loading: true, error: null })
    try {
      const page = await instancesApi.list({ limit: 100 })
      set({ items: page.items, loading: false })
    } catch (error) {
      set({ loading: false, error: error instanceof Error ? error.message : 'Не удалось загрузить инстансы' })
    }
  },

  setStatusFilter: (statusFilter) => {
    set({ statusFilter })
    void get().load()
  },

  // Событие меняет только то, чему что его касается: полная перезагрузка списка
  // на каждом шаге превращала бы в мигание.
  // `set` принимает функцию: читает и пишет в одном атоматном влете без промежуточки.
  // Раздельно `get()` и `set()`: между ними вклиниться load(), и список, собранный до
  // ответа, затерет статус вовращаться на два секунды назад.
  handleEvent: (event) =>
    set((state) => {
      const index = state.items.findIndex((item) => item.id === event.instance_id)
      if (index === -1) {
        // Новый инстанс или шаг уже удалённого: список доберём на ближайшем цикле.
        return { lastEventAt: event.created_at }
      }

      const next: WorkflowInstance = { ...state.items[index] }

      switch (event.type) {
        case 'STEP_STARTED':
          next.current_step = String(event.payload.step ?? next.current_step)
          next.status = 'RUNNING'
          break
        case 'STEP_COMPLETED':
          next.current_step = (event.payload.next as string | null) ?? next.current_step
          next.status = 'RUNNING'
          break
        case 'STEP_FAILED':
          next.status = event.payload.terminal ? 'COMPENSATING' : 'RUNNING'
          break
        case 'COMPENSATION_STARTED':
          next.status = 'COMPENSATING'
          break
        case 'COMPENSATION_COMPLETED':
          // Откат шага проверён, но процесс ещё идёт в откате.
          next.status = 'COMPENSATING'
          break
        case 'COMPENSATION_FAILED':
          next.status = 'COMPENSATION_FAILED'
          break
        case 'INSTANCE_COMPLETED':
          next.status = 'COMPLETED'
          next.current_step = null
          break
        case 'INSTANCE_COMPENSATED':
          next.status = 'COMPENSATED'
          next.current_step = null
          break
        case 'INSTANCE_CANCEL_REQUESTED':
          // Отмена ещё не выполнена: компенсации впереди, статус сменится на
          // COMPENSATED либо CANCELLED.
          next.status = 'CANCELLING'
          break
        case 'INSTANCE_CANCELLED':
          next.status = 'CANCELLED'
          break
        case 'INSTANCE_FAILED':
          // Отказ без компенсации: упавший шаг, откатывать нечему.
          next.status = 'FAILED'
          next.current_step = null
          break
        case 'LOOP_EXHAUSTED':
          // Цикл исчерпан: процесс уходит в откат.
          next.status = 'COMPENSATING'
          break
        case 'TIMER_SCHEDULED':
        case 'TIMER_FIRED':
          // Инстанс отельнён в ожидании и пробудился после сробота: без этих событий
          // статус они до 2 секунд показывал бы уставшена.
          next.status = 'WAITING'
          next.current_step = String(event.payload.step ?? next.current_step)
          break
        default:
          return { lastEventAt: event.created_at }
      }

      const items = [...state.items]
      items[index] = next
      return { items, lastEventAt: event.created_at }
    }),

  setConnected: (connected) => set({ connected }),
}))

export { REFRESH_INTERVAL_MS }