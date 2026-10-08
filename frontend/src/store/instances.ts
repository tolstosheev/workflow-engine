/** Состояние интерфейса: живые обновления и кэш последних данных. */

import { create } from 'zustand'

import { instances as instancesApi } from '@/api/client'
import type { InstanceStatus, StreamEvent, WorkflowInstance } from '@/types/api'

const REFRESH_INTERVAL_MS = 2000
const MAX_TRACKED_INSTANCES = 200

interface InstancesState {
  items: WorkflowInstance[]
  loading: boolean
  error: string | null
  connected: boolean
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

  // Событие меняет только то, чему оно касается: полная перезагрузка списка на
  // каждом шаге превратила бы живой экран в мигание.
  handleEvent: (event) => {
    const current = get().items
    const index = current.findIndex((item) => item.id === event.instance_id)
    if (index === -1) {
      // Новый инстанс или шаг уже удалённого: список доберём на ближайшем цикле.
      return
    }

    const updated = [...current]
    const next: WorkflowInstance = { ...updated[index] }

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
      case 'INSTANCE_CANCELLED':
        next.status = 'CANCELLED'
        break
      default:
        break
    }

    updated[index] = next
    if (updated.length > MAX_TRACKED_INSTANCES) {
      updated.splice(MAX_TRACKED_INSTANCES)
    }
    set({ items: updated })
  },

  setConnected: (connected) => set({ connected }),
}))

export { REFRESH_INTERVAL_MS }