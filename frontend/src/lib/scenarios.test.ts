/**
 * Механика сценариев: порядок вызовов, несколько правил, действие по ходу и
 * проверка утверждения по данным.
 *
 * Проверяется не «кнопка нажимается», а последовательность и содержание:
 * правила ставятся до запуска и снимаются всегда, счёт правил не обязателен,
 * свидетельства читаются из журнала, а не из статуса.
 *
 * Именно здесь ловится класс дефектов, который дал ложный успех на стенде:
 * сценарий ломал компенсацию, но не ломал шаг, поэтому откат не начинался,
 * процесс доходил до конца, а правило оставалось и портило следующие запуски.
 */

import { describe, expect, it, vi } from 'vitest'

import {
  applyAction,
  checkEvidence,
  finishScenario,
  isObserved,
  isTerminal,
  runScenario,
  SCENARIOS,
  shouldStop,
  type RunDeps,
  type ScenarioSpec,
} from '@/lib/scenarios'
import type { WorkflowInstance } from '@/types/api'

function deps(over: Partial<Record<keyof RunDeps, unknown>> = {}): RunDeps {
  const base = {
    definitions: {
      list: vi.fn(async () => ({
        items: [
          { id: 'def-1', name: 'other', version: 1, is_active: true },
          { id: 'def-2', name: 'order_fulfillment', version: 1, is_active: true },
          { id: 'def-3', name: 'order_with_wait', version: 1, is_active: true },
        ],
        total: 3,
        limit: 50,
        offset: 0,
      })),
      activate: vi.fn(async (id: string) => ({ id, is_active: true })),
    },
    chaos: {
      clear: vi.fn(async () => ({ active: [], message: '' })),
      failStep: vi.fn(async () => ({})),
      failCompensation: vi.fn(async () => ({})),
      stopLease: vi.fn(async () => ({})),
    },
    instances: {
      start: vi.fn(async () => ({ id: 'inst-1' })),
      events: vi.fn(async () => ({ items: [], total: 0 })),
      steps: vi.fn(async () => ({ items: [] })),
      cancel: vi.fn(async () => ({})),
      resume: vi.fn(async () => ({})),
      retryCompensation: vi.fn(async () => ({})),
    },
  }
  return { ...base, ...over } as RunDeps
}

function spec(id: string): ScenarioSpec {
  const found = SCENARIOS.find((item) => item.id === id)
  if (!found) throw new Error(`нет сценария ${id}`)
  return found
}

function instance(over: Partial<WorkflowInstance> = {}): WorkflowInstance {
  return { id: 'inst-1', status: 'RUNNING', ...over } as WorkflowInstance
}

const happy = spec('happy')
const paymentFails = spec('payment-fails')
const compensationFails = spec('compensation-fails')
const cancel = spec('cancel')
const pause = spec('human-pause')
const lease = spec('lease-expired')

