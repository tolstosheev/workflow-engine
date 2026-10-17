// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import type { StepRun } from '@/types/api'

import { StepTimeline } from './StepTimeline'

const NOW = Date.parse('2026-03-01T10:00:40Z')

const run = (over: Partial<StepRun> & { id: string; started_at: string }): StepRun => ({
  step_name: 'reserve_inventory',
  attempt: 1,
  status: 'COMPLETED',
  output: null,
  error: null,
  compensated: false,
  finished_at: null,
  ...over,
})

afterEach(cleanup)

describe('лента времени процесса', () => {
  it('говорит долю времени в шагах, а не только длительности', () => {
    // Это главное, ради чего лента и нужна: из таблицы длительностей процесс
    // выглядит мгновенным, и тезис о долгой транзакции из коротких шагов не
    // виден вовсе.
    render(
      <StepTimeline
        now={NOW}
        steps={[
          run({ id: 'a', started_at: '2026-03-01T10:00:00Z', finished_at: '2026-03-01T10:00:00.020Z' }),
          run({ id: 'b', started_at: '2026-03-01T10:00:39Z', finished_at: '2026-03-01T10:00:39.030Z' }),
        ]}
      />,
    )

    // Два шага по 20 и 30 мс на отрезке в 39 с — меньше процента времени.
    expect(screen.getByText(/в шагах 0\.1 %/)).toBeTruthy()
    expect(screen.getByText(/ожидание, повтор или решение человека/)).toBeTruthy()
  })

  it('показывает разрыв между шагами отдельной подписью', () => {
    render(
      <StepTimeline
        now={NOW}
        steps={[
          run({ id: 'a', started_at: '2026-03-01T10:00:00Z', finished_at: '2026-03-01T10:00:01Z' }),
          run({ id: 'b', started_at: '2026-03-01T10:00:31Z', finished_at: '2026-03-01T10:00:32Z' }),
        ]}
      />,
    )

    // Тридцать секунд ожидания подписаны у первого шага: без подписи пустое место
    // между полосами выглядит как ошибка отрисовки, а не как ожидание.
    expect(screen.getByText('+30.0 с')).toBeTruthy()
  })

  it('помечает повторную попытку', () => {
    render(
      <StepTimeline
        now={NOW}
        steps={[
          run({
            id: 'a',
            attempt: 2,
            status: 'FAILED',
            started_at: '2026-03-01T10:00:00Z',
            finished_at: '2026-03-01T10:00:01Z',
          }),
        ]}
      />,
    )

    expect(screen.getByText(/попытка 2/)).toBeTruthy()
    expect(screen.getByText('не выполнен')).toBeTruthy()
  })

  it('помечает компенсированный шаг', () => {
    render(
      <StepTimeline
        now={NOW}
        steps={[
          run({
            id: 'a',
            compensated: true,
            started_at: '2026-03-01T10:00:00Z',
            finished_at: '2026-03-01T10:00:01Z',
          }),
        ]}
      />,
    )

    expect(screen.getByText('компенсирован')).toBeTruthy()
  })

  it('называет шаг, который исполняется сейчас', () => {
    render(
      <StepTimeline
        now={NOW}
        steps={[
          run({ id: 'a', started_at: '2026-03-01T10:00:00Z', finished_at: '2026-03-01T10:00:01Z' }),
          run({
            id: 'b',
            step_name: 'charge_payment',
            attempt: 2,
            status: 'RUNNING',
            started_at: '2026-03-01T10:00:30Z',
            finished_at: null,
          }),
        ]}
      />,
    )

    // Полоса исполняемого шага растёт на глазах, и без подписи над лентой не
    // видно, что именно сейчас происходит с процессом.
    expect(screen.getByText(/Сейчас исполняется: charge_payment/)).toBeTruthy()
  })

  it('не рисует ничего, когда разложить нечего', () => {
    const { container } = render(<StepTimeline now={NOW} steps={[]} />)

    expect(container.innerHTML).toBe('')
  })
})
