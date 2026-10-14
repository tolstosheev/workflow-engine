import { describe, expect, it } from 'vitest'

import type { StepRun } from '@/types/api'

import { dutyRatio, layoutSteps } from './timeline'

const run = (over: Partial<StepRun> & { id: string; started_at: string }): StepRun => ({
  step_name: 'reserve_inventory',
  attempt: 1,
  status: 'COMPLETED',
  output: null,
  error: null,
  compensated: false,
  finished_at: null,
  ...over,
})

const NOW = Date.parse('2026-03-01T10:00:40Z')

describe('раскладка прогона по времени', () => {
  it('отдаёт пусто, когда прогонов нет или они без времени', () => {
    expect(layoutSteps([], NOW)).toBeNull()
    expect(layoutSteps([run({ id: 'x', started_at: 'мусор' })], NOW)).toBeNull()
  })

  it('измеряет долю времени в шагах, а не длительность процесса', () => {
    // Главный тезис проекта в цифрах: шаг занял 30 мс, а процесс жил 40 с.
    // Показывать только длительности шагов значило бы скрыть главное.
    const layout = layoutSteps(
      [
        run({ id: 'a', started_at: '2026-03-01T10:00:00Z', finished_at: '2026-03-01T10:00:00.020Z' }),
        run({ id: 'b', started_at: '2026-03-01T10:00:39Z', finished_at: '2026-03-01T10:00:39.030Z' }),
      ],
      NOW,
    )

    expect(layout).not.toBeNull()
    expect(layout?.totalMs).toBe(39_030)
    expect(layout?.inStepsMs).toBe(50)
    expect(layout?.idleMs).toBe(38_980)
    expect(dutyRatio(layout!)).toBeLessThan(0.01)
  })

  it('достраивает незакрытый шаг до текущего момента', () => {
    const layout = layoutSteps(
      [run({ id: 'a', started_at: '2026-03-01T10:00:00Z', finished_at: null, status: 'RUNNING' })],
      NOW,
    )

    expect(layout?.rows[0].running).toBe(true)
    expect(layout?.rows[0].durationMs).toBe(40_000)
  })

  it('видит разрыв между шагами', () => {
    const layout = layoutSteps(
      [
        run({ id: 'a', started_at: '2026-03-01T10:00:00Z', finished_at: '2026-03-01T10:00:01Z' }),
        run({ id: 'b', started_at: '2026-03-01T10:00:31Z', finished_at: '2026-03-01T10:00:32Z' }),
      ],
      NOW,
    )

    expect(layout?.rows[0].gapMs).toBe(30_000)
    expect(layout?.rows[1].gapMs).toBe(0)
  })

  it('никогда не рисует полосу тоньше пикселя', () => {
    // Мгновенный шаг на часовом отрезке дал бы ширину в тысячные доли процента:
    // его было бы видно в данных, но не на экране.
    const layout = layoutSteps(
      [
        run({ id: 'a', started_at: '2026-03-01T10:00:00Z', finished_at: '2026-03-01T10:00:00.001Z' }),
        run({ id: 'b', started_at: '2026-03-01T10:59:59Z', finished_at: '2026-03-01T11:00:00Z' }),
      ],
      NOW,
    )

    for (const row of layout?.rows ?? []) {
      expect(row.width).toBeGreaterThanOrEqual(0.004)
    }
  })

  it('сортирует прогоны по времени начала, а не по порядку в ответе', () => {
    const layout = layoutSteps(
      [
        run({ id: 'later', started_at: '2026-03-01T10:00:10Z', finished_at: '2026-03-01T10:00:11Z' }),
        run({ id: 'earlier', started_at: '2026-03-01T10:00:00Z', finished_at: '2026-03-01T10:00:01Z' }),
      ],
      NOW,
    )

    expect(layout?.rows.map((row) => row.key)).toEqual(['earlier', 'later'])
  })

  it('не падает на прогоне, который кончился раньше начала', () => {
    // Такое возможно при переводе часов или разных источниках времени; лента
    // обязана его пропустить, а не уронить страницу.
    const layout = layoutSteps(
      [run({ id: 'a', started_at: '2026-03-01T10:00:10Z', finished_at: '2026-03-01T10:00:05Z' })],
      NOW,
    )

    expect(layout).toBeNull()
  })
})
