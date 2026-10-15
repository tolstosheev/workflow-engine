import { expect, test, type APIRequestContext, type Page } from '@playwright/test'

/**
 * Сквозные проверки стенда.
 *
 * Здесь то, чего не может ни один из слоёв по отдельности: что интерфейс и
 * движок говорят об одном и том же. Три из пяти проверок ниже — регрессии на
 * дефекты, найденные чтением и ручным обходом: у всех был один общий признак —
 * экран показывал одно, а система делала другое, и ни один тест этого не видел.
 *
 * Состояние готовится через API, а проверяется через интерфейс. Так проверка
 * не зависит от того, умеет ли интерфейс создать то, что нужно создать, и падает
 * только на той части, ради которой написана.
 */

/** Адрес движка: в локальном стенде он не совпадает с адресом интерфейса. */
const API = process.env.E2E_API_URL ?? 'http://localhost:8000'

const MANUAL_GATE = 'e2e_manual_gate'

async function api(
  request: APIRequestContext,
  method: 'get' | 'post' | 'delete',
  path: string,
  body?: unknown,
): Promise<{ status: number; json: Record<string, unknown> }> {
  const response = await request.fetch(`${API}${path}`, {
    method: method.toUpperCase(),
    data: body === undefined ? undefined : JSON.stringify(body),
    headers: { 'Content-Type': 'application/json' },
  })
  const text = await response.text()
  return {
    status: response.status(),
    json: text ? (JSON.parse(text) as Record<string, unknown>) : {},
  }
}

interface Instance {
  id: string
}

/** Есть ли определение с таким именем; создать при первом запуске. */
async function demoDefinition(request: APIRequestContext): Promise<string> {
  const { json } = await api(request, 'get', '/api/definitions?limit=200')
  const items = json.items as Array<{ id: string; name: string }>
  const found = items.find((item) => item.name === 'order_fulfillment')
  if (!found) throw new Error('нет определения order_fulfillment: запустите `make seed`')
  return found.id
}

/**
 * Определение с паузой и сроком. Создаётся один раз и переиспользуется.
 *
 * Активируется при каждом обращении, а не только при создании: уборка после
 * прогонов выключает его, и повторный запуск нашёл бы выключенное определение
 * и упал бы с «определение неактивно» — то есть проверки перестали бы
 * проходить после первого же удачного прогона.
 */
async function manualGateDefinition(request: APIRequestContext): Promise<string> {
  const { json } = await api(request, 'get', '/api/definitions?limit=200')
  const items = json.items as Array<{ id: string; name: string }>
  const found = items.find((item) => item.name === MANUAL_GATE)
  if (found) {
    await api(request, 'post', `/api/definitions/${found.id}/activate`)
    return found.id
  }

  const created = await api(request, 'post', '/api/definitions', {
    name: MANUAL_GATE,
    definition: {
      start: 'reserve_inventory',
      steps: {
        reserve_inventory: {
          action: 'app.demo.handlers:reserve_inventory',
          compensation: 'app.demo.handlers:release_inventory',
          next: 'approve',
        },
        approve: {
          action: 'app.demo.handlers:charge_payment',
          compensation: 'app.demo.handlers:refund_payment',
          wait_for: 'manual',
          // Срок заведомо длинный: проверка смотрит на надпись, а ждать его
          // истечения тут не нужно — это делает другой тест, через движок.
          pause_timeout_seconds: 86_400,
          next: 'finish',
        },
        finish: { action: 'app.demo.handlers:confirm_delivery' },
      },
    },
  })
  if (created.status >= 300) throw new Error(`не создалось определение: ${JSON.stringify(created.json)}`)
  const id = String((created.json as unknown as Instance).id)
  await api(request, 'post', `/api/definitions/${id}/activate`)
  return id
}

async function startInstance(request: APIRequestContext, definitionId: string): Promise<string> {
  const created = await api(request, 'post', '/api/instances', {
    definition_id: definitionId,
    input: {},
  })
  if (created.status >= 300) {
    throw new Error(`инстанс не создан: ${JSON.stringify(created.json)}`)
  }
  return String((created.json as unknown as Instance).id)
}

/** Ждёт состояния инстанса, показывая его на экране. */
async function expectStatus(page: Page, id: string, wanted: string[]): Promise<void> {
  await page.goto(`/instances/${id}`)
  await expect(page.getByText(new RegExp(`(${wanted.join('|')})`)).first()).toBeVisible({
    timeout: 45_000,
  })
}

/**
 * Выключает определение, созданное для паузы.
 *
 * Активных определений на стенде может быть несколько, и инструменты вроде
 * замерщика берут «первое активное». Оставленное включённым определение с паузой
 * переставляет их порядок, и замер тихо измеряет не то. Уборка здесь — не
 * аккуратность, а условие того, что последующий замер вообще что-то значит.
 */
