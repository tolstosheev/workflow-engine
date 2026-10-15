/** Отбор и порядок строк списка — чистая логика, проверяемая без DOM. */

import { describe, expect, it } from 'vitest'

import { countRunning, selectInstances } from '@/lib/instanceList'
import type { WorkflowInstance } from '@/types/api'

const instance = (
  id: string,
  name: string,
  createdAt: string,
  status: WorkflowInstance['status'] = 'RUNNING',
): WorkflowInstance => ({
  id,
  definition: { id: 'd1', name, version: 1 },
  status,
  input: {},
  variables: {},
  current_step: null,
  error: null,
  lease_owner: null,
  lease_expires_at: null,
  heartbeat_at: null,
  archived_at: null,
  journal_pruned_at: null,
  created_at: createdAt,
  updated_at: createdAt,
})

const LIST: WorkflowInstance[] = [
  instance('a', 'order_fulfillment', '2026-10-10T10:00:00Z', 'RUNNING'),
  instance('b', 'invoice_issue', '2026-10-10T12:00:00Z', 'COMPLETED'),
  instance('c', 'order_refund', '2026-10-10T11:00:00Z', 'RUNNING'),
]

describe('отбор по имени процесса', () => {
  it('оставляет только строки, содержащие подстроку', () => {
    // Поиск — главный способ найти процесс среди сотни; он обязан сузить
    // таблицу, а не просто считать что-то в сноске.
    const rows = selectInstances(LIST, { search: 'order', newestFirst: true })

    expect(rows.map((row) => row.id)).toEqual(['c', 'a'])
  })

  it('не различает регистр и не требует точного имени', () => {
    expect(selectInstances(LIST, { search: 'INVOICE', newestFirst: true }).map((r) => r.id)).toEqual(
      ['b'],
    )
  })

  it('без запроса оставляет всё', () => {
    expect(selectInstances(LIST, { search: '   ', newestFirst: true })).toHaveLength(3)
  })

  it('без совпадений даёт пустой список, а не прежний', () => {
    expect(selectInstances(LIST, { search: 'нет такого', newestFirst: true })).toEqual([])
  })
})

describe('порядок строк', () => {
  it('по умолчанию сначала новые', () => {
    const rows = selectInstances(LIST, { search: '', newestFirst: true })
    expect(rows.map((row) => row.id)).toEqual(['b', 'c', 'a'])
  })

  it('по требованию сначала старые', () => {
    const rows = selectInstances(LIST, { search: '', newestFirst: false })
    expect(rows.map((row) => row.id)).toEqual(['a', 'c', 'b'])
  })

  it('не меняет исходный список', () => {
    // Список приходит из стора и принадлежит ему: сортировка на месте
    // переставила бы строки под всеми, кто читает этот же массив.
    const source = LIST.slice()
    selectInstances(source, { search: '', newestFirst: true })
    expect(source.map((row) => row.id)).toEqual(['a', 'b', 'c'])
  })
})

describe('счётчик выполняющихся', () => {
  it('считает только RUNNING среди отобранных', () => {
    expect(countRunning(selectInstances(LIST, { search: 'order', newestFirst: true }))).toBe(2)
    expect(countRunning(selectInstances(LIST, { search: 'invoice', newestFirst: true }))).toBe(0)
  })
})