describe('запуск сценария', () => {
  it('берёт определение по имени, а не первое активное', async () => {
    const d = deps()

    await runScenario(happy, d)

    // Активных определений на стенде больше одного, и «первое» молча проверило бы
    // не то, что показано на странице.
    expect(d.instances.start).toHaveBeenCalledWith('def-2', expect.objectContaining({ amount: '1499.90' }))
  })

  it('берёт своё определение для сценария с ожиданием', async () => {
    const d = deps()

    await runScenario(spec('long-wait'), d)

    expect(d.instances.start).toHaveBeenCalledWith('def-3', expect.anything())
  })

  it('объясняет, когда демонстрационного определения нет', async () => {
    const d = deps({
      definitions: { list: vi.fn(async () => ({ items: [], total: 0, limit: 50, offset: 0 })) },
    })

    await expect(runScenario(happy, d)).rejects.toThrow(/order_fulfillment/)
  })

  it('включает определение, если его выключили', async () => {
    // Так уже случалось: уборка на стенде выключила определение, и следующий
    // прогон взял его по имени, не включив обратно.
    const d = deps({
      definitions: {
        list: vi.fn(async () => ({
          items: [{ id: 'def-7', name: 'order_fulfillment', version: 1, is_active: false }],
          total: 1,
          limit: 50,
          offset: 0,
        })),
        activate: vi.fn(async () => ({ id: 'def-7', is_active: true })),
      },
    })

    await runScenario(happy, d)

    expect((d.definitions as unknown as { activate: ReturnType<typeof vi.fn> }).activate).toHaveBeenCalledWith(
      'def-7',
    )
    expect(d.instances.start).toHaveBeenCalledWith('def-7', expect.anything())
  })

  it('снимает чужое правило до запуска, а не после', async () => {
    const d = deps()
    const order: string[] = []
    d.chaos.clear = vi.fn(async () => {
      order.push('clear')
      return { active: [], message: '' }
    }) as never
    d.instances.start = vi.fn(async () => {
      order.push('start')
      return { id: 'inst-1' }
    }) as never

    await runScenario(happy, d)

    // Правило от прошлого показа, оставленное на «всегда», начало бы новый
    // процесс с чужой инъекцией — и сценарий развалился бы не по своей причине.
    expect(order).toEqual(['clear', 'start'])
  })

  it('ставит правило до запуска процесса', async () => {
    const d = deps()
    const order: string[] = []
    d.chaos.failStep = vi.fn(async () => {
      order.push('rule')
      return {}
    }) as never
    d.instances.start = vi.fn(async () => {
      order.push('start')
      return { id: 'inst-1' }
    }) as never

    await runScenario(paymentFails, d)

    expect(order).toEqual(['rule', 'start'])
  })

  it('ставит несколько правил одновременно', async () => {
    const d = deps()

    await runScenario(compensationFails, d)

    // Откат без ломки самого шага не начинается: процесс доходит до конца,
    // а правило остаётся и портит следующие запуски.
    expect(d.chaos.failStep).toHaveBeenCalledWith(
      expect.objectContaining({ step: 'create_shipment', mode: 'always' }),
    )
    expect(d.chaos.failCompensation).toHaveBeenCalledWith(
      expect.objectContaining({ handler: 'app.demo.handlers:release_inventory' }),
    )
  })

  it('ломает откат по обработчику, а не по имени шага', async () => {
    const d = deps()

    await runScenario(spec('compensation-retry'), d)

    // Шаг к этому моменту уже выполнен: правило по его имени сработало бы на
    // прямом шаге и не на откате.
    expect(d.chaos.failCompensation).toHaveBeenCalledWith(
      expect.objectContaining({ handler: 'app.demo.handlers:release_inventory', mode: 'always' }),
    )
  })

  it('не ставит правило там, где оно не нужно', async () => {
    const d = deps()

    await runScenario(happy, d)

    expect(d.chaos.failStep).not.toHaveBeenCalled()
    expect(d.chaos.failCompensation).not.toHaveBeenCalled()
  })

  it('подставляет свой вход, когда сценарий требует особого', async () => {
    const d = deps()

    await runScenario(spec('stock-exhausted'), d)

    // Заказ на 99 999 единиц: демонстрация исчерпания остатка должна
    // показывать отказ шага, а не обычный успешный заказ.
    expect(d.instances.start).toHaveBeenCalledWith(
      'def-2',
      expect.objectContaining({ items: [{ sku: 'SKU-000', qty: 99_999 }] }),
    )
  })
})

