// @vitest-environment jsdom
/**
 * Панель инъекции отказов.
 *
 * Панель ломает систему намеренно, поэтому важнее другое: она обязана честно
 * показывать, что сломано сейчас, и не выдавать неудачный запрос за «инъекций
 * нет». И отдельно — адресовать действие туда, куда оператор его направил.
 *
 * Тест на два поля инстанса ловит дефект, который выглядел безобидно: оба поля
 * были связаны с одним значением, поэтому адрес, введённый для инъекции в шаг,
 * молча применялся и к остановке аренды — к совершенно другому действию.
 */

import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import ChaosPage from '@/pages/chaos'

const list = vi.fn<() => Promise<{ active: string[] }>>()
const failStep = vi.fn<() => Promise<unknown>>()
const failService = vi.fn<() => Promise<unknown>>()
const stopLease = vi.fn<() => Promise<unknown>>()
const resumeLease = vi.fn<() => Promise<unknown>>()
const clear = vi.fn<() => Promise<unknown>>()
const failCompensation = vi.fn<(body: CompRule) => Promise<unknown>>()

interface CompRule {
  instance_id: string | null
  handler: string
  mode: string
}

/**
 * Обёртки передают аргументы шпиону.
 *
 * Раньше они вызывали `stopLease()` без ничего, и проверка «инъекция адресована
 * тому инстансу» физически не могла увидеть адрес: шпион получал пустой вызов.
 * Проверка проходила, потому что ничего и не утверждала.
 */
vi.mock('@/api/client', () => ({
  chaos: {
    list: (...args: unknown[]) => list(...(args as [])),
    failStep: (...args: unknown[]) => failStep(...(args as [])),
    failService: (...args: unknown[]) => failService(...(args as [])),
    stopLease: (...args: unknown[]) => stopLease(...(args as [])),
    resumeLease: (...args: unknown[]) => resumeLease(...(args as [])),
    clear: () => clear(),
    failCompensation: (body: CompRule) => failCompensation(body),
  },
}))

/**
 * Дожидается конца обработчика нажатия.
 *
 * Панель отправляет действие «в пустоту» и не ждёт его, поэтому обработчик
 * предыдущего теста мог сработать уже после сброса шпионов — и проверка
 * «шаг не сломан» падала на звонке из чужого теста.
 */
async function flush(): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, 0))
}

afterEach(() => {
  cleanup()
  for (const spy of [
    list,
    failStep,
    failService,
    stopLease,
    resumeLease,
    clear,
    failCompensation,
  ]) {
    spy.mockReset()
  }
})

beforeEach(() => {
  list.mockResolvedValue({ active: [] })
  failStep.mockResolvedValue({})
  failService.mockResolvedValue({})
  stopLease.mockResolvedValue({})
  resumeLease.mockResolvedValue({})
  clear.mockResolvedValue({})
  failCompensation.mockResolvedValue({})
})

