// @vitest-environment jsdom
/**
 * Граф шагов: объявленная структура и фактическое состояние рядом.
 *
 * Ценность схемы в том, что на ней видно не только «как объявлено», но и «что
 * с этим произошло». Значит проверяются две вещи: раскладка строится по
 * объявлению, а цвет узла берётся из прогона шага, а не из статуса процесса —
 * `COMPENSATED` у процесса и у шага означают разное.
 */

import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { DefinitionGraph } from '@/components/DefinitionGraph'
import { nodeStates } from '@/lib/graphState'
import type { InstanceState, StepRun, WorkflowDefinitionSchema } from '@/types/api'

const DEFINITION: WorkflowDefinitionSchema = {
  start: 'validate_cart',
  steps: {
    validate_cart: {
      action: 'app.demo.handlers:validate_cart',
      compensation: 'app.demo.handlers:release_inventory',
      next: 'reserve_inventory',
    },
    reserve_inventory: { action: 'app.demo.handlers:reserve_inventory', next: 'charge' },
    charge: {
      action: 'app.demo.handlers:charge_payment',
      compensation: 'app.demo.handlers:refund_payment',
      retry: { max_attempts: 3, backoff_seconds: 1, backoff_multiplier: 2 },
      wait_before_seconds: 30,
      next: { on_success: 'finish', on_failure: 'reserve_inventory' },
    },
    finish: { action: 'app.demo.handlers:confirm_delivery', loop: true, max_iterations: 3 },
  },
} as unknown as WorkflowDefinitionSchema

function step(name: string, over: Partial<StepRun> = {}): StepRun {
  return {
    id: `r-${name}`,
    step_name: name,
    attempt: 1,
    status: 'COMPLETED',
    output: null,
    error: null,
    compensated: false,
    started_at: '2026-03-01T10:00:00Z',
    finished_at: '2026-03-01T10:00:01Z',
    ...over,
  } as unknown as StepRun
}

function state(over: Partial<InstanceState> = {}): InstanceState {
  return {
    status: 'RUNNING',
    current_step: 'charge',
    variables: {},
    completed_steps: ['validate_cart', 'reserve_inventory'],
    pending_compensations: [],
    is_terminal: false,
    step_iterations: {},
    ...over,
  } as unknown as InstanceState
}

afterEach(cleanup)

describe('состояние узла', () => {
  it('различает выполненный шаг и шаг, который уже откатили', () => {
    const states = nodeStates(
      [step('validate_cart'), step('reserve_inventory', { compensated: true })],
      null,
    )

    // `COMPENSATED` у процесса и у шага — разные вещи, и цвет узла обязан
    // показывать второе, иначе откат выглядел бы как успех.
    expect(states.get('validate_cart')).toBe('done')
    expect(states.get('reserve_inventory')).toBe('compensated')
  })

  it('показывает текущий шаг как выполняющийся, даже без события', () => {
    // Событие о начале может ещё не дойти до интерфейса, а шаг уже идёт.
    const states = nodeStates([], state({ current_step: 'charge' }))

    expect(states.get('charge')).toBe('running')
  })

  it('не перекрашивает упавший шаг в выполняющийся', () => {
    const states = nodeStates([step('charge', { status: 'FAILED' })], state({ current_step: 'charge' }))

    expect(states.get('charge')).toBe('failed')
  })

  it('не перекрашивает откатанный шаг в выполняющийся', () => {
    const states = nodeStates(
      [step('charge', { compensated: true })],
      state({ current_step: 'charge', status: 'COMPENSATING' }),
    )

    expect(states.get('charge')).toBe('compensated')
  })

  it('помечает выполненные шаги как откатываемые во время отката', () => {
    const states = nodeStates(
      [step('validate_cart'), step('reserve_inventory')],
      state({ status: 'COMPENSATING', current_step: null }),
    )

    expect(states.get('validate_cart')).toBe('compensating')
    expect(states.get('reserve_inventory')).toBe('compensating')
  })

  it('помечает так же и при отмене', () => {
    const states = nodeStates([step('validate_cart')], state({ status: 'CANCELLING' }))

    expect(states.get('validate_cart')).toBe('compensating')
  })

  it('не помечает выполненные шаги вне отката', () => {
    const states = nodeStates([step('validate_cart')], state())

    expect(states.get('validate_cart')).toBe('done')
  })
})

