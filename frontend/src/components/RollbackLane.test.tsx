// @vitest-environment jsdom
/**
 * Порядок отката: фактический, а не предполагаемый.
 *
 * Список строится из того, что шаги действительно выполнились, и идёт в
 * обратном порядке — иначе зависимости между шагами не обратимы. Различение
 * между «компенсирован», «откат» и «остаётся» не косметическое: по нему видно, что
 * уже сделано, а что ещё предстоит вернуть, и по одному этому списку видно,
 * где процесс встал.
 *
 * Обратный отсчёт тоже проверяется: без него процесс в ожидании выглядит так
 * же, как забытый, а это противоположные ситуации.
 */

import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { RollbackLane, TimerCountdown } from '@/components/RollbackLane'
import type { InstanceState, StepRun, TimelineEntry } from '@/types/api'

function state(over: Partial<InstanceState> = {}): InstanceState {
  return {
    status: 'RUNNING',
    current_step: 'charge',
    variables: {},
    completed_steps: ['validate_cart', 'reserve_inventory', 'charge_payment'],
    pending_compensations: ['reserve_inventory', 'validate_cart'],
    is_terminal: false,
    step_iterations: {},
    ...over,
  } as unknown as InstanceState
}

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

function markerOf(name: string): string {
  const row = screen.getByText(name).closest('li')!
  return row.querySelectorAll('span')[1].textContent ?? ''
}

afterEach(cleanup)

describe('порядок компенсации', () => {
  it('не показывается, когда выполненных шагов нет', () => {
    // Пустой процесс компенсировать нечего, и панель в виде заголовка без
    // содержимого только занимала бы место.
    render(<RollbackLane instance={state({ completed_steps: [] })} steps={[]} />)

    expect(screen.queryByText('Порядок компенсации')).toBeNull()
  })

  it('не показывается без состояния', () => {
    render(<RollbackLane instance={null} steps={[]} />)

    expect(screen.queryByText('Порядок компенсации')).toBeNull()
  })

  it('идёт в обратном порядке выполнения', () => {
    // Зависимости обратимы только так: откат последнего выполненного шага
    // первым, иначе возврат резерва шёл бы раньше списания.
    const { container } = render(<RollbackLane instance={state()} steps={[]} />)
    const names = Array.from(container.querySelectorAll('li')).map(
      (row) => row.querySelectorAll('span')[2].textContent,
    )

    expect(names).toEqual(['charge_payment', 'reserve_inventory', 'validate_cart'])
  })

  it('нумерует шаги в порядке отката, а не выполнения', () => {
    const { container } = render(<RollbackLane instance={state()} steps={[]} />)
    const numbers = Array.from(container.querySelectorAll('li')).map(
      (row) => row.querySelectorAll('span')[0].textContent,
    )

    // Нумерация идёт сверху вниз по порядку отката: первый откатываемый — «1».
    // Раньше она была правильной по значению, но список шёл в порядке
    // выполнения, и порядок приходилось читать снизу вверх.
    expect(numbers).toEqual(['1', '2', '3'])
  })

  it('различает компенсированный шаг и тот, который предстоит компенсировать', () => {
    render(
      <RollbackLane
        instance={state()}
        steps={[
          step('validate_cart', { compensated: true }),
          step('reserve_inventory'),
          step('charge_payment'),
        ]}
      />,
    )

    expect(markerOf('validate_cart')).toBe('компенсирован')
    expect(markerOf('reserve_inventory')).toBe('компенсация')
  })

  it('помечает шаг, который останется на месте', () => {
    // Шаг без заявленной компенсации компенсироваться не будет: показывать его
    // в списке к откату значило бы обещать возврат, которого не будет.
    render(
      <RollbackLane
        instance={state({ pending_compensations: ['validate_cart'] })}
        steps={[step('charge_payment')]}
      />,
    )

    expect(markerOf('charge_payment')).toBe('остаётся')
  })

  it('говорит, что у упавшего шага компенсации нет', () => {
    render(
      <RollbackLane
        instance={state({ pending_compensations: ['validate_cart', 'reserve_inventory'] })}
        steps={[step('charge_payment', { status: 'FAILED' })]}
      />,
    )

    expect(screen.getByText('упал, компенсации нет')).toBeTruthy()
  })

  it('называет текущее состояние, когда откат идёт', () => {
    render(<RollbackLane instance={state({ status: 'COMPENSATING' })} steps={[]} />)

    expect(screen.getByText(/Сейчас: Компенсация/)).toBeTruthy()
  })
})