describe('действие по ходу процесса', () => {
  it('отменяет процесс, дойдя до состояния', async () => {
    const d = deps()

    const applied = await applyAction(d, cancel, instance({ status: 'WAITING' }))

    expect(applied).toBe(true)
    expect(d.instances.cancel).toHaveBeenCalledWith('inst-1')
  })

  it('не отменяет процесс раньше срока', async () => {
    const d = deps()

    const applied = await applyAction(d, cancel, instance({ status: 'PENDING' }))

    expect(applied).toBe(false)
    expect(d.instances.cancel).not.toHaveBeenCalled()
  })

  it('продолжает процесс после паузы', async () => {
    const d = deps()

    await applyAction(d, pause, instance({ status: 'PAUSED' }))

    expect(d.instances.resume).toHaveBeenCalledWith('inst-1')
  })

  it('останавливает продление аренды с указанным сдвигом', async () => {
    const d = deps()

    await applyAction(d, lease, instance({ status: 'RUNNING' }))

    expect(d.chaos.stopLease).toHaveBeenCalledWith('inst-1', 120)
  })

  it('снимает правило прежде, чем повторять откат', async () => {
    const d = deps()
    const order: string[] = []
    d.chaos.clear = vi.fn(async () => {
      order.push('clear')
      return { active: [], message: '' }
    }) as never
    d.instances.retryCompensation = vi.fn(async () => {
      order.push('retry')
      return {}
    }) as never

    const applied = await applyAction(
      d,
      spec('compensation-retry'),
      instance({ status: 'COMPENSATION_FAILED' }),
    )

    expect(applied).toBe(true)
    // Обратный порядок дал бы кнопку, которая не работает: откат упал бы
    // снова по той же причине, и человек решил бы, что разбор невозможен.
    expect(order).toEqual(['clear', 'retry'])
  })

  it('не повторяет откат, пока процесс не встал в COMPENSATION_FAILED', async () => {
    const d = deps()

    const applied = await applyAction(
      d,
      spec('compensation-retry'),
      instance({ status: 'COMPENSATING' }),
    )

    expect(applied).toBe(false)
    expect(d.instances.retryCompensation).not.toHaveBeenCalled()
  })

  it('не делает ничего у сценария без действия', async () => {
    const d = deps()

    const applied = await applyAction(d, happy, instance({ status: 'RUNNING' }))

    expect(applied).toBe(false)
    expect(d.instances.cancel).not.toHaveBeenCalled()
    expect(d.instances.resume).not.toHaveBeenCalled()
  })
})

function event(type: string, forStep?: string) {
  return { seq: 1, type, payload: forStep ? { for_step: forStep } : {} } as never
}

describe('проверка утверждения по данным', () => {

  it('подтверждает компенсацию по журналу, а не по статусу', () => {
    const notes = checkEvidence(paymentFails, {
      events: [event('COMPENSATION_STARTED'), event('COMPENSATION_COMPLETED', 'reserve_inventory')],
      steps: [],
    })

    // Откат обязан быть виден в журнале: статус COMPENSATED есть и у процесса,
    // у которого резерв остался на складе.
    expect(notes.every((note) => note.passed)).toBe(true)
    expect(notes[0].detail).toContain('завершилась')
  })

  it('обнаруживает, что компенсация провалилась', () => {
    const notes = checkEvidence(paymentFails, {
      events: [event('COMPENSATION_STARTED'), event('COMPENSATION_FAILED', 'reserve_inventory')],
      steps: [],
    })

    const compensation = notes.find((note) => note.label.includes('резерв'))
    expect(compensation?.passed).toBe(false)
    // Подпись обязана объяснять, что означает провал, иначе на защите это
    // выглядит как «движок не сработал».
    expect(compensation?.detail).toContain('провалилась')
    expect(compensation?.detail).toContain('остался в силе')
  })

  it('различает «не выполнялась» и «провалилась»', () => {
    const notes = checkEvidence(paymentFails, { events: [event('COMPENSATION_STARTED')], steps: [] })

    // Оба случая означают «эффект остался в силе», но разные: молчащий откат и
    // упавший откат требуют разного разбора.
    expect(notes.find((note) => note.label.includes('резерв'))?.detail).toContain('не выполнялась')
  })

  it('не ищет компенсацию среди прогонов шага', () => {
    const notes = checkEvidence(paymentFails, {
      events: [event('COMPENSATION_STARTED'), event('COMPENSATION_COMPLETED', 'reserve_inventory')],
      // Прогонов компенсации в `step_runs` нет: искать её там — значит никогда
      // не найти и объявить успешный откат неподтверждённым.
      steps: [{ step_name: 'compensate:reserve_inventory', status: 'COMPLETED' }] as never,
    })

    // Журнал и есть источник истины: прогон-призрак его не заменяет.
    expect(notes.every((note) => note.passed)).toBe(true)
  })

  it('находит событие в журнале', () => {
    const notes = checkEvidence(spec('loop-exhausted'), {
      events: [event('LOOP_EXHAUSTED')],
      steps: [],
    })

    expect(notes.find((note) => note.label.includes('предел'))?.passed).toBe(true)
  })

  it('ловит отсутствующее событие', () => {
    const notes = checkEvidence(spec('loop-exhausted'), { events: [], steps: [] })

    expect(notes.find((note) => note.label.includes('предел'))?.passed).toBe(false)
  })

  it('считает прогоны шага по имени шага графа', () => {
    const notes = checkEvidence(spec('retry-succeeds'), {
      events: [],
      steps: [
        { step_name: 'charge_payment', status: 'FAILED', attempt: 1 },
        { step_name: 'charge_payment', status: 'COMPLETED', attempt: 2 },
      ] as never,
    })

    const attempt = notes.find((note) => note.label.includes('дважды'))
    expect(attempt?.passed).toBe(true)
    // «Два прогона» — это ещё не «выполнен один раз»: подпись обязана
    // показывать оба числа, иначе повтор выглядит как успех.
    expect(attempt?.detail).toContain('успешных: 1')
  })

  it('не считает свидетельством то, чего в данных нет', () => {
    const notes = checkEvidence(happy, { events: [], steps: [] })

    expect(notes.every((note) => note.passed)).toBe(false)
  })
})

