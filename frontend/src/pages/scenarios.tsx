/**
 * Сценарии показа.
 *
 * Демонстрация на защите — это не «нажать кнопку и посмотреть», а «доказать
 * свойство»: сбой шага приводит к откату, провал отката не прячется, обычный
 * заказ проходит целиком. Страница собирает каждый сценарий из уже существующих
 * ручек — новых ручек бэкенда здесь нет.
 *
 * Правило инъекции снимается и до запуска, и после: оставленное правило сломало
 * бы следующий сценарий, и виноват был бы уже он.
 */

import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { chaos as chaosApi, definitions as definitionsApi, instances as instancesApi } from '@/api/client'
import { Button, Card, ErrorState, StatusBadge } from '@/components/ui'
import { finishScenario, runScenario, SCENARIOS, type ScenarioSpec } from '@/lib/scenarios'
import { statusLabel, statusTone } from '@/lib/status'
import type { WorkflowInstance } from '@/types/api'

const DEPS = { definitions: definitionsApi, chaos: chaosApi, instances: instancesApi }

/** Состояния, в которых процесс уже не изменится. */
const TERMINAL = new Set(['COMPLETED', 'COMPENSATED', 'CANCELLED', 'FAILED', 'COMPENSATION_FAILED'])

interface Started {
  instanceId: string
  instance: WorkflowInstance | null
  note: string | null
  matched: boolean | null
}

export default function ScenariosPage() {
  const [running, setRunning] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [started, setStarted] = useState<Record<string, Started>>({})

  const start = useCallback(async (spec: ScenarioSpec) => {
    setRunning(spec.id)
    setError(null)
    try {
      const outcome = await runScenario(spec, DEPS)
      setStarted((prev) => ({
        ...prev,
        [spec.id]: { instanceId: outcome.instanceId, instance: null, note: null, matched: null },
      }))
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Сценарий не запустился')
      // Правило могло остаться: снимаем здесь, иначе следующий сценарий
      // начнётся под чужой инъекцией, и виноват окажется не он.
      await chaosApi.clear().catch(() => undefined)
    } finally {
      setRunning(null)
    }
  }, [])

  // Наблюдение за запущенным процессом: без него страница показала бы только
  // факт запуска, а доказательство свойства — это конечное состояние.
  const watch = useCallback(async (spec: ScenarioSpec, entry: Started) => {
    const instance = await instancesApi.get(entry.instanceId)
    setStarted((prev) => ({ ...prev, [spec.id]: { ...entry, instance } }))
    if (!TERMINAL.has(instance.status)) return
    const matched = await finishScenario(DEPS, spec, instance)
    setStarted((prev) => ({
      ...prev,
      [spec.id]: {
        ...prev[spec.id],
        matched,
        note: matched
          ? 'Процесс пришёл в ожидаемое состояние, правило инъекции снято.'
          : `Ожидалось одно из: ${spec.expect.map((value) => statusLabel(value)).join(', ')}, а процесс в состоянии «${statusLabel(instance.status)}».`,
      },
    }))
  }, [])

  useEffect(() => {
    const pending = Object.entries(started).filter(
      ([, entry]) => entry.instanceId && entry.matched === null,
    )
    if (pending.length === 0) return
    const timer = window.setInterval(() => {
      for (const [id, entry] of pending) {
        const spec = SCENARIOS.find((item) => item.id === id)
        if (spec) void watch(spec, entry).catch(() => undefined)
      }
    }, 1500)
    return () => window.clearInterval(timer)
  }, [started, watch])

  return (
    <section className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold text-white">Сценарии</h1>
        <p className="mt-1 text-sm text-white/60">
          Каждый сценарий ставит правило инъекции, запускает процесс и ждёт конца. Новых ручек
          бэкенда здесь нет — всё собрано из существующих вызовов.
        </p>
      </div>

      {error && <ErrorState message={error} />}

      {SCENARIOS.map((spec) => {
        const entry = started[spec.id]
        const instance = entry?.instance
        return (
          <Card key={spec.id} title={spec.title}>
            <p className="mb-3 text-sm text-white/60">{spec.proves}</p>

            <div className="mb-3 flex flex-wrap items-center gap-2 text-xs text-white/40">
              <span>
                Правило:{' '}
                {spec.rule
                  ? `${spec.rule.kind === 'step' ? `сломать шаг ${spec.rule.target}` : `сломать откат ${spec.rule.target}`}`
                  : 'не нужно'}
              </span>
              <span>Ожидается: {spec.expect.map((value) => statusLabel(value)).join(', ')}</span>
            </div>

            <Button disabled={running !== null} onClick={() => void start(spec)}>
              {running === spec.id ? 'Запускаем…' : 'Запустить'}
            </Button>

            {entry && (
              <div className="mt-3 flex flex-wrap items-center gap-2 text-sm">
                <StatusBadge
                  label={instance ? statusLabel(instance.status) : 'запущен'}
                  toneClass={instance ? statusTone(instance.status) : 'bg-white/10 text-white/60 border-white/20'}
                />
                {instance?.current_step && (
                  <span className="font-mono text-xs text-white/50">{instance.current_step}</span>
                )}
                <Link to={`/instances/${entry.instanceId}`} className="text-accent hover:underline">
                  Открыть процесс
                </Link>
                {entry.matched === false && <span className="text-warning">{entry.note}</span>}
                {entry.matched === true && <span className="text-white/50">{entry.note}</span>}
              </div>
            )}
          </Card>
        )
      })}
    </section>
  )
}