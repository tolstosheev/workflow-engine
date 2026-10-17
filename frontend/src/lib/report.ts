/**
 * Отчёт о процессе — текст, который читают без работающего стенда.
 *
 * В отчёте есть то, из-за чего разбирают: что было сделано, сколько это
 * заняло, где были пробелы и куда процесс пришёл.
 */


import type { InstanceState, StepRun, TimelineEntry, WorkflowEvent, WorkflowInstance } from '@/types/api'
import { statusLabel } from '@/lib/status'

/** Миллисекунды в разборной дате либо `null`: эпоха вместо мусора — тоже ложь. */
function millis(value: string | null | undefined): number | null {
  if (!value) return null
  const parsed = Date.parse(value)
  return Number.isNaN(parsed) ? null : parsed
}

/** Миллисекунды в подписи: ниже секунды — миллисекунды, дальше — с половинкой. */
function duration(from: number | null, to: number | null): string {
  if (from === null || to === null) return '—'
  const span = to - from
  if (span < 0) return '—'
  if (span < 1000) return `${span} мс`
  if (span < 60_000) return `${(span / 1000).toFixed(1)} с`
  return `${Math.round(span / 60_000)} мин`
}

/** Дата в местном времени читателя: отчёт открывает человек, а не машина. */
function stamp(value: string | null | undefined): string {
  if (!value) return '—'
  const parsed = millis(value)
  if (parsed === null) return '—'
  return new Date(parsed).toLocaleString('ru-RU')
}

export interface ReportInput {
  instance: WorkflowInstance
  state: InstanceState | null
  steps: StepRun[]
  timeline: TimelineEntry[]
  events: WorkflowEvent[]
}

export function buildInstanceReport(input: ReportInput): string {
  const { instance, state, steps, timeline, events } = input
  const lines: string[] = []

  const started = millis(instance.created_at)
  const finished = millis(instance.updated_at)

  lines.push(`# Отчёт о процессе ${instance.definition.name}`)
  lines.push('')
  lines.push(`- Процесс: \`${instance.id}\``)
  lines.push(`- Определение: ${instance.definition.name}, версия ${instance.definition.version}`)
  lines.push(`- Состояние: ${statusLabel(instance.status)}`)
  lines.push(`- Текущий шаг: ${instance.current_step ?? '—'}`)
  lines.push(`- Создан: ${stamp(instance.created_at)}`)
  lines.push(`- Полная длительность: ${duration(started, finished)}`)
  if (instance.error) lines.push(`- Ошибка: ${instance.error}`)
  if (instance.archived_at) lines.push(`- Убран из списка (архив): ${stamp(instance.archived_at)}`)

  // Состояние из разбора журнала — отдельной строкой и с пометкой. Расхождение
  // с колонкой для читателя — признак, который стоит увидеть сразу, а не искать
  // глазами между таблицами.
  if (state) {
    lines.push(
      `- Состояние по журналу: ${statusLabel(state.status)}${
        state.status === instance.status ? '' : ' — РАСХОДИТСЯ с колонкой'
      }`,
    )
  }

  lines.push('')
  lines.push('## Шаги')
  if (steps.length === 0) {
    lines.push('Шагов не было.')
  } else {
    lines.push('| Шаг | Попытка | Итог | Начало | Длительность |')
    lines.push('|---|---|---|---|---|')
    for (const step of steps) {
      const from = millis(step.started_at)
      const to = millis(step.finished_at)
      lines.push(
        `| ${step.step_name} | ${step.attempt} | ${step.status} | ${stamp(step.started_at)} | ${duration(from, to)} |`,
      )
    }
  }

  lines.push('')
  lines.push('## Ход времени')
  if (timeline.length === 0) {
    lines.push('Данных о ходе времени нет.')
  } else {
    for (const entry of timeline) {
      const from = millis(entry.started_at)
      const to = millis(entry.finished_at)
      lines.push(
        `- ${entry.kind}${entry.step_name ? ` ${entry.step_name}` : ''}: ${
          entry.started_at ? stamp(entry.started_at) : '—'
        } → ${duration(from, to)}`,
      )
    }
    // Пробелы между шагами — половина интересного: ожидание, повтор или решение
    // человека занимают больше времени, чем сами шаги.
    const inSteps = steps.reduce((sum, step) => {
      const from = millis(step.started_at)
      const to = millis(step.finished_at)
      return from !== null && to !== null ? sum + (to - from) : sum
    }, 0)
    const lastEvent = timeline.length > 0 ? millis(timeline[timeline.length - 1].finished_at) : null
    const total = (lastEvent ?? finished ?? 0) - (started ?? 0)
    // Доля печатается только когда обе величины осмысленны. Прыгающая
    // длительность шага дала бы отрицательный процент времени — в отчёте,
    // который читают при разборе, это выглядит как враньё.
    if (started !== null && total > 0 && inSteps > 0 && inSteps < total) {
      lines.push('')
      lines.push(
        `В шагах прошло ${duration(0, inSteps)} из ${duration(0, total)} — ${Math.round(
          (inSteps / total) * 100,
        )} % времени; остальное ожидание, повторы или решение человека.`,
      )
    }
  }

  lines.push('')
  lines.push('## Журнал')
  if (events.length === 0) {
    lines.push('Событий нет.')
  } else {
    for (const event of events) {
      const detail = Object.keys(event.payload ?? {}).length
        ? ` ${JSON.stringify(event.payload)}`
        : ''
      lines.push(`${event.seq}. ${stamp(event.created_at)} ${event.type}${detail}`)
    }
  }

  return lines.join('\n')
}

/** Имя файла: без него отчёт сохранялся бы как `report`. */
export function reportFileName(instance: WorkflowInstance): string {
  return `отчёт-${instance.definition.name}-${instance.id.slice(0, 8)}.md`
}