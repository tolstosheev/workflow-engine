/**
 * Заголовок шага: описание из схемы, имя — вторым текстом.
 *
 * Подпись объясняет шаг по-человечески, а имя остаётся рядом: оно идентификатор в
 * схеме, и без него разбор процесса невозможен.
 *
 * В узких списках (`showIdentifier={false}`) вторая строка убирается, а имя
 * уходит в текст только для программ чтения с экрана: описание занимает одну
 * строку, а имя обязано остаться доступным без мыши — подсказка через `title`
 * большинством скринридеров игнорируется и появляется только под курсором.
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
  const identifier = title.identifier
  return (
    <span className="min-w-0">
      <span className="block truncate" title={identifier ? `${title.title} (${identifier})` : undefined}>
        {title.title}
      </span>
      {showIdentifier && identifier && (
        <span className="block truncate font-mono text-[11px] text-white/40">{identifier}</span>
      )}
      {!showIdentifier && identifier && (
        // Имя не видно глазом, но остаётся в дереве доступности: иначе вопрос
        // «а какой это шаг» на списке не задать без мыши.
        <span className="sr-only"> шаг {identifier}</span>
      )}
    </span>
  )
}