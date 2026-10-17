/**
 * Заголовок шага: описание из схемы, имя — вторым текстом.
 *
 * Подпись объясняет шаг по-человечески, а имя остаётся рядом: оно идентификатор в
 * схеме, и без него разбор процесса невозможен.
 */

import type { StepTitle } from '@/lib/steps'

export function StepLabel({ title }: { title: StepTitle | null }) {
  if (!title) return null
  return (
    <span className="min-w-0">
      <span className="block truncate">{title.title}</span>
      {title.identifier && (
        <span className="block truncate font-mono text-[11px] text-white/40">{title.identifier}</span>
      )}
    </span>
  )
}