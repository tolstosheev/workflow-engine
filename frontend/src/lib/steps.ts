/**
 * Заголовок шага: описание из схемы и имя рядом.
 *
 * Имя шага — идентификатор в схеме, и читать его на экране невозможно: рядом с
 * подписью «Платёж отклонён» человек видит `charge_payment`. Описание уже есть в
 * самой схеме («Списание средств — главный необратимый шаг») и на страницу просто
 * не выведено. Словарь имён в интерфейсе был бы второй копией данных схемы: правил
 * схему — правь два места, и однажды они разойдутся.
 *
 * Имя остаётся на экране, потому что разбор журнала без него невозможен: в отчёте,
 * в схеме и в вопросах «а какой это шаг» человек называет именно его.
 *
 * Шаг без описания (схема из редактора) показывается по имени: выдуманная подпись
 * хуже имени.
 */

import type { StepDefinition } from '@/types/api'

export interface StepTitle {
  /** То, что читает человек. */
  title: string
  /** Имя шага из схемы — вторым текстом; `null`, если показывать нечего. */
  identifier: string | null
}

export function stepTitle(
  steps: Record<string, StepDefinition> | null | undefined,
  name: string | null | undefined,
): StepTitle | null {
  if (name === null || name === undefined || name === '') return null
  const description = steps?.[name]?.description
  if (typeof description === 'string' && description !== '' && description !== name) {
    return { title: description, identifier: name }
  }
  return { title: name, identifier: null }
}

/**
 * Подмена имени шага для фраз журнала.
 *
 * Без схемы подмена возвращает имя: фраза «Шаг charge_payment выполнен» собирается
 * всегда, и подмена не должна ломать её там, где описания нет.
 */
export function stepResolver(
  steps: Record<string, StepDefinition> | null | undefined,
): (name: string) => string {
  return (name) => stepTitle(steps, name)?.title ?? name
}

/** Карта «имя → описание» для мест, где имя остаётся видимым, а подпись добавляется. */
export function stepDescriptions(
  steps: Record<string, StepDefinition> | null | undefined,
): Record<string, string> {
  const found: Record<string, string> = {}
  for (const name of Object.keys(steps ?? {})) {
    const title = stepTitle(steps, name)
    if (title?.identifier) found[name] = title.title
  }
  return found
}