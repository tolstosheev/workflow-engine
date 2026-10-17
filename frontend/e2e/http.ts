import type { APIRequestContext } from '@playwright/test'

/**
 * Обращение к движку для подготовки состояния.
 *
 * Повтор нужен по одной причине: Playwright переиспользует соединение, а
 * uvicorn закрывает его по таймауту. В тот момент, когда следующий запрос уже
 * отправлен, сокет обрывается, и проверка падает с `socket hang up
 * (ECONNRESET)` на ровном месте — движок при этом здоров и отвечает. На
 * стенде это случилось один раз из шести выполнений шага, и упало не то, что
 * проверялось, а подготовка состояния.
 *
 * Повторяются только идемпотентные обращения: повтор запроса, создающего
 * процесс, создал бы два процесса, и проверка проверяла бы не тот.
 */

const RETRYABLE_CODES = new Set(['ECONNRESET', 'ECONNABORTED', 'EPIPE', 'ETIMEDOUT'])

function isRetryable(method: string): boolean {
  return method === 'GET' || method === 'HEAD' || method === 'DELETE'
}

function isSocketError(cause: unknown): boolean {
  const code = (cause as { code?: string } | null)?.code
  return typeof code === 'string' && RETRYABLE_CODES.has(code)
}

export async function fetchWithRetry(
  request: APIRequestContext,
  url: string,
  method: 'get' | 'post' | 'delete',
  data?: unknown,
): Promise<{ status: number; json: Record<string, unknown> }> {
  const upper = method.toUpperCase()
  const attempts = isRetryable(upper) ? 2 : 1

  let lastError: unknown
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    try {
      const response = await request.fetch(url, {
        method: upper,
        data: data === undefined ? undefined : JSON.stringify(data),
        headers: { 'Content-Type': 'application/json' },
      })
      const text = await response.text()
      return {
        status: response.status(),
        json: text ? (JSON.parse(text) as Record<string, unknown>) : {},
      }
    } catch (cause) {
      lastError = cause
      if (attempt === attempts || !isSocketError(cause)) throw cause
    }
  }
  throw lastError
}