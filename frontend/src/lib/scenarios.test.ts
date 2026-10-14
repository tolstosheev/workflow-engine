/**
 * Порядок вызовов в сценарии.
 *
 * Проверяется не «кнопка нажимается», а последовательность: правило ставится до
 * запуска и снимается после, а перед запуском снимается чужое. Оставленное
 * правило ломает следующий сценарий, и виноват окажется он — поэтому порядок и
 * есть содержание, а не оформление.
 */

import { describe, expect, it, vi } from 'vitest'

import { finishScenario, runScenario, SCENARIOS, type RunDeps, type ScenarioSpec } from '@/lib/scenarios'

function deps(over: Partial<Record<keyof RunDeps, unknown>> = {}): RunDeps {
  const base = {
    definitions: {
      list: vi.fn(async () => ({
        items: [
          { id: 'def-1', name: 'other', version: 1, is_active: true },
          { id: 'def-2', name: 'order_fulfillment', version: 1, is_active: true },
        ],
        total: 2,
        limit: 50,
        offset: 0,
      })),
    },
    chaos: { clear: vi.fn(async () => ({ active: [], message: '' })), failStep: vi.fn(async () => ({})), failCompensation: vi.fn(async () => ({})) },
    instances: { start: vi.fn(async () => ({ id: 'inst-1' })) },
  }
  return { ...base, ...over } as RunDeps
}

const happy = SCENARIOS.find((item) => item.id === 'happy') as ScenarioSpec
const stepFail = SCENARIOS.find((item) => item.id === 'payment-fails') as ScenarioSpec
const compFail = SCENARIOS.find((item) => item.id === 'compensation-fails') as ScenarioSpec

describe('сценарии', () => {
  it('берёт определение по имени, а не первое активное', async () => {
    const d = deps()
    await runScenario(happy, d)
    // Активных определений на стенде больше одного, и «первое» молча проверило
    // бы не то, что показано на странице.
    expect(d.instances.start).toHaveBeenCalledWith('def-2', { amount: '1499.90' })
  })

  it('объясняет, когда демонстрационного определения нет', async () => {
    const d = deps({
      definitions: { list: vi.fn(async () => ({ items: [], total: 0, limit: 50, offset: 0 })) },
    })
    await expect(runScenario(happy, d)).rejects.toThrow(/order_fulfillment/)
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

  it('ставит правило на шаг до запуска процесса', async () => {
    const d = deps()
    await runScenario(stepFail, d)

    expect(d.chaos.failStep).toHaveBeenCalledWith(
      expect.objectContaining({ step: 'charge_payment', mode: 'always' }),
    )
    expect(d.instances.start).toHaveBeenCalledTimes(1)
  })

  it('ломает откат по обработчику, а не по имени шага', async () => {
    const d = deps()
    await runScenario(compFail, d)

    // Шаг к этому моменту уже выполнен: правило по его имени сработало бы на
    // прямом шаге и не на откате.
    expect(d.chaos.failCompensation).toHaveBeenCalledWith(
      expect.objectContaining({ handler: 'app.demo.handlers:release_inventory' }),
    )
    expect(d.chaos.failStep).not.toHaveBeenCalled()
  })

  it('не ставит правило там, где оно не нужно', async () => {
    const d = deps()
    await runScenario(happy, d)

    expect(d.chaos.failStep).not.toHaveBeenCalled()
    expect(d.chaos.failCompensation).not.toHaveBeenCalled()
  })

  it('снимает правило, когда процесс пришёл в ожидаемое состояние', async () => {
    const d = deps()
    const matched = await finishScenario(
      d,
      stepFail,
      { status: 'COMPENSATED' } as never,
    )

    expect(matched).toBe(true)
    expect(d.chaos.clear).toHaveBeenCalled()
  })

  it('говорит, что ожидалось не то, и всё равно снимает правило', async () => {
    const d = deps()
    const matched = await finishScenario(d, stepFail, { status: 'COMPLETED' } as never)

    expect(matched).toBe(false)
    // Правило обязано сниматься даже при неудаче: оставленная «всегда» сломала бы
    // всё, что запустят дальше.
    expect(d.chaos.clear).toHaveBeenCalled()
  })

  it('не снимает правило у сценария без инъекции', async () => {
    const d = deps()
    await finishScenario(d, happy, { status: 'COMPLETED' } as never)

    expect(d.chaos.clear).not.toHaveBeenCalled()
  })
})