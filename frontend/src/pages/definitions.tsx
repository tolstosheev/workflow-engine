import { useCallback, useEffect, useState } from 'react'

import { definitions as definitionsApi, instances as instancesApi } from '@/api/client'
import { Button, Card, EmptyState, ErrorState, StatusBadge } from '@/components/ui'
import { formatDateTime } from '@/lib/status'
import type { DefinitionSummary, ValidationResult } from '@/types/api'

/**
 * Образец схемы: совпадает с тем, что загружает `scripts/seed.py`, но назван
 * иначе — определение с тем же именем и версией сохранить нельзя, сервер
 * вернул бы 409. Ожидание в конце нужно, чтобы процесс дошёл до состояния
 * «Ожидание»: без него он проходит за секунду и ждать нечего.
 */
const SAMPLE = `{
  "name": "demo_order",
  "definition": {
    "start": "validate_cart",
    "steps": {
      "validate_cart": {
        "action": "app.demo.handlers:validate_cart",
        "next": "reserve_inventory"
      },
      "reserve_inventory": {
        "action": "app.demo.handlers:reserve_inventory",
        "compensation": "app.demo.handlers:release_inventory",
        "next": "charge_payment"
      },
      "charge_payment": {
        "action": "app.demo.handlers:charge_payment",
        "compensation": "app.demo.handlers:refund_payment",
        "next": "create_shipment",
        "retry": { "max_attempts": 3, "backoff_seconds": 2 }
      },
      "create_shipment": {
        "action": "app.demo.handlers:create_shipment",
        "compensation": "app.demo.handlers:cancel_shipment",
        "next": "await_delivery"
      },
      "await_delivery": {
        "action": "app.demo.handlers:confirm_delivery",
        "wait_before_seconds": 3600
      }
    }
  }
}`

// Артикул берётся из склада, заполняемого `make seed`. Несуществующий
// артикул приводит к шагу, который падает с «товар не найден на складе».
const START_INPUT =
  '{\n  "items": [{ "sku": "SKU-000", "qty": 2 }],\n  "amount": "1499.90"\n}'