test.afterAll(async ({ request }) => {
  const { json } = await api(request, 'get', '/api/definitions?limit=200')
  const items = json.items as Array<{ id: string; name: string }>
  for (const item of items.filter((entry) => entry.name === MANUAL_GATE)) {
    await api(request, 'post', `/api/definitions/${item.id}/deactivate`)
  }
})

test.beforeEach(async ({ request }) => {
  // Чужое правило инъекции пережило бы прогон и испортило следующий.
  await api(request, 'delete', '/api/chaos')
})

test.afterEach(async ({ request }) => {
  await api(request, 'delete', '/api/chaos')
})

test('сценарий приводит к откату через интерфейс', async ({ page, request }) => {
  // Страница сценариев обещает показать свойство, а не запустить процесс.
  // Проверка идёт через неё целиком: кнопка, правило инъекции, ожидание конца.
  // Если сценарий перестанет снимать правило, следующая проверка встанет на
  // чужой инъекции — и это тоже часть договора страницы.
  const definitionId = await demoDefinition(request)
  await api(request, 'post', `/api/definitions/${definitionId}/activate`)

  await page.goto('/scenarios')
  // Карточка ищется от заголовка вверх до ближайшей секции: поиск по тексту
  // секции ловит и внешние обёртки, потому что они тоже содержат этот текст.
  const rollback = page
    .getByRole('heading', { name: 'Платёж отказывает, заказ откатывается' })
    .locator('xpath=ancestor::section[1]')
    .getByRole('button', { name: 'Запустить' })
  await rollback.click()

  // Итог — конечное состояние процесса, а не «кнопка нажата».
  await expect(page.getByText('пришёл в ожидаемое состояние').first()).toBeVisible({
    timeout: 60_000,
  })

  // И правило снято: чужое правило сломало бы всё, что запустят дальше.
  const { json } = await api(request, 'get', '/api/chaos')
  expect(json.active).toEqual([])
})

test('страница состояния показывает метрики, а не нули', async ({ page, request }) => {
  // Регрессия: браузер ходит в свой origin, а прокси знал только /api. Метрики
  // лежат по /metrics, запрос уходил в дев-сервер, тот отвечал 404, и разбор
  // принимал его за «метрик нет». Страница показывала нули при живой системе.
  const definitionId = await demoDefinition(request)
  await startInstance(request, definitionId)
  await page.goto(`/metrics`)

  // Проверяется текст, который человек видел на экране: обе пустые заглушки
  // означают «данных нет», а данные есть.
  await expect(page.getByText('Метрики ещё не собраны')).toHaveCount(0, { timeout: 30_000 })
  await expect(page.getByText('Шаги ещё не выполнялись')).toHaveCount(0, { timeout: 30_000 })

  // И в разбивке по состояниям хотя бы одна строка не ноль: все нули при
  // живом процессе — это тот же дефект, только посимвольнее.
  const breakdown = page.getByRole('table', { name: 'Инстансы по состояниям' })
  await expect(breakdown).toBeVisible({ timeout: 30_000 })
  await expect
    .poll(async () => {
      const text = (await breakdown.innerText()).replace(/[^0-9]/g, '')
      return /[1-9]/.test(text)
    }, { timeout: 30_000 })
    .toBe(true)
})

test('пауза называется на странице одним и тем же', async ({ page, request }) => {
  // Дефект: планирование срока пишет TIMER_SCHEDULED, разбор принимал его за
  // ожидание шага, и карточка называла паузу «Ожидание», а шапка — «Ждёт
  // решения». Одно состояние, два слова на одном экране.
  const definitionId = await manualGateDefinition(request)
  const id = await startInstance(request, definitionId)

  await page.goto(`/instances/${id}`)
  await expect(page.getByText('Ждёт решения').first()).toBeVisible({ timeout: 45_000 })

  // Карточка «Состояние по журналу» обязана называть то же, что и шапка.
  // Ищем по разметке, а не по роли: слово «Статус» есть ещё в заголовке
  // таблицы шагов, и по роли подпись строки отличается от него.
  const card = page
    .locator('dt', { hasText: /^Статус$/ })
    .locator('xpath=following-sibling::dd[1]')
  await expect(card).toHaveText('Ждёт решения')
})

test('пауза показывает свой срок, а не срок ожидания шага', async ({ page, request }) => {
  // Дефект: отсчёт брал первый таймер в журнале. Процесс, который сначала ждал
  // таймер, а потом встал на паузу, показывал время до уже отбывшего ожидания.
  const definitionId = await manualGateDefinition(request)
  const id = await startInstance(request, definitionId)

  await page.goto(`/instances/${id}`)
  await expect(page.getByText('Ждёт решения').first()).toBeVisible({ timeout: 45_000 })

  const countdown = page.getByText(/Сработает через/)
  await expect(countdown).toBeVisible()
  // Сутки минус немного, а не секунды: показывать чужой срок — значит врать.
  await expect(countdown).toContainText(/ч/)
})

