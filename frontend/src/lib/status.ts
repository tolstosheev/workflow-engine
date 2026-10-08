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
  CANCELLING: 'warning',
  COMPENSATING: 'warning',
  COMPENSATION_FAILED: 'danger',
  COMPLETED: 'success',
  COMPENSATED: 'success',
  CANCELLED: 'neutral',
  FAILED: 'danger',
}

const STATUS_LABELS: Record<InstanceStatus, string> = {
  PENDING: 'В очереди',
  RUNNING: 'Выполняется',
  WAITING: 'Ожидание',
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
  INSTANCE_STARTED: 'Начато исполнение',
  STEP_STARTED: 'Шаг начат',
  STEP_COMPLETED: 'Шаг выполнен',
  STEP_FAILED: 'Шаг провален',
  STEP_RETRY_SCHEDULED: 'Шаг поставлен на повтор',
  TIMER_SCHEDULED: 'Ожидание создано',
  TIMER_FIRED: 'Ожидание сработало',
  COMPENSATION_STARTED: 'Компенсация начата',
  COMPENSATION_COMPLETED: 'Компенсация выполнена',
  COMPENSATION_FAILED: 'Компенсация провалена',
  COMPENSATION_SKIPPED: 'Компенсация не объявлена',
  INSTANCE_COMPLETED: 'Процесс завершён',
  INSTANCE_COMPENSATED: 'Процесс откатан',
  INSTANCE_CANCELLED: 'Процесс отменён',
  LEASE_EXPIRED: 'Аренда истекла',
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

export function stepTone(status: StepRunStatus): string {
  return TONE_CLASSES[STEP_TONES[status]]
}

export function formatTime(value: string | null | undefined): string {
  if (!value) return '—'
  return new Date(value).toLocaleTimeString('ru-RU', { hour12: false })
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return '—'
  return new Date(value).toLocaleString('ru-RU', { hour12: false })
}

export function formatDuration(from: string, to: string | null): string {
  if (!to) return 'выполняется'
  const millis = new Date(to).getTime() - new Date(from).getTime()
  if (millis < 1000) return `${millis} мс`
  if (millis < 60_000) return `${(millis / 1000).toFixed(1)} с`
  return `${Math.round(millis / 60_000)} мин`
}