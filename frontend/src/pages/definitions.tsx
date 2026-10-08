import { useCallback, useEffect, useState } from 'react'

import { definitions as definitionsApi, instances as instancesApi } from '@/api/client'
import { Button, Card, EmptyState, ErrorState, StatusBadge } from '@/components/ui'
import { formatDateTime } from '@/lib/status'
import type { DefinitionSummary, ValidationResult } from '@/types/api'

const SAMPLE = `{
  "name": "order_fulfillment",
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
        "compensation": "app.demo.handlers:refund",
        "next": "create_shipment",
        "retry": { "max_attempts": 3, "backoff_seconds": 2 }
      },
      "create_shipment": {
        "action": "app.demo.handlers:create_shipment",
        "compensation": "app.demo.handlers:cancel_shipment",
        "next": "confirm_delivery"
      },
      "confirm_delivery": {
        "action": "app.demo.handlers:confirm_delivery"
      }
    }
  }
}`

const START_INPUT = '{\n  "items": [{ "sku": "SKU-1", "qty": 2 }],\n  "amount": "1499.90"\n}'

export default function DefinitionsPage() {
  const [list, setList] = useState<DefinitionSummary[]>([])
  const [source, setSource] = useState(SAMPLE)
  const [validation, setValidation] = useState<ValidationResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [selected, setSelected] = useState<string | null>(null)
  const [startInput, setStartInput] = useState(START_INPUT)

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
    try {
      const parsed = JSON.parse(source)
      const created = await definitionsApi.create(parsed)
      await definitionsApi.activate(created.id)
      setError(null)
      setNotice(`Определение ${created.name} сохранено и активировано`)
      await load()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Не удалось сохранить определение')
      setNotice(null)
    }
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
    try {
      const created = await instancesApi.start(definitionId, JSON.parse(startInput))
      setError(null)
      setNotice(`Инстанс ${created.id} запущен`)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Не удалось запустить процесс')
      setNotice(null)
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
            value={source}
            onChange={(event) => setSource(event.target.value)}
            spellCheck={false}
            className="h-96 w-full rounded-md border border-surface-border bg-surface p-3 font-mono text-xs"
          />
          <div className="mt-3 flex gap-2">
            <Button variant="primary" onClick={() => void save()}>
              Сохранить и активировать
            </Button>
            <Button onClick={() => setSource(SAMPLE)}>Сбросить к образцу</Button>
          </div>
        </Card>

        <div className="space-y-4">
          <Card title="Запуск процесса">
            <p className="mb-2 text-sm text-white/60">
              Входные данные инстанса. JSON потому, что его читает тот же бэкенд,
              что проверяет дефинишен.
            </p>
            <textarea
              value={startInput}
              onChange={(event) => setStartInput(event.target.value)}
              spellCheck={false}
              className="h-40 w-full rounded-md border border-surface-border bg-surface p-3 font-mono text-xs"
            />
            {selected && (
              <Button className="mt-3" variant="primary" onClick={() => void start(selected)}>
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
          <table className="w-full text-left text-sm">
            <thead className="text-xs uppercase tracking-wide text-white/40">
              <tr>
                <th className="pb-2">Имя</th>
                <th className="pb-2">Версия</th>
                <th className="pb-2">Состояние</th>
                <th className="pb-2">Создано</th>
                <th className="pb-2">Действия</th>
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
                      <Button onClick={() => setSelected(item.id)}>Выбрать</Button>
                      <Button onClick={() => void validate(item.id)}>Проверить</Button>
                      <Button onClick={() => void start(item.id)}>Запустить</Button>
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