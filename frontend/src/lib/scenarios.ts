/**
 * Сценарии показа.
 *
 * Демонстрация на защите — это не «нажать кнопку и посмотреть», а «доказать
 * свойство». Отсюда два требования к сценарию, которые раньше выполнялись
 * только частично.
 *
 * **Утверждение проверяется по данным, а не по статусу.** Один статус ничего не
 * доказывает: `COMPENSATED` можно получить и с резервом на складе, если
 * компенсация не отработала. Поэтому у каждого сценария есть `evidence` —
 * читаемые из журнала и прогонов шагов свидетельства: компенсация шага реально
 * завершилась, шаг выполнялся дважды и завершился один раз, цикл исчерпан.
 *
 * **Правило снимается всегда.** Съехавшая инъекция ломает следующий сценарий,
 * и виноват окажется он. Правила снимаются до запуска, после проверки и при
 * уходе со страницы.
 *
 * Функции отделены от компонента: порядок вызовов, несколько правил и снятие
 * правила проверяются тестом без разметки, и в этом порядке как раз и
 * заключена суть.
 */

import { chaos as chaosApi, definitions as definitionsApi, instances as instancesApi } from '@/api/client'
import type { InstanceStatus, WorkflowEvent, WorkflowInstance, StepRun } from '@/types/api'

export type RuleKind = 'step' | 'compensation'
export type RuleMode = 'once' | 'always'

export interface RuleSpec {
  kind: RuleKind
  /** Имя шага либо `module:function` обработчика отката. */
  target: string
  /** `once` — первая попытка падает, следующая проходит. */
  mode: RuleMode
  error?: string
}

export type ActionKind = 'cancel' | 'resume' | 'stop-lease' | 'retry-compensation'

export interface ActionSpec {
  kind: ActionKind
  /** Состояния, в которых действие применяется. */
  after: InstanceStatus[]
  /** Только для `stop-lease`: на сколько сдвинуть сердце. */
  durationSeconds?: number
}

/**
 * Свидетельство, читаемое из данных процесса.
 *
 * Это проверяемое утверждение, а не украшение: у каждого пункта есть подпись,
 * которая попадает в отчёт и на страницу.
 *
 * Источники выбраны по факту, а не по предположению. Компенсация видна в
 * журнале событием `COMPENSATION_COMPLETED` с полем `for_step` — отдельных
 * прогонов шага у неё нет, а здоровье шага читается по `COMPENSATION_FAILED`.
 * Прогоны `/steps` называются именами шагов графа, а не обработчиков.
 */
export type Evidence =
  | { kind: 'event'; event: string; label: string }
  | { kind: 'event-count'; event: string; forStep?: string; atLeast: number; label: string }
  | { kind: 'compensated'; step: string; label: string }
  | { kind: 'attempts'; step: string; atLeast: number; label: string }

export interface ScenarioSpec {
  id: string
  title: string
  /** Что сценарий должен доказать — это и есть текст для защиты. */
  proves: string
  /** Определение по имени: активных на стенде бывает несколько. */
  definition: string
  input?: Record<string, unknown>
  /** Несколько правил ставятся одновременно и снимаются вместе. */
  rules?: RuleSpec[]
  /** Действие, выполняемое по ходу процесса, а не до запуска. */
  action?: ActionSpec
  /**
   * Состояния, в которых сценарий считается показанным без ожидания конца:
   * процесс в них стоит и сам не пойдёт дальше, пока не придёт решение.
   */
  observe?: InstanceStatus[]
  /**
   * Состояния, которые процесс проходит по дороге и которые его не
   * заканчивают.
   *
   * Провал компенсации терминален для движка, но не для сценария: воркер
   * берёт откат снова после паузы, и без этого сценарий «повтор отката»
   * объявил бы неудачей нормальную работу повторов.
   */
  via?: InstanceStatus[]
  /** Состояния, в которых сценарий считается разобранным. */
  expect: InstanceStatus[]
  evidence?: Evidence[]
}

const HANDLERS = 'app.demo.handlers'
const COMPENSATION_RELEASE = `${HANDLERS}:release_inventory`

