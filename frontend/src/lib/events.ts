/** Фраза о событии журнала: шаг, попытка, причина и следующий переход. */


import type { EventType, StreamEvent } from '@/types/api'

import { eventLabel, formatDateTime } from './status'

/** Вид таймера, которым планируется срок ожидания решения человека. */
const PAUSE_DEADLINE = 'pause_deadline'

type Payload = Record<string, unknown>

function text(payload: Payload, key: string): string | null {
  const value = payload[key]
  return typeof value === 'string' && value !== '' ? value : null
}

/** Без имени шага фраза не собирается: «Шаг ?» на экране читается как поломка. */
function step(payload: Payload, key = 'step'): string | null {
  return text(payload, key)
}

function count(payload: Payload, key: string): number | null {
  const value = payload[key]
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

/** «на попытке 2» — только когда попытка в payload действительно есть. */
function attempt(payload: Payload): string {
  const value = count(payload, 'attempt')
  return value === null ? '' : ` на попытке ${value}`
}

function reason(payload: Payload): string {
  const value = text(payload, 'error') ?? text(payload, 'reason')
  return value === null ? '' : `: ${value}`
}

/** `«Шаг <имя>: <хвост>»`; без имени — подпись события, см. `step`. */
function withStep(type: EventType, payload: Payload, tail: string): string {
  const name = step(payload)
  return name === null ? eventLabel(type) : `Шаг ${name}: ${tail}`
}

/**
 * Фраза о событии.
 *
 * `type` бывает и `null`, и `undefined`: запись ленты объединяет событие и
 * выполнение шага, а у выполнения поля события нет.
 *
 * Неизвестный тип показывается подписью из словаря, неизвестное значение —
 * самим значением: событие, которого движок ещё не знает, должно быть видно.
 */
export function describeEvent(event: { type?: string | null; payload?: unknown }): string {
  const type = (event.type ?? 'INSTANCE_CREATED') as EventType
  const payload: Payload =
    event.payload && typeof event.payload === 'object' ? (event.payload as Payload) : {}

  switch (type) {
    case 'INSTANCE_CREATED':
      return 'Процесс создан'

    case 'INSTANCE_PAUSED':
      return withStep(type, payload, 'процесс ждёт решения человека')

    case 'INSTANCE_RESUMED':
      return withStep(type, payload, 'ожидание снято, процесс продолжен')

    case 'STEP_STARTED': {
      const name = step(payload)
      if (name === null) return eventLabel(type)
      const number = count(payload, 'attempt')
      return `Шаг ${name}${number === null ? '' : `, попытка ${number}`}`
    }

    case 'STEP_COMPLETED': {
      const name = step(payload)
      if (name === null) return eventLabel(type)
      const next = text(payload, 'next')
      return `Шаг ${name} выполнен${next === null ? '' : `, дальше ${next}`}`
    }

    case 'STEP_FAILED': {
      const name = step(payload)
      if (name === null) return eventLabel(type)
      return `Шаг ${name} не выполнен${attempt(payload)}${reason(payload)}`
    }

    case 'STEP_RETRY_SCHEDULED': {
      const name = step(payload)
      if (name === null) return eventLabel(type)
      const number = count(payload, 'attempt')
      const next = number === null ? '' : `, следующая попытка ${number}`
      return `Шаг ${name} поставлен на повтор${next}${reason(payload)}`
    }

    case 'LOOP_EXHAUSTED': {
      const name = step(payload)
      if (name === null) return eventLabel(type)
      const iterations = count(payload, 'iterations')
      const limit = count(payload, 'max_iterations')
      const detail =
        iterations === null || limit === null ? '' : ` — ${iterations} из ${limit} проходов`
      return `Шаг ${name}: исчерпаны проходы цикла${detail}`
    }

    case 'TIMER_SCHEDULED': {
      const name = step(payload)
      if (name === null) return eventLabel(type)
      const due = text(payload, 'due_at')
      // Срок ожидания решения планируется тем же событием, что и ожидание шага:
      // без разбора по виду страница называла одно состояние двумя словами.
      if (payload.kind === PAUSE_DEADLINE) {
        return `Шаг ${name}: срок ожидания решения${due === null ? '' : ` — до ${formatDateTime(due)}`}`
      }
      return `Шаг ${name}: ожидание${due === null ? '' : ` до ${formatDateTime(due)}`}`
    }

    case 'TIMER_FIRED':
      return withStep(type, payload, 'ожидание сработало')

    case 'COMPENSATION_STARTED': {
      const name = step(payload, 'for_step')
      if (name === null) return eventLabel(type)
      const compensation = text(payload, 'compensation')
      const action = compensation === null ? '' : ` вызывается ${compensation}`
      return `Компенсация начата: для шага ${name}${action}${attempt(payload)}`
    }

    case 'COMPENSATION_COMPLETED': {
      const name = step(payload, 'for_step')
      if (name === null) return eventLabel(type)
      return `Компенсация шага ${name} выполнена${attempt(payload)}`
    }

    case 'COMPENSATION_FAILED': {
      const name = step(payload, 'for_step')
      if (name === null) return eventLabel(type)
      return `Компенсация шага ${name} не выполнена${attempt(payload)}${reason(payload)}`
    }

    case 'INSTANCE_COMPLETED':
      return 'Процесс завершён: все шаги выполнены'

    case 'INSTANCE_COMPENSATED':
      return 'Процесс компенсирован: выполненные шаги возвращены в исходное состояние'

    case 'INSTANCE_FAILED':
      return 'Процесс завершился ошибкой: компенсировать было нечего'

    case 'INSTANCE_CANCEL_REQUESTED':
      return `Запрошена отмена процесса${reason(payload)}`

    case 'INSTANCE_CANCELLED':
      return 'Процесс отменён'

    default:
      return eventLabel(type)
  }
}

/** Фраза о событии из потока — то же самое, но с готовым типом. */
export function describeStreamEvent(event: StreamEvent): string {
  return describeEvent({ type: event.type, payload: event.payload })
}