describe('обратный отсчёт ожидания', () => {
  function timer(over: Partial<TimelineEntry> = {}): TimelineEntry {
    return {
      kind: 'event',
      seq: 1,
      type: 'TIMER_SCHEDULED',
      payload: { due_at: new Date(Date.now() + 90_000).toISOString() },
      step_name: 'charge_payment',
      created_at: '2026-03-01T10:00:00Z',
      ...over,
    } as TimelineEntry
  }

  it('не показывается, если процесс не ждёт', () => {
    render(<TimerCountdown timeline={[timer()]} waiting={false} />)

    expect(screen.queryByText(/Сработает через/)).toBeNull()
  })

  it('считает время до срабатывания и называет шаг', () => {
    render(<TimerCountdown timeline={[timer()]} waiting />)

    expect(screen.getByText(/Сработает через/)).toBeTruthy()
    expect(screen.getByText(/charge_payment/)).toBeTruthy()
  })

  it('говорит прямо, когда ожидание уже истекло', () => {
    render(
      <TimerCountdown
        timeline={[timer({ payload: { due_at: new Date(Date.now() - 1000).toISOString() } })]}
        waiting
      />,
    )

    // Истёкшее ожидание и живое выглядели бы одинаково, если бы не надпись:
    // в первом случае исполнитель вот-вот поднимет процесс, во втором он его не
    // поднимет никогда.
    expect(screen.getByText(/Ожидание истекло/)).toBeTruthy()
  })

  it('говорит, что ожидание отбыло', () => {
    render(
      <TimerCountdown
        timeline={[timer(), { kind: 'event', seq: 2, type: 'TIMER_FIRED' } as TimelineEntry]}
        waiting
      />,
    )

    expect(screen.getByText(/Ожидание отбыло/)).toBeTruthy()
  })

  it('не выдумывает срок, которого нет в журнале', () => {
    render(<TimerCountdown timeline={[timer({ payload: {} })]} waiting />)

    expect(screen.getByText(/срок из журнала неизвестен/)).toBeTruthy()
  })

  it('берёт срок того ожидания, ради которого процесс и стоит', () => {
    const stepWait = timer({ seq: 1, payload: { due_at: new Date(Date.now() + 5_000).toISOString() } })
    const deadline = timer({
      seq: 2,
      step_name: 'approve_order',
      payload: {
        due_at: new Date(Date.now() + 3_600_000).toISOString(),
        kind: 'pause_deadline',
      },
    })

    render(<TimerCountdown timeline={[stepWait, deadline]} waiting kind="pause_deadline" />)

    // Оба ожидания стоят в одном журнале, и «первое по порядку» показало бы
    // срок уже отбывшего ожидания шага: процесс стоял бы час там, где на самом
    // деле пять минут до отмены.
    expect(screen.getByText(/approve_order/)).toBeTruthy()
    expect(screen.queryByText(/charge_payment/)).toBeNull()
  })

  it('не путает срок ожидания шага со сроком паузы', () => {
    const stepWait = timer({ seq: 1, payload: { due_at: new Date(Date.now() + 5_000).toISOString() } })
    const deadline = timer({
      seq: 2,
      step_name: 'approve_order',
      payload: {
        due_at: new Date(Date.now() + 3_600_000).toISOString(),
        kind: 'pause_deadline',
      },
    })

    render(<TimerCountdown timeline={[stepWait, deadline]} waiting />)

    // Обратный случай: шаг ждёт таймер, и срок паузы к этому отношения не имеет.
    expect(screen.getByText(/charge_payment/)).toBeTruthy()
    expect(screen.queryByText(/approve_order/)).toBeNull()
  })

  it('говорит, что по истечении срока паузы процесс отменится', () => {
    render(
      <TimerCountdown
        timeline={[timer({ payload: { due_at: new Date(Date.now() - 1000).toISOString(), kind: 'pause_deadline' } })]}
        waiting
        kind="pause_deadline"
      />,
    )

    // «Исполнитель поднимет процесс» здесь было бы неправдой: срок паузы не
    // поднимает, а отменяет, и процесс уходит в откат.
    expect(screen.getByText(/отменится/)).toBeTruthy()
  })
})