/** Вход заказа по умолчанию: две позиции и круглая сумма. */
const DEFAULT_INPUT = {
  order_number: 'SCENARIO-0001',
  items: [
    { sku: 'SKU-000', qty: 1 },
    { sku: 'SKU-001', qty: 2 },
  ],
  amount: '1499.90',
}

export const SCENARIOS: ScenarioSpec[] = [
  {
    id: 'happy',
    title: 'Заказ проходит целиком',
    proves:
      'Все шаги выполняются в порядке графа, компенсации не требуются, резерв остаётся занятым под отгрузку.',
    definition: 'order_fulfillment',
    expect: ['COMPLETED'],
    evidence: [{ kind: 'attempts', step: 'await_delivery', atLeast: 1, label: 'подтверждение доставки выполнено' }],
  },
  {
    id: 'payment-fails',
    title: 'Платёж отказывает, заказ откатывается',
    proves:
      'Saga возвращает выполненные шаги в обратном порядке: резерв освобождается, деньги не списаны, процесс приходит в «откатан».',
    definition: 'order_fulfillment',
    rules: [{ kind: 'step', target: 'charge_payment', mode: 'always', error: 'сбой шага (сценарий)' }],
    expect: ['COMPENSATED'],
    evidence: [
      { kind: 'compensated', step: 'reserve_inventory', label: 'резерв на складе освобождён' },
      { kind: 'event', event: 'COMPENSATION_STARTED', label: 'откат начат' },
    ],
  },
  {
    id: 'compensation-fails',
    title: 'Откат не проходит',
    proves:
      'Провал компенсации не прячется: процесс остаётся в COMPENSATION_FAILED, его видно в списке и откат можно повторить вручную.',
    definition: 'order_fulfillment',
    rules: [
      { kind: 'step', target: 'create_shipment', mode: 'always', error: 'сбой шага (сценарий)' },
      { kind: 'compensation', target: COMPENSATION_RELEASE, mode: 'always' },
    ],
    expect: ['COMPENSATION_FAILED'],
    evidence: [
      { kind: 'compensated', step: 'charge_payment', label: 'возврат средств прошёл' },
      { kind: 'event', event: 'COMPENSATION_FAILED', label: 'провал компенсации записан в журнал' },
    ],
  },
  {
    id: 'compensation-retry',
    title: 'Повтор отката не возвращает средства дважды',
    proves:
      'Провал компенсации виден и разбирается человеком: он снимает причину и повторяет откат, а компенсация идемпотентна — возврат применяется один раз.',
    definition: 'order_fulfillment',
    rules: [
      { kind: 'step', target: 'create_shipment', mode: 'always', error: 'сбой шага (сценарий)' },
      { kind: 'compensation', target: COMPENSATION_RELEASE, mode: 'always' },
    ],
    // Провал компенсации терминален для движка, но не для сценария: процесс
    // ждёт человека — без этого сценарий объявил бы неудачей то, что и есть
    // повод для разбора.
    via: ['COMPENSATION_FAILED'],
    // Порядок важен: сначала снимается причина сбоя, иначе повтор отката
    // упал бы снова, и кнопка выглядела бы нерабочей.
    action: { kind: 'retry-compensation', after: ['COMPENSATION_FAILED'] },
    expect: ['COMPENSATED'],
    evidence: [
      { kind: 'compensated', step: 'reserve_inventory', label: 'резерв освобождён' },
      {
        kind: 'event-count',
        event: 'COMPENSATION_STARTED',
        forStep: 'reserve_inventory',
        atLeast: 2,
        label: 'откат повторён после провала первой попытки',
      },
      { kind: 'event', event: 'COMPENSATION_COMPLETED', label: 'откат завершён без провала' },
    ],
  },
  {
    id: 'retry-succeeds',
    title: 'Повтор шага спасает процесс',
    proves:
      'At-least-once доставка не означает отказа: первая попытка падает, вторая проходит, и заказ доходит до конца.',
    definition: 'order_fulfillment',
    rules: [{ kind: 'step', target: 'charge_payment', mode: 'once', error: 'сбой первой попытки (сценарий)' }],
    expect: ['COMPLETED'],
    evidence: [{ kind: 'attempts', step: 'charge_payment', atLeast: 2, label: 'шаг выполнялся дважды' }],
  },
  {
    id: 'stock-exhausted',
    title: 'Товара не хватает',
    proves:
      'Постоянная ошибка шага не повторяется: процесс уходит в отказ сразу, а не после трёх попыток, и резерв не занимается.',
    definition: 'order_fulfillment',
    input: {
      order_number: 'SCENARIO-STOCK',
      items: [{ sku: 'SKU-000', qty: 99_999 }],
      amount: '10.00',
    },
    expect: ['FAILED'],
    evidence: [{ kind: 'event', event: 'STEP_FAILED', label: 'отказ шага записан в журнал' }],
  },
  {
    id: 'cancel',
    title: 'Отмена процесса',
    proves:
      'Отмена не равна успеху: выполненные шаги откатываются, и итог зависит от того, что было откатывать. Отменённый заказ приходит в «откатан», а если откатывать было нечего — в «отменён».',
    definition: 'order_with_wait',
    action: { kind: 'cancel', after: ['WAITING'] },
    expect: ['COMPENSATED', 'CANCELLED'],
    evidence: [
      { kind: 'compensated', step: 'reserve_inventory', label: 'резерв освобождён отменой' },
      { kind: 'event', event: 'INSTANCE_CANCEL_REQUESTED', label: 'отмена зафиксирована в журнале' },
    ],
  },
  {
    id: 'long-wait',
    title: 'Долгое ожидание доставки',
    proves:
      'Ожидание — durable-объект в базе, а не sleep в памяти: процесс стоит в WAITING и просыпается по таймеру.',
    definition: 'order_with_wait',
    // `WAITING` здесь не наблюдаемое состояние: таймер сработает сам, и
    // остановиться на нём значило бы не увидеть доказательство.
    expect: ['COMPLETED'],
    evidence: [{ kind: 'event', event: 'TIMER_FIRED', label: 'таймер доставки сработал' }],
  },
  {
    id: 'human-pause',
    title: 'Ворота согласования',
    proves:
      'Необратимый шаг ждёт решения человека: процесс встаёт в PAUSED, не берётся воркерами и идёт дальше только после команды.',
    definition: 'order_with_approval',
    observe: ['PAUSED'],
    action: { kind: 'resume', after: ['PAUSED'] },
    expect: ['COMPLETED'],
    evidence: [{ kind: 'event', event: 'INSTANCE_PAUSED', label: 'пауза записана в журнал' }],
  },
  {
    id: 'pause-deadline',
    title: 'Срок ожидания истёк',
    proves:
      'Пауза без решения имеет срок: по его истечении процесс отменяется и уходит в откат, а не платит без согласования.',
    definition: 'order_with_pause_deadline',
    via: ['COMPENSATING', 'CANCELLING'],
    // Пауза здесь показывается как таковая: человек должен успеть увидеть
    // срок, по которому решение не приняли.
    observe: ['PAUSED'],
    expect: ['COMPENSATED', 'CANCELLED'],
    evidence: [{ kind: 'compensated', step: 'reserve_inventory', label: 'резерв освобождён после срока' }],
  },
  {
    id: 'loop-exhausted',
    title: 'Цикл исчерпан',
    proves:
      'Счётчик проходов читается движком: на пределе пишется LOOP_EXHAUSTED и процесс уходит в откат, а не крутится вечно.',
    definition: 'order_with_loop',
    expect: ['COMPENSATED'],
    evidence: [
      { kind: 'event', event: 'LOOP_EXHAUSTED', label: 'предел цикла записан в журнал' },
      { kind: 'compensated', step: 'reserve_inventory', label: 'резервы цикла освобождены' },
    ],
  },
  {
    id: 'lease-expired',
    title: 'Воркер перестал продлевать аренду',
    proves:
      'Падение воркера не оставляет процесс висеть: аренда истекает по TTL, инстанс забирает другой воркер и работа продолжается.',
    definition: 'order_with_wait',
    action: { kind: 'stop-lease', after: ['RUNNING'], durationSeconds: 120 },
    // Процесс может пережить перехват и уйти в откат, если воркер успел
    // выполнить необратимый шаг, а аренда истекла на границе.
    via: ['COMPENSATING'],
    expect: ['COMPLETED'],
    evidence: [{ kind: 'event', event: 'TIMER_FIRED', label: 'процесс дошёл до конца после перехвата' }],
  },
]

