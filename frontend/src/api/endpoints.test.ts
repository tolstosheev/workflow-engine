/**
 * Адреса и методы API.
 *
 * Тест проходит по всем вызовам клиента: ошибка в адресе не ломает интерфейс
 * сразу — она всплывает на живом стенде, когда действие уже нажали. Здесь
 * каждый вызов сверяется с путём и методом, которые объявлены на бэкенде.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { chaos, definitions, instances, service } from '@/api/client'

interface Call {
  url: string
  init: RequestInit | undefined
}

let calls: Call[] = []

beforeEach(() => {
  calls = []
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    calls.push({ url, init })
    return { ok: true, status: 200, json: async () => ({}) } as unknown as Response
  })
})

afterEach(() => {
  vi.unstubAllGlobals()
})

function last(): Call {
  return calls[calls.length - 1]
}

describe('определения', () => {
  const cases: [string, () => Promise<unknown>, string, string][] = [
    ['список', () => definitions.list(), 'GET', '/api/definitions'],
    ['чтение', () => definitions.get('d1'), 'GET', '/api/definitions/d1'],
    ['создание', () => definitions.create({}), 'POST', '/api/definitions'],
    ['изменение', () => definitions.update('d1', {}), 'PUT', '/api/definitions/d1'],
    ['проверка', () => definitions.validate('d1'), 'POST', '/api/definitions/d1/validate'],
    [
      'проверка текста',
      () => definitions.validatePayload({ start: 'a', steps: {} }),
      'POST',
      '/api/definitions/validate',
    ],
    ['активация', () => definitions.activate('d1'), 'POST', '/api/definitions/d1/activate'],
    ['деактивация', () => definitions.deactivate('d1'), 'POST', '/api/definitions/d1/deactivate'],
  ]

  it.each(cases)('%s: %s %s', async (_name, call, method, path) => {
    await call()

    expect(last().url).toContain(path)
    expect(last().init?.method ?? 'GET').toBe(method)
  })
})

describe('инстансы', () => {
  const cases: [string, () => Promise<unknown>, string, string][] = [
    ['список', () => instances.list(), 'GET', '/api/instances'],
    ['чтение', () => instances.get('i1'), 'GET', '/api/instances/i1'],
    ['события', () => instances.events('i1'), 'GET', '/api/instances/i1/events'],
    ['шаги', () => instances.steps('i1'), 'GET', '/api/instances/i1/steps'],
    ['лента', () => instances.timeline('i1'), 'GET', '/api/instances/i1/timeline'],
    ['состояние', () => instances.state('i1'), 'GET', '/api/instances/i1/state'],
    ['запуск', () => instances.start('d1', {}), 'POST', '/api/instances'],
    ['отмена', () => instances.cancel('i1'), 'POST', '/api/instances/i1/cancel'],
    [
      'форсированное завершение',
      () => instances.forceComplete('i1', 'потому что'),
      'POST',
      '/api/instances/i1/complete',
    ],
    [
      'повтор отката',
      () => instances.retryCompensation('i1'),
      'POST',
      '/api/instances/i1/compensation/retry',
    ],
  ]

  it.each(cases)('%s: %s %s', async (_name, call, method, path) => {
    await call()

    expect(last().url).toContain(path)
    expect(last().init?.method ?? 'GET').toBe(method)
  })

  it('просит события с заданного места журнала', async () => {
    await instances.events('i1', 42)

    expect(last().url).toContain('from_seq=42')
  })

  it('передаёт входные данные при запуске', async () => {
    await instances.start('d1', { items: [{ sku: 'SKU-1', qty: 2 }] })

    // Запуск несёт идентификатор отдельно: без него база считала бы 404,
    // и понятия, которая предоставляется на стороне, показанной в списке определений.
    expect(JSON.parse(String(last().init?.body))).toEqual({
      definition_id: 'd1',
      input: { items: [{ sku: 'SKU-1', qty: 2 }] },
    })
  })
})

describe('инъекции отказов', () => {
  const cases: [string, () => Promise<unknown>, string, string][] = [
    ['список', () => chaos.list(), 'GET', '/api/chaos'],
    ['сломать шаг', () => chaos.failStep({ mode: 'once' }), 'POST', '/api/chaos/fail-step'],
    ['отключить сервис', () => chaos.failService('payments'), 'POST', '/api/chaos/fail-service'],
    [
      'остановить воркер',
      () => chaos.stopLease('i1', 40),
      'POST',
      '/api/chaos/stop-lease',
    ],
    ['возобновить', () => chaos.resumeLease('i1'), 'POST', '/api/chaos/resume-lease'],
    ['очистить', () => chaos.clear(), 'DELETE', '/api/chaos'],
  ]

  it.each(cases)('%s: %s %s', async (_name, call, method, path) => {
    await call()

    expect(last().url).toContain(path)
    expect(last().init?.method ?? 'GET').toBe(method)
  })

  it('передаёт инъекцию шага целиком', async () => {
    await chaos.failStep({ mode: 'always', step: 'charge_payment', instance_id: 'i1' })

    expect(JSON.parse(String(last().init?.body))).toEqual({
      mode: 'always',
      step: 'charge_payment',
      instance_id: 'i1',
    })
  })
})

describe('служебное', () => {
  it('проверяет здоровье без префикса api', async () => {
    await service.health()

    expect(last().url).toMatch(/\/health$/)
  })
})