export default function DefinitionsPage() {
  const [list, setList] = useState<DefinitionSummary[]>([])
  const [source, setSource] = useState(SAMPLE)
  const [validation, setValidation] = useState<ValidationResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [selected, setSelected] = useState<string | null>(null)
  const [startInput, setStartInput] = useState(START_INPUT)
  const [busy, setBusy] = useState(false)
  const dirty = source !== SAMPLE || startInput !== START_INPUT

  // Несохранённый JSON терялся бы молча: браузер или F5 уносят
  // набранный текст. Без этого возвращение нашло бы его снова.
  useEffect(() => {
    if (!dirty) return
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault()
    }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty])

  const load = useCallback(async () => {
    try {
      const page = await definitionsApi.list({ limit: 100 })
      setList(page.items)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Не удалось загрузить определения')
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const save = async () => {
    // Кнопка во время выполнения должна быть заблокирована: save выполняет
    // три последовательных запроса, и второй клик создал бы
    // два одинаковых определения.
    if (busy) return
    setBusy(true)
    try {
      let parsed: Record<string, unknown>
      try {
        parsed = JSON.parse(source) as Record<string, unknown>
      } catch {
        // Нераспознаваемый JSON — обычное дело при вставке из буфера. Сообщение
        // движка на русской странице ничего не объясняет: «Unexpected token»
        // читается как отказ сервера, а это ошибка того, что введено в поле.
        setError('JSON не разбирается — проверьте синтаксис')
        setValidation(null)
        return
      }
      const name = String(parsed.name ?? '')
      const version = Number(parsed.version ?? 1)

      // Повторное сохранение того же образца — обычное дело, а не ошибка:
      // определение с тем же именем и версией создать нельзя. Предложение
      // обновить снимает тупик, в который попадает каждый, кто сохранил
      // образец дважды.
      const clash = list.find((item) => item.name === name && item.version === version)
      if (
        clash &&
        !window.confirm(
          `Определение «${name}» версии ${version} уже есть. Обновить его?`,
        )
      ) {
        return
      }
      // Обновление принимает только саму схему без имени и версии:
      // DefinitionUpdate с extra="forbid" отвергает name и version,
      // и запрос падал бы с 422.
      const definition = clash
        ? await definitionsApi.update(clash.id, {
            definition: parsed.definition,
            is_active: true,
          })
        : await definitionsApi.create(parsed)

      await definitionsApi.activate(definition.id)
      setError(null)
      setNotice(`Определение ${definition.name} сохранено и активировано`)
      await load()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Не удалось сохранить определение')
      setNotice(null)
    } finally {
      setBusy(false)
    }
  }

  /** Проверяет текст в редакторе до сохранения. */
  const validateSource = async () => {
    let parsed: unknown
    try {
      parsed = JSON.parse(source)
    } catch {
      setError('JSON не разбирается — проверьте синтаксис')
      setValidation(null)
      return
    }
    setError(null)
    setNotice(null)
    setValidation(await definitionsApi.validatePayload(parsed))
  }

  const validate = async (id: string) => {
    try {
      setValidation(await definitionsApi.validate(id))
      setError(null)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Проверка не выполнена')
    }
  }

  const start = async (definitionId: string) => {
    // Без такой блокировки двойной клик создаст два инстанса: запросы уйдут
    // почти одновременно, а пользователь увидит только один из них.
    if (busy) return
    setBusy(true)
    try {
      let input: Record<string, unknown>
      try {
        input = JSON.parse(startInput) as Record<string, unknown>
      } catch {
        // Причина та же, что и при сохранении: входные данные вводятся руками,
        // и обрывок в поле — обычное дело, а не сбой сервиса.
        setError('JSON не разбирается — проверьте синтаксис')
        setNotice(null)
        return
      }
      const created = await instancesApi.start(definitionId, input)
      setError(null)
      setNotice(`Процесс ${created.id} запущен`)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Не удалось запустить процесс')
      setNotice(null)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="space-y-4">
      <h1 className="text-xl font-semibold">Определения workflow</h1>

      {error && <ErrorState message={error} />}
      {notice && (
        <p className="rounded-md border border-success/40 bg-success/10 p-3 text-sm text-success">
          {notice}
        </p>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Редактор определения">
          <textarea
            aria-label="Определение workflow в формате JSON"
            value={source}
            onChange={(event) => setSource(event.target.value)}
            spellCheck={false}
            className="h-96 w-full rounded-md border border-surface-border bg-surface p-3 font-mono text-xs"
          />
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <Button variant="primary" disabled={busy} onClick={() => void save()}>
              Сохранить и активировать
            </Button>
            <Button disabled={busy} onClick={() => void validateSource()}>
              Проверить
            </Button>
            <Button
              disabled={busy || source === SAMPLE}
              onClick={() => {
                // Сброс затирает набранный JSON молча: без вопроса потеряться работа.
                if (window.confirm('Сбросить набранный текст определения?')) {
                  setSource(SAMPLE)
                  setValidation(null)
                  setError(null)
                }
              }}
            >
              Сбросить к образцу
            </Button>
            {dirty && !busy && (
              <span className="text-xs text-warning">Есть несохранённые изменения</span>
            )}
          </div>
          <p className="mt-2 text-xs text-white/40">
            «Проверить» разбирает текст и показывает проблемы без сохранения: ошибка в определении
            видна до того, как под неё создадут процесс.
          </p>
        </Card>

        <div className="space-y-4">
          <Card title="Запуск процесса">
            <p className="mb-2 text-sm text-white/60">
              Входные данные процесса. JSON потому, что его читает тот же бэкенд,
              что проверяет схему.
            </p>
            <textarea
              aria-label="Входные данные процесса в формате JSON"
              value={startInput}
              onChange={(event) => setStartInput(event.target.value)}
              spellCheck={false}
              className="h-40 w-full rounded-md border border-surface-border bg-surface p-3 font-mono text-xs"
            />
            {selected && (
              <Button className="mt-3" variant="primary" disabled={busy} onClick={() => void start(selected)}>
                Запустить выбранное определение
              </Button>
            )}
          </Card>

          <ValidationPanel validation={validation} />
        </div>
      </div>

      <Card title="Сохранённые определения">
        {list.length === 0 ? (
          <EmptyState message="Определений пока нет" />
        ) : (
          <table aria-label="Определения workflow" className="w-full text-left text-sm">
            <thead className="text-xs uppercase tracking-wide text-white/40">
              <tr>
                <th scope="col" className="pb-2">Имя</th>
                <th scope="col" className="pb-2">Версия</th>
                <th scope="col" className="pb-2">Состояние</th>
                <th scope="col" className="pb-2">Создано</th>
                <th scope="col" className="pb-2">Действия</th>
              </tr>
            </thead>
            <tbody>
              {list.map((item) => (
                <tr key={item.id} className="border-t border-surface-border/60">
                  <td className="py-2">{item.name}</td>
                  <td className="py-2 text-white/50">{item.version}</td>
                  <td className="py-2">
                    <StatusBadge
                      label={item.is_active ? 'активно' : 'не активно'}
                      toneClass={
                        item.is_active
                          ? 'bg-success/15 text-success border-success/40'
                          : 'bg-surface-border text-white/60'
                      }
                    />
                  </td>
                  <td className="py-2 text-white/50">{formatDateTime(item.created_at)}</td>
                  <td className="py-2">
                    <div className="flex gap-2">
                      {/* Выбор виден только цветом кнопки: с клавиатуры и для
                          скринридера было непонятно, какое из определений сейчас
                          выбрано, а от этого зависит карточка запуска. */}
                      <Button aria-pressed={selected === item.id} onClick={() => setSelected(item.id)}>
                        Выбрать
                      </Button>
                      <Button onClick={() => void validate(item.id)}>Проверить</Button>
                      <Button disabled={busy} onClick={() => void start(item.id)}>Запустить</Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </section>
  )
}

/**
 * Ошибки и предупреждения показываются раздельно: ошибка означает, что процесс
 * не запустится, предупреждение — что он откатится не полностью.
 */
function ValidationPanel({ validation }: { validation: ValidationResult | null }) {
  if (!validation) {
    return (
      <Card title="Проверка определения">
        <EmptyState message="Нажмите «Проверить» у определения" />
      </Card>
    )
  }

  return (
    <Card title="Проверка определения">
      <StatusBadge
        label={validation.valid ? 'Ошибок нет' : `Ошибок: ${validation.errors.length}`}
        toneClass={
          validation.valid
            ? 'bg-success/15 text-success border-success/40'
            : 'bg-danger/15 text-danger border-danger/40'
        }
      />
      <IssueList title="Ошибки" issues={validation.errors} tone="text-danger" />
      <IssueList title="Предупреждения" issues={validation.warnings} tone="text-warning" />
    </Card>
  )
}

function IssueList({
  title,
  issues,
  tone,
}: {
  title: string
  issues: ValidationResult['errors']
  tone: string
}) {
  if (issues.length === 0) return null
  return (
    <div className="mt-3">
      <p className="mb-1 text-xs uppercase tracking-wide text-white/40">{title}</p>
      <ul className="space-y-1 text-xs">
        {issues.map((issue, index) => (
          <li key={`${issue.code}-${index}`} className={tone}>
            {issue.step ? `${issue.step}: ` : ''}
            {issue.message}
          </li>
        ))}
      </ul>
    </div>
  )
}