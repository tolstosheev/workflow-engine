/**
 * Язык надписей интерфейса.
 *
 * На экране не должно быть слов внутреннего устройства: «процесс», «откат»,
 * «исполнитель», «закрепление» — это имена таблиц, полей и решений, а не то, что человек
 * делает. Словарь и обоснование — в `docs/UI-GLOSSARY.md`.
 *
 * Проверка смотрит исходники без комментариев: комментарий объясняет решение
 * разработчика и остаётся на языке проекта, а надпись обращается к человеку.
 * Файлы проверок не смотрятся — они повторяют надписи, и их правка происходит
 * вместе с ними.
 */

import { describe, expect, it } from 'vitest'

import { SOURCES } from './sources'

/**
 * Слово, которого не должно быть в надписях, и чем оно заменено.
 *
 * Границы слова не задаются: в JavaScript `\b` работает по ASCII, и кириллица
 * стоящим рядом с кавычкой словом не считается — с такой границей проверка
 * молча пропускала всё, что и требовала найти.
 */
const FORBIDDEN: Array<[RegExp, string]> = [
  [/инстанс/iu, 'процесс заказа'],
  [/откат/iu, 'компенсация'],
  [/откатан/iu, 'компенсация'],
  [/воркер/iu, 'исполнитель'],
  [/аренд/iu, 'закрепление'],
  [/дефинишен/iu, 'схема процесса'],
  [/инъекц/iu, 'внесение неисправностей'],
  [/\bheartbeat\b/iu, 'активность'],
  [/прогон/iu, 'выполнение шага'],
]

/** Комментарии не являются надписями: строки с двумя слэшами и блоки в звёздочках. */
function stripComments(source: string): string {
  return source.replace(/\/\*[\s\S]*?\*\//gu, ' ').replace(/\/\/[^\n]*/gu, ' ')
}

function isTestFile(path: string): boolean {
  return /\.test\.tsx?$/u.test(path)
}

function offenders(): string[] {
  const found: string[] = []
  for (const [path, source] of Object.entries(SOURCES)) {
    if (isTestFile(path) || path.includes('uiLanguage.test')) continue
    const text = stripComments(source)
    text.split('\n').forEach((line, index) => {
      for (const [pattern, replacement] of FORBIDDEN) {
        const match = pattern.exec(line)
        if (match) found.push(`${path}:${index + 1}: «${match[0]}» → ${replacement}`)
      }
    })
  }
  return found
}

describe('язык надписей', () => {
  it('не оставляет в надписях слов внутреннего устройства', () => {
    const found = offenders()

    // Показываются все вхождения, а не первые пять: список — это работа на
    // один заход, и молчаливое усечение заставило бы искать остаток по
    // одному.
    expect(found, `слова вне словаря:\n${found.join('\n')}`).toEqual([])
  })

  it('видит файлы, которые проверяет', () => {
    // Страж, который ничего не читает, был бы зелёным всегда: пустое
    // множество проходит проверку «слов нет». Поэтому число файлов здесь
    // утверждается явно.
    const checked = Object.keys(SOURCES).filter((path) => !isTestFile(path))

    expect(checked.length).toBeGreaterThan(40)
    expect(checked).toContain('/src/lib/status.ts')
  })
})