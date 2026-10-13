/** Цвет и подпись состояния инстанса — единое место, чтобы не расходились. */

import type { EventType, InstanceStatus, StepRunStatus } from '@/types/api'

type Tone = 'neutral' | 'active' | 'success' | 'warning' | 'danger'

const TONE_CLASSES: Record<Tone, string> = {
  neutral: 'bg-surface-raised text-white/70 border-surface-border',
  active: 'bg-accent/15 text-accent border-accent/40',
  success: 'bg-success/15 text-success border-success/40',
  warning: 'bg-warning/15 text-warning border-warning/40',
  danger: 'bg-danger/15 text-danger border-danger/40',
}

const STATUS_TONES: Record<InstanceStatus, Tone> = {
  PENDING: 'neutral',
  RUNNING: 'active',
  WAITING: 'warning',
  // Пауза ждёт человека: тон предупреждения тот же, что у таймера, но
  // подпись отличается — ждать время и ждать решения это разное.
  PAUSED: 'warning',
  CANCELLING: 'warning',
  COMPENSATING: 'warning',
  COMPENSATION_FAILED: 'danger',
  COMPLETED: 'success',
  COMPENSATED: 'success',
  CANCELLED: 'neutral',
  FAILED: 'danger',
}

/**
 * Подписи состояний вынесены наружу: ими пользуются и бейджи, и текстовые
 * пояснения, а расходиться им нельзя.
 */
export const STATUS_LABELS: Record<InstanceStatus, string> = {
  PENDING: 'В очереди',
  RUNNING: 'Выполняется',
  WAITING: 'Ожидание',
  PAUSED: 'Ждёт решения',
  CANCELLING: 'Отменяется',
  COMPENSATING: 'Откат',
  COMPENSATION_FAILED: 'Откат не удался',
  COMPLETED: 'Завершён',
  COMPENSATED: 'Откатан',
  CANCELLED: 'Отменён',
  FAILED: 'Ошибка',
}

export function statusTone(status: InstanceStatus): string {
  return TONE_CLASSES[STATUS_TONES[status]]
}

export function statusLabel(status: InstanceStatus): string {
  return STATUS_LABELS[status]
}

const EVENT_LABELS: Record<EventType, string> = {
  INSTANCE_CREATED: 'Инстанс создан',

  INSTANCE_PAUSED: 'Процесс ждёт решения',
  INSTANCE_RESUMED: 'Процесс продолжен',
  STEP_STARTED: 'Шаг начат',
  STEP_COMPLETED: 'Шаг выполнен',
  STEP_FAILED: 'Шаг провален',
  STEP_RETRY_SCHEDULED: 'Шаг поставлен на повтор',
  LOOP_EXHAUSTED: 'Исчерпаны проходы цикла',
  TIMER_SCHEDULED: 'Ожидание создано',
  TIMER_FIRED: 'Ожидание сработало',
  COMPENSATION_STARTED: 'Компенсация начата',
  COMPENSATION_COMPLETED: 'Компенсация выполнена',
  COMPENSATION_FAILED: 'Компенсация провалена',

  INSTANCE_COMPLETED: 'Процесс завершён',
  INSTANCE_COMPENSATED: 'Процесс откатан',
  INSTANCE_FAILED: 'Процесс провален без отката',
  INSTANCE_CANCEL_REQUESTED: 'Запрошена отмена',
  INSTANCE_CANCELLED: 'Процесс отменён',
}

export function eventLabel(type: EventType): string {
  return EVENT_LABELS[type] ?? type
}

const STEP_TONES: Record<StepRunStatus, Tone> = {
  PENDING: 'neutral',
  RUNNING: 'active',
  COMPLETED: 'success',
  FAILED: 'danger',
  COMPENSATED: 'success',
}

/**
 * Подпись прогона шага.
 *
 * Тон у прогона был, а подписи не было: страница отдавала сырое `COMPLETED` из
 * базы рядом с русскими подписями состояний процесса. Человек, читающий список
 * прогов, видел английские слова посреди русского текста.
 */
export const STEP_LABELS: Record<StepRunStatus, string> = {
  PENDING: 'не начат',
  RUNNING: 'выполняется',
  COMPLETED: 'выполнен',
  FAILED: 'провален',
  COMPENSATED: 'откатан',
}

/**
 * Подпись прогона шага, а при неизвестном значении — само значение.
 *
 * Лента приходит из бэкенда свободным текстом, и состояние, которого движок
 * ещё не знает, должно показываться как есть, а не пропадать: как и в
 * `eventLabel`.
 */
export function stepLabel(status: StepRunStatus | string): string {
  return STEP_LABELS[status as StepRunStatus] ?? status
}

export function stepTone(status: StepRunStatus): string {
  return TONE_CLASSES[STEP_TONES[status]]
}

export function formatDateTime(value: string | null | undefined): string {
  return format(value, DATE_TIME)
}

/**
 * Подпись момента времени.
 *
 * Неразбираная дата показывается прочерком: строка «Invalid Date» в журнале
 * выглядит как сбой интерфейса, а не как отсутствие данных, и отвлекает от
 * того, что смотрели.
 */
function format(value: string | null | undefined, formatter: Intl.DateTimeFormat): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  return formatter.format(date)
}

/**
 * Форматтеры создаются один раз: `toLocaleString` собирает локаль заново при
 * каждом вызове, а подпись времени показывается в каждой строке таблицы и в
 * каждом событии журнала.
 */
const DATE_TIME = new Intl.DateTimeFormat('ru-RU', {
  day: '2-digit',
  month: '2-digit',
  year: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hour12: false,
})

export function formatDuration(from: string, to: string | null): string {
  if (!to) return 'выполняется'
  const millis = new Date(to).getTime() - new Date(from).getTime()
  if (millis < 1000) return `${millis} мс`
  if (millis < 60_000) return `${(millis / 1000).toFixed(1)} с`
  return `${Math.round(millis / 60_000)} мин`
}
