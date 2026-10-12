/**
 * Периодическое обновление страницы.
 *
 * Три страницы опрашивали API одинаково и по-разному: у одной не было защиты
 * от наложения запросов, у другой cleanup ставился после раннего выхода, у
 * третьей интервал жёстко зашит. Правило вынесено сюда, чтобы список опрашиваемого
 * не расползался по разметке, а проверялся в одном месте.
 *
 * Интервал `0` означает «не опрашивать вовсе» — так ведёт себя завершённый
 * процесс, который больше не меняется.
 */

import { useEffect, useRef } from 'react'

/**
 * Вызывает `load` сразу и затем с указанной частотой.
 *
 * Отложенный вызов, а не `setInterval`: интервалы накапливают погрешность и
 * накладывают запросы, если предыдущий не успел ответить. Здесь следующий запуск
 * планируется после завершения предыдущего — так очередь не растёт.
 */
export function usePolling(load: () => void | Promise<void>, delayMs: number): void {
  const loadRef = useRef(load)
  loadRef.current = load
  const alive = useRef(false)

  useEffect(() => {
    alive.current = true
    let timer: number | null = null
    let running = false

    const tick = async () => {
      // Запрос не дожидается предыдущего: страница и так получает данные
      // потоком событий, а второй запрос ничего не добавит.
      if (running) return
      running = true
      try {
        await loadRef.current()
      } finally {
        running = false
      }
      if (alive.current && delayMs > 0) {
        timer = window.setTimeout(() => void tick(), delayMs)
      }
    }

    void tick()

    return () => {
      alive.current = false
      if (timer !== null) window.clearTimeout(timer)
    }
  }, [delayMs])
}