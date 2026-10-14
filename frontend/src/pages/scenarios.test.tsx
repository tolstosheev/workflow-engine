// @vitest-environment jsdom
/**
 * Страница сценариев.
 *
 * Проверяется то, что человек видит на защите: сценарий называет, что он
 * доказывает, и после запуска — во что процесс пришёл. Страница без ожидаемого
 * состояния превращалась бы в кнопку «запустить», а доказательство свойства —
 * это конечное состояние.
 */

import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import ScenariosPage from '@/pages/scenarios'

// `vi.hoisted` обязателен: фабрика мока поднимается в начало файла и
// переменная, объявленная ниже, на момент подъёма ещё не существует.
const { start, get, clear } = vi.hoisted(() => ({
  start: vi.fn(),
  get: vi.fn(),
  clear: vi.fn(),
}))

vi.mock('@/api/client', () => ({
  chaos: { clear, failStep: vi.fn(async () => ({})), failCompensation: vi.fn(async () => ({})) },
  definitions: {
    list: vi.fn(async () => ({
      items: [{ id: 'def-1', name: 'order_fulfillment', version: 1, is_active: true }],
      total: 1,
      limit: 50,
      offset: 0,
    })),
  },
  instances: { start, get },
}))

function renderPage() {
  return render(
    <MemoryRouter>
      <ScenariosPage />
    </MemoryRouter>,
  )
}

describe('страница сценариев', () => {
  beforeEach(() => {
    start.mockReset()
    get.mockReset()
    clear.mockReset()
    clear.mockResolvedValue({ active: [], message: '' })
    start.mockResolvedValue({ id: 'inst-9' })
  })

  afterEach(cleanup)

  it('называет, что каждый сценарий доказывает', () => {
    renderPage()

    // Без этого текста страница — список кнопок, и на защите она ничего не
    // объясняет: демонстрация состоит из фразы и результата.
    expect(screen.getByText(/Saga возвращает выполненные шаги в обратном порядке/)).toBeTruthy()
    expect(screen.getByText(/Провал компенсации не прячется/)).toBeTruthy()
  })

  it('запускает выбранный сценарий и даёт ссылку на процесс', async () => {
    renderPage()
    const buttons = await screen.findAllByRole('button', { name: 'Запустить' })

    await fireEvent.click(buttons[1])

    // Сценарий — цепочка вызовов, и проверяется она целиком: сначала читаются
    // определения и снимаются правила, и только потом запускается процесс.
    await vi.waitFor(() => expect(start).toHaveBeenCalledWith('def-1', { amount: '1499.90' }))
    expect(await screen.findByText('Открыть процесс')).toBeTruthy()
  })

  it('объявляет, если сценарий не запустился', async () => {
    start.mockRejectedValueOnce(new Error('нет активного определения order_fulfillment: запустите make seed'))
    renderPage()
    const buttons = await screen.findAllByRole('button', { name: 'Запустить' })

    await fireEvent.click(buttons[0])

    expect(await screen.findByText(/нет активного определения/)).toBeTruthy()
  })

  it('подтверждает, что процесс пришёл куда ожидалось', async () => {
    // Сценарий без такого подтверждения ничего не доказывает: «запустилось» —
    // это факт о кнопке, а не о свойстве.
    get.mockResolvedValueOnce({ id: 'inst-9', status: 'COMPENSATED', current_step: null })
    renderPage()
    const buttons = await screen.findAllByRole('button', { name: 'Запустить' })
    await fireEvent.click(buttons[1])
    await screen.findByText('Открыть процесс')

    await vi.waitFor(() =>
      expect(screen.getByText(/пришёл в ожидаемое состояние/)).toBeTruthy(),
      { timeout: 6000 },
    )
    expect(clear).toHaveBeenCalled()
  })

  it('продолжает ждать, пока процесс не пришёл к концу', async () => {
    // Процесс почти всегда требует нескольких секунд: без ожидания страница
    // объявила бы неудачей нормальный ход дела.
    get.mockResolvedValue({ id: 'inst-9', status: 'RUNNING', current_step: 'charge_payment' })
    renderPage()
    const buttons = await screen.findAllByRole('button', { name: 'Запустить' })
    await fireEvent.click(buttons[1])

    await vi.waitFor(() => expect(get).toHaveBeenCalled(), { timeout: 6000 })
    expect(screen.queryByText(/Ожидалось одно из/)).toBeNull()
    expect(clear).toHaveBeenCalledTimes(1)
  })

  it('не падает, если наблюдение оборвалось ошибкой', async () => {
    // Обрыв чтения не должен ронять страницу: процесс продолжает идти, и
    // следующий опрос его увидит.
    get.mockRejectedValue(new Error('инстанс недоступен'))
    renderPage()
    const buttons = await screen.findAllByRole('button', { name: 'Запустить' })
    await fireEvent.click(buttons[1])
    await screen.findByText('Открыть процесс')

    await vi.waitFor(() => expect(get).toHaveBeenCalled(), { timeout: 6000 })
    expect(screen.getByText('Открыть процесс')).toBeTruthy()
  })

  it('называет ожидаемое состояние, когда процесс пришёл не туда', async () => {
    // Молчаливое «готово» здесь опаснее пустоты: докладчик решил бы, что откат
    // сработал, а он не сработал.
    get.mockResolvedValue({ id: 'inst-9', status: 'COMPLETED', current_step: null })
    start.mockResolvedValue({ id: 'inst-9' })
    renderPage()
    const buttons = await screen.findAllByRole('button', { name: 'Запустить' })
    await fireEvent.click(buttons[1])
    await screen.findByText('Открыть процесс')

    await vi.waitFor(() => expect(screen.getByText(/Ожидалось одно из/)).toBeTruthy(), { timeout: 6000 })
  })
})