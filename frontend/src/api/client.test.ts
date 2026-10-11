/**
 * Клиент REST: договорённости, о которых стоит помнить.
 *
 * Ценность тестов здесь не в «вызвался запрос», а в трёх местах, где раньше
 * ломалась видимость: разбор ошибки бэкенда, сборка query-параметров и
 * отправка тела. Ошибка без разбора показывала пользователю «Request failed».
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError, definitions, instances } from '@/api/client'

interface Call {
  url: string
  init: RequestInit | undefined
}

let calls: Call[] = []

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as unknown as Response
}

beforeEach(() => {
  calls = []
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    calls.push({ url, init })
    return jsonResponse({})
  })
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('разбор ошибок', () => {
  it('показывает текст бэкенда, а не обобщённое сообщение', async () => {
    vi.stubGlobal('fetch', async () =>
      jsonResponse({ detail: 'определение не найдено' }, 404),
    )

    await expect(definitions.get('missing')).rejects.toThrow('определение не найдено')
  })

  it('сохраняет код ответа и поле detail', async () => {
    vi.stubGlobal('fetch', async () => jsonResponse({ detail: 'занято' }, 409))

    const error = await definitions.get('x').catch((cause: unknown) => cause)

    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).status).toBe(409)
    expect((error as ApiError).detail).toBe('занято')
  })

  it('не падает, если тело ошибки не разбирается', async () => {
    // Ответ прокси или редиректа может оказаться не JSON: раньше попытка
    // разбора глушила исходную ошибку и показывала «не удалось загрузить».
    vi.stubGlobal('fetch', async () => ({
      ok: false,
      status: 502,
      json: async () => {
        throw new SyntaxError('Unexpected token <')
      },
    }) as unknown as Response)

    const error = await definitions.get('x').catch((cause: unknown) => cause)

    expect((error as ApiError).status).toBe(502)
    expect((error as ApiError).message).toContain('502')
  })

  it('подставляет свой текст, если detail не строка', async () => {
    vi.stubGlobal('fetch', async () => jsonResponse({ detail: [{ loc: 'body' }] }, 422))

    const error = await definitions.get('x').catch((cause: unknown) => cause)

    expect((error as ApiError).detail).toBeUndefined()
    expect((error as ApiError).message).toContain('422')
  })
})

describe('сборка запроса', () => {
  it('не добавляет пустые query-параметры', async () => {
    await definitions.list()

    expect(calls[0].url).not.toContain('?')
  })

  it('передаёт заданные параметры списка', async () => {
    await definitions.list({ limit: 10, offset: 20, is_active: false })

    const query = calls[0].url.split('?')[1]
    expect(new URLSearchParams(query).get('limit')).toBe('10')
    expect(new URLSearchParams(query).get('offset')).toBe('20')
    expect(new URLSearchParams(query).get('is_active')).toBe('false')
  })

  it('отправляет тело запроса как JSON', async () => {
    await definitions.update('abc', { definition: { start: 'a' }, is_active: true })

    expect(calls[0].init?.method).toBe('PUT')
    expect(calls[0].init?.body).toBe('{"definition":{"start":"a"},"is_active":true}')
    expect(calls[0].init?.headers).toMatchObject({ 'Content-Type': 'application/json' })
  })

  it('возвращает пустое значение на ответе без тела', async () => {
    vi.stubGlobal('fetch', async () => jsonResponse(undefined, 204))

    await expect(instances.cancel('abc')).resolves.toBeUndefined()
  })
})