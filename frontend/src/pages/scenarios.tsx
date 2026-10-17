/**
 * Сценарии показа.
 *
 * Страница ждёт и **проверяет утверждение**, а не сообщает о факте запуска:
 * проверка состоит из ожидаемого состояния и свидетельств из журнала, потому
 * что одного состояния мало — компенсация может быть и не выполнена.
 *
 * Правила снимаются всегда: до запуска, после проверки, при уходе со страницы и
 * при ошибке. Оставленное правило ломает следующий сценарий.
 *
 * **Один сценарий за раз:** правило на стенде одно, а снятие общее, поэтому
 * кнопки блокируются, пока идёт другой.
 */


import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'

import { chaos as chaosApi, definitions as definitionsApi, instances as instancesApi } from '@/api/client'
import { Button, Card, ErrorState, StatusBadge } from '@/components/ui'
import {
  applyAction,
  finishScenario,
  isObserved,
  runScenario,
  SCENARIOS,
  shouldStop,
  type EvidenceNote,
  type ScenarioSpec,
} from '@/lib/scenarios'
import { statusLabel, statusTone } from '@/lib/status'
import type { WorkflowInstance } from '@/types/api'

const DEPS = { definitions: definitionsApi, chaos: chaosApi, instances: instancesApi }

const POLL_MS = 1500

/**
 * Предельный срок показа сценария.
 *
 * Без него опрос шёл бы вечно: процесс, застрявший в состоянии, которое
 * сценарий считает проходным, выглядел бы так же, как идущий. Превращение
 * «ждём» в «не пришёл» обязано происходить по часам, а не по терпению.
 */
const DEADLINE_MS = 180_000

type Phase = 'running' | 'observed' | 'done' | 'failed'

interface Started {
  instanceId: string
  instance: WorkflowInstance | null
  phase: Phase
  notes: EvidenceNote[]
  /** Утверждение подтверждено: и состояние, и свидетельства. */
  proven: boolean | null
  message: string | null
  /** Когда запущен: по часам решается, что сценарий не придёт. */
  startedAt: number
}

function idle(id: string): Started {
  return {
    instanceId: id,
    instance: null,
    phase: 'running',
    notes: [],
    proven: null,
    message: null,
    startedAt: Date.now(),
  }
}

function ruleLabel(spec: ScenarioSpec): string {
  if (!spec.rules || spec.rules.length === 0) return 'не нужны'
  return spec.rules
    .map((rule) => {
      const what =
        rule.kind === 'step'
          ? `сломать шаг ${rule.target}`
          : `сломать компенсацию ${rule.target}`
      return rule.mode === 'once' ? `${what} (одна попытка)` : `${what} (всегда)`
    })
    .join('; ')
}

function actionLabel(spec: ScenarioSpec): string {
  const action = spec.action
  if (!action) return 'не требуется'
  const when = action.after.map((value) => statusLabel(value)).join(' или ')
  if (action.kind === 'cancel') return `отменить, дойдя до «${when}»`
  if (action.kind === 'resume') return `продолжить, дойдя до «${when}»`
  if (action.kind === 'retry-compensation')
    return `снять правило и повторить компенсацию, дойдя до «${when}»`
  return `остановить продление закрепления, дойдя до «${when}»`
}

const POLL_MS_UNRESOLVED = 10_000

