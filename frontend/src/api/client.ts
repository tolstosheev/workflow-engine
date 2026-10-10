/**
 * Клиент REST API и потока событий.
 *
 * Компоненты не знают URL: всё общение с бэкендом идёт через этот слой, и
 * контракт меняется в одном месте.
 */

import type {
  ActionResult,
  ChaosResult,
  DefinitionSummary,
  InstanceState,
  Paginated,
  TimelineEntry,
  ValidationResult,
  WorkflowDefinition,
  WorkflowEvent,
  WorkflowInstance,
  StepRun,
} from '@/types/api'

const BASE_URL = (import.meta.env.VITE_API_URL ?? '').replace(/\/$/, '')

/** Адрес используется относительным путём: в разработке запрос идёт через прокси Vite. */
const api = (path: string): string => `${BASE_URL}${path}`

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly detail?: string,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(api(path), {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })

  if (!response.ok) {
    // Текст ошибки бэкенда полезнее «Request failed»: по нему видно, какой
    // именно шаг или переход не прошёл валидацию.
    const body = await response.json().catch(() => null)
    const detail = typeof body?.detail === 'string' ? body.detail : undefined
    throw new ApiError(detail ?? `Запрос завершился ошибкой ${response.status}`, response.status, detail)
  }

  if (response.status === 204) {
    return undefined as T
  }
  return (await response.json()) as T
}

// --- Определения ----------------------------------------------------------

export const definitions = {
  list: (params: { limit?: number; offset?: number; is_active?: boolean } = {}) => {
    const query = new URLSearchParams()
    if (params.limit !== undefined) query.set('limit', String(params.limit))
    if (params.offset !== undefined) query.set('offset', String(params.offset))
    if (params.is_active !== undefined) query.set('is_active', String(params.is_active))
    const suffix = query.toString() ? `?${query}` : ''
    return request<Paginated<DefinitionSummary>>(`/api/definitions${suffix}`)
  },

  get: (id: string) => request<WorkflowDefinition>(`/api/definitions/${id}`),

  create: (body: unknown) =>
    request<WorkflowDefinition>('/api/definitions', {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  update: (id: string, body: unknown) =>
    request<WorkflowDefinition>(`/api/definitions/${id}`, {
      method: 'PUT',
      body: JSON.stringify(body),
    }),

  validate: (id: string) => request<ValidationResult>(`/api/definitions/${id}/validate`, { method: 'POST' }),

  /**
   * Проверка текста без сохранения.
   *
   * Отдельная ручка: сохранённое определение проверяется по `id`, а здесь
   * дефинишена ещё нет. Без неё единственный способ увидеть ошибки до
   * сохранения — сохранить и получить отказ, что уводит вопрос не туда.
   */
  validatePayload: (body: unknown) =>
    request<ValidationResult>('/api/definitions/validate', { method: 'POST', body: JSON.stringify(body) }),

  activate: (id: string) =>
    request<WorkflowDefinition>(`/api/definitions/${id}/activate`, { method: 'POST' }),

  deactivate: (id: string) =>
    request<WorkflowDefinition>(`/api/definitions/${id}/deactivate`, { method: 'POST' }),
}

// --- Инстансы -------------------------------------------------------------

export const instances = {
  list: (params: { limit?: number; offset?: number; status?: string; definition_id?: string } = {}) => {
    const query = new URLSearchParams()
    if (params.limit !== undefined) query.set('limit', String(params.limit))
    if (params.offset !== undefined) query.set('offset', String(params.offset))
    if (params.status) query.set('status', params.status)
    if (params.definition_id) query.set('definition_id', params.definition_id)
    const suffix = query.toString() ? `?${query}` : ''
    return request<Paginated<WorkflowInstance>>(`/api/instances${suffix}`)
  },

  get: (id: string) => request<WorkflowInstance>(`/api/instances/${id}`),

  events: (id: string, fromSeq = 0) =>
    request<{ items: WorkflowEvent[]; total: number }>(`/api/instances/${id}/events?from_seq=${fromSeq}`),

  steps: (id: string) => request<{ items: StepRun[] }>(`/api/instances/${id}/steps`),

  timeline: (id: string) => request<{ items: TimelineEntry[] }>(`/api/instances/${id}/timeline`),

  state: (id: string) => request<InstanceState>(`/api/instances/${id}/state`),

  start: (definitionId: string, input: Record<string, unknown>) =>
    request<WorkflowInstance>('/api/instances', {
      method: 'POST',
      body: JSON.stringify({ definition_id: definitionId, input }),
    }),

  cancel: (id: string) => request<ActionResult>(`/api/instances/${id}/cancel`, { method: 'POST' }),

  forceComplete: (id: string, reason: string) =>
    request<ActionResult>(`/api/instances/${id}/complete`, {
      method: 'POST',
      body: JSON.stringify({ reason }),
    }),

  retryCompensation: (id: string) =>
    request<ActionResult>(`/api/instances/${id}/compensation/retry`, { method: 'POST' }),
}

// --- Инъекция отказов ------------------------------------------------------

export const chaos = {
  list: () => request<ChaosResult>('/api/chaos'),

  failStep: (body: { instance_id?: string | null; step?: string | null; mode: string; error?: string }) =>
    request<ChaosResult>('/api/chaos/fail-step', { method: 'POST', body: JSON.stringify(body) }),

  failService: (service: string) =>
    request<ChaosResult>('/api/chaos/fail-service', {
      method: 'POST',
      body: JSON.stringify({ service }),
    }),

  stopLease: (instanceId: string, durationSeconds: number) =>
    request<ChaosResult>('/api/chaos/stop-lease', {
      method: 'POST',
      body: JSON.stringify({ instance_id: instanceId, duration_seconds: durationSeconds }),
    }),

  resumeLease: (instanceId: string) =>
    request<ChaosResult>('/api/chaos/resume-lease', {
      method: 'POST',
      body: JSON.stringify({ instance_id: instanceId }),
    }),

  clear: () => request<ChaosResult>('/api/chaos', { method: 'DELETE' }),
}

export const service = {
  health: () => request<{ status: string }>('/health'),
  metrics: async () => {
    const response = await fetch(api('/metrics'))
    if (!response.ok) {
      // Без этой проверки парсер вернёт текст ошибки как обычное содержимое,
      // а parseMetrics не найдёт обрацев и вернёт пустой список: упавшая панель снову
      // показывала бы «метрики ещё не собрани» — разнешение само о себе.
      throw new ApiError(`Метрики недоступны: ответ ${response.status}`, response.status)
    }
    return response.text()
  },
}