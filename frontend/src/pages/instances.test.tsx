// @vitest-environment jsdom
/**
 * Список инстансов: поиск и сортировка должны быть видны на экране.
 *
 * Обе функции считались верно и результат выбрасывался: страница рендерила
 * исходный список, а счётчик под поисковой строкой показывал отфильтрованное
 * число. Выглядело это как «поиск сломался, но что-то же нашлось», и дефект
 * обнаружился только глазами на стенде.
 *
 * Тест держит разрыв закрытым: ищет по имени процесса, жмёт переключатель
 * направления и требует, чтобы строки в таблице шли в том же порядке, о
 * котором говорит надпись на кнопке.
 */

import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import InstancesPage from '@/pages/instances'
import { storeFixture } from '@/pages/instances.test.data'
import { useInstancesStore } from '@/store/instances'

vi.mock('@/api/stream', () => ({ useEventStream: () => undefined }))

// Ответ сервера отдаёт те же строки, что подставлены в стор. Мок с пустым
// списком выглядел бы безобидно, но страница опрашивает API при монтировании,
// и незавершившийся опрос предыдущего теста стирал бы подставленные данные
// следующего: первый тест видел таблицу, остальные — пустое поле.
vi.mock('@/api/client', async () => {
  const { storeFixture: fixture } = await import('./instances.test.data')

  return {
    instances: {
      list: vi.fn(async () => ({ items: fixture(), total: fixture().length })),
    },
    chaosApi: { list: vi.fn(async () => ({ active: [] })) },
    definitions: {},
    chaos: {},
    service: {},
  }
})

function renderPage() {
  return render(
    <MemoryRouter>
      <InstancesPage />
    </MemoryRouter>,
  )
}

/**
 * Строки таблицы в порядке показа.
 *
 * При пустом результате таблицы нет — вместо неё страница показывает
 * объяснение, — поэтому отсутствие таблицы и есть «ничего не выведено».
 */
function rows(): string[] {
  const table = screen.queryByRole('table')
  if (!table) return []

  return Array.from(table.querySelectorAll('tbody tr')).map((row) =>
    Array.from(row.querySelectorAll('td'))
      .map((cell) => cell.textContent?.trim() ?? '')
      .join(' | '),
  )
}

async function search(text: string): Promise<void> {
  await fireEvent.change(screen.getByLabelText('Поиск инстанса по имени'), {
    target: { value: text },
  })
}

// Разметка предыдущего теста иначе остаётся в документе, и поиск находит по
// два одинаковых поля — тесты падают не на своём, а на чужом элементе.
afterEach(cleanup)

beforeEach(() => {
  useInstancesStore.setState({
    items: storeFixture(),
    loading: false,
    error: null,
    connected: true,
    lastEventAt: null,
    statusFilter: 'ALL',
    lastSeq: {},
  })
})

describe('список инстансов', () => {
  it('показывает новые сверху по умолчанию', () => {
    renderPage()

    expect(rows()[0]).toContain('order-new')
  })

  it('переключает порядок, и надпись кнопки говорит о том же', async () => {
    renderPage()

    await fireEvent.click(screen.getByRole('button', { name: 'Сначала новые' }))

    expect(screen.getByRole('button', { name: 'Сначала старые' })).toBeTruthy()
    expect(rows()[0]).toContain('order-old')
    expect(rows()).toHaveLength(3)
  })

  it('сужает таблицу по имени процесса', async () => {
    renderPage()

    await search('mid')

    expect(rows()).toHaveLength(1)
    expect(rows()[0]).toContain('order-mid')
  })

  it('ищет без учёта регистра', async () => {
    renderPage()

    await search('ORDER-MID')

    expect(rows()).toHaveLength(1)
  })

  it('не оставляет строк, когда ничего не найдено', async () => {
    renderPage()

    await search('нетакого')

    expect(rows()).toHaveLength(0)
  })

  it('объявляет то же число, что и показывает', async () => {
    renderPage()

    await search('order')

    // Число живёт в том же текстовом узле, что и слова вокруг, поэтому
    // проверяется всё подпись целиком, а не отдельный элемент с цифрой.
    const live = screen.getByText(/найдено/i)

    expect(live.textContent).toContain('найдено 3')
    expect(rows()).toHaveLength(3)
  })

  it('фильтр по статусу и поиск складываются, а не заменяют друг друга', async () => {
    useInstancesStore.setState({ items: storeFixture(), statusFilter: 'COMPLETED' })

    renderPage()
    await search('order')

    // Поиск не должен возвращать то, что фильтр статуса уже отсек: обе
    // части условия применяются одновременно.
    expect(rows()).toHaveLength(0)
  })
})