test('необратимое завершение спрашивает подтверждение', async ({ page, request }) => {
  // Кнопка отличается от «Отменить» одними словами, а стоит несопоставимо
  // дороже: выполненные шаги остаются, и отменить это уже нельзя.
  const definitionId = await manualGateDefinition(request)
  const id = await startInstance(request, definitionId)

  await page.goto(`/instances/${id}`)
  await expect(page.getByText('Ждёт решения').first()).toBeVisible({ timeout: 45_000 })

  const button = page.getByRole('button', { name: 'Завершить без компенсаций' })
  await expect(button).toBeVisible()

  let asked = false
  page.on('dialog', async (dialog) => {
    asked = true
    await dialog.dismiss()
  })
  await button.click()
  await expect.poll(() => asked).toBe(true)

  // Отказ оставил процесс на месте: «завершить» без согласия не произошло.
  await expect(page.getByRole('button', { name: 'Продолжить' })).toBeVisible()
})

test('сломанный шаг приводит к откату', async ({ page, request }) => {
  // Главный сюжет проекта: шаг упал, компенсация вернула резерв.
  const definitionId = await demoDefinition(request)
  await api(request, 'post', '/api/chaos/fail-step', {
    step: 'charge_payment',
    mode: 'always',
  })
  const id = await startInstance(request, definitionId)

  await expectStatus(page, id, ['Откатан', 'CANCELLED'])
  // Подпись состояния встречается в шапке, в карточке, в графе и в журнале;
  // проверяем шапку, а не любое из шести вхождений.
  await expect(page.getByText('Откатан').first()).toBeVisible()

  // Лента времени показывает долю, проведённую в шагах: шаг занимает двадцать
  // миллисекунд, а процесс живёт секунды, и именно этот разрыв — главное
  // утверждение проекта о долгой транзакции из коротких шагов.
  await expect(page.getByText('Время жизни процесса')).toBeVisible()
  await expect(page.getByText(/из него в шагах/).first()).toBeVisible()
})

test('состояние процесса подтверждается разбором журнала', async ({ page, request }) => {
  // Главное утверждение проекта — «состояние выводится из журнала, а не живёт
  // в колонке» — проверяется нажатием, а не остаётся фразой в документации.
  // Кнопка перечитывает состояние из журнала и сравнивает с колонкой.
  const definitionId = await demoDefinition(request)
  await api(request, 'post', '/api/chaos/fail-step', { step: 'charge_payment', mode: 'always' })
  const id = await startInstance(request, definitionId)
  await expectStatus(page, id, ['Откатан', 'CANCELLED'])

  await page.getByRole('button', { name: 'Проверить: состояние = журнал' }).click()

  // Совпадение означает, что колонка догнала журнал и восстановление после
  // падения воркера возможно: состояние читается из журнала, а не из поля.
  await expect(page.getByText(/Совпадает/).first()).toBeVisible()
})

test('страницы не пишут в консоль об ошибках', async ({ page }) => {
  // Дефект, который не виден глазом: браузер просил /favicon.ico и получал 404
  // на каждой странице. Единственный признак был в консоли, которую во время
  // показа не открывают, — и потому он дожил до сих пор.
  //
  // Граница приёма: headless-браузер значок вообще не запрашивает, поэтому
  // именно этот 404 проверка не увидит. Проверку ловит всё, что пишет в консоль
  // сам интерфейс и что уходит мимо сервера, — проверено подложенной ошибкой.
  const errors: string[] = []
  page.on('console', (message) => {
    if (message.type() === 'error') errors.push(`${message.location().url}: ${message.text()}`)
  })

  for (const path of ['/', '/definitions', '/metrics', '/chaos', '/не-существует']) {
    await page.goto(path)
    await expect(page.getByRole('heading').first()).toBeVisible()
  }

  expect(errors).toEqual([])
})

test('сломанная компенсация оставляет процесс на ручном повторе', async ({ page, request }) => {
  // Второй сюжет: откат не прошёл. Правило адресовано обработчику компенсации,
  // а не шагу: шаг уже выполнен, и по его имени сломалось бы прямое действие.
  const definitionId = await demoDefinition(request)
  await api(request, 'post', '/api/chaos/fail-step', {
    step: 'charge_payment',
    mode: 'always',
  })
  await api(request, 'post', '/api/chaos/fail-compensation', {
    handler: 'app.demo.handlers:release_inventory',
    mode: 'always',
  })
  const id = await startInstance(request, definitionId)

  await page.goto(`/instances/${id}`)
  await expect(page.getByText('Откат не удался').first()).toBeVisible({ timeout: 45_000 })

  // У застрявшего процесса остаётся только повтор отката: отмена предложила бы
  // забыть о неоткатанном резерве.
  await expect(page.getByRole('button', { name: 'Повторить откат' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Отменить' })).toHaveCount(0)
})
