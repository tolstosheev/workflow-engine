/**
 * Темп процессов по минутам.
 *
 * График отвечает на вопрос, которого не отвечают счётчики: «движется ли
 * система сейчас». Метрики накапливают счётчики с начала работы, и по ним
 * нельзя отличить «десять процессов за час» от «десять за десять секунд».
 *
 * Данные берутся из списка инстансов, а не из Prometheus: замерщик снимает
 * темп под нагрузкой, а здесь нужен темп стенда прямо сейчас, и поднимать
 * сборщик ради одной карточки незачем.
 */

import { useCallback, useEffect, useState } from 'react'

import { instances as instancesApi } from '@/api/client'
import { ErrorState } from '@/components/ui'
import { barHeight, bucketByMinute, minuteLabel, peakOf } from '@/lib/rate'
import type { WorkflowInstance } from '@/types/api'

/** Окно графика. Час — достаточен, чтобы увидеть и работу, и простой. */
const WINDOW_MINUTES = 30
const POLL_MS = 10_000

export function ThroughputChart() {
  const [instances, setInstances] = useState<WorkflowInstance[]>([])
  const [now, setNow] = useState(() => Date.now())
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      // Архивные входят в подсчёт: уборка списка не должна обнулять историю
      // темпа, иначе нажатие кнопки стирало бы график.
      const page = await instancesApi.list({ limit: 200, archived: 'all' })
      setInstances(page.items)
      setNow(Date.now())
      setError(null)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Темп не собран')
    }
  }, [])

  useEffect(() => {
    void load()
    const timer = window.setInterval(() => void load(), POLL_MS)
    return () => window.clearInterval(timer)
  }, [load])

  const buckets = bucketByMinute(instances, now, WINDOW_MINUTES)
  const maxStarted = peakOf(buckets, 'started')
  const maxFinished = peakOf(buckets, 'finished')

  return (
    <section className="rounded-lg border border-surface-border bg-surface-raised p-4">
      <header className="mb-3 flex items-center justify-between gap-3">
        <h2 className="text-sm font-semibold text-white">Темп за последние полчаса</h2>
        <span className="text-xs text-white/40">
          пик: {maxStarted} запусков, {maxFinished} завершений в минуту
        </span>
      </header>

      {error && <ErrorState message={error} />}

      {!error && maxStarted === 0 && maxFinished === 0 && (
        <p className="text-sm text-white/50">За это окно процессов не было</p>
      )}

      {!error && (maxStarted > 0 || maxFinished > 0) && (
        <div
          className="flex h-24 items-end gap-px"
          role="img"
          aria-label={`Темп по минутам: максимум ${maxStarted} запусков и ${maxFinished} завершений`}
        >
          {buckets.map((bucket) => (
            <div
              key={bucket.start}
              title={`${minuteLabel(bucket.start)} — запусков ${bucket.started}, завершений ${bucket.finished}`}
              className="flex h-full flex-1 flex-col justify-end gap-px"
            >
              <span
                className="block w-full bg-success/60"
                style={{ height: `${Math.max(barHeight(bucket.finished, maxFinished), bucket.finished > 0 ? 4 : 0)}%` }}
              />
              <span
                className="block w-full bg-accent/60"
                style={{ height: `${Math.max(barHeight(bucket.started, maxStarted), bucket.started > 0 ? 4 : 0)}%` }}
              />
            </div>
          ))}
        </div>
      )}

      <p className="mt-3 text-[11px] text-white/30">
        Текущая минута не показана: она ещё не истекла, и полоса за неё всегда была бы ниже
        соседних. Тёмная полоса — завершения, светлая — запуски. Это темп стенда, а не замер под
        нагрузкой: его снимает `scripts/load.py`.
      </p>
    </section>
  )
}