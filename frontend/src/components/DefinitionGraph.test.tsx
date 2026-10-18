// @vitest-environment jsdom
/**
 * Граф шагов: объявленная структура и фактическое состояние рядом.
 *
 * Ценность схемы в том, что на ней видно не только «как объявлено», но и «что
 * с этим произошло». Значит проверяются две вещи: раскладка строится по
 * объявлению, а цвет узла берётся из выполнения шага, а не из статуса процесса —
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
    cancel_requested: false,
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

  it('не перекрашивает компенсированный шаг в выполняющийся', () => {
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

    expect(screen.getByLabelText('Шаг reserve_inventory: компенсирован')).toBeTruthy()
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
    expect(screen.getByText('компенсирован')).toBeTruthy()
    expect(screen.getByText('есть компенсация')).toBeTruthy()
  })

  it('помечает компенсируемый шаг точкой на рамке, а не стрелкой в себя', () => {
    const { container } = render(<DefinitionGraph definition={DEFINITION} steps={[]} />)

    // Стрелка «шаг → тот же шаг» означала повтор шага, то есть цикл, и на
    // вопрос «почему шаг указывает сам на себя» рисунок ответа не давал.
    expect(container.querySelectorAll('[marker-end="url(#arrow-warning)"]')).toHaveLength(0)
    expect(container.querySelectorAll('path[stroke-dasharray="3 3"]')).toHaveLength(0)

    const markers = container.querySelectorAll('[data-compensation-marker]')
    expect(markers).toHaveLength(2)
    const hints = [...container.querySelectorAll('title')].map((node) => node.textContent)
    expect(hints).toContain('Компенсация: app.demo.handlers:refund_payment')
    expect(hints).toContain('Компенсация: app.demo.handlers:release_inventory')
  })

  it('оставляет шаг без компенсации без пометки', () => {
    render(<DefinitionGraph definition={DEFINITION} steps={[]} />)

    // Пометка обязана означать «компенсируется», а не «есть любая информация»:
    // в определении четыре шага и компенсация объявлена у двух.
    const withoutMarker = screen.getByLabelText(/^Шаг finish:/)
    expect(withoutMarker.querySelectorAll('[data-compensation-marker]')).toHaveLength(0)
  })

  it('объявляет компенсацию в подписи узла для скринридера', () => {
    // Иначе точка остаётся безымянным кружком, и карта снова становится
    // недоступной с клавиатуры.
    render(<DefinitionGraph definition={DEFINITION} steps={[]} />)

    expect(screen.getByLabelText(/^Шаг charge:.*есть компенсация$/)).toBeTruthy()
    expect(screen.getByLabelText(/^Шаг finish:/).getAttribute('aria-label')).not.toContain('компенсация')
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

  it('открывает шаг с клавиатуры — и Enter, и пробелом', () => {
    // Клавиша без обработчика оставляет карту доступной только зрением: узел
    // получает фокус, но выбрать его нельзя.
    const chosen: string[] = []
    render(
      <DefinitionGraph
        definition={DEFINITION}
        steps={[]}
        onSelect={(name: string) => chosen.push(name)}
      />,
    )
    const node = screen.getByRole('button', { name: /charge/ })

    fireEvent.keyDown(node, { key: 'Enter' })
    fireEvent.keyDown(node, { key: ' ' })

    expect(chosen).toEqual(['charge', 'charge'])
  })

  it('не отвечает на остальные клавиши', () => {
    const chosen: string[] = []
    render(
      <DefinitionGraph
        definition={DEFINITION}
        steps={[]}
        onSelect={(name: string) => chosen.push(name)}
      />,
    )

    fireEvent.keyDown(screen.getByRole('button', { name: /finish/ }), { key: 'Escape' })

    expect(chosen).toEqual([])
  })

  it('закрывает подпись, когда фокус и мышь ушли', () => {
    // Подпись, которая не закрывается, висит поверх следующего шага и читается
    // как его свойства: уводишь взгляд, а на экране всё то же самое.
    render(<DefinitionGraph definition={DEFINITION} steps={[]} />)
    focusNode('charge')
    expect(screen.getByText('app.demo.handlers:charge_payment')).toBeTruthy()

    fireEvent.blur(screen.getByLabelText(/^Шаг charge/))
    expect(screen.queryByText('app.demo.handlers:charge_payment')).toBeNull()

    fireEvent.mouseEnter(screen.getByLabelText(/^Шаг finish/))
    expect(screen.getByText('app.demo.handlers:confirm_delivery')).toBeTruthy()

    fireEvent.mouseLeave(screen.getByLabelText(/^Шаг finish/))
    expect(screen.queryByText('app.demo.handlers:confirm_delivery')).toBeNull()
  })
})