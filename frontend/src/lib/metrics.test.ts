/** Тесты разбора метрик — без БД и без интерфейса. */

import { describe, expect, it } from 'vitest'

import { labelsOf, parseMetrics, parseSample, sumBy, valueOf } from '@/lib/metrics'

const SAMPLE = `
# HELP workflow_instances Инстансы по состояниям
# TYPE workflow_instances gauge
workflow_instances{status="RUNNING"} 3
workflow_instances{status="COMPLETED"} 12
workflow_steps{step="charge_payment",result="failure"} 2
workflow_step_duration_seconds_bucket{step="reserve_inventory",le="0.05"} 300
workflow_lease_lost_total 4.0
`

describe('parseSample', () => {
  it('разбирает образец с метками', () => {
    expect(parseSample('workflow_instances{status="RUNNING"} 3')).toEqual({
      name: 'workflow_instances',
      labels: { status: 'RUNNING' },
      value: 3,
    })
  })

  it('разбирает образец без меток', () => {
    expect(parseSample('workflow_lease_lost_total 4.0')).toEqual({
      name: 'workflow_lease_lost_total',
      labels: {},
      value: 4,
    })
  })

  it('пропускает служебные строки', () => {
    expect(parseSample('# HELP workflow_instances описание')).toBeNull()
    expect(parseSample('')).toBeNull()
  })

  it('переживает запятую и кавычки внутри значения', () => {
    const sample = parseSample('wf{reason="a, b: c"} 1')

    expect(sample?.labels.reason).toBe('a, b: c')
  })
})

describe('parseMetrics', () => {
  it('собирает все образцы и пропускает комментарии', () => {
    const samples = parseMetrics(SAMPLE)

    expect(samples).toHaveLength(5)
    expect(samples.every((sample) => !sample.name.startsWith('#'))).toBe(true)
  })
})

describe('sumBy', () => {
  it('складывает образцы с указанной меткой', () => {
    const samples = parseMetrics(SAMPLE)

    expect(sumBy(samples, 'workflow_instances', ['status', 'RUNNING'])).toBe(3)
    expect(sumBy(samples, 'workflow_instances')).toBe(15)
  })

  it('возвращает ноль для отсутствующей метрики', () => {
    expect(sumBy(parseMetrics(SAMPLE), 'workflow_missing')).toBe(0)
  })
})

describe('valueOf', () => {
  it('ищет образец по набору меток', () => {
    const samples = parseMetrics(SAMPLE)

    expect(valueOf(samples, 'workflow_steps', { step: 'charge_payment', result: 'failure' })).toBe(2)
  })

  it('возвращает ноль, если образца нет', () => {
    expect(valueOf(parseMetrics(SAMPLE), 'workflow_steps', { step: 'ghost' })).toBe(0)
  })
})

describe('labelsOf', () => {
  it('собирает уникальные значения метки', () => {
    expect(labelsOf(parseMetrics(SAMPLE), 'workflow_instances', 'status')).toEqual([
      'COMPLETED',
      'RUNNING',
    ])
  })
})