// @vitest-environment jsdom
/**
 * Панель аренды: кто держит процессы и кто давно не отвечал.
 *
 * Панель показывает главное свойство аренды — владелец меняется сам после
 * смерти воркера — и потому обязана отличать три вещи: воркер работает,
 * воркер молчит, и воркера нет вовсе. Пустой список и «молчат» выглядят на
 * экране одинаково, а разница ровно та, ради которой панель существует.
 */

import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { WorkerLeases } from '@/components/WorkerLeases'
import type { WorkflowInstance } from '@/types/api'

function instance(
  id: string,
  owner: string | null,
  step: string | null,
  heartbeat: string | null,
): WorkflowInstance {
  return {
    id,
    definition: { id: 'd1', name: 'order_demo', version: 1 },
    status: 'RUNNING',
    current_step: step,
    variables: {},
    lease_owner: owner,
    lease_expires_at: null,
    heartbeat_at: heartbeat,
    created_at: '2026-03-01T10:00:00Z',
    updated_at: '2026-03-01T10:00:00Z',
  } as unknown as WorkflowInstance
}

afterEach(cleanup)

describe('панель аренды', () => {
  it('говорит прямо, что аренду никто не держит', () => {
    render(<WorkerLeases instances={[instance('i-1', null, 'charge', null)]} />)

    expect(screen.getByText('Никто не держит аренду — воркеры простаивают')).toBeTruthy()
    expect(screen.getByText(/1 свободно/)).toBeTruthy()
  })

  it('группирует инстансы по владельцу аренды', () => {
    render(
      <WorkerLeases
        instances={[
          instance('i-1', 'w1', 'charge', '2026-03-01T10:00:00Z'),
          instance('i-2', 'w1', 'ship', '2026-03-01T10:00:05Z'),
          instance('i-3', 'w2', 'reserve', null),
        ]}
      />,
    )

    expect(screen.getByText('w1')).toBeTruthy()
    expect(screen.getByText('w2')).toBeTruthy()
    expect(screen.getByText(/2 с арендой/)).toBeTruthy()
  })

  it('показывает прочерк вместо молчания воркера', () => {
    // Пустой heartbeat и «его нет» выглядели бы одинаково, и панель не могла
    // бы сказать, воркер ли молчит или его просто нет в списке.
    render(<WorkerLeases instances={[instance('i-1', 'w1', 'charge', null)]} />)

    expect(screen.getByText('heartbeat —')).toBeTruthy()
  })

  it('показывает шаг каждого инстанса, а не только владельца', () => {
    render(
      <WorkerLeases
        instances={[
          instance('i-1', 'w1', 'charge', '2026-03-01T10:00:00Z'),
          instance('i-2', 'w1', 'ship', '2026-03-01T10:00:05Z'),
        ]}
      />,
    )

    expect(screen.getByText('charge')).toBeTruthy()
    expect(screen.getByText('ship')).toBeTruthy()
  })

  it('не путает без шага с именем шага', () => {
    render(<WorkerLeases instances={[instance('i-1', 'w1', null, '2026-03-01T10:00:00Z')]} />)

    // Прочерк на месте шага — это «шаг неизвестен», а не пустое имя шага.
    expect(screen.getByTitle(/без шага/)).toBeTruthy()
  })
})