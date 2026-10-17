/** Выборка строк списка: чистая функция, чтобы правило проверялось тестом. */


import type { InstanceStatus, WorkflowInstance } from '@/types/api'

/**
 * Вкладки списка — группы состояний, а не отдельные состояния: «Завершённые»
 * означала ровно `COMPLETED`, и компенсированные, отменённые и упавшие в неё
 * не попадали. Покрытие состояний сверяется с движком тестом
 * `engine/tests/unit/test_instance_filters_contract.py`.
 */
export type InstanceFilterId = 'all' | 'running' | 'rollback' | 'rollback-failed' | 'finished'

export interface InstanceFilter {
  id: InstanceFilterId
  label: string
  /** Пустой список означает «все». */
  statuses: InstanceStatus[]
}

export const INSTANCE_FILTERS: InstanceFilter[] = [
  { id: 'all', label: 'Все', statuses: [] },
  {
    id: 'running',
    label: 'Выполняются',
    statuses: ['PENDING', 'RUNNING', 'WAITING', 'PAUSED', 'CANCELLING'],
  },
  { id: 'rollback', label: 'Компенсация', statuses: ['COMPENSATING'] },
  {
    id: 'rollback-failed',
    label: 'Компенсация не выполнена',
    statuses: ['COMPENSATION_FAILED'],
  },
  {
    id: 'finished',
    label: 'Завершённые',
    statuses: ['COMPLETED', 'COMPENSATED', 'CANCELLED', 'FAILED'],
  },
]

const KNOWN_FILTERS = new Set<string>(INSTANCE_FILTERS.map((filter) => filter.id))

/** Адрес вправе назвать только существующую вкладку; всё прочее — опечатка. */
export function readFilterId(value: string | null): InstanceFilterId {
  return value !== null && KNOWN_FILTERS.has(value) ? (value as InstanceFilterId) : 'all'
}

export function filterInstances(
  instances: WorkflowInstance[],
  id: InstanceFilterId,
): WorkflowInstance[] {
  const statuses = INSTANCE_FILTERS.find((filter) => filter.id === id)?.statuses ?? []
  if (statuses.length === 0) return instances
  const wanted = new Set<InstanceStatus>(statuses)
  return instances.filter((instance) => wanted.has(instance.status))
}

/** Сколько процессов в каждой вкладке: пустая вкладка выглядит поломкой. */
export function countByFilter(instances: WorkflowInstance[]): Record<InstanceFilterId, number> {
  const counts = Object.fromEntries(
    INSTANCE_FILTERS.map((filter) => [filter.id, 0]),
  ) as Record<InstanceFilterId, number>
  for (const filter of INSTANCE_FILTERS) {
    counts[filter.id] = filterInstances(instances, filter.id).length
  }
  return counts
}

export interface InstanceListQuery {
  /** Подстрока в имени процесса. Пустая строка — без фильтра. */
  search: string
  /** Сначала новые — по убыванию времени создания. */
  newestFirst: boolean
}

/** Число выполняющихся процессов среди отобранных — для строки под поиском. */
export function countRunning(instances: WorkflowInstance[]): number {
  let total = 0
  for (const instance of instances) {
    if (instance.status === 'RUNNING') total += 1
  }
  return total
}

/** Порядок задаётся явно: `sort` мутирует массив, а список отдан наружу. */
export function selectInstances(
  instances: WorkflowInstance[],
  { search, newestFirst }: InstanceListQuery,
): WorkflowInstance[] {
  const needle = search.trim().toLowerCase()
  const matched = needle
    ? instances.filter((instance) => instance.definition.name.toLowerCase().includes(needle))
    : instances.slice()

  return [...matched].sort((a, b) =>
    newestFirst ? b.created_at.localeCompare(a.created_at) : a.created_at.localeCompare(b.created_at),
  )
}