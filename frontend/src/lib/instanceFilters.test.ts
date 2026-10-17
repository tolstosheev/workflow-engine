/**
 * Вкладки списка — это группы состояний.
 *
 * Проверяется то, в чём был дефект: вкладка «Завершённые» обязана показывать
 * компенсированные, отменённые и упавшие, а не только успешные. На стенде их
 * большинство, и точный статус в подписи вкладки давал пустой список при полном
 * списке процессов.
 *
 * Второе — число на вкладке: пустая вкладка без числа читается как поломка,
 * хотя означает всего лишь «сейчас таких нет».
 */

import { describe, expect, it } from 'vitest'

import {
  INSTANCE_FILTERS,
  countByFilter,
  filterInstances,
  readFilterId,
} from '@/lib/instanceList'
import type { InstanceStatus, WorkflowInstance } from '@/types/api'

function instance(status: InstanceStatus, name = 'order'): WorkflowInstance {
  return {
    id: `i-${status}`,
    definition: { id: 'd-1', name, version: 1 },
    status,
    current_step: null,
    input: {},
    variables: {},
    error: null,
    lease_owner: null,
    lease_expires_at: null,
    heartbeat_at: null,
    archived_at: null,
    journal_pruned_at: null,
    created_at: '2026-03-01T12:00:00Z',
    updated_at: '2026-03-01T12:00:01Z',
  }
}

describe('вкладки списка', () => {
  it('показывает под «Завершённые» все конечные состояния, а не одно', () => {
    const finished: InstanceStatus[] = ['COMPLETED', 'COMPENSATED', 'CANCELLED', 'FAILED']
    const items = finished.map((status) => instance(status))

    const visible = filterInstances(items, 'finished')

    // Прежде здесь оставался только COMPLETED, а компенсированные и отменённые были
    // не видны нигде, кроме «Все».
    expect(visible).toHaveLength(4)
  })

  it('показывает под «Выполняются» и те, что ждут', () => {
    const waiting: InstanceStatus[] = ['PENDING', 'RUNNING', 'WAITING', 'PAUSED', 'CANCELLING']

    expect(filterInstances(waiting.map((status) => instance(status)), 'running')).toHaveLength(5)
  })

  it('не смешивает откат с откатом, который не удался', () => {
    const items = [instance('COMPENSATING'), instance('COMPENSATION_FAILED')]

    expect(filterInstances(items, 'rollback')).toHaveLength(1)
    expect(filterInstances(items, 'rollback-failed')).toHaveLength(1)
    expect(filterInstances(items, 'rollback')[0].status).toBe('COMPENSATING')
  })

  it('под «Все» показывает всё', () => {
    const items = [instance('RUNNING'), instance('COMPENSATED'), instance('PAUSED')]

    expect(filterInstances(items, 'all')).toHaveLength(3)
  })

  it('считает процессы в каждой вкладке', () => {
    const items = [instance('RUNNING'), instance('PAUSED'), instance('COMPENSATED')]

    const counts = countByFilter(items)

    expect(counts.all).toBe(3)
    expect(counts.running).toBe(2)
    expect(counts.finished).toBe(1)
    // Пустые вкладки остаются в счётчике: иначе «0» неотличим от «не считали».
    expect(counts.rollback).toBe(0)
  })

  it('забывает незнакомую вкладку, а не падает', () => {
    expect(readFilterId('ВЫДУМАННАЯ')).toBe('all')
    expect(readFilterId(null)).toBe('all')
    expect(readFilterId('rollback-failed')).toBe('rollback-failed')
  })

  it('называет каждую вкладку по-человечески', () => {
    for (const filter of INSTANCE_FILTERS) {
      expect(filter.label.length).toBeGreaterThan(0)
    }
  })
})