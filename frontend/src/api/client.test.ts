/**
 * Клиент REST: договорённости, о которых стоит помнить.
 *
 * Ценность тестов здесь не в «вызвался запрос», а в трёх местах, где раньше
 * ломалась видимость: разбор ошибки бэкенда, сборка query-параметров и
 * отправка тела. Ошибка без разбора показывала пользователю «Request failed».
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError, definitions, instances, service, workers } from '@/api/client'

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

function textResponse(text: string, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    text: async () => text,
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

describe('архивирование и реестр воркеров', () => {
  it('просит список без архива по умолчанию', async () => {
    // Параметр уходит всегда: так список ведёт себя одинаково независимо от
    // настроек сервера, а не «как получится».
    await instances.list()
    expect(calls[0].url).toContain('archived=active')
  })

  it('просит именно архивные, когда их просят', async () => {
    await instances.list({ archived: 'archived', limit: 10 })
    expect(calls[0].url).toContain('archived=archived')
    expect(calls[0].url).toContain('limit=10')
  })

  it('шлит пустое тело при уборке без срока', async () => {
    await instances.archive()
    expect(calls[0].url).toContain('/api/instances/archive')
    expect(calls[0].init?.method).toBe('POST')
    expect(calls[0].init?.body).toBe('{}')
  })

  it('шлит срок, если он задан', async () => {
    await instances.archive('2026-03-01T12:00:00Z')
    expect(String(calls[0].init?.body)).toContain('2026-03-01T12:00:00Z')
  })

  it('возвращает процесс из архива отдельной ручкой', async () => {
    await instances.unarchive('inst-1')
    expect(calls[0].url).toContain('/api/instances/inst-1/unarchive')
    expect(calls[0].init?.method).toBe('POST')
  })

  it('читает реестр воркеров', async () => {
    await workers.list()
    expect(calls[0].url).toContain('/api/workers')
  })
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

describe('\u043c\u0435\u0442\u0440\u0438\u043a\u0438', () => {
  it('\u043e\u0442\u0434\u0430\u0451\u0442 \u0442\u0435\u043a\u0441\u0442 \u043e\u0442\u0432\u0435\u0442\u0430, \u0430 \u043d\u0435 \u0440\u0430\u0437\u0431\u0438\u0440\u0430\u0435\u043c\u044b\u0439 \u0442\u0435\u043b\u043e', async () => {
    vi.stubGlobal('fetch', async () => textResponse('workflow_up 1'))

    await expect(service.metrics()).resolves.toBe('workflow_up 1')
  })

  it('\u0441\u043e\u043e\u0431\u0449\u0430\u0435\u0442 \u043a\u043e\u0434, \u0430 \u043d\u0435 \u043f\u0440\u043e\u043f\u0443\u0441\u043a\u0430\u0435\u0442 \u043f\u0443\u0441\u0442\u043e\u0439 \u0441\u043f\u0438\u0441\u043e\u043a', async () => {
    // \u041e\u0448\u0438\u0431\u043a\u0430 \u0431\u0435\u0437 \u0440\u0430\u0437\u0431\u043e\u0440\u0430 \u043f\u043e\u043a\u0430\u0437\u044b\u0432\u0430\u0435\u0442 \u043f\u0443\u0441\u0442\u043e\u0439 \u0441\u043f\u0438\u0441\u043e\u043a \u0432\u043c\u0435\u0441\u0442\u043e \u0437\u0430\u043f\u0440\u043e\u0441\u0430:
    // \u044d\u0442\u043e \u0442\u043e \u0436\u0435, \u0447\u0442\u043e \u043f\u0440\u0438 \u0441\u0431\u043e\u0435 \u0434\u0430\u043d\u043d\u044b\u0445 \u043c\u0435\u0442\u0440\u0438\u043a \u0438\u043c\u0435\u043d\u043d\u043e \u043b\u0438 \u0441\u0447\u0438\u0442\u0430\u0435\u0442 \u043d\u0435\u0442.
    vi.stubGlobal('fetch', async () => textResponse('', 503))

    const error = await service.metrics().catch((cause: unknown) => cause)

    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).status).toBe(503)
    expect((error as ApiError).message).toContain('503')
  })
})
