/**
 * Отмена собственного запроса при уходе со страницы.
 *
 * Без ссылки на текущий запрос нечем отменить предыдущий: пришедший позже
 * ответ затирал бы более новый, а ушедшая со страницы вкладка продолжала бы
 * писать в состояние уже несуществующего компонента.
 */

import { useCallback, useEffect, useRef } from 'react'

/**
 * Выдаёт контроллер для следующего запроса и отменяет предыдущий.
 *
 * `current` — контроллер запроса, который идёт сейчас. Проверка `aborted`
 * после `await` обязательна: успешный ответ не значит актуальный.
 */
export function useAbortable(): {
  next: () => AbortController
  abort: () => void
  current: React.MutableRefObject<AbortController | null>
} {
  const current = useRef<AbortController | null>(null)

  const next = useCallback((): AbortController => {
    current.current?.abort()
    const controller = new AbortController()
    current.current = controller
    return controller
  }, [])

  const abort = useCallback((): void => {
    current.current?.abort()
    current.current = null
  }, [])

  useEffect(() => abort, [abort])

  return { next, abort, current }
}