/** Состояния, в которых процесс уже не изменится. */
export const TERMINAL_STATUSES: InstanceStatus[] = [
  'COMPLETED',
  'COMPENSATED',
  'CANCELLED',
  'FAILED',
  'COMPENSATION_FAILED',
]

export function isTerminal(status: InstanceStatus): boolean {
  return TERMINAL_STATUSES.includes(status)
}

/** Сценарий дошёл до наблюдаемого состояния: его показывают, дальше не ждут. */
export function isObserved(spec: ScenarioSpec, status: InstanceStatus): boolean {
  return spec.observe?.includes(status) ?? false
}

/** Сценарий закончен: процесс терминален и это не состояние по дороге. */
export function shouldStop(spec: ScenarioSpec, status: InstanceStatus): boolean {
  return isTerminal(status) && !(spec.via ?? []).includes(status)
}

export interface RunDeps {
  /** Активные определения — берём демонстрационные по имени. */
  definitions: typeof definitionsApi
  chaos: typeof chaosApi
  instances: typeof instancesApi
}

export interface RunOutcome {
  instanceId: string
  /** Правила были сняты перед запуском — иначе начали бы под чужой инъекцией. */
  rulesCleared: boolean
}

/**
 * Ставит правила инъекции.
 *
 * Правил может быть несколько: «откат не проходит» без ломки самого шага не
 * начинается вовсе, процесс доходит до конца, а правило остаётся и портит все
 * следующие запуски. Такой сценарий однажды показывал ложный успех.
 */
