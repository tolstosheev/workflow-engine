/**
 * Сценарии показа.
 *
 * Страница не добавляет ручек бэкенда: каждый сценарий собирается из уже
 * существующих вызовов — поставить правило инъекции, запустить процесс, дождаться
 * конца, снять правило. Проверяет она ровно то, что человек видит на защите:
 * сквозной проход, откат после отказа, провал отката и ручное решение.
 *
 * Функции отделены от компонента: порядок вызовов и снятие правила проверяются
 * тестом без разметки, и в этом порядке как раз и заключена суть — правило,
 * оставленное после сценария, ломает следующий.
 */

import { chaos as chaosApi, definitions as definitionsApi, instances as instancesApi } from '@/api/client'
import type { InstanceStatus, WorkflowInstance } from '@/types/api'

/** Определение демонстрации берётся по имени: активных может быть несколько. */
export const DEMO_DEFINITION = 'order_fulfillment'

export type RuleKind = 'step' | 'compensation'

export interface ScenarioSpec {
  id: string
  title: string
  /** Что сценарий должен доказать — это и есть текст для защиты. */
  proves: string
  /** Правило инъекции. Отсутствует у сценария «просто работает». */
  rule?: { kind: RuleKind; target: string }
  /** Состояния, в которых сценарий считается разобранным. */
  expect: InstanceStatus[]
}

export const SCENARIOS: ScenarioSpec[] = [
  {
    id: 'happy',
    title: 'Заказ проходит целиком',
    proves:
      'Все шаги выполняются, компенсации не требуются, резерв на складе остаётся занятым под отгрузку.',
    expect: ['COMPLETED'],
  },
  {
    id: 'payment-fails',
    title: 'Платёж отказывает, заказ откатывается',
    proves:
      'Saga возвращает выполненные шаги в обратном порядке: резерв освобождается, деньги не списаны, процесс приходит в «откатан».',
    rule: { kind: 'step', target: 'charge_payment' },
    expect: ['COMPENSATED', 'FAILED', 'COMPENSATION_FAILED'],
  },
  {
    id: 'compensation-fails',
    title: 'Откат не проходит',
    proves:
      'Провал компенсации не прячется: процесс остаётся в COMPENSATION_FAILED, его видно в списке и можно повторить откат вручную.',
    rule: { kind: 'compensation', target: 'app.demo.handlers:release_inventory' },
    expect: ['COMPENSATION_FAILED', 'COMPENSATED'],
  },
]

/** Правило ставится на «всегда»: разовое правило после первой попытки снимется. */
const FAILURE_MODE = 'always'

export interface RunDeps {
  /** Активные определения — берём демонстрационный по имени. */
  definitions: typeof definitionsApi
  chaos: typeof chaosApi
  instances: typeof instancesApi
}

export interface RunOutcome {
  instanceId: string
  /** Правило было снято — иначе следующий сценарий начнётся под ним. */
  ruleCleared: boolean
}

export async function runScenario(
  spec: ScenarioSpec,
  deps: RunDeps,
  input: Record<string, unknown> = { amount: '1499.90' },
): Promise<RunOutcome> {
  const definitions = await deps.definitions.list()
  const target = definitions.items.find(
    (item) => item.name === DEMO_DEFINITION && item.is_active,
  )
  if (!target) {
    throw new Error(`нет активного определения ${DEMO_DEFINITION}: запустите make seed`)
  }

  // Старые правила снимаются до запуска, а не после: иначе сценарий, упавший на
  // середине, оставил бы инъекцию следующему.
  await deps.chaos.clear()

  if (spec.rule) {
    if (spec.rule.kind === 'step') {
      await deps.chaos.failStep({
        step: spec.rule.target,
        mode: FAILURE_MODE,
        error: 'сбой шага (сценарий)',
      })
    } else {
      await deps.chaos.failCompensation({
        handler: spec.rule.target,
        mode: FAILURE_MODE,
      })
    }
  }

  const created = await deps.instances.start(target.id, input)
  return { instanceId: created.id, ruleCleared: true }
}

/** Снимает правило после того, как процесс пришёл в ожидаемое состояние. */
export async function finishScenario(
  deps: RunDeps,
  spec: ScenarioSpec,
  instance: WorkflowInstance,
): Promise<boolean> {
  if (spec.rule) await deps.chaos.clear()
  return spec.expect.includes(instance.status)
}