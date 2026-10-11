import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { StreamEvent, WorkflowInstance } from '@/types/api'

vi.mock('@/api/client', () => ({
  instances: {
    list: vi.fn(async () => ({ items: [], total: 0, limit: 100, offset: 0 })),
  },
}))

const { useInstancesStore } = await import('@/store/instances')

const STUB = (id: string, status: WorkflowInstance['status'] = 'RUNNING'): WorkflowInstance => ({
  id,
  definition: { id: 'd1', name: 'demo', version: 1 },
  status,
  input: {},
  variables: {},
  current_step: 'a',
  error: null,
  lease_owner: null,
  lease_expires_at: null,
  heartbeat_at: null,
  created_at: '2026-10-10T00:00:00Z',
  updated_at: '2026-10-10T00:00:00Z',
})

const event = (type: StreamEvent['type'], payload: Record<string, unknown>): StreamEvent =>
  ({
    instance_id: 'i1',
    type,
    payload,
    created_at: '2026-10-10T00:01:00Z',
  }) as StreamEvent

describe('handleEvent', () => {
  beforeEach(() => {
    useInstancesStore.setState({ items: [STUB('i1'), STUB('i2', 'PENDING')], lastEventAt: null })
  })

  it('updates only the instance the event belongs to', () => {
    useInstancesStore.getState().handleEvent(event('COMPENSATION_STARTED', {}))

    const items = useInstancesStore.getState().items
    expect(items[0].status).toBe('COMPENSATING')
    expect(items[1].status).toBe('PENDING')
  })

  it('keeps the other instance untouched when the event is unknown', () => {
    const before = useInstancesStore.getState().items[1]

    useInstancesStore.getState().handleEvent(event('LEASE_EXPIRED', {}))

    expect(useInstancesStore.getState().items[1]).toBe(before)
  })

  it('advances status on compensation completion', () => {
    // Раньше обрабатывал COMPENSATION_COMPLETED был нельзя, и строка зависала на недействующем статусе
    useInstancesStore.getState().handleEvent(event('COMPENSATION_COMPLETED', {}))

    expect(useInstancesStore.getState().items[0].status).toBe('COMPENSATING')
  })

  it('marks the instance as waiting on timer events', () => {
    useInstancesStore.getState().handleEvent(event('TIMER_SCHEDULED', { step: 'b' }))

    const first = useInstancesStore.getState().items[0]
    expect(first.status).toBe('WAITING')
    expect(first.current_step).toBe('b')
  })

  it('marks failure without compensation as FAILED', () => {
    useInstancesStore.getState().handleEvent(event('INSTANCE_FAILED', {}))

    const first = useInstancesStore.getState().items[0]
    expect(first.status).toBe('FAILED')
    expect(first.current_step).toBeNull()
  })

  it('records the event time even for an unknown instance', () => {
    const unknown = { ...event('INSTANCE_COMPLETED', {}), instance_id: 'missing' }

    useInstancesStore.getState().handleEvent(unknown)

    expect(useInstancesStore.getState().lastEventAt).toBe('2026-10-10T00:01:00Z')
    expect(useInstancesStore.getState().items).toHaveLength(2)
  })

  it('отменённый запрос не пишет в состояние', async () => {
    // Отменённый запрос — разовязанная возвратная загрузка, а не ошибка: если бы он
    // записался бы, список подстаневшего уходящего вокна был бы старым.
    const { instances } = await import('@/api/client')
    vi.mocked(instances.list).mockResolvedValue({
      items: [STUB('старый')],
      total: 1,
      limit: 100,
      offset: 0,
    } as never)
    useInstancesStore.setState({ items: [STUB('прежний')], error: 'сбой', loading: false })

    const controller = new AbortController()
    controller.abort()
    await useInstancesStore.getState().load(controller.signal)

    expect(useInstancesStore.getState().items.map((item) => item.id)).toEqual(['прежний'])
    expect(useInstancesStore.getState().error).toBe('сбой')
  })
})
