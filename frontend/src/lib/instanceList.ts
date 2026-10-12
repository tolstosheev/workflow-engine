/**
 * Выборка строк списка инстансов.
 *
 * Чистая функция без React и без DOM: правило «что показывать» легко проверить
 * тестом и легко сломать незаметно — поиск и сортировка уже молча перестали
 * влиять на таблицу, потому что считались рядом с ней.
 */

import type { WorkflowInstance } from '@/types/api'

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

/**
 * Отбор по имени процесса и порядок строк.
 *
 * Порядок задаётся явно, а не через сортировку на месте: `sort` мутирует
 * массив, и список, отданный наружу, менялся бы вместе с порядком отрисовки.
 */
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