// @vitest-environment jsdom
/**
 * Реестр исполнителей (ADR-0018).
 *
 * Панель проверяется на границах, а не на красоте: главное свойство реестра в том,
 * что молчащий исполнитель виден отдельно от отсутствующего, и что граница молчания
 * названа цифрой. Без второго подпись «не отвечает» была бы загадкой, а без
 * первого вопрос о живости системы остался бы без ответа.
 */

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
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

describe('реестр исполнителей', () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
  })

  afterEach(() => {
    cleanup()
    vi.useRealTimers()
  })

  it('показывает отвечающего исполнителя и его счётчики', async () => {
    const list = await load()
    list.mockResolvedValue({ items: [record()], fresh_for_seconds: 3, departed_total: 0 })

    render(<WorkerRegistry />)

    expect(await screen.findByText('worker-a1b2')).toBeTruthy()
    expect(screen.getByText('Отвечает')).toBeTruthy()
    expect(screen.getByText('циклов 12')).toBeTruthy()
    expect(screen.getByText('шагов 40')).toBeTruthy()
  })

  it('отличает молчащего от отсутствующего', async () => {
    // Упавший исполнитель обязан остаться в списке с отдельной пометкой: если бы он
    // исчезал, «исполнитель молчит» и «исполнителя нет» стали бы одним и тем же.
    const list = await load()
    list.mockResolvedValue({
      items: [
        record({ worker_id: 'worker-live' }),
        record({ worker_id: 'worker-dead', state: 'stale', heartbeat_at: '2026-03-01T12:00:00Z' }),
      ],
      fresh_for_seconds: 3,
      departed_total: 0,
    })

    render(<WorkerRegistry />)

    expect(await screen.findByText('worker-dead')).toBeTruthy()
    expect(screen.getByText('Не отвечает')).toBeTruthy()
    expect(screen.getByText(/1 молчат/)).toBeTruthy()
  })

  it('различает остановленного и не отвечающего', async () => {
    const list = await load()
    list.mockResolvedValue({
      items: [record({ state: 'stopped', stopped_at: '2026-03-01T12:30:00Z' })],
      fresh_for_seconds: 3,
      departed_total: 0,
    })

    render(<WorkerRegistry />)

    expect(await screen.findByText('Остановлен')).toBeTruthy()
    expect(screen.queryByText('Не отвечает')).toBeNull()
  })

  it('называет границу молчания цифрой', async () => {
    const list = await load()
    list.mockResolvedValue({ items: [], fresh_for_seconds: 3, departed_total: 0 })

    render(<WorkerRegistry />)

    expect(await screen.findByText(/дольше 3 с/)).toBeTruthy()
  })

  it('показывает потери закрепления, когда они были', async () => {
    const list = await load()
    list.mockResolvedValue({
      items: [record({ lease_lost: 3 })],
      fresh_for_seconds: 3,
      departed_total: 0,
    })

    render(<WorkerRegistry />)

    // Молчаливое число в счётчике ничего не значит: потерянное закрепление — это удвоенная
    // работа другого исполнителя, и её надо назвать.
    expect(await screen.findByText('потерянных закреплений 3')).toBeTruthy()
  })

  it('называет отвечающих, а не всех, кто когда-либо запускался', async () => {
    // Заголовок «12 в системе» при трёх отвечающих был просто неправдой: он
    // считал строки реестра, а не живые исполнители.
    const list = await load()
    list.mockResolvedValue({
      items: [
        record({ worker_id: 'live-1' }),
        record({ worker_id: 'live-2' }),
        record({ worker_id: 'silent-1', state: 'stale', heartbeat_at: '2026-03-01T12:00:00Z' }),
        record({ worker_id: 'gone-1', state: 'stopped', stopped_at: '2026-03-01T12:30:00Z' }),
      ],
      fresh_for_seconds: 3,
      departed_total: 0,
    })

    render(<WorkerRegistry />)

    expect(await screen.findByText('2 отвечают · 1 молчат · 1 остановлены')).toBeTruthy()
  })

  it('прячет завершённых за переключателем и говорит, сколько их', async () => {
    const list = await load()
    list.mockResolvedValue({ items: [], fresh_for_seconds: 3, departed_total: 7 })

    render(<WorkerRegistry />)

    // Без числа переключатель выглядел бы пустым, и человек решил бы, что
    // никто никогда не выходил из строя.
    const toggle = await screen.findByRole('button', { name: 'Показать завершённых (7)' })
    expect(toggle).toBeTruthy()
    expect(screen.getByText(/завершённые скрыты за переключателем/)).toBeTruthy()
  })

  it('запрашивает завершённых по нажатию и убирает их обратно', async () => {
    const list = await load()
    list.mockResolvedValueOnce({ items: [], fresh_for_seconds: 3, departed_total: 1 })
    list.mockResolvedValueOnce({
      items: [record({ worker_id: 'gone-2', state: 'stopped', stopped_at: '2026-03-01T12:30:00Z' })],
      fresh_for_seconds: 3,
      departed_total: 1,
    })

    render(<WorkerRegistry />)
    await fireEvent.click(await screen.findByRole('button', { name: 'Показать завершённых (1)' }))

    expect(await screen.findByText('gone-2')).toBeTruthy()
    expect(list).toHaveBeenLastCalledWith({ departed: true })
  })

  it('не предлагает переключатель, когда скрывать нечего', async () => {
    const list = await load()
    list.mockResolvedValue({ items: [record()], fresh_for_seconds: 3, departed_total: 0 })

    render(<WorkerRegistry />)
    await screen.findByText('worker-a1b2')

    // Переключатель без числа был бы кнопкой, которая ничего не делает.
    expect(screen.queryByRole('button', { name: /завершённых/ })).toBeNull()
  })

  it('говорит, когда исполнителей нет ни одного', async () => {
    const list = await load()
    list.mockResolvedValue({ items: [], fresh_for_seconds: 3, departed_total: 0 })

    render(<WorkerRegistry />)

    expect(await screen.findByText('Исполнителей нет ни одного')).toBeTruthy()
  })

  it('объявляет, что реестр не прочитан', async () => {
    const list = await load()
    list.mockRejectedValue(new Error('реестр недоступен'))

    render(<WorkerRegistry />)

    expect(await screen.findByText('реестр недоступен')).toBeTruthy()
  })

  it('читает реестр повторно, потому что сердце исполнителя движется', async () => {
    const list = await load()
    list.mockResolvedValue({ items: [record()], fresh_for_seconds: 3, departed_total: 0 })

    render(<WorkerRegistry />)
    await screen.findByText('worker-a1b2')

    await vi.advanceTimersByTimeAsync(5000)

    await waitFor(() => expect(list.mock.calls.length).toBeGreaterThan(1))
  })
})