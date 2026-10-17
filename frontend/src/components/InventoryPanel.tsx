/**
 * Склад предметной области: резерв занял место и вернулся после доставки
 * (ADR-0020).
 *
 * Только чтение: иначе человек «освободил» бы резерв кнопкой и увидел успех
 * там, где должен увидеть компенсацию. Числа — снимок склада целиком, у
 * склада нет журнала, поэтому подпись так и говорит.
 */


import { useCallback, useEffect, useState } from 'react'

import { demo as demoApi } from '@/api/client'
import { Card, EmptyState, ErrorState } from '@/components/ui'
import type { InventoryItem } from '@/types/api'

const POLL_MS = 10_000

export function InventoryPanel() {
  const [items, setItems] = useState<InventoryItem[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const page = await demoApi.inventory()
      setItems(page.items)
      setError(null)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Склад не прочитан')
    }
  }, [])

  useEffect(() => {
    void load()
    // Опрос медленный: склад меняется от шагов, а страницу процесса открывают
    // надолго, и человек должен увидеть возвращение резерва без перезагрузки.
    const timer = window.setInterval(() => void load(), POLL_MS)
    return () => window.clearInterval(timer)
  }, [load])

  return (
    <Card title="Склад" className="lg:col-span-2">
      {error && <ErrorState message={error} />}

      {items !== null && items.length === 0 && <EmptyState message="Склад пуст: выполните seed" />}

      {items !== null && items.length > 0 && (
        <>
          <table aria-label="Остатки склада" className="w-full text-left text-sm">
            <thead>
              <tr className="text-xs text-white/40">
                <th scope="col" className="py-1">Артикул</th>
                <th scope="col" className="py-1">Всего</th>
                <th scope="col" className="py-1">В резерве</th>
                <th scope="col" className="py-1">Свободно</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.sku} className="border-t border-surface-border">
                  <td className="py-1 font-mono text-xs">{item.sku}</td>
                  <td className="py-1">{item.available}</td>
                  <td className={item.reserved > 0 ? 'py-1 text-warning' : 'py-1'}>
                    {item.reserved}
                  </td>
                  <td className="py-1">{item.available - item.reserved}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="mt-2 text-[11px] text-white/30">
            Снимок склада целиком, а не состояние процесса: у склада нет журнала. Возврат
            резерва после доставки — свойство шага, а не следствие чтения этой таблицы.
          </p>
        </>
      )}
    </Card>
  )
}