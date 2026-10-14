// @vitest-environment jsdom
/**
 * Страница инстанса: кнопки решения и разбор журнала.
 *
 * Страница отвечает на два вопроса оператора: что сейчас происходит и что с
 * этим можно сделать. Ошибка в первом вопросе стоит дороже всего — процесс
 * ждёт человека, а кнопки нет, и снять паузу можно было бы только из консоли.
 *
 * Закреплено:
 *
 * - «Продолжить» есть только у паузы, и нажатие действительно её снимает;
 * - отмена работает и из паузы: компенсировать выполненное нужно независимо от
 *   того, чем процесс занимался, когда остановился;
 * - необратимые действия исчезают у завершённого процесса;
 * - у процесса, застрявшего на откате, остаётся только повтор отката: «закрыть»
 *   его здесь означало бы забыть о неоткатанном резерве;
 * - определение перечитывается один раз, а не на каждый цикл опроса.
 */

import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import InstancePage from '@/pages/instance'
import type { WorkflowInstance } from '@/types/api'

/** Обработчики, которые страница отдала потоку: ими можно управлять в тесте. */
const stream: { current: { onEvent?: (event: unknown) => void } | null } = { current: null }

vi.mock('@/api/stream', () => ({
  useEventStream: (handlers: { onEvent?: (event: unknown) => void }) => {
    stream.current = handlers
  },
}))

const get = vi.fn()
const state = vi.fn()
const steps = vi.fn()
const timeline = vi.fn()
const definitionGet = vi.fn()
const cancel = vi.fn()
const resume = vi.fn()
const forceComplete = vi.fn()
const retryCompensation = vi.fn()

vi.mock('@/api/client', () => ({
  instances: {
    get: (...args: unknown[]) => get(...args),
    state: (...args: unknown[]) => state(...args),
    steps: (...args: unknown[]) => steps(...args),
    timeline: (...args: unknown[]) => timeline(...args),
    cancel: (...args: unknown[]) => cancel(...args),
    resume: (...args: unknown[]) => resume(...args),
    forceComplete: (...args: unknown[]) => forceComplete(...args),
    retryCompensation: (...args: unknown[]) => retryCompensation(...args),
  },
  definitions: { get: (...args: unknown[]) => definitionGet(...args) },
}))

function instance(status: WorkflowInstance['status']): WorkflowInstance {
  return {
    id: 'i-1',
    definition: { id: 'd1', name: 'order_demo', version: 2 },
    status,
    current_step: 'approve_order',
    variables: {},
    lease_owner: 'w1',
    lease_expires_at: null,
    heartbeat_at: null,
    archived_at: null,
    created_at: '2026-03-01T10:00:00Z',
    updated_at: '2026-03-01T10:00:00Z',
  } as unknown as WorkflowInstance
}

const REBUILT = {
  status: 'RUNNING',
  current_step: 'approve_order',
  variables: {},
  completed_steps: ['validate_cart'],
  pending_compensations: ['validate_cart'],
  is_terminal: false,
  step_iterations: {},
}

const DEFINITION = {
  id: 'd1',
  name: 'order_demo',
  version: 2,
  is_active: true,
  created_at: '2026-03-01T09:00:00Z',
  updated_at: '2026-03-01T09:00:00Z',
  definition: {
    start: 'validate_cart',
    steps: {
      validate_cart: {
        action: 'app.demo.handlers:validate_cart',
        compensation: 'app.demo.handlers:release_inventory',
        next: 'approve_order',
      },
      approve_order: { action: 'app.demo.handlers:charge_payment', next: 'finish' },
      finish: { action: 'app.demo.handlers:confirm_delivery' },
    },
  },
}

/** Единственный смонтированный экземпляр на тест: два рендера искали бы кнопки друг друга. */
async function open(status: WorkflowInstance['status'] = 'RUNNING'): Promise<void> {
  get.mockResolvedValue(instance(status))
  renderPage()
  await screen.findByRole('heading', { name: 'order_demo' })
}

