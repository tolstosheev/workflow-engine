/** Тесты группировки аренд по владельцам. */

import { describe, expect, it } from 'vitest'

import { collectLeases } from '@/lib/leases'
import type { WorkflowInstance } from '@/types/api'

function instance(partial: Partial<WorkflowInstance> & { id: string }): WorkflowInstance {
  const base: WorkflowInstance = {
    id: partial.id,
    definition: { id: 'd1', name: 'order', version: 1 },
    status: 'RUNNING',
    current_step: 'reserve',
    input: {},
    variables: {},
    error: null,
    lease_owner: null,
    lease_expires_at: null,
    heartbeat_at: null,
    archived_at: null,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  }
  return { ...base, ...partial }
}

describe('collectLeases', () => {
  it('группирует инстансы по владельцу аренды', () => {
    const leases = collectLeases([
      instance({ id: '1', lease_owner: 'w1' }),
      instance({ id: '2', lease_owner: 'w2' }),
      instance({ id: '3', lease_owner: 'w1' }),
    ])

    expect(leases).toHaveLength(2)
    expect(leases.find((lease) => lease.owner === 'w1')?.instances).toHaveLength(2)
  })

  it('не учитывает инстансы без аренды', () => {
    const leases = collectLeases([instance({ id: '1', lease_owner: null })])

    expect(leases).toEqual([])
  })

  it('берёт последний heartbeat среди инстансов воркера', () => {
    const leases = collectLeases([
      instance({ id: '1', lease_owner: 'w1', heartbeat_at: '2026-01-01T00:00:05Z' }),
      instance({ id: '2', lease_owner: 'w1', heartbeat_at: '2026-01-01T00:00:09Z' }),
    ])

    expect(leases[0].lastHeartbeat).toBe('2026-01-01T00:00:09Z')
  })

  it('упорядочивает воркеров по имени, чтобы список не прыгал', () => {
    const leases = collectLeases([
      instance({ id: '1', lease_owner: 'w10' }),
      instance({ id: '2', lease_owner: 'w2' }),
    ])

    expect(leases.map((lease) => lease.owner)).toEqual(['w10', 'w2'])
  })
})