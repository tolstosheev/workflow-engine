/** Тесты раскраски узлов графа по фактическому состоянию процесса. */

import { describe, expect, it } from 'vitest'

import { nodeStates } from '@/lib/graphState'
import type { InstanceState, StepRun } from '@/types/api'

function step(partial: Partial<StepRun> & { step_name: string }): StepRun {
  const base: StepRun = {
    id: partial.step_name,
    step_name: partial.step_name,
    attempt: 1,
    status: 'COMPLETED',
    output: {},
    error: null,
    compensated: false,
    started_at: '2026-01-01T00:00:00Z',
    finished_at: '2026-01-01T00:00:01Z',
  }
  return { ...base, ...partial }
}

function state(partial: Partial<InstanceState>): InstanceState {
  return {
    status: 'RUNNING',
    current_step: null,
    variables: {},
    completed_steps: [],
    pending_compensations: [],
    is_terminal: false,
    step_iterations: {},
    ...partial,
  }
}

describe('nodeStates', () => {
  it('красит не начатые шаги как ожидающие', () => {
    const states = nodeStates([], state({ current_step: 'reserve' }))

    expect(states.get('reserve')).toBe('running')
    expect(states.get('payment')).toBeUndefined()
  })

  it('различает выполненный и упавший шаг', () => {
    const states = nodeStates(
      [step({ step_name: 'reserve' }), step({ step_name: 'payment', status: 'FAILED' })],
      state({}),
    )

    expect(states.get('reserve')).toBe('done')
    expect(states.get('payment')).toBe('failed')
  })

  it('предпочитает факт выполнения шага статусу процесса', () => {
    // Статус процесса COMPENSATED не делает каждый шаг компенсированным: откат
    // мог пройти не по всем шагам, а список выполненных — общий.
    const states = nodeStates(
      [step({ step_name: 'reserve', compensated: true }), step({ step_name: 'payment' })],
      state({ status: 'COMPENSATED', completed_steps: ['reserve', 'payment'] }),
    )

    expect(states.get('reserve')).toBe('compensated')
    expect(states.get('payment')).toBe('done')
  })

  it('показывает идущий откат до его завершения', () => {
    const states = nodeStates(
      [step({ step_name: 'reserve' }), step({ step_name: 'payment' })],
      state({
        status: 'COMPENSATING',
        current_step: 'payment',
        completed_steps: ['reserve', 'payment'],
        pending_compensations: ['reserve'],
      }),
    )

    expect(states.get('reserve')).toBe('compensating')
    // Текущий шаг при откате — тот, чью компенсацию выполняют: он уже
    // выполняется, а не «просто выполнен».
    expect(states.get('payment')).toBe('running')
  })

  it('не объявляет провалившийся шаг текущим', () => {
    const states = nodeStates(
      [step({ step_name: 'payment', status: 'FAILED' })],
      state({ current_step: 'payment' }),
    )

    expect(states.get('payment')).toBe('failed')
  })
})