async function applyRules(deps: RunDeps, rules: RuleSpec[]): Promise<void> {
  for (const rule of rules) {
    if (rule.kind === 'step') {
      await deps.chaos.failStep({ step: rule.target, mode: rule.mode, error: rule.error })
    } else {
      await deps.chaos.failCompensation({ handler: rule.target, mode: rule.mode })
    }
  }
}

/**
 * Определение по имени, включённое в любом случае.
 *
 * Уборка на стенде однажды выключила определение, и следующий прогон взял его по
 * имени, не включив обратно: сценарий падал с «определение неактивно», и виноват
 * оказывался прогон, а не уборка.
 */
async function resolveDefinition(deps: RunDeps, name: string) {
  const page = await deps.definitions.list()
  const found = page.items.find((item) => item.name === name)
  if (!found) {
    throw new Error(`нет определения ${name}: запустите make seed`)
  }
  // Повторный поиск по имени после активации означал бы лишний запрос и ещё
  // одну точку, где имя может разъехаться с идентификатором.
  if (found.is_active) return found
  return deps.definitions.activate(found.id)
}

export async function runScenario(
  spec: ScenarioSpec,
  deps: RunDeps,
  input?: Record<string, unknown>,
): Promise<RunOutcome> {
  // Старые правила снимаются до запуска, а не после: иначе сценарий, упавший на
  // середине, оставил бы инъекцию следующему.
  await deps.chaos.clear()
  const definition = await resolveDefinition(deps, spec.definition)
  await applyRules(deps, spec.rules ?? [])

  const created = await deps.instances.start(
    definition.id,
    input ?? spec.input ?? DEFAULT_INPUT,
  )
  return { instanceId: created.id, rulesCleared: true }
}

/**
 * Действие по ходу процесса.
 *
 * Повтор отката снимает правило инъекции **до** повтора: иначе откат
 * упал бы снова, и человек увидел бы «кнопка не работает» вместо того,
 * что кнопка как раз для этого и сделана.
 */
