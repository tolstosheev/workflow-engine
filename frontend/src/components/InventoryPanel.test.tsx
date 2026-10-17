// @vitest-environment jsdom
/**
 * Панель склада.
 *
 * Проверяется то, ради чего панель появилась: занятый резерв виден, свободный
 * остаток считается, и подпись не обещает того, чего в таблице нет. Без неё
 * возврат резерва после доставки виден только в базе, а на защите это половина
 * утверждения ADR-0020.
 */

import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { InventoryPanel } from '@/components/InventoryPanel'

const { inventory } = vi.hoisted(() => ({ inventory: vi.fn() }))

vi.mock('@/api/client', () => ({ demo: { inventory } }))

function item(sku: string, available: number, reserved: number) {
  return { sku, available, reserved }
}

describe('панель склада', () => {
  beforeEach(() => {
    inventory.mockReset()
    inventory.mockResolvedValue({
      items: [item('SKU-000', 10_000, 3), item('SKU-001', 100, 0)],
    })
  })

  afterEach(cleanup)

  it('показывает занятый резерв и свободный остаток', async () => {
    render(<InventoryPanel />)

    await screen.findByText('SKU-000')

    const row = screen.getByText('SKU-000').closest('tr')
    // Свободный остаток считается, а не показывается тем же числом: иначе
    // строка «свободно» повторяла бы «всего» и обманывала на защите.
    expect(row?.textContent).toContain('10000')
    expect(row?.textContent).toContain('9997')
  })

  it('говорит, что это снимок склада, а не состояние процесса', async () => {
    render(<InventoryPanel />)

    await screen.findByText('SKU-000')

    // У склада нет журнала, и обещание совпадения с разбором событий было бы
    // враньём: числа снимаются напрямую из таблицы.
    expect(screen.getByText(/снимок склада/i)).toBeTruthy()
  })

  it('объясняет пустой склад вместо молчаливых нулей', async () => {
    inventory.mockResolvedValue({ items: [] })

    render(<InventoryPanel />)

    // Пустая таблица без подписи читалась бы как «всё занято».
    expect(await screen.findByText(/выполните seed/i)).toBeTruthy()
  })

  it('объявляет ошибку чтения', async () => {
    inventory.mockRejectedValue(new Error('склад недоступен'))

    render(<InventoryPanel />)

    expect(await screen.findByText(/склад недоступен/)).toBeTruthy()
  })

  it('перечитывает склад без перезагрузки страницы', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    inventory.mockResolvedValue({ items: [item('SKU-000', 10_000, 1)] })

    render(<InventoryPanel />)
    await waitFor(() => expect(inventory).toHaveBeenCalledTimes(1))

    inventory.mockResolvedValue({ items: [item('SKU-000', 10_000, 0)] })
    await vi.advanceTimersByTimeAsync(10_000)

    await waitFor(() => expect(inventory).toHaveBeenCalledTimes(2))
    vi.useRealTimers()
  })
})