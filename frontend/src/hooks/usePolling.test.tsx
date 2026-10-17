// @vitest-environment jsdom
/**
 * Периодическое обновление страниц.
 *
 * Хук объединяет три страницы, и проверяется то, что у них общее: когда
 * вызывается загрузка, когда она не вызывается и что происходит при смене
 * интервала.
 *
 * Последнее и оказалось дефектом: смена интервала перезапускала всю цепочку
 * с немедленной загрузкой. Страница процесса начинает с интервала в две
 * секунды — статус ещё не прочитан — и переходит на «не опрашивать», как только
 * статус известен, и каждое открытие страницы грузило данные дважды.
 */

import { cleanup, render, waitFor } from '@testing-library/react'
import { useState } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { usePolling } from './usePolling'

afterEach(cleanup)

/** Страница с интервалом, который тест меняет нажатием. */
function Poller({ load, initial }: { load: () => Promise<void> | void; initial: number }) {
  const [delay, setDelay] = useState(initial)
  usePolling(load, delay)
  return (
    <button type="button" onClick={() => setDelay(0)}>
      остановить
    </button>
  )
}

describe('периодическое обновление', () => {
  it('грузит сразу, не дожидаясь интервала', async () => {
    const load = vi.fn(async () => undefined)
    render(<Poller load={load} initial={60_000} />)

    await waitFor(() => expect(load).toHaveBeenCalledTimes(1))
  })

  it('не перезагружает страницу, когда интервал меняется', async () => {
    // Смена интервала — это смена частоты, а не повод запрашивать данные
    // немедленно. Иначе страница процесса тянет четыре запроса дважды при
    // каждом открытии: сначала с двухсекундным интервалом, затем с «не
    // опрашивать», и каждая смена перезапускает цепочку немедленной загрузкой.
    const load = vi.fn(async () => undefined)
    const { getByRole } = render(<Poller load={load} initial={2_000} />)
    await waitFor(() => expect(load).toHaveBeenCalledTimes(1))

    getByRole('button', { name: 'остановить' }).click()
    await new Promise((resolve) => setTimeout(resolve, 50))

    expect(load).toHaveBeenCalledTimes(1)
  })
})