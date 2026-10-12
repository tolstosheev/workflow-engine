/**
 * Состояние системы по метрикам.
 *
 * Страница переносит в интерфейс таблицу «что считать нормой» из
 * `docs/OPERATIONS.md` §2: инцидент виден не по подписи, а по тому, что
 * счётчик ненулевой. Ненулевой откат и потерянная аренда подсвечиваются
 * красным — это те два значения, которые в норме равны нулю.
 */

import { useCallback, useState } from 'react'

import { service } from '@/api/client'
import { Card, EmptyState, ErrorState } from '@/components/ui'
import { usePolling } from '@/hooks/usePolling'
import { BREAKDOWN_STATUSES, labelsOf, parseMetrics, sumBy, valueOf } from '@/lib/metrics'
import type { Sample } from '@/lib/metrics'

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

/**
 * Список живёт в `lib/metrics`, а не здесь: он проверяется тестом на стороне
 * движка, который сверяет его с перечислением статусов, а тест из файла
 * компонента прочитать не может.
 */
const STATUSES = BREAKDOWN_STATUSES

/**
 * Частота опроса метрик. Значение короткое: метрики собираются из БД при
 * каждом запросе, и редкий опрос показывал бы устаревшую картину.
 */
const METRICS_POLL_MS = 5000

export function MetricsPage() {
  const [samples, setSamples] = useState<Sample[]>([])
  const [error, setError] = useState<string | null>(null)
  const [updatedAt, setUpdatedAt] = useState<string | null>(null)

  // Метрики собираются из БД при каждом запросе, поэтому и опрос частый:
  // редкий опрос показывал бы устаревшую картину. Следующий запуск
  // планируется после ответа предыдущего — наложения запросов не будет.
  const load = useCallback(async () => {
    try {
      const text = await service.metrics()
      setSamples(parseMetrics(text))
      setError(null)
      setUpdatedAt(new Date().toLocaleTimeString('ru-RU', { hour12: false }))
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Метрики недоступны')
    }
  }, [])

  usePolling(load, METRICS_POLL_MS)

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
        {/* Панель появляется и меняется сама: без объявления скринридер
            не узнает ни о появлении тревоги, ни о том, что она прошла. */}
        <div aria-live="polite">
        {error ? (
          // При ошибке запроса важно сказать, что именно не удалось: иначе пустой
          // ответ панели тревоги читается как «инцидентов нет».
          <p className="text-sm text-white/40">Тревоги показать нельзя: запрос метрик не удался, причина выше.</p>
        ) : samples.length === 0 ? (
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
        </div>
        <p className="mt-3 text-[11px] text-white/30">
          Правила взяты из таблицы «что считать нормой» в docs/OPERATIONS.md §2.
        </p>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Инстансы по состояниям">
          <table aria-label="Инстансы по состояниям" className="w-full text-left text-sm">
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
            <table aria-label="Шаги: успех и провал" className="w-full text-left text-sm">
              <thead className="text-xs uppercase tracking-wide text-white/40">
                <tr>
                  <th scope="col" className="pb-2">Шаг</th>
                  <th scope="col" className="pb-2 text-right">успех</th>
                  <th scope="col" className="pb-2 text-right">провал</th>
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
