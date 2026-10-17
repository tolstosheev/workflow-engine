/**
 * Периодическое обновление страницы.
 *
 * Следующий запуск планируется после завершения предыдущего, а не через
 * `setInterval`: интервалы накапливают погрешность и накладывают запросы.
 * Интервал `0` — «не опрашивать вовсе».
 */


import { useEffect, useRef } from 'react'

/**
 * Вызывает `load` сразу, затем с указанной частотой.
 *
 * Следующий запуск планируется после завершения предыдущего, а не через
 * `setInterval`: интервалы накладывают запросы, если предыдущий не успел.
 * Смена интервала не вызывает загрузку — иначе открытие страницы процесса
 * (интервал меняется с двух секунд на «не опрашивать») грузило данные дважды.
 */
export function usePolling(load: () => void | Promise<void>, delayMs: number): void {
  const loadRef = useRef(load)
  loadRef.current = load
  const alive = useRef(false)
  // Первый запуск — при появлении хука, дальше — по расписанию. Разница видна
  // на странице процесса: её интервал меняется один раз, с двух секунд на ноль,
  // и немедленная перезагрузка в этот момент удваивала четыре запроса.
  const started = useRef(false)

  useEffect(() => {
    alive.current = true
    let timer: number | null = null
    let running = false

    // Объявления функций, а не констант с функциями: tick вызывает schedule, а
    // schedule ставит tick на таймер, и порядок объявления здесь не должен
    // ничего значить.
    function schedule(wait: number): void {
      if (alive.current && wait > 0) {
        timer = window.setTimeout(() => void tick(), wait)
      }
    }

    async function tick(): Promise<void> {
      // Запрос не дожидается предыдущего: страница и так получает данные
      // потоком событий, а второй запрос ничего не добавит.
      if (running) return
      running = true
      try {
        await loadRef.current()
      } finally {
        running = false
      }
      schedule(delayMs)
    }

    if (started.current) {
      // Смена частоты: следующий запуск по новому расписанию, без запроса
      // сейчас. Данные на экране устарели максимум на один интервал, а запрос
      // не отменяет ничего из того, что человек уже прочитал.
      schedule(delayMs)
    } else {
      started.current = true
      void tick()
    }

    return () => {
      alive.current = false
      if (timer !== null) window.clearTimeout(timer)
    }
  }, [delayMs])
}