// @vitest-environment jsdom
/**
 * Реестр воркеров (ADR-0018).
 *
 * Панель проверяется на границах, а не на красоте: главное свойство реестра в том,
 * что молчащий воркер виден отдельно от отсутствующего, и что граница молчания
 * названа цифрой. Без второго подпись «не отвечает» была бы загадкой, а без
 * первого вопрос о живости системы остался бы без ответа.
 */

import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { WorkerRegistry } from '@/components/WorkerRegistry'
import type { WorkerRecord } from '@/types/api'

vi.mock('@/api/client', () => ({
  workers: { list: vi.fn() },
}))

async function load() {
  const { workers } = await import('@/api/client')
  return vi.mocked(workers.list)
}

function record(over: Partial<WorkerRecord> = {}): WorkerRecord {
  return {
    worker_id: 'worker-a1b2',
    state: 'active',
    started_at: '2026-03-01T12:00:00Z',
    heartbeat_at: new Date().toISOString(),
    stopped_at: null,
    cycles_completed: 12,
    steps_executed: 40,
    lease_lost: 0,
    ...over,
  }
}

describe('реестр воркеров', () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
  })

  afterEach(() => {
    cleanup()
    vi.useRealTimers()
  })

  it('показывает отвечающего воркера и его счётчики', async () => {
    const list = await load()
    list.mockResolvedValue({ items: [record()], fresh_for_seconds: 3 })

    render(<WorkerRegistry />)

    expect(await screen.findByText('worker-a1b2')).toBeTruthy()
    expect(screen.getByText('Отвечает')).toBeTruthy()
    expect(screen.getByText('циклов 12')).toBeTruthy()
    expect(screen.getByText('шагов 40')).toBeTruthy()
  })

  it('отличает молчащего от отсутствующего', async () => {
    // Упавший воркер обязан остаться в списке с отдельной пометкой: если бы он
    // исчезал, «воркер молчит» и «воркера нет» стали бы одним и тем же.
    const list = await load()
    list.mockResolvedValue({
      items: [
        record({ worker_id: 'worker-live' }),
        record({ worker_id: 'worker-dead', state: 'stale', heartbeat_at: '2026-03-01T12:00:00Z' }),
      ],
      fresh_for_seconds: 3,
    })

    render(<WorkerRegistry />)

    expect(await screen.findByText('worker-dead')).toBeTruthy()
    expect(screen.getByText('Не отвечает')).toBeTruthy()
    expect(screen.getByText(/1 не отвечает/)).toBeTruthy()
  })

  it('различает остановленного и не отвечающего', async () => {
    const list = await load()
    list.mockResolvedValue({
      items: [record({ state: 'stopped', stopped_at: '2026-03-01T12:30:00Z' })],
      fresh_for_seconds: 3,
    })

    render(<WorkerRegistry />)

    expect(await screen.findByText('Остановлен')).toBeTruthy()
    expect(screen.queryByText('Не отвечает')).toBeNull()
  })

  it('называет границу молчания цифрой', async () => {
    const list = await load()
    list.mockResolvedValue({ items: [], fresh_for_seconds: 3 })

    render(<WorkerRegistry />)

    expect(await screen.findByText(/дольше 3 с/)).toBeTruthy()
  })

  it('показывает потери аренды, когда они были', async () => {
    const list = await load()
    list.mockResolvedValue({ items: [record({ lease_lost: 3 })], fresh_for_seconds: 3 })

    render(<WorkerRegistry />)

    // Молчаливое число в счётчике ничего не значит: потерянная аренда — это удвоенная
    // работа другого воркера, и её надо назвать.
    expect(await screen.findByText('потерянных аренд 3')).toBeTruthy()
  })

  it('говорит, когда воркеров нет ни одного', async () => {
    const list = await load()
    list.mockResolvedValue({ items: [], fresh_for_seconds: 3 })

    render(<WorkerRegistry />)

    expect(await screen.findByText('Воркеров нет ни одного')).toBeTruthy()
  })

  it('объявляет, что реестр не прочитан', async () => {
    const list = await load()
    list.mockRejectedValue(new Error('реестр недоступен'))

    render(<WorkerRegistry />)

    expect(await screen.findByText('реестр недоступен')).toBeTruthy()
  })

  it('читает реестр повторно, потому что сердце воркера движется', async () => {
    const list = await load()
    list.mockResolvedValue({ items: [record()], fresh_for_seconds: 3 })

    render(<WorkerRegistry />)
    await screen.findByText('worker-a1b2')

    await vi.advanceTimersByTimeAsync(5000)

    await waitFor(() => expect(list.mock.calls.length).toBeGreaterThan(1))
  })
})