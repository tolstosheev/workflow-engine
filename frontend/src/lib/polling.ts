/** Интервал опроса: чистая функция, чтобы правило проверялось тестом. */


import type { InstanceStatus } from '@/types/api'

/** Статусы, после которых процесс больше не меняется сам. */
const TERMINAL: ReadonlySet<InstanceStatus> = new Set<InstanceStatus>([
  'COMPLETED',
  'COMPENSATED',
  'CANCELLED',
  'FAILED',
])

/** Поток жив: события уже приносят изменения, добавочный опрос — только страховка. */
const INTERVAL_WITH_STREAM_MS = 15_000

/** Поток оборван: SSE не доставляет ничего, опрос остаётся единственным источником. */
const INTERVAL_WITHOUT_STREAM_MS = 2_000

export function isTerminal(status: InstanceStatus): boolean {
  return TERMINAL.has(status)
}

/**
 * Интервал опроса в миллисекундах. `0` — опрос не нужен.
 *
 * Терминальный процесс не меняется: опрос для него был бы вечной нагрузкой на
 * API без единой новой строки на экране.
 */
export function pollingIntervalMs(status: InstanceStatus | null, connected: boolean): number {
  if (status !== null && isTerminal(status)) return 0
  return connected ? INTERVAL_WITH_STREAM_MS : INTERVAL_WITHOUT_STREAM_MS
}