describe('завершение сценария', () => {
  it('подтверждает утверждение, когда состояние и данные сошлись', async () => {
    const d = deps({
      instances: {
        start: vi.fn(),
        events: vi.fn(async () => ({
          items: [
            { seq: 5, type: 'COMPENSATION_STARTED', payload: {} },
            { seq: 6, type: 'COMPENSATION_COMPLETED', payload: { for_step: 'reserve_inventory' } },
          ] as never,
          total: 2,
        })),
        steps: vi.fn(async () => ({
          items: [{ step_name: 'reserve_inventory', status: 'COMPLETED' }],
        })),
      },
    })

    const verdict = await finishScenario(d, paymentFails, instance({ status: 'COMPENSATED' }))

    expect(verdict.stateMatched).toBe(true)
    expect(verdict.evidencePassed).toBe(true)
  })

  it('говорит, что состояние верное, а данные не подтвердились', async () => {
    // Самое опасное расхождение: процесс в «откатан», а резерв остался.
    const d = deps({
      instances: {
        start: vi.fn(),
        events: vi.fn(async () => ({
          items: [{ seq: 6, type: 'COMPENSATION_FAILED', payload: { for_step: 'reserve_inventory' } }],
          total: 1,
        })),
        steps: vi.fn(async () => ({ items: [] })),
      },
    })

    const verdict = await finishScenario(d, paymentFails, instance({ status: 'COMPENSATED' }))

    expect(verdict.stateMatched).toBe(true)
    expect(verdict.evidencePassed).toBe(false)
  })

  it('называет ожидаемое состояние, когда процесс пришёл не туда', async () => {
    const d = deps()

    const verdict = await finishScenario(d, paymentFails, instance({ status: 'COMPLETED' }))

    expect(verdict.stateMatched).toBe(false)
  })

  it('снимает правило, даже когда утверждение не подтвердилось', async () => {
    const d = deps()

    await finishScenario(d, paymentFails, instance({ status: 'COMPLETED' }))

    // Правило обязано сниматься всегда: оставленная «всегда» сломала бы всё,
    // что запустят дальше, и виноват оказался бы следующий сценарий.
    expect(d.chaos.clear).toHaveBeenCalled()
  })
})

