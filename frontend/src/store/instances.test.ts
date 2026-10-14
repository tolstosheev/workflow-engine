import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { StreamEvent, WorkflowInstance } from '@/types/api'

/** Страница списка: тип задан руками, иначе пустой массив выводится как `never[]`. */
interface Page {
  items: WorkflowInstance[]
  total: number
  limit: number
  offset: number
}

const list = vi.fn<(params: unknown, signal?: AbortSignal) => Promise<Page>>(
  async () => ({ items: [], total: 0, limit: 100, offset: 0 }),
)

// Шпион отдаётся напрямую, а не в обёртке: иначе `vi.mocked(list)` доставал
// обычную функцию, у которой нет ни `mockResolvedValue`, ни счётчика вызовов.
vi.mock('@/api/client', () => ({
  instances: { list },
}))

const { useInstancesStore } = await import('@/store/instances')

const STUB = (id: string, status: WorkflowInstance['status'] = 'RUNNING'): WorkflowInstance => ({
  id,
  definition: { id: 'd1', name: 'demo', version: 1 },
  status,
  input: {},
  variables: {},
  current_step: 'a',
  error: null,
  lease_owner: null,
  lease_expires_at: null,
  heartbeat_at: null,
  archived_at: null,
  created_at: '2026-10-10T00:00:00Z',
  updated_at: '2026-10-10T00:00:00Z',
})

const event = (
  type: StreamEvent['type'],
  payload: Record<string, unknown>,
  over: Partial<StreamEvent> = {},
): StreamEvent =>
  ({
    instance_id: 'i1',
    seq: 1,
    type,
    payload,
    created_at: '2026-10-10T00:01:00Z',
    ...over,
  }) as StreamEvent

