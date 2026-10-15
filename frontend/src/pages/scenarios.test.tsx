// @vitest-environment jsdom
/**
 * Страница сценариев.
 *
 * Проверяется то, что человек видит на защите: сценарий называет, что он
 * доказывает, и после запуска — во что процесс пришёл **и что подтверждается по
 * данным**. Одного состояния мало: `COMPENSATED` бывает и при неоткатанном
 * резерве, и страница без свидетельств превращалась бы в кнопку «запустить».
 */

import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import ScenariosPage from '@/pages/scenarios'

// `vi.hoisted` обязателен: фабрика мока поднимается в начало файла и
// переменная, объявленная ниже, на момент подъёма ещё не существует.
const { start, get, clear, events, steps, cancel } = vi.hoisted(() => ({
  start: vi.fn(),
  get: vi.fn(),
  clear: vi.fn(),
  events: vi.fn(),
  steps: vi.fn(),
  cancel: vi.fn(),
}))

vi.mock('@/api/client', () => ({
  chaos: {
    clear,
    failStep: vi.fn(async () => ({})),
    failCompensation: vi.fn(async () => ({})),
    stopLease: vi.fn(async () => ({})),
  },
  definitions: {
    list: vi.fn(async () => ({
      items: [
        { id: 'def-1', name: 'order_fulfillment', version: 1, is_active: true },
        { id: 'def-2', name: 'order_with_wait', version: 1, is_active: true },
        { id: 'def-3', name: 'order_with_approval', version: 1, is_active: true },
      ],
      total: 3,
      limit: 50,
      offset: 0,
    })),
    activate: vi.fn(async () => ({})),
  },
  instances: { start, get, events, steps, cancel, resume: vi.fn(async () => ({})) },
}))

function renderPage() {
  return render(
    <MemoryRouter>
      <ScenariosPage />
    </MemoryRouter>,
  )
}

/** Кнопки «Запустить» в порядке каталога сценариев. */
async function buttons(): Promise<HTMLElement[]> {
  return screen.findAllByRole('button', { name: 'Запустить' })
}