export default function ScenariosPage() {
  const [running, setRunning] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [started, setStarted] = useState<Record<string, Started>>({})
  const [unresolved, setUnresolved] = useState<number | null>(null)
  // Правила, снятые при уходе со страницы: без этого инъекция пережила бы
  // вкладку и досталась следующему прогону.
  const dirty = useRef(false)

  // Один сценарий за раз — не вежливость, а устройство стенда: правило инъекции
  // на ней одно на всех, и новый сценарий снимает его перед стартом. Запущенный
  // поверх идущего прогон терял свою поломку и проходил «корректно» — то есть
  // доказывал ровно противоположное заявленному (ADR-0022 про границу глобальной
  // инъекции).
  const inFlight = Object.entries(started).find(([, entry]) => entry.phase === 'running')
  const inFlightTitle = inFlight
    ? SCENARIOS.find((item) => item.id === inFlight[0])?.title
    : undefined

  const start = useCallback(async (spec: ScenarioSpec) => {
    setRunning(spec.id)
    setError(null)
    try {
      const outcome = await runScenario(spec, DEPS)
      dirty.current = spec.rules !== undefined && spec.rules.length > 0
      setStarted((prev) => ({ ...prev, [spec.id]: idle(outcome.instanceId) }))
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Сценарий не запустился')
      // Правило могло остаться: снимаем здесь, иначе следующий сценарий
      // начнётся под чужой инъекцией, и виноват окажется не он.
      dirty.current = false
      await chaosApi.clear().catch(() => undefined)
    } finally {
      setRunning(null)
    }
  }, [])

  const watch = useCallback(async (spec: ScenarioSpec, entry: Started) => {
    const instance = await instancesApi.get(entry.instanceId)

    // Действие по ходу процесса. Повтор возможен, пока состояние прежнее:
    // отмена меняет его быстрее опроса, и на стенде применялась один раз, но
    // гарантии «ровно один раз» здесь нет — она обеспечивается тем, что
    // состояние уезжает, а не флагом.
    let actionNote: string | null = null
    if (entry.phase === 'running' && spec.action) {
      const applied = await applyAction(DEPS, spec, instance)
      if (applied) actionNote = `сценарий выполнил действие при «${statusLabel(instance.status)}»`
    }

    // Срок вышел: процесс не пришёл. Молчаливое ожидание читалось бы как
    // «идёт», а это ровно то, чего на защите ждать нельзя.
    if (Date.now() - entry.startedAt > DEADLINE_MS) {
      await finishScenario(DEPS, spec, instance)
      setStarted((prev) => ({
        ...prev,
        [spec.id]: {
          ...entry,
          instance,
          phase: 'failed',
          proven: false,
          message: `Процесс не пришёл к ожидаемому состоянию за ${DEADLINE_MS / 60_000} минут и остался в «${statusLabel(instance.status)}».`,
        },
      }))
      dirty.current = false
      return
    }

    if (shouldStop(spec, instance.status)) {
      const verdict = await finishScenario(DEPS, spec, instance)
      const proven = verdict.stateMatched && verdict.evidencePassed
      setStarted((prev) => ({
        ...prev,
        [spec.id]: {
          ...entry,
          instance,
          phase: 'done',
          notes: verdict.notes,
          proven,
          message: proven
            ? 'Утверждение подтверждено состоянием и данными журнала.'
            : verdict.stateMatched
              ? 'Состояние верное, но свидетельство не подтвердилось.'
              : `Ожидалось одно из: ${spec.expect.map((value) => statusLabel(value)).join(', ')}.`,
        },
      }))
      dirty.current = false
      return
    }

    // Наблюдаемое состояние — конец показа для этого сценария: процесс стоит и
    // сам не пойдёт дальше, пока не придёт решение. Ждать тут нечего.
    if (isObserved(spec, instance.status) && !(spec.action && spec.action.after.includes(instance.status))) {
      setStarted((prev) => ({
        ...prev,
        [spec.id]: {
          ...entry,
          instance,
          phase: 'observed',
          message: `Процесс в «${statusLabel(instance.status)}» и ждёт решения. Продолжение — вручную.`,
        },
      }))
      dirty.current = false
      return
    }

    setStarted((prev) => ({ ...prev, [spec.id]: { ...entry, instance, message: actionNote } }))
  }, [])

  useEffect(() => {
    const pending = Object.entries(started).filter(
      ([, entry]) => entry.phase === 'running',
    )
    if (pending.length === 0) return
    const timer = window.setInterval(() => {
      for (const [id, entry] of pending) {
        const spec = SCENARIOS.find((item) => item.id === id)
        if (spec) void watch(spec, entry).catch(() => undefined)
      }
    }, POLL_MS)
    return () => window.clearInterval(timer)
  }, [started, watch])

  // Незакрытые откаты на стенде (ADR-0022): правило инъекции без инстанса
  // достаётся в том числе им, и человек узнаёт об этом раньше результата.
  const watchUnresolved = useCallback(async () => {
    try {
      const page = await instancesApi.list({ limit: 100 })
      setUnresolved(
        page.items.filter(
          (item) =>
            item.status === 'COMPENSATION_FAILED' || item.status === 'PAUSED',
        ).length,
      )
    } catch {
      // Не читается — молчим: предупреждение не обязано ломать страницу.
      setUnresolved(null)
    }
  }, [])

  useEffect(() => {
    void watchUnresolved()
    const timer = window.setInterval(() => void watchUnresolved(), POLL_MS_UNRESOLVED)
    return () => window.clearInterval(timer)
  }, [watchUnresolved])

  // Уход со страницы снимает правила: забытая инъекция ломает всё, что дальше.
  useEffect(() => {
    return () => {
      if (dirty.current) void chaosApi.clear().catch(() => undefined)
    }
  }, [])

  return (
    <section className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold text-white">Сценарии</h1>
        <p className="mt-1 text-sm text-white/60">
          Каждый сценарий ставит правило неисправности, запускает процесс, ждёт конца и сверяет
          утверждение с журналом. Новых ручек бэкенда здесь нет — всё собрано из
          существующих вызовов.
        </p>
      </div>

      {error && <ErrorState message={error} />}

      {inFlightTitle && (
        <p
          role="status"
          className="rounded-md border border-accent/40 bg-accent/10 p-3 text-xs text-accent"
        >
          Идёт сценарий «{inFlightTitle}». Следующий запускается после его итога: правило
          неисправности на стенде одно на всех, и два сценария под ним шли бы вразнобой — второй
          снял бы поломку первого, и тот прошёл бы «корректно».
        </p>
      )}

      {unresolved !== null && unresolved > 0 && (
        <p className="rounded-md border border-warning/40 bg-warning/10 p-3 text-xs text-warning">
          На стенде {unresolved} незакрытых процессов: они ждут решения человека или повтора
          компенсации. Правило неисправности без адресата достаётся в том числе им, поэтому сценарий,
          требующий «сломать один раз», показывается через ручной повтор (ADR-0022).
        </p>
      )}

      {SCENARIOS.map((spec) => {
        const entry = started[spec.id]
        const instance = entry?.instance
        return (
          <Card key={spec.id} title={spec.title}>
            <p className="mb-3 text-sm text-white/60">{spec.proves}</p>

            <div className="mb-3 flex flex-wrap items-center gap-2 text-xs text-white/40">
              <span>Определение: {spec.definition}</span>
              <span>Правило: {ruleLabel(spec)}</span>
              <span>Действие: {actionLabel(spec)}</span>
              <span>Ожидается: {spec.expect.map((value) => statusLabel(value)).join(', ')}</span>
            </div>

            <Button
              disabled={running !== null || inFlight !== undefined}
              onClick={() => void start(spec)}
            >
              {running === spec.id ? 'Запускаем…' : 'Запустить'}
            </Button>

            {entry && (
              <div className="mt-3 space-y-2 text-sm">
                <div className="flex flex-wrap items-center gap-2">
                  <StatusBadge
                    label={instance ? statusLabel(instance.status) : 'запущен'}
                    toneClass={
                      instance
                        ? statusTone(instance.status)
                        : 'bg-white/10 text-white/60 border-white/20'
                    }
                  />
                  {instance?.current_step && (
                    <span className="font-mono text-xs text-white/50">{instance.current_step}</span>
                  )}
                  <Link to={`/instances/${entry.instanceId}`} className="text-accent hover:underline">
                    Открыть процесс
                  </Link>
                  {entry.message && (
                    <span className={entry.proven === false ? 'text-warning' : 'text-white/50'}>
                      {entry.message}
                    </span>
                  )}
                </div>

                {entry.notes.length > 0 && (
                  <ul className="space-y-1 text-xs">
                    {entry.notes.map((note) => (
                      <li key={note.label} className={note.passed ? 'text-white/50' : 'text-warning'}>
                        {note.passed ? '✓' : '✗'} {note.label} — {note.detail}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}
          </Card>
        )
      })}
    </section>
  )
}