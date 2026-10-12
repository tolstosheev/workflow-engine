// @vitest-environment jsdom
/**
 * Панель тревог обязана отличать «инцидентов нет» от «показать нечем».
 *
 * Обе картины выглядят на экране одинаково, пока одна из них не пустая: счётчик
 * ненулевой означает, что откат не прошёл, а нулевой — что всё в порядке. При
 * ошибке запроса пустой список читался как «инцидентов нет», то есть страница
 * врала ровно тогда, когда её смотрели из-за тревоги.
 */

import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { MetricsPage } from '@/pages/metrics'

const metrics = vi.fn<() => Promise<string>>()

vi.mock('@/api/client', () => ({
  service: {
    metrics: () => metrics(),
  },
}))

const CLEAN = `
# TYPE workflow_instances gauge
workflow_instances{status="RUNNING"} 2
workflow_instances{status="COMPLETED"} 7
workflow_lease_lost_total 0
`

const BROKEN = `
workflow_instances{status="COMPENSATION_FAILED"} 1
workflow_lease_lost_total 3
`

afterEach(() => {
  cleanup()
  metrics.mockReset()
})

describe('состояние системы', () => {
  it('говорит прямо, что инцидентов нет', async () => {
    metrics.mockResolvedValue(CLEAN)

    render(<MetricsPage />)

    expect(await screen.findByText(/Инцидентов нет/)).toBeTruthy()
  })

  it('называет тревогу, когда счётчик ненулевой', async () => {
    metrics.mockResolvedValue(BROKEN)

    render(<MetricsPage />)

    expect(await screen.findByText(/откат не удался/)).toBeTruthy()
    expect(screen.getByText(/потеряно аренд/)).toBeTruthy()
  })

  it('не выдаёт пустой ответ за отсутствие инцидентов', async () => {
    metrics.mockRejectedValue(new Error('метрики недоступны'))

    render(<MetricsPage />)

    // Сказать «запрос не удался» важнее, чем показать ноль: ноль в тревогах
    // читается как «всё хорошо», и оператор успокоится зря.
    expect(await screen.findByText(/запрос метрик не удался/)).toBeTruthy()
    expect(screen.queryByText(/Инцидентов нет/)).toBeNull()
    expect(screen.getByText('метрики недоступны')).toBeTruthy()
  })

  it('объявляет панель тревог для скринридера', async () => {
    metrics.mockResolvedValue(BROKEN)

    render(<MetricsPage />)
    await screen.findByText(/откат не удался/)

    // Панель появляется и исчезает сама, без действия человека: без объявления
    // скринридер узнаёт о тревоге только при следующем переходе по странице.
    const live = document.querySelector('[aria-live="polite"]')

    expect(live?.textContent).toContain('откат не удался')
  })
})