describe('схема шагов', () => {
  it('рисует узел на каждый объявленный шаг', () => {
    const { container } = render(<DefinitionGraph definition={DEFINITION} steps={[]} />)

    // Схема без шагов — это не схема: объявление не показано, и проверить
    // структуру нечем.
    expect(container.querySelectorAll('g').length).toBeGreaterThanOrEqual(4)
  })

  it('говорит прямо, когда в определении нет шагов', () => {
    render(
      <DefinitionGraph
        definition={{ start: 'a', steps: {} } as unknown as WorkflowDefinitionSchema}
        steps={[]}
      />,
    )

    expect(screen.getByText('В определении нет шагов')).toBeTruthy()
  })

  it('доступна как изображение с названием', () => {
    render(<DefinitionGraph definition={DEFINITION} steps={[]} />)

    // SVG со схемой читается скринридером как набор безымянных элементов;
    // роль и название дают хоть какое-то представление.
    expect(screen.getByRole('img', { name: /Граф шагов/ })).toBeTruthy()
  })

  it('называет узел состоянием, а не именем', () => {
    render(
      <DefinitionGraph
        definition={DEFINITION}
        steps={[step('reserve_inventory', { compensated: true })]}
        instance={state()}
      />,
    )

    expect(screen.getByLabelText('Шаг reserve_inventory: откатан')).toBeTruthy()
  })

  /** Узел доступен по наведению и по фокусу: подпись шага обязана открываться обоими. */
  function focusNode(name: string): void {
    fireEvent.mouseEnter(screen.getByLabelText(new RegExp(`^Шаг ${name}`)))
  }

  function keyboardFocusNode(name: string): void {
    fireEvent.focus(screen.getByLabelText(new RegExp(`^Шаг ${name}`)))
  }

  it('по наведению показывает действие, компенсацию и правила шага', () => {
    render(<DefinitionGraph definition={DEFINITION} steps={[]} />)

    focusNode('charge')

    expect(screen.getByText('app.demo.handlers:charge_payment')).toBeTruthy()
    expect(screen.getByText('app.demo.handlers:refund_payment')).toBeTruthy()
    expect(screen.getByText('3 попыток')).toBeTruthy()
    expect(screen.getByText('30 с')).toBeTruthy()
  })

  it('по фокусу с клавиатуры показывает то же самое', () => {
    // Подпись открывалась только по наведению: человек с клавиатуры видел
    // голые прямоугольники без действия, компенсации и правил повтора, а
    // состояние шага — единственное, ради чего карта и рисуется.
    render(<DefinitionGraph definition={DEFINITION} steps={[]} />)

    keyboardFocusNode('charge')

    expect(screen.getByText('app.demo.handlers:charge_payment')).toBeTruthy()
    expect(screen.getByText('app.demo.handlers:refund_payment')).toBeTruthy()
  })

  it('делает узлы достижимыми с клавиатуры без выбора', () => {
    // Раньше узел получал фокус только вместе с обработчиком выбора, и без
    // него карта целиком была недоступна с клавиатуры.
    render(<DefinitionGraph definition={DEFINITION} steps={[]} />)

    expect(screen.getByLabelText(/^Шаг finish/)).toHaveProperty('tabIndex', 0)
  })

  it('показывает прочерк там, где компенсации нет', () => {
    render(<DefinitionGraph definition={DEFINITION} steps={[]} />)

    focusNode('finish')

    // Прочерк и «по умолчанию» говорят разное: отсутствие компенсации — это
    // решение автора, а не нехватка данных.
    expect(screen.getByText('—')).toBeTruthy()
    expect(screen.getByText('по умолчанию')).toBeTruthy()
  })

  it('объясняет легенду цветов', () => {
    render(<DefinitionGraph definition={DEFINITION} steps={[]} />)

    expect(screen.getByText('обратное ребро цикла')).toBeTruthy()
    expect(screen.getByText('откатан')).toBeTruthy()
  })

  it('зовёт выбранный шаг, когда он передан', () => {
    const chosen: string[] = []
    render(
      <DefinitionGraph
        definition={DEFINITION}
        steps={[]}
        onSelect={(name: string) => chosen.push(name)}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: /validate_cart/ }))

    expect(chosen).toEqual(['validate_cart'])
  })
})