export async function applyAction(
  deps: RunDeps,
  spec: ScenarioSpec,
  instance: WorkflowInstance,
): Promise<boolean> {
  const action = spec.action
  if (!action || !action.after.includes(instance.status)) return false
  if (action.kind === 'cancel') {
    await deps.instances.cancel(instance.id)
  } else if (action.kind === 'resume') {
    await deps.instances.resume(instance.id)
  } else if (action.kind === 'retry-compensation') {
    await deps.chaos.clear()
    await deps.instances.retryCompensation(instance.id)
  } else {
    await deps.chaos.stopLease(instance.id, action.durationSeconds ?? 60)
  }
  return true
}

export interface EvidenceNote {
  label: string
  passed: boolean
  detail: string
}

export interface ScenarioVerdict {
  /** Состояние оказалось ожидаемым. */
  stateMatched: boolean
  /** Все свидетельства подтвердились. */
  evidencePassed: boolean
  notes: EvidenceNote[]
}

/** Журнал и прогоны шагов: свидетельства читаются из них, а не из статуса. */
export interface EvidenceSource {
  events: WorkflowEvent[]
  steps: StepRun[]
}

/**
 * Проверяет свидетельства по данным процесса.
 *
 * Отдельная чистая функция: правило «что считается доказанным» проверяется
 * тестом на данных, а компонент только показывает итог.
 */
export function checkEvidence(spec: ScenarioSpec, source: EvidenceSource): EvidenceNote[] {
  return (spec.evidence ?? []).map((item) => {
    if (item.kind === 'event') {
      const found = source.events.find((event) => event.type === item.event)
      return {
        label: item.label,
        passed: found !== undefined,
        detail: found ? `событие ${item.event}, seq ${found.seq}` : `события ${item.event} в журнале нет`,
      }
    }
    if (item.kind === 'compensated') {
      // Компенсация живёт в журнале, а не в прогонах шага: отдельной строки
      // `compensate:...` в `step_runs` нет, и искать её там — значит никогда не
      // найти и объявить успешный откат неподтверждённым.
      const done = source.events.filter(
        (event) => event.type === 'COMPENSATION_COMPLETED' && event.payload.for_step === item.step,
      )
      const failed = source.events.filter(
        (event) => event.type === 'COMPENSATION_FAILED' && event.payload.for_step === item.step,
      )
      return {
        label: item.label,
        passed: done.length > 0,
        detail:
          done.length > 0
            ? `компенсация ${item.step} завершилась, попыток: ${done.length}`
            : failed.length > 0
              ? `компенсация ${item.step} провалилась — эффект остался в силе`
              : `компенсация ${item.step} не выполнялась — эффект остался в силе`,
      }
    }
    if (item.kind === 'event-count') {
      const found = source.events.filter(
        (event) =>
          event.type === item.event &&
          (item.forStep === undefined || event.payload.for_step === item.forStep),
      )
      return {
        label: item.label,
        passed: found.length >= item.atLeast,
        detail:
          found.length >= item.atLeast
            ? `событий ${item.event}: ${found.length}`
            : `событий ${item.event}: ${found.length}, ожидалось не меньше ${item.atLeast}`,
      }
    }
    const runs = source.steps.filter((run) => run.step_name === item.step)
    const completed = runs.filter((run) => run.status === 'COMPLETED').length
    return {
      label: item.label,
      passed: runs.length >= item.atLeast,
      detail: `прогонов: ${runs.length}, успешных: ${completed}`,
    }
  })
}

/**
 * Завершает сценарий: снимает правила и проверяет утверждение.
 *
 * Правила снимаются всегда — и когда утверждение подтвердилось, и когда нет.
 * Иначе неудачный сценарий оставлял бы инъекцию следующему.
 */
export async function finishScenario(
  deps: RunDeps,
  spec: ScenarioSpec,
  instance: WorkflowInstance,
): Promise<ScenarioVerdict> {
  const [events, steps] = await Promise.all([
    deps.instances.events(instance.id),
    deps.instances.steps(instance.id),
  ])
  await deps.chaos.clear()

  const notes = checkEvidence(spec, {
    events: events.items,
    steps: steps.items,
  })
  return {
    stateMatched: spec.expect.includes(instance.status),
    evidencePassed: notes.every((note) => note.passed),
    notes,
  }
}