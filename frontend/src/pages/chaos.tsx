import { useCallback, useEffect, useState } from 'react'

import { chaos as chaosApi } from '@/api/client'
import { Button, Card, EmptyState, ErrorState } from '@/components/ui'

const MODES = [
  { value: 'once', label: 'Один раз' },
  { value: 'always', label: 'Всегда' },
  { value: 'timeout', label: 'Зависание' },
] as const

const SERVICES = [
  { value: 'inventory', label: 'Склад' },
  { value: 'payment', label: 'Платежи' },
  { value: 'shipment', label: 'Доставка' },
] as const

/** Шаги заказа: подставляются по имени, без правки текста вручную. */
const PRESETS = [
  'validate_cart',
  'reserve_inventory',
  'charge_payment',
  'create_shipment',
  'confirm_delivery',
] as const

/**
 * Панель инъекции отказов.
 *
 * Кнопки ломают систему намеренно: без них нельзя показать ни компенсацию, ни
 * восстановление после падения воркера. Для защиты такие сценарии включаются
 * заранее, а не «на лету» посреди показа.
 */
export default function ChaosPage() {
  const [active, setActive] = useState<string[]>([])
  const [mode, setMode] = useState<(typeof MODES)[number]['value']>('always')
  const [step, setStep] = useState('charge_payment')
  // Инстанс указывается для двух разных действий, и одно поле не годится на
  // оба: адресат инъекции и инстанс с остановленной арендой — разные вещи,
  // а одно значение на оба поля означало бы, что введённый адрес молча
  // применяется к действию, о котором оператор не думал.
  const [failInstanceId, setFailInstanceId] = useState('')
  const [leaseInstanceId, setLeaseInstanceId] = useState('')
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      setActive((await chaosApi.list()).active)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Не удалось получить список инъекций')
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const run = async (action: () => Promise<unknown>) => {
    try {
      await action()
      setError(null)
      await load()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Операция не выполнена')
    }
  }

  return (
    <section className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold">Инъекция отказов</h1>
        <p className="mt-1 text-sm text-white/60">
          Правила применяются исполнителем шага до вызова обработчика. Режим
          «Зависание» проверяет, что аренда продлевается во время долгой работы.
        </p>
      </div>

      {error && <ErrorState message={error} />}

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Сломать шаг">
          <p className="mb-3 text-sm text-white/60">
            Пресет ломает шаг на любом инстансе: правило адресное, параллельные прогоны не мешают
            друг другу.
          </p>
          <div className="mb-3 flex flex-wrap gap-2">
            {PRESETS.map((preset) => (
              <Button
                key={preset}
                variant="secondary"
                aria-pressed={step === preset}
                onClick={() => setStep(preset)}
                className="font-mono"
              >
                {preset}
              </Button>
            ))}
          </div>

          <label htmlFor="chaos-step" className="mb-1 block text-xs uppercase tracking-wide text-white/40">
            Шаг
          </label>
          <input
            id="chaos-step"
            value={step}
            onChange={(event) => setStep(event.target.value)}
            className="mb-3 w-full rounded-md border border-surface-border bg-surface px-3 py-1.5 font-mono text-sm"
          />

          <div className="mb-1 text-xs uppercase tracking-wide text-white/40" id="chaos-mode-label">
            Режим
          </div>
          <div role="group" aria-labelledby="chaos-mode-label" className="mb-3 flex gap-2">
            {MODES.map((option) => (
              <Button
                key={option.value}
                variant={mode === option.value ? 'primary' : 'secondary'}
                aria-pressed={mode === option.value}
                onClick={() => setMode(option.value)}
              >
                {option.label}
              </Button>
            ))}
          </div>

          <label htmlFor="chaos-instance-fail" className="mb-1 block text-xs uppercase tracking-wide text-white/40">
            Инстанс (необязательно)
          </label>
          <input
            id="chaos-instance-fail"
            value={failInstanceId}
            onChange={(event) => setFailInstanceId(event.target.value)}
            placeholder="любой инстанс"
            className="mb-3 w-full rounded-md border border-surface-border bg-surface px-3 py-1.5 font-mono text-xs"
          />

          <Button
            variant="danger"
            onClick={() =>
              void run(() =>
                chaosApi.failStep({
                  step: step || null,
                  mode,
                  instance_id: failInstanceId || null,
                  error: 'сбой шага (инъекция)',
                }),
              )
            }
          >
            Сломать шаг
          </Button>
        </Card>

        <Card title="Отключить сервис">
          <p className="mb-3 text-sm text-white/60">
            Отключается шаг сервиса: склад — reserve_inventory, платежи —
            charge_payment, доставка — create_shipment.
          </p>
          <div className="flex flex-wrap gap-2">
            {SERVICES.map((service) => (
              <Button
                key={service.value}
                variant="danger"
                onClick={() => void run(() => chaosApi.failService(service.value))}
              >
                {service.label}
              </Button>
            ))}
          </div>
        </Card>

        <Card title="Остановить воркер">
          <p className="mb-3 text-sm text-white/60">
            Продление аренды прекращается, инстанс освобождается по TTL, и его
            подхватывает другой воркер — это и есть проверка восстановления.
          </p>
          <label htmlFor="chaos-instance-lease" className="mb-1 block text-xs uppercase tracking-wide text-white/40">
            Инстанс
          </label>
          <input
            id="chaos-instance-lease"
            value={leaseInstanceId}
            onChange={(event) => setLeaseInstanceId(event.target.value)}
            placeholder="идентификатор инстанса"
            className="mb-3 w-full rounded-md border border-surface-border bg-surface px-3 py-1.5 font-mono text-xs"
          />
          <div className="flex gap-2">
            <Button
              variant="danger"
              disabled={!leaseInstanceId}
              onClick={() => void run(() => chaosApi.stopLease(leaseInstanceId, 40))}
            >
              Остановить на 40 с
            </Button>
            <Button
              disabled={!leaseInstanceId}
              onClick={() => void run(() => chaosApi.resumeLease(leaseInstanceId))}
            >
              Возобновить
            </Button>
          </div>
        </Card>

        <Card title="Активные правила">
          {active.length === 0 ? (
            <EmptyState message="Инъекций нет" />
          ) : (
            <>
              <ul className="space-y-1 font-mono text-xs">
                {active.map((rule) => (
                  <li key={rule} className="text-white/70">
                    {rule}
                  </li>
                ))}
              </ul>
              <Button className="mt-3" onClick={() => void run(() => chaosApi.clear())}>
                Снять все
              </Button>
            </>
          )}
        </Card>
      </div>
    </section>
  )
}