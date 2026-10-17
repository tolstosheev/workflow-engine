// @vitest-environment jsdom
/**
 * График темпа.
 *
 * Проверяются три состояния, и каждое отвечает на вопрос, который счётчики не
 * отвечают: есть ли работа в последние полминуты, был ли застой, и удалось ли
 * собрать данные вообще. Молчаливая пустота читалась бы как «простоя не было».
 */

import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ThroughputChart } from '@/components/ThroughputChart'

// `vi.hoisted` обязателен: фабрика мока поднимается в начало файла, и переменная,
// объявленная ниже, на тот момент ещё не существует.
const { list } = vi.hoisted(() => ({ list: vi.fn() }))

vi.mock('@/api/client', () => ({
  instances: { list },
}))

function instance(createdIso: string, status: string) {
  return {
    id: `i-${createdIso}`,
    definition: { id: 'd-1', name: 'order_fulfillment', version: 1 },
    status,
    current_step: null,
    input: {},
    variables: {},
    error: null,
    lease_owner: null,
    lease_expires_at: null,
    heartbeat_at: null,
    archived_at: null,
    created_at: createdIso,
    updated_at: createdIso,
  }
}

function minutesAgo(count: number): string {
  return new Date(Date.now() - count * 60_000).toISOString()
}

describe('график темпа', () => {
  beforeEach(() => {
    list.mockReset()
  })

  afterEach(cleanup)

  it('рисует полосы по завершениям и называет пик', async () => {
    list.mockResolvedValue({
      items: [instance(minutesAgo(3), 'COMPLETED'), instance(minutesAgo(3), 'COMPLETED')],
      total: 2,
      limit: 200,
      offset: 0,
      archived_total: 0,
    })

    render(<ThroughputChart />)

    // Пик назван числом: иначе график пришлось бы измерять глазами.
    expect(await screen.findByText(/пик: 2 запусков, 2 завершений в минуту/)).toBeTruthy()
  })

  it('говорит, что за окно процессов не было', async () => {
    // Пустой график без слов читался бы как «система сломалась».
    list.mockResolvedValue({ items: [], total: 0, limit: 200, offset: 0, archived_total: 0 })

    render(<ThroughputChart />)

    expect(await screen.findByText('За это окно процессов не было')).toBeTruthy()
  })

  it('объявляет, что данные не собрались', async () => {
    list.mockRejectedValue(new Error('список недоступен'))

    render(<ThroughputChart />)

    expect(await screen.findByText('список недоступен')).toBeTruthy()
  })

  it('учитывает архивные процессы: уборка не стирает историю темпа', async () => {
    const captured: unknown[] = []
    list.mockImplementation(async (params: { archived?: string }) => {
      captured.push(params.archived)
      return { items: [], total: 0, limit: 200, offset: 0, archived_total: 0 }
    })

    render(<ThroughputChart />)
    await screen.findByText('За это окно процессов не было')

    // Без этого график обнулялся бы нажатием кнопки уборки — и на экране это
    // выглядело бы как остановка работы.
    expect(captured[0]).toBe('all')
  })

  it('объявляет, если список не прочитан', async () => {
    // График без данных и график, который не смог прочитать данные, — разные
    // вещи: первый означает «работы не было», второй «показать нечем».
    list.mockRejectedValue(new Error('список недоступен'))

    render(<ThroughputChart />)

    expect(await screen.findByText(/список недоступен/)).toBeTruthy()
  })

  it('объявляет график для скринридера одной фразой', async () => {
    list.mockResolvedValue({
      items: [instance(minutesAgo(2), 'COMPLETED')],
      total: 1,
      limit: 200,
      offset: 0,
      archived_total: 0,
    })

    const { container } = render(<ThroughputChart />)
    await screen.findByText(/пик:/)

    // Опрашиваем с ожиданием: компонент перерисовывается по своему таймеру, и
    // под нагрузкой полного выполнения шага между поиском подписи и поиском узла графика
    // успевал отрисоваться другой кадр. Проверка без ожидания падала случайно —
    // то есть утверждала «графика нет» в момент, когда он есть.
    await vi.waitFor(() => {
      const chart = container.querySelector('[role="img"]')
      expect(chart?.getAttribute('aria-label')).toMatch(/максимум 1/)
    })
  })
})