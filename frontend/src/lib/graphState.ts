/**
 * Состояние шага по факту выполнения.
 *
 * Отдельный модуль, а не часть компонента: это логика, которую имеет смысл
 * проверять тестами и переиспользовать вне схемы.
 *
 * Различие принципиально: у процесса статус `COMPENSATED`, и у отдельного
 * шага — тоже, но означают они разное. Первое — «откат прошёл», второе —
 * «этот шаг уже откатан». Поэтому состояние узла строится из прогона шага,
 * а не из статуса инстанса.
 */

import type { InstanceState, StepRun } from '@/types/api'

export type NodeState = 'pending' | 'running' | 'done' | 'failed' | 'compensated' | 'compensating'

export function nodeStates(
  steps: StepRun[],
  instance: InstanceState | null,
): Map<string, NodeState> {
  const states = new Map<string, NodeState>()

  for (const step of steps) {
    if (step.status === 'FAILED') states.set(step.step_name, 'failed')
    else if (step.compensated) states.set(step.step_name, 'compensated')
    else if (step.status === 'COMPLETED') states.set(step.step_name, 'done')
    else if (step.status === 'RUNNING') states.set(step.step_name, 'running')
  }

  const current = instance?.current_step
  // Текущий шаг важнее записанного прогона: он уже начат, даже если событие
  // о начале ещё не дошло до интерфейса.
  if (current && states.get(current) !== 'failed' && states.get(current) !== 'compensated') {
    states.set(current, 'running')
  }

  if (instance?.status === 'COMPENSATING' || instance?.status === 'CANCELLING') {
    for (const name of instance.completed_steps) {
      if (states.get(name) === 'done') states.set(name, 'compensating')
    }
  }

  return states
}