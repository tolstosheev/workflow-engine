// @vitest-environment jsdom
/**
 * Отчёт о процессе.
 *
 * Проверяется состав отчёта, а не вид: отчёт читают после того, как стенд
 * остановили, и там нет ни кнопок, ни цветов. Поэтому каждая проверка спрашивает
 * «есть ли в тексте то, без чего разбор невозможен».
 */

import { describe, expect, it } from 'vitest'

import { buildInstanceReport, reportFileName } from '@/lib/report'
import type { InstanceState, StepRun, TimelineEntry, WorkflowEvent, WorkflowInstance } from '@/types/api'

const instance: WorkflowInstance = {
  id: 'abcdef12-3456-7890-abcd-ef1234567890',
  definition: { id: 'd-1', name: 'order_fulfillment', version: 2 },
  status: 'COMPENSATED',
  current_step: null,
  input: { amount: '1499.90' },
  variables: {},
  error: null,
  lease_owner: null,
  lease_expires_at: null,
  heartbeat_at: null,
  archived_at: null,
  created_at: '2026-03-01T12:00:00Z',
  updated_at: '2026-03-01T12:05:00Z',
}

const steps: StepRun[] = [
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
  {
    id: 's-2',
    step_name: 'charge_payment',
    attempt: 1,
    status: 'FAILED',
    output: null,
    error: 'отказ платёжного сервиса',
    compensated: true,
    started_at: '2026-03-01T12:00:10Z',
    finished_at: '2026-03-01T12:00:12Z',
  },
]

const events: WorkflowEvent[] = [
  { seq: 1, type: 'INSTANCE_CREATED', payload: {}, created_at: '2026-03-01T12:00:00Z' },
  { seq: 2, type: 'STEP_COMPLETED', payload: { step: 'reserve_inventory' }, created_at: '2026-03-01T12:00:07Z' },
]

function timelineEntry(over: Partial<TimelineEntry> = {}): TimelineEntry {
  return {
    kind: 'step',
    step_name: 'reserve_inventory',
    started_at: '2026-03-01T12:00:05Z',
    finished_at: '2026-03-01T12:00:07Z',
    ...over,
  } as TimelineEntry
}

const state: InstanceState = {
  status: 'COMPENSATED',
  current_step: null,
  completed_steps: ['validate_cart', 'reserve_inventory'],
  variables: {},
} as unknown as InstanceState

describe('отчёт о процессе', () => {
  it('называет процесс, состояние и определение', () => {
    const text = buildInstanceReport({ instance, state, steps, timeline: [timelineEntry()], events })

    expect(text).toContain('order_fulfillment')
    expect(text).toContain('Откатан')
    expect(text).toContain(abcdef12())
  })

  function abcdef12(): string {
    return 'abcdef12'
  }

  it('перечисляет шаги с попытками и длительностью', () => {
    const text = buildInstanceReport({ instance, state, steps, timeline: [timelineEntry()], events })

    expect(text).toMatch(/reserve_inventory \| 1 \| COMPLETED/)
    expect(text).toMatch(/charge_payment \| 1 \| FAILED/)
    expect(text).toContain('2.0 с')
  })

  it('пишет журнал со сносками событий', () => {
    const text = buildInstanceReport({ instance, state, steps, timeline: [timelineEntry()], events })

    expect(text).toContain('1. ')
    expect(text).toContain('INSTANCE_CREATED')
    // payload сохраняется: без него в записи нет ни шага, ни попытки.
    expect(text).toContain('"step":"reserve_inventory"')
  })

  it('отмечает расхождение колонки и разбора', () => {
    // Расхождение — это признак, который разбирают в первую очередь, и прятать
    // его в отчёте нельзя.
    const text = buildInstanceReport({
      instance,
      state: { ...state, status: 'RUNNING' } as InstanceState,
      steps,
      timeline: [timelineEntry()],
      events,
    })

    expect(text).toContain('РАСХОДИТСЯ с колонкой')
  })

  it('не выдумывает время, если его нет', () => {
    const text = buildInstanceReport({
      instance: { ...instance, error: null },
      state: null,
      steps: [],
      timeline: [],
      events: [],
    })

    expect(text).toContain('Шагов не было.')
    expect(text).toContain('Событий нет.')
    expect(text).not.toContain('1970')
  })

  it('говорит, какая доля времени ушла в шаги', () => {
    const text = buildInstanceReport({ instance, state, steps, timeline: [timelineEntry()], events })

    // Пробелы между шагами — половина интересного, и в таблице они не видны.
    expect(text).toMatch(/В шагах прошло .* из .* — \d+ % времени/)
  })

  it('отмечает процесс, убранный в архив', async () => {
    // Отчёт читают спустя время; без этой строки процесс выглядел бы обычным,
    // и вывод «его убрали из списка» пришлось бы догадываться.
    const text = buildInstanceReport({
      instance: { ...instance, archived_at: '2026-03-01T12:06:00Z' },
      state,
      steps,
      timeline: [timelineEntry()],
      events,
    })

    expect(text).toContain('Убран из списка')
  })

  it('пишет прочерк там, где времени нет, а не ноль', () => {
    const text = buildInstanceReport({
      instance,
      state,
      steps: [
        {
          ...steps[0],
          finished_at: null,
        },
      ],
      timeline: [],
      events,
    })

    // Ноль миллисекунд означал бы «шаг выполнился мгновенно», а на деле шаг ещё
    // шёл либо время неизвестно.
    expect(text).toContain('| — |')
    expect(text).toContain('Данных о ходе времени нет.')
  })

  it('не печатает отрицательную длительность', () => {
    const text = buildInstanceReport({
      instance,
      state,
      steps: [{ ...steps[0], finished_at: '2026-03-01T11:00:00Z' }],
      timeline: [timelineEntry()],
      events,
    })

    // Отрицательный процент в отчёте — это враньё, а не число.
    expect(text).not.toMatch(/-?\d+ % времени/)
    expect(text).not.toContain('из 7.0 с — -')
  })

  it('переводит минуты в минуты, а не в тысячи секунд', () => {
    const text = buildInstanceReport({
      instance: { ...instance, updated_at: '2026-03-01T12:20:00Z' },
      state,
      steps,
      timeline: [timelineEntry({ finished_at: '2026-03-01T12:20:00Z' })],
      events,
    })

    expect(text).toContain('20 мин')
  })

  it('называет файл по процессу', () => {
    expect(reportFileName(instance)).toBe('отчёт-order_fulfillment-abcdef12.md')
  })
})