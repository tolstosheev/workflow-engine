/**
 * Человеческое прочтение события журнала.
 *
 * Подписи в `status.ts` отвечают на вопрос «что за событие», и на этом
 * человек, читающий журнал, останавливался: «Шаг выполнен» — какой шаг,
 * «Компенсация провалена» — чья и почему. Payload рядом показывал поле и
 * значение, но не читался как предложение, и разбираться приходилось глазами
 * по ключам на английском.
 *
 * Здесь то же самое собирается в одну фразу: шаг, попытка, причина и — где
 * применимо — куда двинулся процесс.
 */

import type { EventType, StreamEvent } from '@/types/api'

import { eventLabel, formatDateTime } from './status'

/** Вид таймера, которым планируется срок ожидания решения человека. */
const PAUSE_DEADLINE = 'pause_deadline'

type Payload = Record<string, unknown>

function text(payload: Payload, key: string): string | null {
  const value = payload[key]
  return typeof value === 'string' && value !== '' ? value : null
}

/**
 * Имя шага из payload.
 *
 * `null`, если имени нет. Тогда фраза не собирается, а показывается подпись
 * события: «Шаг ?» на экране читается как поломка интерфейса, и человек,
 * не знающий, где смотреть payload, потратит время впустую.
 */
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

/**
 * Фраза о шаге: `«Шаг <имя>: <хвост>»`.
 *
 * Без имени шага в payload подпись события лучше собранной фразы: см. `step`.
 */
function withStep(type: EventType, payload: Payload, tail: string): string {
  const name = step(payload)
  return name === null ? eventLabel(type) : `Шаг ${name}: ${tail}`
}

/**
 * Фраза о событии.
 *
 * `type` принимается и `null`, и `undefined`: запись ленты времени — это
 * объединение события и прогона шага, и у прогона поля события просто нет.
 * Требовать приведения на стороне вызова значило бы прятать эту разницу под
 * `as never`.
 *
 * Неизвестный тип показывается подписью из словаря, а неизвестное значение
 * `type` — самим значением: событие, которого движок ещё не знает, должно быть
 * видно, а не молча исчезнуть из журнала.
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
      return `Шаг ${name} провален${attempt(payload)}${reason(payload)}`
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
      // Срок ожидания человека планируется тем же событием, что и ожидание
      // шага: без разбора по виду страница называла одно состояние двумя
      // словами — «Ждёт решения» в шапке и «Ожидание» в карточке.
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
      return `Откат начат: для шага ${name}${action}${attempt(payload)}`
    }

    case 'COMPENSATION_COMPLETED': {
      const name = step(payload, 'for_step')
      if (name === null) return eventLabel(type)
      return `Компенсация шага ${name} выполнена${attempt(payload)}`
    }

    case 'COMPENSATION_FAILED': {
      const name = step(payload, 'for_step')
      if (name === null) return eventLabel(type)
      return `Компенсация шага ${name} не удалась${attempt(payload)}${reason(payload)}`
    }

    case 'INSTANCE_COMPLETED':
      return 'Процесс завершён: все шаги выполнены'

    case 'INSTANCE_COMPENSATED':
      return 'Процесс откатан: выполненные шаги скомпенсированы'

    case 'INSTANCE_FAILED':
      return 'Процесс провален без отката: отменять было нечего'

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