describe('панель инъекций', () => {
  it('говорит, что правил нет, а не показывает пустой список молча', async () => {
    render(<ChaosPage />)

    expect(await screen.findByText('Инъекций нет')).toBeTruthy()
  })

  it('перечисляет действующее правило', async () => {
    list.mockResolvedValue({ active: ['payment:charge_payment:always'] })

    render(<ChaosPage />)

    expect(await screen.findByText('payment:charge_payment:always')).toBeTruthy()
  })

  it('ломает шаг из пресета, а не из того, что в поле', async () => {
    render(<ChaosPage />)

    // Пресет защищает от опечатки в имени обработчика: правило с неверным
    // именем никогда не срабатывает и выглядит как «инъекция не работает».
    await fireEvent.click(screen.getByRole('button', { name: 'create_shipment' }))
    await fireEvent.click(screen.getByRole('button', { name: 'Сломать шаг' }))
    await flush()

    expect(failStep).toHaveBeenCalledOnce()
  })

  it('не путает адрес инъекции с адресом для аренды', async () => {
    render(<ChaosPage />)

    await fireEvent.change(screen.getByLabelText('Инстанс для инъекции (необязательно)'), {
      target: { value: 'i-инъекция' },
    })
    await fireEvent.change(screen.getByLabelText('Инстанс для аренды'), {
      target: { value: 'i-аренда' },
    })

    // Одно значение на оба поля означало бы, что введённый адрес применяется к
    // действию, о котором оператор не думал.
    expect((screen.getByLabelText('Инстанс для инъекции (необязательно)') as HTMLInputElement).value).toBe(
      'i-инъекция',
    )
    expect(
      (screen.getByLabelText('Инстанс для аренды') as HTMLInputElement)
        .value,
    ).toBe('i-аренда')
  })

  it('не даёт остановить аренду без адреса', async () => {
    render(<ChaosPage />)

    const stop = screen.getByRole('button', { name: 'Остановить на 40 с' }) as HTMLButtonElement

    expect(stop.disabled).toBe(true)
  })

  it('останавливает аренду по введённому адресу', async () => {
    render(<ChaosPage />)

    await fireEvent.change(screen.getByLabelText('Инстанс для аренды'), {
      target: { value: 'i-1' },
    })
    await fireEvent.click(screen.getByRole('button', { name: 'Остановить на 40 с' }))

    expect(stopLease).toHaveBeenCalledOnce()
  })

  it('принимает имя шага, введённое руками', async () => {
    render(<ChaosPage />)
    await screen.findByText('Инъекций нет')

    await fireEvent.change(screen.getByLabelText('Шаг'), { target: { value: 'reserve_inventory' } })
    await fireEvent.click(screen.getByRole('button', { name: 'Сломать шаг' }))

    // Поле существует не для красоты: имена шагов длиннее, помещаются в пресет,
    // и без правки вручную инъекция на свой шаг была бы недоступна.
    expect(failStep).toHaveBeenCalledWith(
      expect.objectContaining({ step: 'reserve_inventory', mode: 'always' }),
    )
  })

  it('возобновляет аренду указанного инстанса', async () => {
    render(<ChaosPage />)
    await screen.findByText('Инъекций нет')

    await fireEvent.change(screen.getByLabelText('Инстанс для аренды'), { target: { value: 'i-9' } })
    await fireEvent.click(screen.getByRole('button', { name: 'Возобновить' }))
    await flush()

    // Адрес аренды и адрес инъекции — разные поля намеренно: сначала было одно
    // на оба, и остановка аренды применялась к адресу инъекции.
    expect(resumeLease).toHaveBeenCalledWith('i-9')
  })

  it('снимает все правила разом', async () => {
    list.mockResolvedValue({ active: ['*:reserve_inventory:always'] })
    render(<ChaosPage />)
    await screen.findByText('*:reserve_inventory:always')

    await fireEvent.click(screen.getByRole('button', { name: 'Снять все' }))
    await flush()

    expect(clear).toHaveBeenCalled()
  })

  it('объясняет ошибку действия, даже если пришла не строкой', async () => {
    // Отказ может прийти чем угодно, а не только объектом Error: без
    // запасного текста на экране оказалось бы `undefined` вместо причины.
    failStep.mockRejectedValue('просто строка')
    render(<ChaosPage />)
    await screen.findByText('Инъекций нет')

    await fireEvent.click(screen.getByRole('button', { name: 'Сломать шаг' }))

    expect(await screen.findByText('Операция не выполнена')).toBeTruthy()
  })

  it('объясняет, что список инъекций не пришёл', async () => {
    // Отказ при загрузке — другое место, чем отказ действия, и сообщение у него
    // своё: человек должен понять, что не удалось прочитать состояние.
    list.mockRejectedValue(new Error('панель недоступна'))
    render(<ChaosPage />)

    expect(await screen.findByText('панель недоступна')).toBeTruthy()
  })

  it('ломает компенсацию отдельным адресом, а не шагом', async () => {
    render(<ChaosPage />)
    await screen.findByText('Инъекций нет')

    await fireEvent.click(screen.getByRole('button', { name: 'Отменить резерв' }))
    await flush()

    // Правило адресует обработчик отката, а не шаг: одно поле, значившее оба
    // действия, сделало бы «сломать возврат» тем же, что «сломать шаг».
    expect(failCompensation).toHaveBeenCalledWith({
      instance_id: null,
      handler: 'app.demo.handlers:release_inventory',
      mode: 'always',
    })
    expect(failStep).not.toHaveBeenCalled()
  })

  it('адресует компенсацию тому инстансу, что указан', async () => {
    render(<ChaosPage />)
    await screen.findByText('Инъекций нет')

    await fireEvent.change(screen.getByLabelText('Инстанс для инъекции (необязательно)'), {
      target: { value: 'i-1' },
    })
    await fireEvent.click(screen.getByRole('button', { name: 'Вернуть оплату' }))
    await flush()

    expect(failCompensation.mock.calls[0][0]).toMatchObject({
      instance_id: 'i-1',
      handler: 'app.demo.handlers:refund_payment',
    })
  })

  it('уважает выбранный режим', async () => {
    render(<ChaosPage />)
    await screen.findByText('Инъекций нет')

    await fireEvent.click(screen.getByRole('button', { name: 'Один раз' }))
    await fireEvent.click(screen.getByRole('button', { name: 'Отменить доставку' }))
    await flush()

    expect(failCompensation.mock.calls[0][0]).toMatchObject({
      handler: 'app.demo.handlers:cancel_shipment',
      mode: 'once',
    })
  })

  it('показывает причину, когда запрос не прошёл', async () => {
    list.mockRejectedValue(new Error('сервис инъекций недоступен'))

    render(<ChaosPage />)

    expect(await screen.findByText('сервис инъекций недоступен')).toBeTruthy()
  })

  it('обновляет список после включения правила', async () => {
    list.mockResolvedValueOnce({ active: [] }).mockResolvedValueOnce({ active: ['inventory'] })

    render(<ChaosPage />)
    await screen.findByText('Инъекций нет')

    // Кнопки сервисов подписаны именем дела («Платежи»), а не названием
    // карточки: адресат инъекции — платёжный шаг.
    await fireEvent.click(screen.getByRole('button', { name: 'Платежи' }))

    expect(failService).toHaveBeenCalledOnce()
    await vi.waitFor(() => expect(list).toHaveBeenCalledTimes(2))
  })
})