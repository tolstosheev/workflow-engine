/**
 * Состояние системы по метрикам.
 *
 * Страница переносит в интерфейс таблицу «что считать нормой» из
 * `docs/OPERATIONS.md` §2: инцидент виден не по подписи, а по тому, что
 * счётчик ненулевой. Ненулевой откат и потерянная аренда подсвечиваются
 * красным — это те два значения, которые в норме равны нулю.
 */

import { useEffect, useState } from 'react'

import { service } from '@/api/client'
import { Card, EmptyState, ErrorState } from '@/components/ui'
import { labelsOf, parseMetrics, sumBy, valueOf } from '@/lib/metrics'
import type { Sample } from '@/lib/metrics'
import type { InstanceStatus } from '@/types/api'

/** Значения, ненулевые в которых означают инцидент. */
const ALARMS: Array<{ test: (samples: Sample[]) => number; text: string }> = [
  {
    test: (samples) => sumBy(samples, 'workflow_instances', ['status', 'COMPENSATION_FAILED']),
    text: 'откат не удался — процесс остался с частично применённым эффектом',
  },
  {
    test: (samples) => valueOf(samples, 'workflow_compensations', { result: 'failure' }),
    text: 'попыток компенсации провалилось — каждый случай разбирается вручную',
  },
  {
    test: (samples) => valueOf(samples, 'workflow_lease_lost_total', {}),
    text: 'потеряно аренд — воркер перегружен или срок аренды мал',
  },
]

/** Статусы, показываются всегда: нулевое значение тоже информативно. */
const STATUSES: InstanceStatus[] = [
  'RUNNING',
  'WAITING',
  'COMPENSATING',
  'COMPENSATION_FAILED',
  'COMPLETED',
  'COMPENSATED',
  'CANCELLED',
  'FAILED',
]

export function MetricsPage() {
  const [samples, setSamples] = useState<Sample[]>([])
  const [error, setError] = useState<string | null>(null)
  const [updatedAt, setUpdatedAt] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false

    const poll = async () => {
      try {
        const text = await service.metrics()
        if (!cancelled) {
          setSamples(parseMetrics(text))
          setError(null)
          setUpdatedAt(new Date().toLocaleTimeString('ru-RU', { hour12: false }))
        }
      } catch (cause) {
        if (!cancelled) {
          setError(cause instanceof Error ? cause.message : 'Метрики недоступны')
        }
      }
    }

    void poll()
    // Метрики собираются из БД при каждом запросе, поэтому и опрос частый:
    // редкий опрос показывал бы устаревшую картину.
    const timer = setInterval(() => void poll(), 5000)
    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [])

  const alarms = ALARMS.map((alarm) => ({ ...alarm, value: alarm.test(samples) })).filter(
    (alarm) => alarm.value > 0,
  )

  const steps = labelsOf(samples, 'workflow_steps', 'step')

  return (
    <section className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Состояние системы</h1>
        {updatedAt && <span className="text-xs text-white/40">обновлено в {updatedAt}</span>}
      </div>

      {error && <ErrorState message={error} />}

      <Card title="Тревоги">
        {samples.length === 0 ? (
          <EmptyState message="Метрики ещё не собраны" />
        ) : alarms.length === 0 ? (
          <p className="text-sm text-success">
            Инцидентов нет: откаты проходят, компенсации не проваливаются, аренды не теряются.
          </p>
        ) : (
          <ul className="space-y-2">
            {alarms.map((alarm) => (
              <li
                key={alarm.text}
                className="rounded-md border border-danger/40 bg-danger/10 p-3 text-sm text-danger"
              >
                <span className="font-semibold">{alarm.value}</span> — {alarm.text}
              </li>
            ))}
          </ul>
        )}
        <p className="mt-3 text-[11px] text-white/30">
          Правила взяты из таблицы «что считать нормой» в docs/OPERATIONS.md §2.
        </p>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Инстансы по состояниям">
          <table className="w-full text-left text-sm">
            <tbody>
              {STATUSES.map((status) => (
                <tr key={status} className="border-t border-surface-border/60">
                  <td className="py-1.5 text-white/70">{status}</td>
                  <td className="py-1.5 text-right font-mono">
                    {sumBy(samples, 'workflow_instances', ['status', status])}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>

        <Card title="Шаги: успех и провал">
          {steps.length === 0 ? (
            <EmptyState message="Шаги ещё не выполнялись" />
          ) : (
            <table className="w-full text-left text-sm">
              <thead className="text-xs uppercase tracking-wide text-white/40">
                <tr>
                  <th className="pb-2">Шаг</th>
                  <th className="pb-2 text-right">успех</th>
                  <th className="pb-2 text-right">провал</th>
                </tr>
              </thead>
              <tbody>
                {steps.map((step) => (
                  <tr key={step} className="border-t border-surface-border/60">
                    <td className="py-1.5 font-mono text-white/70">{step}</td>
                    <td className="py-1.5 text-right font-mono text-success">
                      {valueOf(samples, 'workflow_steps', { step, result: 'success' })}
                    </td>
                    <td className="py-1.5 text-right font-mono text-danger">
                      {valueOf(samples, 'workflow_steps', { step, result: 'failure' })}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      </div>
    </section>
  )
}