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
import { MemoryRouter, useLocation } from 'react-router-dom'
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

/**
 * Показывает текущий адрес.
 *
 * Отдельный элемент рядом со страницей, а не чтение из `window.location`:
 * `MemoryRouter` держит историю в памяти, и настоящий адрес не изменился бы —
 * проверка прошла бы, ничего не проверив.
 */
function LocationProbe() {
  const location = useLocation()
  return <span data-testid="адрес">{location.search}</span>
}

function renderPage(path = '/') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <InstancesPage />
      <LocationProbe />
    </MemoryRouter>,
  )
}

/** Текущий адрес: пустая строка означает «параметров нет». */
function address(): string {
  return screen.getByTestId('адрес').textContent ?? ''
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
  await fireEvent.change(screen.getByLabelText('Поиск по имени процесса'), {
    target: { value: text },
  })
}

/**
 * Подпись поля поиска обязана называть то, что ищется.
 *
 * У инстанса нет имени — есть идентификатор, — а поле ищет по имени процесса.
 * Разные подписи у одного поля («инстанс» и «процесс») означают, что скринридер
 * читает не то, что делает страница.
 */
function searchLabel(): string {
  return screen.getByRole('textbox', { name: /по имени процесса/i }).getAttribute(
    'aria-label',
  ) ?? ''
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
    lastSeq: {},
  })
})

describe('список инстансов', () => {
  describe('состояние списка в адресе', () => {
    it('не засоряет адрес, пока ничего не выбрано', () => {
      renderPage()

      // Лишние параметры в адресе делают ссылку негодной: открытая дважды
      // страница выглядела бы по-разному.
      expect(address()).toBe('')
    })

    it('кладёт выбранный статус в адрес', async () => {
      renderPage()

      await fireEvent.click(screen.getByRole('button', { name: 'Завершённые' }))

      expect(address()).toContain('status=COMPLETED')
    })

    it('открывается по адресу уже отфильтрованной', () => {
      renderPage('/?status=COMPLETED')

      // Ссылка, отправленная товарищу, обязана открыть то же, что видит
      // отправитель: иначе это просто ссылка, а состояние живёт в браузере.
      expect(screen.getByRole('button', { name: 'Завершённые' }).getAttribute('aria-pressed')).toBe(
        'true',
      )
    })

    it('кладёт набранный поиск в адрес', async () => {
      renderPage()

      await search('order')

      expect(address()).toContain('q=order')
    })

    it('открывается с уже набранным поиском', () => {
      renderPage('/?q=order-new')

      const field = screen.getByLabelText('Поиск по имени процесса') as HTMLInputElement

      expect(field.value).toBe('order-new')
      expect(rows()[0]).toContain('order-new')
    })

    it('помнит порядок строк в адресе', async () => {
      renderPage()

      // Надпись кнопки называет текущий порядок, поэтому переключает его
      // нажатие на неё же.
      await fireEvent.click(screen.getByRole('button', { name: 'Сначала новые' }))

      expect(address()).toContain('sort=old')
      expect(screen.getByRole('button', { name: 'Сначала старые' })).toBeTruthy()
    })

    it('открывается в указанном порядке', () => {
      renderPage('/?sort=old')

      expect(screen.getByRole('button', { name: 'Сначала старые' })).toBeTruthy()
      expect(rows()[0]).toContain('order-old')
    })

    it('забывает незнакомый статус, а не падает', () => {
      // Адрес мог остаться от прежней версии или быть дописан вручную.
      renderPage('/?status=ВЫДУМАННЫЙ')

      expect(screen.getByRole('button', { name: 'Все' }).getAttribute('aria-pressed')).toBe('true')
    })
  })

  it('называет в поле поиска то, что ищет на самом деле', () => {
    renderPage()

    expect(searchLabel()).toBe('Поиск по имени процесса')
  })

  it('ищет по имени процесса, а не по шагу', () => {
    renderPage()

    void search('charge_payment')

    // Имя шага не является именем процесса: подставив его в поле, человек
    // ждал бы процесс по шагу и получал бы пустой список без объяснения.
    expect(rows()).toEqual([])
    expect(screen.getByText(/ничего не найдено/)).toBeTruthy()
  })

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
    renderPage('/?status=COMPLETED')
    await search('order')

    // Поиск не должен возвращать то, что фильтр статуса уже отсек: обе
    // части условия применяются одновременно.
    expect(rows()).toHaveLength(0)
  })
})