describe('состояния процесса', () => {
  it('считает наблюдаемым состояние, на котором сценарий показывается', () => {
    expect(isObserved(pause, 'PAUSED')).toBe(true)
    // Пауза без действия дальше — это уход в ожидание навсегда, и страница
    // ждала бы того, чего не будет.
    expect(isObserved(pause, 'COMPLETED')).toBe(false)
  })

  it('не считает наблюдаемым то, что ещё изменится', () => {
    expect(isTerminal('RUNNING')).toBe(false)
    expect(isTerminal('PAUSED')).toBe(false)
    expect(isTerminal('COMPENSATION_FAILED')).toBe(true)
  })

  it('не останавливает сценарий на состоянии, которое проходится по дороге', () => {
    // Живой прогон сценария «повтор отката» вскрыл это: падение компенсации
    // терминально для движка, но воркер берёт откат снова после паузы, и
    // страница объявляла неудачей работу повторов.
    expect(shouldStop(spec('compensation-retry'), 'COMPENSATION_FAILED')).toBe(false)
    expect(shouldStop(spec('compensation-fails'), 'COMPENSATION_FAILED')).toBe(true)
  })

  it('останавливает сценарий на конечном состоянии', () => {
    expect(shouldStop(happy, 'COMPLETED')).toBe(true)
    expect(shouldStop(happy, 'RUNNING')).toBe(false)
  })

  it('считает события по счёту, а не по наличию', () => {
    const once = spec('compensation-retry')
    const one = {
      events: [event('COMPENSATION_STARTED', 'reserve_inventory')] as never,
      steps: [] as never,
    }
    const two = {
      events: [
        event('COMPENSATION_STARTED', 'reserve_inventory'),
        event('COMPENSATION_STARTED', 'reserve_inventory'),
      ] as never,
      steps: [] as never,
    }

    const repeat = once.evidence?.find((item) => item.kind === 'event-count')
    if (repeat?.kind !== 'event-count') throw new Error('у сценария нет счётчика событий')
    expect(checkEvidence({ ...once, evidence: [repeat] }, one)[0].passed).toBe(false)
    expect(checkEvidence({ ...once, evidence: [repeat] }, two)[0].passed).toBe(true)
  })
})

describe('каталог сценариев', () => {
  it('у каждого сценария есть определение, ожидание и свидетельство', () => {
    for (const item of SCENARIOS) {
      expect(item.definition, item.id).not.toBe('')
      expect(item.expect.length, item.id).toBeGreaterThan(0)
      // Сценарий без свидетельства проверяет только статус, а это ничего не
      // доказывает: COMPENSATED бывает и при неоткатанном резерве.
      expect(item.evidence?.length ?? 0, item.id).toBeGreaterThan(0)
    }
  })

  it('у каждого сценария уникальный идентификатор', () => {
    const ids = SCENARIOS.map((item) => item.id)

    expect(new Set(ids).size).toBe(ids.length)
  })

  it('сценарий, ломающий компенсацию, ломает и шаг', () => {
    // Откат без ломки шага не начинается, а правило остаётся: это ровно тот
    // сценарий, который однажды показывал ложный успех.
    for (const item of SCENARIOS) {
      const breaksCompensation = (item.rules ?? []).some((rule) => rule.kind === 'compensation')
      if (!breaksCompensation) continue
      const breaksStep = (item.rules ?? []).some((rule) => rule.kind === 'step')
      expect(breaksStep, `${item.id}: ломает компенсацию, но не шаг`).toBe(true)
    }
  })

  it('действие сценария объявлено до состояния, на котором сработает', () => {
    for (const item of SCENARIOS) {
      if (!item.action) continue
      expect(item.action.after.length, item.id).toBeGreaterThan(0)
    }
  })

  it('сценарий не останавливается раньше, чем доказывает', () => {
    // Наблюдаемое состояние заканчивает сценарий: страница показывает «ждёт
    // решения» и перестаёт следить. Если на этом состоянии не срабатывает
    // действие, ожидаемое состояние и свидетельства не проверяются никогда —
    // сценарий заявляет одно, а на экране видно другое. Именно так был устроен
    // «срок ожидания истёк»: он останавливался на паузе и не дожидался отката,
    // о котором и говорил в названии.
    for (const item of SCENARIOS) {
      const observed = item.observe ?? []
      if (observed.length === 0) continue
      const acted = item.action?.after ?? []
      const unchecked = observed.filter((value) => !acted.includes(value))
      expect(unchecked, `${item.id}: останавливается на ${unchecked.join(', ')}`).toEqual([])
    }
  })

  it('состояния по дороге не совпадают с ожидаемыми', () => {
    // Если состояние прохода стоит и в ожидаемых, сценарий объявит успех на
    // промежуточном шаге и покажет то, чего не доказывал.
    for (const item of SCENARIOS) {
      const overlap = (item.via ?? []).filter((value) => item.expect.includes(value))
      expect(overlap, item.id).toEqual([])
    }
  })
})