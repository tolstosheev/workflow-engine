/**
 * Заголовок шага: описание из схемы, имя — вторым текстом.
 *
 * Подпись объясняет шаг по-человечески, а имя остаётся рядом: оно идентификатор в
 * схеме, и без него разбор процесса невозможен.
 *
 * В узких списках (`showIdentifier={false}`) вторая строка убирается, а имя
 * уходит в подсказку: шесть шагов по две строки делали карточку состояния втрое
 * выше, и блок проверки уезжал за пределы видимости.
 */

import type { StepTitle } from '@/lib/steps'

export function StepLabel({
  title,
  showIdentifier = true,
}: {
  title: StepTitle | null
  showIdentifier?: boolean
}) {
  if (!title) return null
  // Полное имя в подсказке: без неё обрезанное описание не сказало бы, какой шаг
  // имеется в виду, а карточка отдаёт его одной строкой.
  const hint = title.identifier ? `${title.title} (${title.identifier})` : title.title
  return (
    <span className="min-w-0">
      <span className="block truncate" title={hint}>
        {title.title}
      </span>
      {showIdentifier && title.identifier && (
        <span className="block truncate font-mono text-[11px] text-white/40">{title.identifier}</span>
      )}
    </span>
  )
}