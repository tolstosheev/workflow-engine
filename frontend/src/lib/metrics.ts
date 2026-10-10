/**
 * Разбор текста метрик Prometheus.
 *
 * Парсится текстом, а не запрашивается структурой: эндпоинт отдаёт формат
 * OpenMetrics, и единственный способ получить те же числа в интерфейсе —
 * разобрать текст. Полноценная клиентская библиотека для одного показателя
 * не окупилась бы.
 *
 * Модуль без React: разбор стоит проверять тестами, и он ничего не знает об
 * интерфейсе.
 */

/** Значение одного образца: метрика с набором меток. */
export interface Sample {
  name: string
  labels: Record<string, string>
  value: number
}

const SAMPLE = /^([a-zA-Z_:][a-zA-Z0-9_:]*)(\{(.*)\})?\s+([-+0-9.eE]+|NaN|[+-]?Inf)/

/** Разбирает одну строку в образец. `null`, если строка не образец. */
export function parseSample(line: string): Sample | null {
  const match = SAMPLE.exec(line.trim())
  if (!match) return null

  const [, name, , labelPart, rawValue] = match
  const value = Number(rawValue)
  return {
    name,
    labels: parseLabels(labelPart ?? ''),
    value: Number.isNaN(value) ? 0 : value,
  }
}

function parseLabels(text: string): Record<string, string> {
  if (!text) return {}
  const labels: Record<string, string> = {}
  // Разбор вручную, а не JSON: кавычки и запятые внутри значений встречаются
  // (имя воркера, текст ошибки), и JSON их не примет без экранирования.
  const pattern = /([a-zA-Z_][a-zA-Z0-9_]*)="((?:[^"\\]|\\.)*)"/g
  for (const match of text.matchAll(pattern)) {
    labels[match[1]] = match[2].replace(/\\"/g, '"')
  }
  return labels
}

/** Все образцы из текста метрик. Служебные строки `#` пропускаются. */
export function parseMetrics(text: string): Sample[] {
  const samples: Sample[] = []
  for (const line of text.split('\n')) {
    if (!line.trim() || line.trimStart().startsWith('#')) continue
    const sample = parseSample(line)
    if (sample) samples.push(sample)
  }
  return samples
}

/** Сумма образцов метрики; без меток — все с указанным значением метки. */
export function sumBy(samples: Sample[], name: string, label?: [string, string]): number {
  return samples
    .filter((sample) => sample.name === name)
    .filter((sample) => !label || sample.labels[label[0]] === label[1])
    .reduce((total, sample) => total + sample.value, 0)
}

/** Последнее значение метрики по набору меток. */
export function valueOf(samples: Sample[], name: string, labels: Record<string, string>): number {
  const found = samples.find(
    (sample) =>
      sample.name === name &&
      Object.entries(labels).every(([key, value]) => sample.labels[key] === value),
  )
  return found?.value ?? 0
}

/** Значения одного лейбла по указанной метрике: статус или имя шага. */
export function labelsOf(samples: Sample[], name: string, label: string): string[] {
  return [...new Set(samples.filter((sample) => sample.name === name).map((s) => s.labels[label]))]
    .filter((value): value is string => value !== undefined)
    .sort()
}