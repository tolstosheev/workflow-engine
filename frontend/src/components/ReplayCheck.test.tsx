// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ReplayCheck } from './ReplayCheck'

const state = vi.fn()

vi.mock('@/api/client', () => ({
  instances: {
    state: (...args: unknown[]) => state(...args),
  },
}))

const replay = (status: string, currentStep: string | null) => ({
  status,
  current_step: currentStep,
  completed_steps: [],
  pending_compensations: [],
  variables: {},
  iteration: 0,
})

describe('проверка состояния по журналу', () => {
  beforeEach(() => {
    state.mockReset()
    cleanup()
  })

  it('подтверждает, что колонка сходится с разбором журнала', async () => {
    // Главное утверждение проекта становится проверкой, а не словами: журнал —
    // источник истины, и колонка обязана ему соответствовать.
    state.mockResolvedValue(replay('COMPENSATED', null))

    render(<ReplayCheck instanceId="i-1" status="COMPENSATED" currentStep={null} />)
    await fireEvent.click(screen.getByRole('button', { name: /состояние = журнал/ }))

    expect(await screen.findByText(/Совпадает/)).toBeTruthy()
    // «Компенсирован» ждёт и колонка, и разбор: обе строки обязаны совпасть, и
    // именно это проверка и утверждает.
    expect(screen.getAllByText('Компенсирован')).toHaveLength(2)
  })

  it('называет расхождение, а не прячет его', async () => {
    // Расхождение означает правку состояния в обход журнала. Показывать такое
    // зелёным было бы враньём на экране разбора инцидента.
    state.mockResolvedValue(replay('RUNNING', 'charge_payment'))

    render(<ReplayCheck instanceId="i-1" status="COMPLETED" currentStep={null} />)
    await fireEvent.click(screen.getByRole('button', { name: /состояние = журнал/ }))

    expect(await screen.findByText(/Расхождение/)).toBeTruthy()
    expect(screen.getByText('Завершён')).toBeTruthy()
    expect(screen.getByText('Выполняется')).toBeTruthy()
  })

  it('объясняет расхождение по шагу отдельно от расхождения состояния', async () => {
    // Колонка стоит на шаг впереди разбора на текущей итерации цикла: это
    // ожидаемо и дефектом не является, поэтому verdict остаётся «совпадает».
    state.mockResolvedValue(replay('RUNNING', 'reserve_inventory'))

    render(<ReplayCheck instanceId="i-1" status="RUNNING" currentStep="charge_payment" />)
    await fireEvent.click(screen.getByRole('button', { name: /состояние = журнал/ }))

    expect(await screen.findByText(/Совпадает/)).toBeTruthy()
    expect(screen.getByText(/Колонка стоит на шаг впереди/i)).toBeTruthy()
  })

  it('показывает причину, если состояние не перечиталось', async () => {
    state.mockRejectedValue(new Error('журнал недоступен'))

    render(<ReplayCheck instanceId="i-1" status="RUNNING" currentStep="a" />)
    await fireEvent.click(screen.getByRole('button', { name: /состояние = журнал/ }))

    await waitFor(() => expect(screen.getByText('журнал недоступен')).toBeTruthy())
  })

  it('у процесса с убранным журналом объявляет уборку, а не расхождение', () => {
    // Сравнивать нечего: событий нет, и пустой разбор выглядел бы как
    // «состояние изменено в обход журнала». Выдуманный дефект в разборе
    // инцидента хуже, чем отсутствие кнопки.
    render(
      <ReplayCheck
        instanceId="i-1"
        status="COMPLETED"
        currentStep={null}
        journalPrunedAt="2026-10-01T12:00:00Z"
      />,
    )

    expect(screen.getByText(/убран уборкой/i)).toBeTruthy()
    expect(screen.queryByRole('button', { name: /Проверить/ })).toBeNull()
  })

  it('у процесса с убранным журналом не просит разбор', () => {
    render(
      <ReplayCheck
        instanceId="i-1"
        status="COMPLETED"
        currentStep={null}
        journalPrunedAt="2026-10-01T12:00:00Z"
      />,
    )

    expect(state).not.toHaveBeenCalled()
  })

  it('не запрашивает состояние, пока кнопку не нажали', () => {
    // Проверка добровольная: страница и так опрашивает состояние по циклу, и
    // лишний запрос на каждом открытии ничего не добавлял бы.
    render(<ReplayCheck instanceId="i-1" status="RUNNING" currentStep="a" />)

    expect(state).not.toHaveBeenCalled()
    expect(screen.queryByText(/Совпадает|Расхождение/)).toBeNull()
  })
})
