/** Цвет и подпись состояния — в одном месте, чтобы бейджи не расходились. */

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
  // Пауза ждёт человека: тон тот же, что у таймера, но подпись отличается —
  // ждать время и ждать решения это разное.
  PAUSED: 'warning',
  CANCELLING: 'warning',
  COMPENSATING: 'warning',
  COMPENSATION_FAILED: 'danger',
  COMPLETED: 'success',
  COMPENSATED: 'success',
  CANCELLED: 'neutral',
  FAILED: 'danger',
}

/** Подписи состояний: ими пользуются и бейджи, и текстовые пояснения. */
export const STATUS_LABELS: Record<InstanceStatus, string> = {
  PENDING: 'В очереди',
  RUNNING: 'Выполняется',
  WAITING: 'Ожидание',
  PAUSED: 'Ждёт решения',
  CANCELLING: 'Отменяется',
  COMPENSATING: 'Компенсация идёт',
  COMPENSATION_FAILED: 'Компенсация не выполнена',
  COMPLETED: 'Завершён',
  COMPENSATED: 'Компенсирован',
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
  INSTANCE_CREATED: 'Процесс создан',

  INSTANCE_PAUSED: 'Процесс ждёт решения',
  INSTANCE_RESUMED: 'Процесс продолжен',
  STEP_STARTED: 'Шаг начат',
  STEP_COMPLETED: 'Шаг выполнен',
  STEP_FAILED: 'Шаг не выполнен',
  STEP_RETRY_SCHEDULED: 'Шаг поставлен на повтор',
  LOOP_EXHAUSTED: 'Исчерпаны проходы цикла',
  TIMER_SCHEDULED: 'Ожидание создано',
  TIMER_FIRED: 'Ожидание сработало',
  COMPENSATION_STARTED: 'Компенсация начата',
  COMPENSATION_COMPLETED: 'Компенсация выполнена',
  COMPENSATION_FAILED: 'Компенсация не выполнена',

  INSTANCE_COMPLETED: 'Процесс завершён',
  INSTANCE_COMPENSATED: 'Процесс компенсирован',
  INSTANCE_FAILED: 'Процесс завершился ошибкой',
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

/** Подпись выполнения шага: без неё в списке были слова из базы. */
export const STEP_LABELS: Record<StepRunStatus, string> = {
  PENDING: 'не начат',
  RUNNING: 'выполняется',
  COMPLETED: 'выполнен',
  FAILED: 'не выполнен',
  COMPENSATED: 'компенсирован',
}

/** Неизвестное значение показывается как есть, а не пропадает. */
export function stepLabel(status: StepRunStatus | string): string {
  return STEP_LABELS[status as StepRunStatus] ?? status
}

export function stepTone(status: StepRunStatus): string {
  return TONE_CLASSES[STEP_TONES[status]]
}

export function formatDateTime(value: string | null | undefined): string {
  return format(value, DATE_TIME)
}

/** Неразбираная дата — прочерком: «Invalid Date» читается как сбой. */
function format(value: string | null | undefined, formatter: Intl.DateTimeFormat): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  return formatter.format(date)
}

/** Форматтер создаётся один раз: лок заново не собирается при каждом вызове. */
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
