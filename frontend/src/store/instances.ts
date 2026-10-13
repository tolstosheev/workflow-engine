/** Состояние интерфейса: живые обновления и кэш последних данных. */

import { create } from 'zustand'

import { instances as instancesApi } from '@/api/client'
import type { InstanceStatus, StreamEvent, WorkflowInstance } from '@/types/api'

interface InstancesState {
  items: WorkflowInstance[]
  loading: boolean
  error: string | null
  connected: boolean
  lastEventAt: string | null
  statusFilter: InstanceStatus | 'ALL'
  /**
   * Последний применённый номер события по каждому инстансу.
   *
   * Поток переподключается с последнего события и присылает его снова, а
   * поздний кадр может прийти вовсе после нового. Без этого счётчика повтор
   * возвращал завершённый процесс в «выполняется» и включал кнопки, которые
   * для него не должны быть доступны.
   */
  lastSeq: Record<string, number>
  load: (signal?: AbortSignal) => Promise<void>
  setStatusFilter: (status: InstanceStatus | 'ALL') => void
  handleEvent: (event: StreamEvent) => void
  setConnected: (connected: boolean) => void
}

export const useInstancesStore = create<InstancesState>((set) => ({
  items: [],
  loading: false,
  error: null,
  connected: false,
  lastEventAt: null,
  statusFilter: 'ALL',
  lastSeq: {},

  load: async (signal) => {
    // Ошибку не стираем до попытки: фоновая подстраховка мигает на экране,
    // а прежнее сообщение ещё верно и полезнее молчания.
    set({ loading: true })
    try {
      const page = await instancesApi.list({ limit: 100 }, signal)
      // Отменённый запрос не пишет в состояние: его отменили намеренно, и
      // вернувшийся позже список затёр бы более новый.
      if (signal?.aborted) {
        set({ loading: false })
        return
      }

      // Счётчик событий сбрасывается вместе со списком: новая выборка —
      // это новая точка отсчёта, и события предыдущей к ней не относятся.
      set({ items: page.items, loading: false, error: null, lastSeq: {} })
    } catch (error) {
      if (signal?.aborted) {
        // Отмена — не ошибка: ответит следующая загрузка, а сообщение об
        // ошибке остаётся: без него экран молчал бы о проблеме.
        set({ loading: false })
        return
      }
      set({
        loading: false,
        error: error instanceof Error ? error.message : 'Не удалось загрузить инстансы',
      })
    }
  },

  setStatusFilter: (statusFilter) => {
    // Фильтр применяется на клиенте, а не на сервере: список уже загружен,
    // и повторный запрос вернул бы те же строки.
    set({ statusFilter })
  },

  // Событие меняет только то, к чему относится: полная перезагрузка списка на
  // каждом шаге превращала бы страницу в мигание.
  // `set` принимает функцию: чтение и запись происходят в одном атомарном
  // вылете. Раздельно `get()` и `set()`: между ними вклинилась бы загрузка,
  // и список, собранный до ответа, вернул бы статус на два шага назад.
  handleEvent: (event) =>
    set((state) => {
      const seen = state.lastSeq[event.instance_id] ?? -1
      if (event.seq <= seen) {
        // Повтор или запоздавший кадр: состояние уже новее этого события.
        return { lastEventAt: event.created_at }
      }
      const index = state.items.findIndex((item) => item.id === event.instance_id)
      if (index === -1) {
        // Новый инстанс или шаг удалённого: список доберёт ближайший опрос.
        return { lastSeq: { ...state.lastSeq, [event.instance_id]: event.seq }, lastEventAt: event.created_at }
      }

      const next: WorkflowInstance = { ...state.items[index] }

      switch (event.type) {
        case 'INSTANCE_PAUSED':
          // Процесс встал и ждёт человека: строка должна показать это сразу,
          // иначе кнопка «продолжить» появится лишь после следующего опроса.
          next.status = 'PAUSED'
          break
        case 'INSTANCE_RESUMED':
          next.status = 'RUNNING'
          break
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
          // Откат шага прошёл, но процесс ещё идёт в откате.
          next.status = 'COMPENSATING'
          break
        case 'COMPENSATION_FAILED':
          // Откат не удался: воркер повторит его сам, когда служба оживёт.
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
          // Отказ без компенсации: упал первый шаг, откатывать нечего.
          next.status = 'FAILED'
          next.current_step = null
          break
        case 'LOOP_EXHAUSTED':
          // Цикл исчерпан: процесс уходит в откат.
          next.status = 'COMPENSATING'
          break
        case 'TIMER_SCHEDULED':
        case 'TIMER_FIRED':
          // Инстанс встал на ожидание и проснулся после таймера: без этих
          // событий статус показывал бы устаревшее состояние до двух секунд.
          next.status = 'WAITING'
          next.current_step = String(event.payload.step ?? next.current_step)
          break
        default:
          return { lastSeq: { ...state.lastSeq, [event.instance_id]: event.seq }, lastEventAt: event.created_at }
      }

      const items = [...state.items]
      items[index] = next
      return {
        items,
        lastSeq: { ...state.lastSeq, [event.instance_id]: event.seq },
        lastEventAt: event.created_at,
      }
    }),

  setConnected: (connected) => set({ connected }),
}))
