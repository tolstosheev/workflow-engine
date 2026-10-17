import { describe, expect, it } from 'vitest'

import type { StepDefinition } from '@/types/api'

import { stepDescriptions, stepResolver, stepTitle } from './steps'

const SCHEMA = {
  validate_cart: { action: 'a', description: 'Проверка корзины перед любым побочным эффектом' },
  charge_payment: { action: 'b', description: 'Списание средств — главный необратимый шаг' },
  plain: { action: 'c' },
  same: { action: 'd', description: 'same' },
} satisfies Record<string, StepDefinition>

describe('stepTitle', () => {
  it('показывает описание шага, а имя оставляет вторым текстом', () => {
    expect(stepTitle(SCHEMA, 'charge_payment')).toEqual({
      title: 'Списание средств — главный необратимый шаг',
      identifier: 'charge_payment',
    })
  })

  it('у шага без описания показывает имя и не выдумывает подпись', () => {
    expect(stepTitle(SCHEMA, 'plain')).toEqual({ title: 'plain', identifier: null })
  })

  it('описание, совпадающее с именем, не дублируется', () => {
    expect(stepTitle(SCHEMA, 'same')).toEqual({ title: 'same', identifier: null })
  })

  it('без схемы показывает имя, а не пустоту', () => {
    expect(stepTitle(undefined, 'charge_payment')).toEqual({
      title: 'charge_payment',
      identifier: null,
    })
  })

  it('шага нет — нет и заголовка', () => {
    expect(stepTitle(SCHEMA, null)).toBeNull()
    expect(stepTitle(SCHEMA, '')).toBeNull()
  })
})

describe('stepResolver', () => {
  it('подставляет описание в фразу журнала', () => {
    const resolve = stepResolver(SCHEMA)
    expect(resolve('charge_payment')).toBe('Списание средств — главный необратимый шаг')
  })

  it('неизвестный шаг остаётся именем, а не пропадает', () => {
    expect(stepResolver(SCHEMA)('from_the_future')).toBe('from_the_future')
  })
})

describe('stepDescriptions', () => {
  it('отдаёт только шаги, у которых описание отличается от имени', () => {
    expect(stepDescriptions(SCHEMA)).toEqual({
      validate_cart: 'Проверка корзины перед любым побочным эффектом',
      charge_payment: 'Списание средств — главный необратимый шаг',
    })
  })

  it('без схемы карта пустая, а не undefined', () => {
    expect(stepDescriptions(null)).toEqual({})
  })
})