describe('страница сценариев', () => {
  beforeEach(() => {
    for (const mock of [start, get, clear, events, steps, cancel]) mock.mockReset()
    clear.mockResolvedValue({ active: [], message: '' })
    start.mockResolvedValue({ id: 'inst-9' })
    events.mockResolvedValue({
      items: [
        { seq: 1, type: 'COMPENSATION_STARTED', payload: {} },
        { seq: 2, type: 'COMPENSATION_COMPLETED', payload: { for_step: 'reserve_inventory' } },
      ],
      total: 2,
    })
    steps.mockResolvedValue({
      items: [
        { step_name: 'reserve_inventory', status: 'COMPLETED', compensated: true },
      ],
    })
  })

  afterEach(cleanup)

  it('называет, что каждый сценарий доказывает', () => {
    renderPage()

    // Без этого текста страница — список кнопок, и на защите она ничего не
    // объясняет: демонстрация состоит из фразы и результата.
    expect(screen.getByText(/Saga возвращает выполненные шаги в обратном порядке/)).toBeTruthy()
    expect(screen.getByText(/Провал компенсации не прячется/)).toBeTruthy()
    expect(screen.getByText(/Счётчик проходов читается движком/)).toBeTruthy()
  })

  it('показывает правило, действие и ожидание каждого сценария', () => {
    renderPage()

    // Без этого докладчик не знает, что сейчас увидят: сценарий без
    // объявленного ожидания неотличим от кнопки.
    expect(screen.getByText('Определение: order_with_approval')).toBeTruthy()
    expect(screen.getByText(/сломать шаг charge_payment \(всегда\)/)).toBeTruthy()
    expect(screen.getByText(/остановить продление аренды/)).toBeTruthy()
  })

  it('запускает выбранный сценарий и даёт ссылку на процесс', async () => {
    renderPage()

    await fireEvent.click((await buttons())[1])

    // Сценарий — цепочка вызовов, и проверяется она целиком: сначала читаются
    // определения и снимаются правила, и только потом запускается процесс.
    await vi.waitFor(() =>
      expect(start).toHaveBeenCalledWith('def-1', expect.objectContaining({ amount: '1499.90' })),
    )
    expect(await screen.findByText('Открыть процесс')).toBeTruthy()
  })

  it('объявляет, если сценарий не запустился', async () => {
    start.mockRejectedValueOnce(new Error('нет определения order_fulfillment: запустите make seed'))
    renderPage()

    await fireEvent.click((await buttons())[0])

    expect(await screen.findByText(/нет определения/)).toBeTruthy()
    // Правило могло остаться: снимаем, иначе следующий сценарий начнётся под
    // чужой инъекцией, и виноват окажется не он.
    expect(clear).toHaveBeenCalled()
  })

  it('подтверждает утверждение состоянием и данными', async () => {
    get.mockResolvedValue({ id: 'inst-9', status: 'COMPENSATED', current_step: null })
    renderPage()

    await fireEvent.click((await buttons())[1])
    await screen.findByText('Открыть процесс')

    // Сценарий без такого подтверждения ничего не доказывает: «запустилось» —
    // это факт о кнопке, а не о свойстве.
    await vi.waitFor(
      () => expect(screen.getByText(/Утверждение подтверждено/)).toBeTruthy(),
      { timeout: 6000 },
    )
    expect(clear).toHaveBeenCalled()
  })

  it('показывает, какое свидетельство не подтвердилось', async () => {
    events.mockResolvedValue({
      items: [
        { seq: 1, type: 'COMPENSATION_STARTED', payload: {} },
        { seq: 2, type: 'COMPENSATION_FAILED', payload: { for_step: 'reserve_inventory' } },
      ],
      total: 2,
    })
    get.mockResolvedValue({ id: 'inst-9', status: 'COMPENSATED', current_step: null })
    renderPage()

    await fireEvent.click((await buttons())[1])
    await screen.findByText('Открыть процесс')

    // Состояние верное, а резерв остался: сказать «подтверждено» здесь было бы
    // враньём, и на защите именно это проверяли бы.
    await vi.waitFor(
      () => expect(screen.getByText(/свидетельство не подтвердилось/)).toBeTruthy(),
      { timeout: 6000 },
    )
    expect(screen.getByText(/остался в силе/)).toBeTruthy()
  })

  it('продолжает ждать, пока процесс не пришёл к концу', async () => {
    // Процесс почти всегда требует нескольких секунд: без ожидания страница
    // объявила бы неудачей нормальный ход дела.
    get.mockResolvedValue({ id: 'inst-9', status: 'RUNNING', current_step: 'charge_payment' })
    renderPage()

    await fireEvent.click((await buttons())[1])

    await vi.waitFor(() => expect(get).toHaveBeenCalled(), { timeout: 6000 })
    expect(screen.queryByText(/Ожидалось одно из/)).toBeNull()
  })

  it('отменяет процесс, дойдя до ожидания', async () => {
    get.mockResolvedValue({ id: 'inst-9', status: 'WAITING', current_step: 'await_delivery' })
    renderPage()

    // Каталог: happy, payment-fails, compensation-fails, compensation-retry-once,
    // retry-succeeds, stock-exhausted, cancel.
    await fireEvent.click((await buttons())[6])

    await vi.waitFor(() => expect(cancel).toHaveBeenCalledWith('inst-9'), { timeout: 6000 })
  })

  it('показывает паузу как конец показа, а не как ожидание', async () => {
    get.mockResolvedValue({ id: 'inst-9', status: 'PAUSED', current_step: 'charge_payment' })
    renderPage()

    // В воротах согласования действие — продолжить, поэтому ждать конца нельзя:
    // процесс не придёт в терминальное состояние, пока решение не принято.
    const index = 8
    await fireEvent.click((await buttons())[index])

    await vi.waitFor(
      () => expect(screen.getByText(/ждёт решения/)).toBeTruthy(),
      { timeout: 6000 },
    )
  })

  it('не падает, если наблюдение оборвалось ошибкой', async () => {
    // Обрыв чтения не должен ронять страницу: процесс продолжает идти, и
    // следующий опрос его увидит.
    get.mockRejectedValue(new Error('инстанс недоступен'))
    renderPage()

    await fireEvent.click((await buttons())[1])
    await screen.findByText('Открыть процесс')

    await vi.waitFor(() => expect(get).toHaveBeenCalled(), { timeout: 6000 })
    expect(screen.getByText('Открыть процесс')).toBeTruthy()
  })

  it('называет ожидаемое состояние, когда процесс пришёл не туда', async () => {
    // Молчаливое «готово» здесь опаснее пустоты: докладчик решил бы, что откат
    // сработал, а он не сработал.
    get.mockResolvedValue({ id: 'inst-9', status: 'COMPLETED', current_step: null })
    renderPage()

    await fireEvent.click((await buttons())[1])
    await screen.findByText('Открыть процесс')

    await vi.waitFor(() => expect(screen.getByText(/Ожидалось одно из/)).toBeTruthy(), {
      timeout: 6000,
    })
  })
})