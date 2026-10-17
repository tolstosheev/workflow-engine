// @vitest-environment jsdom
import { act, cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { LeaseDetails } from './LeaseDetails'

const NOW = Date.parse('2026-03-01T10:00:00Z')

afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

/** Замораживает время и тикает по секундам, как настоящий отсчёт. */
function freezeClock() {
  vi.useFakeTimers()
  vi.setSystemTime(NOW)
}

describe('закрепление на процессе', () => {
  it('показывает, сколько осталось до истечения', () => {
    // Важно не «кто держит», а «до какого»: по истечении срока процесс
    // свободен для захвата, и в этом суть решения вместо блокировок.
    freezeClock()

    render(
      <LeaseDetails
        owner="worker-1"
        expiresAt="2026-03-01T10:00:25Z"
        heartbeatAt="2026-03-01T09:59:58Z"
      />,
    )

    expect(screen.getByText(/держит ещё 25 с/)).toBeTruthy()
    expect(screen.getByText(/активность 2 с назад/)).toBeTruthy()
  })

  it('отсчитывает живьём, а не показывает значение на момент монтирования', () => {
    freezeClock()

    render(<LeaseDetails owner="worker-1" expiresAt="2026-03-01T10:00:20Z" heartbeatAt={null} />)

    act(() => {
      vi.advanceTimersByTime(5000)
    })

    expect(screen.getByText(/держит ещё 15 с/)).toBeTruthy()
  })

  it('говорит об истёкшем сроке прямо, а не показывает отрицательное время', () => {
    freezeClock()

    render(<LeaseDetails owner="worker-2" expiresAt="2026-03-01T09:59:00Z" heartbeatAt={null} />)

    // Минус в подписи читался бы как ошибка; на самом деле это и есть
    // interesting state: процесс свободен, и его заберёт кто угодно.
    expect(screen.getByText(/срок истёк, процесс свободен/)).toBeTruthy()
  })

  it('объясняет свободный процесс вместо пустой строки', () => {
    render(<LeaseDetails owner={null} expiresAt={null} heartbeatAt={null} />)

    expect(screen.getByText(/Закрепления нет/)).toBeTruthy()
  })

  it('не сбивается, если срок неизвестен', () => {
    freezeClock()

    render(<LeaseDetails owner="worker-1" expiresAt={null} heartbeatAt={null} />)

    expect(screen.getByText(/срок закрепления неизвестен/)).toBeTruthy()
  })
})