describe('handleEvent', () => {
  beforeEach(() => {
    useInstancesStore.setState({
      items: [STUB('i1'), STUB('i2', 'PENDING')],
      lastEventAt: null,
      lastSeq: {},
    })
  })

  it('отбрасывает кадр, который уже применён', () => {
    // Поток переподключается с последнего события и присылает его снова, а
    // поздний кадр приходит позже нового. Без сверки номера повтор возвращал
    // завершённый процесс в «выполняется» и включал кнопки, которые для него
    // закрыты.
    useInstancesStore.getState().handleEvent(
      event('INSTANCE_COMPLETED', {}, { seq: 5 }),
    )

    useInstancesStore.getState().handleEvent(
      event('STEP_STARTED', { step: 'a' }, { seq: 4 }),
    )

    expect(useInstancesStore.getState().items[0].status).toBe('COMPLETED')
  })

  it('применяет кадр с номером больше предыдущего', () => {
    useInstancesStore.getState().handleEvent(event('STEP_COMPLETED', { next: 'b' }, { seq: 5 }))

    useInstancesStore.getState().handleEvent(
      event('STEP_STARTED', { step: 'b' }, { seq: 6 }),
    )

    expect(useInstancesStore.getState().items[0].status).toBe('RUNNING')
    expect(useInstancesStore.getState().items[0].current_step).toBe('b')
  })

  it('следит за номерами каждого инстанса отдельно', () => {
    useInstancesStore.getState().handleEvent(event('STEP_COMPLETED', {}, { seq: 9 }))

    // Номер девять у одного инстанса не означает «девять всего»: у второго
    // событий могло быть больше или меньше.
    useInstancesStore.getState().handleEvent(
      event('STEP_STARTED', { step: 'z' }, { instance_id: 'i2', seq: 1 }),
    )

    expect(useInstancesStore.getState().items[1].current_step).toBe('z')
  })

  it('показывает паузу и снятие паузы', () => {
    useInstancesStore.getState().handleEvent(event('INSTANCE_PAUSED', { step: 'approve' }, { seq: 2 }))

    expect(useInstancesStore.getState().items[0].status).toBe('PAUSED')

    useInstancesStore.getState().handleEvent(event('INSTANCE_RESUMED', { step: 'approve' }, { seq: 3 }))

    expect(useInstancesStore.getState().items[0].status).toBe('RUNNING')
  })

  it('отмечает отмену', () => {
    useInstancesStore.getState().handleEvent(event('INSTANCE_CANCEL_REQUESTED', {}))

    expect(useInstancesStore.getState().items[0].status).toBe('CANCELLING')
  })

  it('отмечает завершённую отмену', () => {
    // Отмена проходит двумя событиями: запрос и итог. Без второго список
    // вечно показывал бы «отменяется» на процессе, который уже закончен.
    useInstancesStore.getState().handleEvent(event('INSTANCE_CANCELLED', {}))

    expect(useInstancesStore.getState().items[0].status).toBe('CANCELLED')
  })

  it('отмечает процесс, ушедший в откат по исчерпанному циклу', () => {
    // Цикл исчерпал проходы: процесс откатывается. Если это событие не было бы
    // обработано, список показывал бы шаг, который на самом деле повторяться
    // уже перестал.
    useInstancesStore.getState().handleEvent(event('LOOP_EXHAUSTED', {}))

    expect(useInstancesStore.getState().items[0].status).toBe('COMPENSATING')
  })

  it('помнит, жив ли поток событий', () => {
    // Разрыв потока и его восстановление — разные состояния: без отметки
    // экран не отличил бы «никто не слышит» от «всё тихо».
    useInstancesStore.getState().setConnected(false)
    expect(useInstancesStore.getState().connected).toBe(false)

    useInstancesStore.getState().setConnected(true)
    expect(useInstancesStore.getState().connected).toBe(true)
  })

  it('updates only the instance the event belongs to', () => {
    useInstancesStore.getState().handleEvent(event('COMPENSATION_STARTED', {}))

    const items = useInstancesStore.getState().items
    expect(items[0].status).toBe('COMPENSATING')
    expect(items[1].status).toBe('PENDING')
  })

  it('keeps the other instance untouched when the event is unknown', () => {
    const before = useInstancesStore.getState().items[1]

    useInstancesStore.getState().handleEvent(event('STEP_RETRY_SCHEDULED', {}))

    expect(useInstancesStore.getState().items[1]).toBe(before)
  })

  it('advances status on compensation completion', () => {
    // Раньше COMPENSATION_COMPLETED не обрабатывался, и строка зависала на старом статусе
    useInstancesStore.getState().handleEvent(event('COMPENSATION_COMPLETED', {}))

    expect(useInstancesStore.getState().items[0].status).toBe('COMPENSATING')
  })

  it('marks the instance as waiting on timer events', () => {
    useInstancesStore.getState().handleEvent(event('TIMER_SCHEDULED', { step: 'b' }))

    const first = useInstancesStore.getState().items[0]
    expect(first.status).toBe('WAITING')
    expect(first.current_step).toBe('b')
  })

  it('marks failure without compensation as FAILED', () => {
    useInstancesStore.getState().handleEvent(event('INSTANCE_FAILED', {}))

    const first = useInstancesStore.getState().items[0]
    expect(first.status).toBe('FAILED')
    expect(first.current_step).toBeNull()
  })

  it('records the event time even for an unknown instance', () => {
    const unknown = { ...event('INSTANCE_COMPLETED', {}), instance_id: 'missing' }

    useInstancesStore.getState().handleEvent(unknown)

    expect(useInstancesStore.getState().lastEventAt).toBe('2026-10-10T00:01:00Z')
    expect(useInstancesStore.getState().items).toHaveLength(2)
  })

  it('загрузка заменяет список и снимает прошлаю ошибку', async () => {
    list.mockResolvedValue({ items: [STUB('из-базы')], total: 1, limit: 100, offset: 0 })
    useInstancesStore.setState({ items: [STUB('старый')], loading: true, error: 'прошлая ошибка' })

    await useInstancesStore.getState().load()

    // Список, который показали, и тот, что пришёл с сервера, — разные вещи:
    // пока идёт загрузка, на экране старое, и без замены он остался бы навсегда.
    expect(useInstancesStore.getState().items.map((item) => item.id)).toEqual(['из-базы'])
    expect(useInstancesStore.getState().loading).toBe(false)
    expect(useInstancesStore.getState().error).toBeNull()
  })

  it('загрузка начинает отсчёт событий заново', async () => {
    list.mockResolvedValue({ items: [STUB('из-базы')], total: 1, limit: 100, offset: 0 })
    useInstancesStore.setState({ items: [], lastSeq: { i1: 50 }, loading: true })

    await useInstancesStore.getState().load()

    // Номера событий принадлежат прежней выборке. Оставленные на месте, они
    // отбросили бы первые же кадры новой — и список перестал бы оживать.
    expect(useInstancesStore.getState().lastSeq).toEqual({})
  })

  it('показывает причину, если загрузка не удалась', async () => {
    list.mockRejectedValue(new Error('сервер недоступен'))
    useInstancesStore.setState({ items: [STUB('прежний')], loading: true })

    await useInstancesStore.getState().load()

    expect(useInstancesStore.getState().error).toBe('сервер недоступен')
    expect(useInstancesStore.getState().loading).toBe(false)
    // Прежние строки остаются: пустой список читался бы как «процессов нет».
    expect(useInstancesStore.getState().items).toHaveLength(1)
  })

  it('объясняет ошибку, даже если пришла не строкой', async () => {
    // Отказ может прийти чем угодно: строкой, объектом, ничем. Безfallback
    // на экране оказалось бы `undefined` вместо причины.
    list.mockRejectedValue('просто строка')
    useInstancesStore.setState({ loading: true })

    await useInstancesStore.getState().load()

    expect(useInstancesStore.getState().error).toBe('Не удалось загрузить инстансы')
  })

  it('отменённый запрос не пишет в состояние', async () => {
    // Отменённый запрос — разовая отменённая загрузка, а не ошибка: если бы он
    // записался, список уходящей со страницы вкладки вернулся бы старым.
    const { instances } = await import('@/api/client')
    vi.mocked(instances.list).mockResolvedValue({
      items: [STUB('старый')],
      total: 1,
      limit: 100,
      offset: 0,
    } as never)
    useInstancesStore.setState({ items: [STUB('прежний')], error: 'сбой', loading: false })

    const controller = new AbortController()
    controller.abort()
    await useInstancesStore.getState().load(controller.signal)

    expect(useInstancesStore.getState().items.map((item) => item.id)).toEqual(['прежний'])
    expect(useInstancesStore.getState().error).toBe('сбой')
  })
})
