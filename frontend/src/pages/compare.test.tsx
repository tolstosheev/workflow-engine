// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { StepRun, WorkflowInstance } from '@/types/api'

import { ComparePage } from './compare'

const list = vi.fn()
const get = vi.fn()
const steps = vi.fn()

vi.mock('@/api/client', () => ({
  instances: {
    list: (...args: unknown[]) => list(...args),
    get: (...args: unknown[]) => get(...args),
    steps: (...args: unknown[]) => steps(...args),
  },
}))

const instance = (id: string, status: WorkflowInstance['status']): WorkflowInstance => ({
  id,
  definition: { id: 'd1', name: 'order_fulfillment', version: 1 },
  status,
  input: {},
  variables: {},
  current_step: null,
  error: null,
  lease_owner: null,
  lease_expires_at: null,
  heartbeat_at: null,
  archived_at: null, journal_pruned_at: null,
  created_at: '2026-03-01T10:00:00Z',
  updated_at: '2026-03-01T10:00:30Z',
})

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

/** Адрес нужен, чтобы читать `a` и `b` из строки запроса. */
function Location() {
  const location = useLocation()
  return <span data-testid="path">{location.pathname + location.search}</span>
}

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <ComparePage />
      <Location />
    </MemoryRouter>,
  )
}

beforeEach(() => {
  list.mockReset()
  get.mockReset()
  steps.mockReset()
  list.mockResolvedValue({
    items: [instance('short-1', 'COMPLETED'), instance('long-1', 'COMPENSATED')],
    total: 2,
    limit: 50,
    offset: 0,
  })
  get.mockImplementation((id: string) => Promise.resolve(instance(id, 'COMPLETED')))
  steps.mockImplementation((id: string) =>
    Promise.resolve({
      items:
        id === 'short-1'
          ? [run({ id: 's', started_at: '2026-03-01T10:00:00Z', finished_at: '2026-03-01T10:00:00.020Z' })]
          : [run({ id: 'l', started_at: '2026-03-01T10:00:00Z', finished_at: '2026-03-01T10:00:30Z' })],
    }),
  )
})

afterEach(cleanup)

describe('сравнение процессов', () => {
  it('ничего не сравнивает, пока не выбраны оба процесса', async () => {
    renderAt('/compare')

    expect(await screen.findByText(/Выберите два процесса/)).toBeTruthy()
  })

  it('кладёт оба процесса на один отрезок', async () => {
    renderAt('/compare?a=short-1&b=long-1')

    // Подписи карточек совпадают с подписями полей выбора, поэтому проверка
    // идёт по тому, что доказывает загрузку: у каждой стороны своя
    // длительность жизни на общем отрезке.
    expect(await screen.findByText('20 мс жизни')).toBeTruthy()
    expect(await screen.findByText('30.0 с жизни')).toBeTruthy()
  })

  it('берёт и второй процесс из адреса', async () => {
    // Один выбор уже проверялся; второй вёлся бы отдельно, а забытый
    // обработчик сделал бы «сравнение» односторонним — и это не заметно.
    renderAt('/compare')

    await screen.findByRole('combobox', { name: 'Второй процесс' })
    await fireEvent.change(screen.getByRole('combobox', { name: 'Второй процесс' }), {
      target: { value: 'long-1' },
    })

    await waitFor(() =>
      expect(screen.getByTestId('path').textContent).toBe('/compare?b=long-1'),
    )
  })

  it('рисует обе стороны на одном отрезке', async () => {
    renderAt('/compare?a=short-1&b=long-1')

    // Обе стороны показаны одновременно — ради этого страница и писалась. Проверка
    // по подписям, а не по наличию списка: список есть и у стороны без
    // выполнений шага, и у стороны с ними.
    expect(await screen.findByText('short-1')).toBeTruthy()
    expect(await screen.findByText('long-1')).toBeTruthy()
    expect(await screen.findByText('20 мс жизни')).toBeTruthy()
    expect(await screen.findByText('30.0 с жизни')).toBeTruthy()
  })

  it('хранит выбор в адресе, а не в памяти', async () => {
    // Сравнение показывают комиссии, и ссылка должна переносить выбор целиком.
    renderAt('/compare')

    await screen.findByRole('combobox', { name: 'Первый процесс' })
    await fireEvent.change(screen.getByRole('combobox', { name: 'Первый процесс' }), {
      target: { value: 'short-1' },
    })

    await waitFor(() =>
      expect(screen.getByTestId('path').textContent).toBe('/compare?a=short-1'),
    )
  })

  it('объясняет, если список процессов не пришёл', async () => {
    list.mockRejectedValue(new Error('список недоступен'))

    renderAt('/compare')

    // Без этого страница выглядела бы просто пустой: ни выбора, ни объяснения,
    // и человек решил бы, что процессов нет.
    expect(await screen.findByText('список недоступен')).toBeTruthy()
  })

  it('снимает выбор, когда процесс возвращён в «не выбран»', async () => {
    renderAt('/compare?a=short-1')

    await screen.findByRole('combobox', { name: 'Первый процесс' })
    await fireEvent.change(screen.getByRole('combobox', { name: 'Первый процесс' }), {
      target: { value: '' },
    })

    // Пустой выбор убирает параметр из адреса, а не оставляет `a=` — иначе
    // ссылка выглядит как «что-то выбрано».
    await waitFor(() => expect(screen.getByTestId('path').textContent).toBe('/compare'))
  })

  it('объясняет, если процесс не прочитался', async () => {
    get.mockRejectedValue(new Error('процесс недоступен'))

    renderAt('/compare?a=short-1&b=long-1')

    expect(await screen.findByText('процесс недоступен')).toBeTruthy()
  })
})