/**
 * Рендер без ожидания заголовка.
 *
 * Нужен там, где страница не показывается: при непрочитанном состоянии
 * заголовка нет, и `open()` ждал бы его до отказа таймаутом, вместо того
 * чтобы показать, что на самом деле выведено на экран.
 */
function renderPage(): void {
  render(
    <MemoryRouter initialEntries={['/instances/i-1']}>
      <Routes>
        <Route path="/instances/:id" element={<InstancePage />} />
      </Routes>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.spyOn(window, 'confirm').mockReturnValue(true)
  // Счётчики запросов копятся между тестами, если их не обнулять: проверка
  // «определение прочитано один раз» видела бы вызовы предыдущих тестов.
  for (const spy of [
    get,
    state,
    steps,
    timeline,
    definitionGet,
    cancel,
    resume,
    forceComplete,
    retryCompensation,
  ]) {
    spy.mockReset()
  }
  state.mockResolvedValue(REBUILT)
  steps.mockResolvedValue({ items: [] })
  timeline.mockResolvedValue({ items: [] })
  definitionGet.mockResolvedValue(DEFINITION)
  cancel.mockResolvedValue({ status: 'CANCELLING' })
  resume.mockResolvedValue({ status: 'RUNNING' })
  forceComplete.mockResolvedValue({ status: 'COMPLETED' })
  retryCompensation.mockResolvedValue({ status: 'COMPENSATING' })
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('кнопки решения', () => {
  it('показывает срок, если он задан, и молчит, если нет', async () => {
    // «Ждёт решения» без срока и «ждёт, пока не отменится» выглядят на экране
    // одинаково, а разница в том, вернётся ли человек вообще.
    timeline.mockResolvedValue({ items: [] })
    await open('PAUSED')

    expect(await screen.findByText(/срок из журнала неизвестен/)).toBeTruthy()
  })

  it('не показывает «Продолжить» у идущего процесса', async () => {
    await open('RUNNING')

    expect(screen.queryByRole('button', { name: 'Продолжить' })).toBeNull()
  })

  it('снимает паузу по нажатию', async () => {
    await open('PAUSED')

    await fireEvent.click(screen.getByRole('button', { name: 'Продолжить' }))

    expect(resume).toHaveBeenCalledOnce()
    expect(resume.mock.calls[0][0]).toBe('i-1')
  })

  it('показывает причину, если возобновить не удалось', async () => {
    resume.mockRejectedValue(new Error('инстанс не на паузе'))
    await open('PAUSED')

    await fireEvent.click(screen.getByRole('button', { name: 'Продолжить' }))

    // Молчаливый отказ хуже отказа с текстом: оператор решил бы, что нажал
    // мимо, и стал бы искать, где ещё можно снять паузу.
    expect(await screen.findByText('инстанс не на паузе')).toBeTruthy()
  })

  it('доступна отмена из паузы', async () => {
    await open('PAUSED')

    // Выполненные шаги нужно откатить независимо от того, что процесс ждал:
    // резерв остался бы в силе навсегда.
    expect(screen.getByRole('button', { name: 'Отменить' })).toBeTruthy()
  })

  it('прячет необратимые действия у завершённого процесса', async () => {
    await open('COMPLETED')

    expect(screen.queryByRole('button', { name: 'Отменить' })).toBeNull()
    expect(screen.queryByRole('button', { name: /Завершить без компенсаций/ })).toBeNull()
  })

  it('спрашивает перед необратимым завершением', async () => {
    // «Завершить без компенсаций» отличается от «Отменить» одними словами, а
    // стоит несопоставимо дороже: выполненные шаги остаются, и отменить
    // это уже нельзя. Вопрос обязателен.
    await open('RUNNING')
    await fireEvent.click(screen.getByRole('button', { name: /Завершить без компенсаций/ }))

    expect(window.confirm).toHaveBeenCalled()
  })

  it('ничего не завершает, если отказ', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    await open('RUNNING')

    await fireEvent.click(screen.getByRole('button', { name: /Завершить без компенсаций/ }))

    expect(forceComplete).not.toHaveBeenCalled()
  })

  it('завершает по подтверждению', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    await open('RUNNING')

    await fireEvent.click(screen.getByRole('button', { name: /Завершить без компенсаций/ }))

    expect(forceComplete).toHaveBeenCalledOnce()
  })

  it('показывает процесс без графа, если определение не прочиталось', async () => {
    // Граф берётся из определения, а состояние — из журнала. Отказ первого не
    // должен уводить со страницы всё второе: человек пришёл посмотреть, что с
    // процессом, а не что с картинкой.
    definitionGet.mockRejectedValue(new Error('определение недоступно'))
    await open('RUNNING')

    expect(await screen.findByRole('heading', { name: 'order_demo' })).toBeTruthy()
    expect(screen.queryByText('Граф шагов')).toBeNull()
    expect(screen.getByText('Выполнено')).toBeTruthy()
  })

  it('собирает отчёт из уже загруженного, без второго запроса', async () => {
    // Журнал приходит на страницу в любом случае, а второй запрос за теми же
    // данными означал бы, что страница тяжелее, чем кажется.
    get.mockResolvedValue(instance('COMPLETED'))
    steps.mockResolvedValue({
      items: [
        {
          id: 's-1',
          step_name: 'reserve_inventory',
          attempt: 1,
          status: 'COMPLETED',
          output: null,
          error: null,
          compensated: false,
          started_at: '2026-03-01T12:00:05Z',
          finished_at: '2026-03-01T12:00:07Z',
        },
      ],
    })
    const createObjectURL = vi.fn(() => 'blob:отчёт')
    vi.stubGlobal('URL', { ...URL, createObjectURL, revokeObjectURL: vi.fn() })

    renderPage()
    // Шаг виден и в таблице, и на ленте — берём любой: проверяется не число
    // вхождений, а то, что отчёт не тянет данных со стороны.
    await screen.findAllByText('reserve_inventory')

    // Счётчик снимается после загрузки: до него считали бы собственные
    // запросы страницы и сравнивали бы их с самими собой.
    const before = timeline.mock.calls.length
    await fireEvent.click(screen.getByRole('button', { name: 'Скачать отчёт' }))

    expect(timeline.mock.calls.length).toBe(before)
    // Отчёт действительно отдан файлом, а не просто нажата кнопка.
    expect(createObjectURL).toHaveBeenCalled()
    vi.unstubAllGlobals()
  })

  it('показывает прогоны шагов', async () => {
    steps.mockResolvedValue({
      items: [
        {
          id: 'sr-1',
          step_name: 'charge_payment',
          attempt: 2,
          status: 'FAILED',
          started_at: '2026-03-01T10:00:00Z',
          finished_at: '2026-03-01T10:00:03Z',
          error: 'отказ платёжной системы',
          compensated: false,
        },
      ],
    })
    await open('RUNNING')

    // Прогон с ошибкой — главное, ради чего сюда приходят: страница обязана
    // показать и попытку, и саму ошибку, иначе непонятно, что именно сломалось.
    expect(await screen.findByText('charge_payment')).toBeTruthy()
    // «провален» теперь встречается дважды: в таблице прогонов и на ленте
    // времени. Оба показа — осознанные, поэтому проверка ищет не единственное
    // вхождение, а наличие обоих.
    expect(screen.getAllByText('провален')).toHaveLength(2)
    expect(screen.getByText('отказ платёжной системы')).toBeTruthy()
  })

  it('не перечитывает данные по событию чужого процесса', async () => {
    await open('RUNNING')
    const before = get.mock.calls.length

    stream.current?.onEvent?.({ instance_id: 'чужой', type: 'STEP_COMPLETED', payload: {} })
    await new Promise((resolve) => setTimeout(resolve, 600))

    // Поток общий для всех процессов. Отклик на чужое событие означал бы опрос
    // списка на каждом шаге любого процесса на стенде.
    expect(get.mock.calls.length).toBe(before)
  })

  it('сливает частые события в одно обновление', async () => {
    await open('RUNNING')
    const before = get.mock.calls.length

    for (let index = 0; index < 5; index += 1) {
      stream.current?.onEvent?.({ instance_id: 'i-1', type: 'STEP_COMPLETED', payload: {} })
    }
    await new Promise((resolve) => setTimeout(resolve, 700))

    // Шаги идут пачками, и без слияния страница дёргала бы сервер на каждый
    // шаг: пять событий — пять одинаковых ответов.
    const calls = get.mock.calls.length - before
    expect(calls).toBeGreaterThan(0)
    expect(calls).toBeLessThanOrEqual(2)
  })

  it('предлагает повтор отката там, где он провалился', async () => {
    await open('COMPENSATION_FAILED')

    expect(screen.getByRole('button', { name: 'Повторить откат' })).toBeTruthy()
  })

  it('не смешивает повтор отката с обычными действиями', async () => {
    await open('COMPENSATION_FAILED')

    // У процесса, который застрял на откате, есть только повтор: отмена и
    // «завершить» здесь предложили бы забыть о неоткатанном резерве.
    expect(screen.queryByRole('button', { name: 'Отменить' })).toBeNull()
  })

  it('повторяет откат по нажатию', async () => {
    await open('COMPENSATION_FAILED')

    await fireEvent.click(screen.getByRole('button', { name: 'Повторить откат' }))

    // Кнопка без действия хуже отсутствующей: оператор решил бы, что откат
    // пошёл, а процесс остался бы ждать следующего подходящего момента.
    expect(retryCompensation).toHaveBeenCalledWith('i-1')
  })

  it('отменяет процесс по нажатию', async () => {
    await open('RUNNING')

    await fireEvent.click(screen.getByRole('button', { name: 'Отменить' }))

    expect(cancel).toHaveBeenCalledWith('i-1')
  })

  it('показывает причину, если отмена не прошла', async () => {
    cancel.mockRejectedValue(new Error('отмена отклонена'))
    await open('RUNNING')

    await fireEvent.click(screen.getByRole('button', { name: 'Отменить' }))

    // Молчаливая неудача опаснее отсутствия кнопки: процесс остался бы в
    // работе, а человек считал бы его отменённым.
    expect(await screen.findByText('отмена отклонена')).toBeTruthy()
  })

  it('объясняет, если состояние процесса не прочиталось', async () => {
    state.mockRejectedValue(new Error('журнал недоступен'))
    get.mockResolvedValue(instance('RUNNING'))
    renderPage()

    // Страница не показывается частично: заголовок с именем процесса и кнопки
    // действий без состояния означали бы «всё в порядке», и оператор нажал бы
    // на кнопку вслепую. Вместо этого — причина и повтор.
    expect(await screen.findByRole('alert')).toBeTruthy()
    expect(screen.getByText('журнал недоступен')).toBeTruthy()
    expect(screen.queryByRole('heading', { name: 'order_demo' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Отменить' })).toBeNull()
  })

  it('повторяет чтение по кнопке', async () => {
    state.mockRejectedValueOnce(new Error('журнал недоступен'))
    get.mockResolvedValue(instance('RUNNING'))
    renderPage()

    await screen.findByRole('alert')
    await fireEvent.click(screen.getByRole('button', { name: 'Повторить' }))

    // Повтор без надежды на успех — просто предложение начать заново.
    expect(await screen.findByRole('heading', { name: 'order_demo' })).toBeTruthy()
  })
})

describe('разбор журнала', () => {
  it('показывает, сколько раз повторялся каждый шаг', async () => {
    state.mockResolvedValue({
      ...REBUILT,
      step_iterations: { validate_cart: 3, approve_order: 1 },
    })

    await open('RUNNING')

    // Единицы не интересны: видно только те шаги, что повторялись на самом
    // деле, — по ним видно, где процесс буксует.
    expect(await screen.findByText('validate_cart: 3')).toBeTruthy()
  })

  it('показывает выполненные шаги из состояния, а не из колонки', async () => {
    state.mockResolvedValue({
      ...REBUILT,
      completed_steps: ['validate_cart', 'reserve_inventory'],
    })

    await open('RUNNING')

    expect(await screen.findByText('validate_cart → reserve_inventory')).toBeTruthy()
  })

  it('раскрывает данные события по требованию', async () => {
    timeline.mockResolvedValue({
      items: [
        {
          kind: 'event',
          seq: 3,
          type: 'STEP_COMPLETED',
          payload: { step: 'reserve_inventory' },
          created_at: '2026-03-01T10:00:01Z',
        },
      ],
    })

    await open('RUNNING')

    const toggle = await screen.findByRole('button', { name: /данные события/ })
    expect(toggle.getAttribute('aria-expanded')).toBe('false')

    await fireEvent.click(toggle)

    expect(toggle.getAttribute('aria-expanded')).toBe('true')
  })

  it('читает журнал предложениями, а не названиями событий', async () => {
    timeline.mockResolvedValue({
      items: [
        {
          kind: 'event',
          seq: 4,
          type: 'STEP_COMPLETED',
          payload: { step: 'reserve_inventory', next: 'charge_payment' },
          created_at: '2026-03-01T10:00:01Z',
        },
        {
          kind: 'event',
          seq: 5,
          type: 'COMPENSATION_FAILED',
          payload: { for_step: 'reserve_inventory', attempt: 2, error: 'склад недоступен' },
          created_at: '2026-03-01T10:00:09Z',
        },
      ],
    })

    await open('RUNNING')

    // Человек, разбирающий инцидент, должен понять из строки журнала, что
    // случилось, не открывая payload по каждому событию.
    expect(
      await screen.findByText('Шаг reserve_inventory выполнен, дальше charge_payment'),
    ).toBeTruthy()
    expect(
      await screen.findByText('Компенсация шага reserve_inventory не удалась на попытке 2: склад недоступен'),
    ).toBeTruthy()
  })

  it('перечитывает определение один раз, а не на каждый цикл опроса', async () => {
    // Опрос страницы повторяется каждые две секунды; определение — единственная
    // строка, которая не меняется, и запрос на каждый цикл на длинном процессе
    // давал бы десятки одинаковых ответов впустую.
    await open('RUNNING')
    await new Promise((resolve) => setTimeout(resolve, 2100))

    expect(definitionGet).toHaveBeenCalledTimes(1)
    // Опрос при этом продолжает работать: иначе «одна загрузка» означала бы
    // «страница не обновляется», и тест проходил бы по неверной причине.
    expect(get.mock.calls.length).toBeGreaterThan(1)
  })
})

describe('сбой загрузки', () => {
  it('отличает «нет данных» от «загрузка идёт»', async () => {
    get.mockRejectedValue(new Error('инстанс не найден'))
    render(
      <MemoryRouter initialEntries={['/instances/i-1']}>
        <Routes>
          <Route path="/instances/:id" element={<InstancePage />} />
        </Routes>
      </MemoryRouter>,
    )

    expect(await screen.findByText('инстанс не найден')).toBeTruthy()
    expect(screen.queryByText('Загрузка…')).toBeNull()
  })

  it('объясняет отсутствие идентификатора', () => {
    render(
      <MemoryRouter initialEntries={['/instances']}>
        <Routes>
          <Route path="/instances" element={<InstancePage />} />
        </Routes>
      </MemoryRouter>,
    )

    expect(screen.getByText('Идентификатор инстанса не указан')).toBeTruthy()
  })
})