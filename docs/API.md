# API

HTTP-интерфейс движка. Базовый адрес — `http://localhost:8000`.
Интерактивная схема — `/docs` (Swagger UI), `/redoc`, спецификация — `/openapi.json`.

Все примеры предполагают `Content-Type: application/json` и `Accept: application/json`.

---

## 1. Общие соглашения

| Аспект | Соглашение |
|---|---|
| Формат | JSON, кодировка UTF-8 |
| Даты | ISO 8601 в UTC: `2026-10-08T12:34:56.789Z` |
| Идентификаторы | UUID в виде строки |
| Деньги | Десятичная строка: `"1499.90"` — не число, чтобы не потерять точность |
| Ошибки | `{"detail": "текст"}` |
| Пагинация | `limit` + `offset`, ответ `{items, total, limit, offset}` |

### Коды ошибок

| Код | Когда |
|---|---|
| `400` | Некорректный запрос, не прошедший бизнес-проверку |
| `404` | Объект не найден |
| `409` | Конфликт: инстанс в неподходящем статусе, дубликат, компенсация не удалась |
| `422` | Нарушена схема или семантика графа дефинишена |
| `500` | Внутренняя ошибка сервера |

---

## 2. Сервисные методы

### `GET /health`

```bash
curl http://localhost:8000/health
```

```json
{ "status": "ok" }
```

### `GET /metrics`

Метрики Prometheus в экспозиционном формате. Порт отдельный не выделяется:
эндпоинт закрыт на уровне сети в эксплуатации, в разработке доступен свободно.

| Метрика | Тип | Что показывает |
|---|---|---|
| `workflow_instances{status}` | gauge | Инстансы по состояниям |
| `workflow_instances_created_total` | counter | Создано инстансов |
| `workflow_steps{step,result}` | gauge | Исполнения шагов: `success`/`failure` |
| `workflow_step_duration_seconds` | histogram | Длительность шагов |
| `workflow_compensations{step,result}` | gauge | Попытки компенсаций |
| `workflow_lease_acquired_total` | counter | Успешные захваты аренды |
| `workflow_lease_lost_total` | counter | Потерянные аренды |

Метрики состояния читаются из БД при каждом сборе, а не ведутся в памяти:
после перезапуска воркера счётчики обнулились бы, тогда как реальная картина
по инстансам сохранилась. Статусы с нулевым значением публикуются явно — иначе
статус просто исчезает с графика, и падение выглядит как «инстансы пропали».

---

## 3. Определения workflow

### `GET /api/definitions`

Список дефинишенов.

| Параметр | Тип | Описание |
|---|---|---|
| `limit` | int | По умолчанию 50, максимум 200 |
| `offset` | int | По умолчанию 0 |
| `is_active` | bool | Фильтр по активности |

```bash
curl "http://localhost:8000/api/definitions?limit=10"
```

```json
{
  "items": [
    {
      "id": "6f1a1f7c-2b4e-4b7a-9d3a-0a1b2c3d4e5f",
      "name": "order_fulfillment",
      "version": 1,
      "is_active": true,
      "steps": ["validate_cart", "reserve_inventory", "charge_payment", "create_shipment", "confirm_delivery"],
      "created_at": "2026-10-08T12:00:00Z"
    }
  ],
  "total": 1,
  "limit": 10,
  "offset": 0
}
```

### `GET /api/definitions/{id}`

Полное описание дефинишена вместе с телом графа.

### `POST /api/definitions`

Создание дефинишена.

```bash
curl -X POST http://localhost:8000/api/definitions \
  -H "Content-Type: application/json" \
  -d '{
    "name": "order_fulfillment",
    "version": 1,
    "definition": {
      "start": "validate_cart",
      "steps": {
        "validate_cart": {
          "action": "app.demo.handlers.inventory:validate_cart",
          "next": "reserve_inventory"
        },
        "reserve_inventory": {
          "action": "app.demo.handlers.inventory:reserve_inventory",
          "compensation": "app.demo.handlers.inventory:release_inventory",
          "next": "charge_payment"
        },
        "charge_payment": {
          "action": "app.demo.handlers.payment:charge",
          "compensation": "app.demo.handlers.payment:refund",
          "next": "create_shipment",
          "retry": {"max_attempts": 3, "backoff_seconds": 2}
        },
        "create_shipment": {
          "action": "app.demo.handlers.shipment:create",
          "compensation": "app.demo.handlers.shipment:cancel",
          "next": "confirm_delivery"
        },
        "confirm_delivery": {
          "action": "app.demo.handlers.shipment:confirm",
          "wait_before_seconds": 60
        }
      }
    }
  }'
```

**Валидация дефинишена** (`422` при нарушении):

| Проверка | Пример ошибки |
|---|---|
| `start` существует в `steps` | `поле 'start' ссылается на неизвестный шаг 'validate' — такого шага в определении нет` |
| Все `next` разрешаются | `шаг 'reserve_inventory': переход 'next' указывает на неизвестный шаг 'reserved'` |
| Все компенсации разрешаются | `шаг 'charge_payment': компенсация 'app.demo:refund_v2' не существует` |
| Нет циклов без явного признака | `цикл по шагам ['a','b'] не объявлен через loop: true` |
| `loop` имеет предел | `шаг 'a': цикл без максимального числа итераций — процесс не завершится` |
| Обработчики импортируемы | `шаг 'validate_cart': обработчик 'app.demo:missing' не найден` |
| Параллельные ветки не исполняются | `шаг 'split': параллельные ветки (fork) объявлены, но не исполняются — движок обрабатывает один шаг за раз` |

Отказ на `fork` / `join` — сознательное решение, а не недостаток проверки.
Раньше такие определения сохранялись, а процесс выполнял префикс графа и
отчитывался об успехе. Причина — [ADR-0008](adr/0008-parallelnye-vetki-otkaz.md).

**Ошибки** отвергают сохранение, **предупреждения** — нет: процесс запустится,
но при откате откатится не полностью.

### `PUT /api/definitions/{id}`

Обновление дефинишена. Действующие инстансы не затрагиваются: они ссылаются на
конкретную версию.

### `POST /api/definitions/{id}/validate`

Проверка без сохранения.

```json
{ "valid": true, "errors": [], "warnings": ["шаг 'confirm_delivery' не имеет компенсации — при откате он выполнит побочные эффекты безвозвратно"] }
```

### `POST /api/definitions/validate`

Проверка текста определения без сохранения.

| Параметр | Тип | Обязательно |
|---|---|---|
| `definition` | object | тело определения |

Отвечает тем же, что и проверка сохранённой версии, но не создаёт версию:
владелец в редакторе держит несохранённый текст и должен видеть ошибки до
того, как что-то сохранит.

### `POST /api/definitions/{id}/activate` · `POST /api/definitions/{id}/deactivate`

Переключение активной версии для запуска.

---

## 4. Инстансы

### `GET /api/instances`

| Параметр | Тип | Описание |
|---|---|---|
| `status` | string | Фильтр по состоянию |
| `definition_id` | uuid | Фильтр по дефинишену |
| `limit` / `offset` | int | Пагинация |
| `archived` | `active` \| `archived` \| `all` | Кого показывать. По умолчанию `active` — архивные скрыты, иначе уборка списка ничего бы не убирала |

```json
{
  "items": [
    {
      "id": "b2c3d4e5-f6a7-4b8c-9d0e-1f2a3b4c5d6e",
      "definition": {"name": "order_fulfillment", "version": 1},
      "status": "RUNNING",
      "current_step": "charge_payment",
      "input": {"items": [{"sku": "SKU-1", "qty": 2}], "amount": "1499.90"},
      "lease_owner": "worker-a1b2c3d4",
      "lease_expires_at": "2026-10-08T12:05:00Z",
      "created_at": "2026-10-08T12:00:00Z",
      "updated_at": "2026-10-08T12:03:20Z"
    }
  ],
  "total": 1,
  "limit": 50,
  "offset": 0,
  "archived_total": 0
}
```

Поле `archived_total` нужно клиенту, чтобы кнопка уборки знала, сколько
процессов она убирает.

### `POST /api/instances`

Запуск workflow.

```bash
curl -X POST http://localhost:8000/api/instances \
  -H "Content-Type: application/json" \
  -d '{
    "definition_id": "6f1a1f7c-2b4e-4b7a-9d3a-0a1b2c3d4e5f",
    "input": {"items": [{"sku": "SKU-1", "qty": 2}], "amount": "1499.90"}
  }'
```

`201` → создан инстанс в статусе `PENDING`. Воркер подхватит его на ближайшем
цикле; принудительный запуск не требуется.

`409`, если дефинишен неактивен или уже исполняется.

### `GET /api/instances/{id}`

Полное состояние инстанса: статус, текущий шаг, lease, переменные, ошибка.

### `GET /api/instances/{id}/events`

Журнал событий.

| Параметр | Тип | Описание |
|---|---|---|
| `from_seq` | int | Начиная с события |
| `limit` | int | По умолчанию 200 |

```json
{
  "items": [
    {"seq": 1, "type": "INSTANCE_CREATED", "payload": {}, "created_at": "2026-10-08T12:00:00Z"},
    {"seq": 2, "type": "STEP_STARTED", "payload": {"step": "reserve_inventory", "attempt": 1}, "created_at": "2026-10-08T12:00:01Z"},
    {"seq": 3, "type": "STEP_COMPLETED", "payload": {"step": "reserve_inventory"}, "created_at": "2026-10-08T12:00:03Z"}
  ],
  "total": 3
}
```

### `GET /api/instances/{id}/steps`

Прогоны шагов из таблицы `step_runs`, по возрастанию времени старта.

| Параметр | Тип | По умолчанию |
|---|---|---|
| `limit` | int | до 500 |

### `GET /api/instances/{id}/timeline`

Единая лента для интерфейса: шаги, события и компенсации в порядке времени.
Отдаются последние записи: шаги и события выбираются по отдельному с тем же
пределом и сливаются по времени, поэтому начало ленты всегда достоверно.

| Параметр | Тип | По умолчанию |
|---|---|---|
| `limit` | int | до 500 |

Обе выборки ограничиваются в самом запросе, а не после чтения: журнал
процесса долгой транзакции растёт месяцами, и читать его целиком ради первых строк не нужно.

### `GET /api/instances/{id}/state`

Состояние, восстановленное из журнала: то, что увидит воркер после подхвата.

```json
{
  "status": "RUNNING",
  "current_step": "charge_payment",
  "variables": {"items_count": 2, "reserved_units": 2},
  "completed_steps": ["validate_cart", "reserve_inventory"],
  "pending_compensations": [],
  "is_terminal": false,
  "step_iterations": {"validate_cart": 1, "reserve_inventory": 1}
}
```

`pending_compensations` — шаги, которые ещё ждут отката, в обратном порядке
выполнения. Список пуст, пока откат не начался, и укорачивается по мере
прохождения компенсаций; у завершённого процесса он пуст, потому что
компенсировать нечего. Считается по журналу, а не по структуре определения:
определение не знает, какие шаги успели выполниться, а шаг цикла выполняется
несколько раз и откатывается по прогонам (ADR-0024).

`step_iterations` — сколько раз шаг успешно завершился: по нему интерфейс
показывает «проход 2 из 3» у шага с `max_iterations`. Поле выводится из
журнала, поэтому подхват воркера даёт то же значение, что видел упавший.

### `GET /api/instances/{id}/exists`

Диагностическая проверка без выборки целого инстанса.

| Параметр | Тип | Обязательно |
|---|---|---|
| `instance_id` | uuid | идентификатор из пути |

Отвечает `{"exists": true}` или `{"exists": false}`. Интерфейсом не
используется: пригодно для разбора в терминале, когда надо проверить, что
идентификатор из логов ещё существует.

### `POST /api/instances/{id}/resume`

Снимает паузу: процесс ждал решения человека и продолжает работу с того же
шага (ADR-0014).

`409`, если инстанс не на паузе: возобновлять процесс, который идёт или уже
завершён, нечего, и молчаливое изменение состояния скрыло бы ошибку вызова.

Отмена работает и из паузы: компенсировать выполненные шаги нужно независимо
от того, чем процесс занимался.

### `POST /api/instances/{id}/cancel`

Запрос отмены. Ответ отдаёт статус `CANCELLING` — отмена ещё не выполнена.

Дальше инстанс уходит в откат: `COMPENSATING` → `COMPENSATED`, если были
выполненные шаги с компенсациями. Если компенсировать нечего, процесс
завершается как `CANCELLED` — отката не было, и называть его компенсацией
было бы враньём.

Ожидания инстанса при этом снимаются: оставленный таймер разбудил бы уже
откатанный процесс.

```text
POST /cancel → CANCELLING → COMPENSATING → COMPENSATED
                              └── (нечего откатывать) → CANCELLED
```

```bash
curl -X POST http://localhost:8000/api/instances/b2c3d4e5-f6a7-4b8c-9d0e-1f2a3b4c5d6e/cancel
```

`409`, если инстанс уже в терминальном состоянии.

### `POST /api/instances/{id}/complete`

Форсированное завершение без компенсаций. Требует `{"reason": "..."}`: приёмка
в обход компенсаций оставляет побочные эффекты, и это должно быть отражено
в данных. Принудительное завершение снимает и неотменённые таймеры — иначе
таймер разбудил бы уже завершённый инстанс.

```bash
curl -X POST http://localhost:8000/api/instances/{id}/complete \
  -H "Content-Type: application/json" \
  -d '{"reason": "клиент подтвердил вручную"}'
```

### `GET /api/workers`

Реестр воркеров (ADR-0018). Отдаёт и тех, кто не отвечает: упавший воркер
не удаляется из реестра, иначе падение выглядело бы как отсутствие воркера, а
вопрос был как раз в том, жив ли он.

Параметр `departed` (по умолчанию `false`) включает записи старше суток.
Скрытие — только в выдаче: в базе строки на месте и возвращаются по
`?departed=true` (ADR-0021). Их количество отдаётся всегда полем
`departed_total`, чтобы переключатель не выглядел пустым.

```json
{
  "items": [
    {
      "worker_id": "workflow-engine-api-1-4f2a9c81",
      "state": "active",
      "started_at": "2026-10-14T12:00:00Z",
      "heartbeat_at": "2026-10-14T12:04:31Z",
      "stopped_at": null,
      "cycles_completed": 1284,
      "steps_executed": 3971,
      "lease_lost": 0
    }
  ],
  "fresh_for_seconds": 3.0,
  "departed_total": 0
}
```

`state` — `active` (сердце свежее), `stale` (молчит дольше `fresh_for_seconds`)
или `stopped` (остановлен штатно). Граница молчания — тройной интервал опроса;
отдаётся вместе со списком, чтобы подпись «не отвечает» не была загадкой.

### `GET /api/demo/inventory`

Остатки склада демонстрационной области. Только чтение: ручек изменения нет и
не будет — человек не должен освобождать резерв кнопкой и наблюдать «успех»
там, где демонстрируется компенсация (ADR-0020).

```json
{
  "items": [
    { "sku": "SKU-000", "available": 10000, "reserved": 3 }
  ]
}
```

Резерв снимается при подтверждении доставки: товар у покупателя, холд на
складе больше не нужен. Проверку возврата делает `make smoke` по снимкам базы,
а эта ручка показывает состояние.

### `POST /api/instances/archive`

Убирает завершённые процессы из списка (ADR-0017). **Это не удаление:** журнал
процесса остаётся целым, а состояние по-прежнему выводится его повтором.
Убираются только терминальные процессы — `PAUSED` ждёт решения человека, а
`COMPENSATION_FAILED` ждёт повтора отката, и спрятать их значило бы спрятать
незакрытое.

Необязательное поле `before` ограничивает уборку возрастом: берутся только
процессы, созданные раньше срока.

```bash
curl -X POST http://localhost:8000/api/instances/archive \
  -H "Content-Type: application/json" -d '{}'
```

```json
{"archived": 42, "message": "в архив убрано процессов: 42"}
```

### `POST /api/instances/{id}/unarchive`

Возвращает процесс из архива в список. Отвечает `404`, если процесс не был
архивирован: такой инстанс существует и жив, и называть его «не найденным в
архиве» — единственное честное, что здесь можно сказать.

```bash
curl -X POST http://localhost:8000/api/instances/{id}/unarchive
```

### `POST /api/instances/{id}/compensation/retry`

Повтор компенсации после `COMPENSATION_FAILED`.

```json
{ "status": "COMPENSATING", "message": "откат поставлен на повтор" }
```

`409`, если откат не требовался: инстанс не в состоянии `COMPENSATION_FAILED`.

---

## 5. Живые обновления (SSE)

### `GET /api/stream`

Поток событий всех инстансов.

```bash
curl -N http://localhost:8000/api/stream
```

```
id: 2026-10-10T12:00:03.145+00:00
event: STEP_COMPLETED
data: {"instance_id":"b2c3d4e5-...","type":"STEP_COMPLETED","payload":{"step":"charge_payment","next":"create_shipment","output":{}},"created_at":"2026-10-10T12:00:03.145+00:00"}
```

Поля `id` — это время последнего события, и браузер передает его в
`Last-Event-ID` при переподключении. Поток продолжается с момента
подключения, а не с эпохи: раньше новое подключение проверяло бы
всю историю журнала.

| Тип события | Когда |
|---|---|
| `instance_status` | Изменился статус инстанса |
| `step_event` | Новое событие в журнале |
| `compensation` | Изменилось состояние компенсации |
| `heartbeat` | Служебное, раз в `SSE_HEARTBEAT_SECONDS` |

Фактический поток отдаёт события журнала, а не отдельные типы:

```
event: STEP_COMPLETED
data: {"instance_id":"b2c3d4e5-...","type":"STEP_COMPLETED","payload":{"step":"reserve_inventory","output":{...}},"created_at":"2026-10-08T12:00:03Z"}
```

Публикуются `INSTANCE_CREATED`, `INSTANCE_COMPLETED`, `INSTANCE_COMPENSATED`,
`INSTANCE_FAILED`, `INSTANCE_CANCEL_REQUESTED`, `INSTANCE_CANCELLED`,
`LOOP_EXHAUSTED`, `STEP_COMPLETED`,
`STEP_FAILED`, `COMPENSATION_STARTED`, `COMPENSATION_COMPLETED`,
`COMPENSATION_FAILED`. `LEASE_EXPIRED` не публикуется: на экране он ничего
не меняет и только увеличивает поток.

Пользователь теперь опроса через `sse_poll_seconds` (по умолчанию 1 с), а heartbeat шётся отдельно в
`sse_heartbeat_seconds` (15 с) и независит от опроса на саму явные события.

Heartbeat нужен, чтобы прокси и балансировщики не закрывали соединение по
таймауту простоя. Заголовки ответа: `Cache-Control: no-cache` и
`X-Accel-Buffering: no` — без второго прокси соберёт все события в пачку.

Фронтенд получает поток через `EventSource`. Прокси настроен только на путь
`/api`: `EventSource` не позволяет задавать заголовки, поэтому CORS
настраивается на конкретные origins вместо заголовка `Authorization`.

---

## 6. Инъекция отказов

Эндпоинты для демонстрации отказоустойчивости. Не для эксплуатации.

Правила хранятся в таблице `chaos_rules`, а не в памяти API: воркер работает
в отдельном процессе и должен увидеть инъекцию, зарегистрированную через HTTP.
Проверка правила выполняется непосредственно перед вызовом обработчика и
**после** проверки идемпотентности: ломать уже применённый шаг бессмысленно.

Поля `instance_id` и `step` необязательны. Пустое поле означает «любой»:
правило без инстанса действует на все инстансы, без шага — на все шаги.

### `POST /api/chaos/fail-step`

```bash
curl -X POST http://localhost:8000/api/chaos/fail-step \
  -H "Content-Type: application/json" \
  -d '{"instance_id": "b2c3d4e5-...", "step": "charge_payment", "mode": "always", "error": "payment gateway timeout"}'
```

| Поле | Описание |
|---|---|
| `instance_id` | Инстанс; `null` — все инстансы |
| `step` | Шаг; `null` — все шаги инстанса |
| `mode` | `once`, `always`, `timeout` |
| `error` | Текст ошибки |

Режим `timeout` имитирует зависший сервис: обработчик не возвращает результат,
пока не истечёт таймаут шага. Это проверяет, что lease продлевается во время
долгой работы.

Режим `once` помечается израсходованным при первом применении — иначе «сломать
один раз» ломало бы все последующие шаги.

### `POST /api/chaos/fail-service`

Отключает шаг предметной области: `{"service": "payment"}` →
правило на шаг `charge_payment`.

### `POST /api/chaos/fail-compensation`

Помечает обработчик компенсации отказывающим (ADR-0015).

| Параметр | Тип | Обязательно |
|---|---|---|
| `handler` | строка | ссылка на обработчик, `модуль:функция` |
| `instance_id` | uuid \| null | только этот инстанс; без значения — любой |
| `mode` | `once` \| `always` | режим; по умолчанию `always` |
| `error` | строка | текст ошибки |

`handler` обязателен: пустой адрес означал бы «сломать любое возмещение», а это
не демонстрация сбоя, а порча стенда целиком. Обработчик резолвится сразу, и
`404`, если его нет или он вне разрешённых модулей: правило, которое никогда
не сработает, выглядит на панели как работающая инъекция.

Режим `timeout` не принимается: зависание проверяется на прямом шаге, где есть
продление аренды, и для отката нужен отказ, а не ожидание.

Адрес отличается от адреса шага намеренно: правило по имени шага не может
уронить откат, потому что шаг уже выполнен, и такое правило сработало бы на
прямом шаге, а не на возврате.

### `POST /api/chaos/stop-lease`

Останавливает продление lease для инстанса — имитация зависшего воркера.

```bash
curl -X POST http://localhost:8000/api/chaos/stop-lease \
  -H "Content-Type: application/json" \
  -d '{"instance_id": "b2c3d4e5-...", "duration_seconds": 40}'
```

Сдвигает `heartbeat_at` в прошлое: heartbeat истекает вместе с lease, и инстанс
освобождается по TTL.

### `POST /api/chaos/resume-lease`

Возвращает `heartbeat_at` в настоящее.

### `GET /api/chaos` · `DELETE /api/chaos`

Список активных инъекций и снятие всех разом.

---

## 7. Типичные последовательности

### Успешное выполнение

```bash
# 1. Создать дефинишен
curl -X POST http://localhost:8000/api/definitions -H "Content-Type: application/json" -d @definition.json

# 2. Запустить инстанс
curl -X POST http://localhost:8000/api/instances -H "Content-Type: application/json" -d @instance.json

# 3. Наблюдать
curl -N http://localhost:8000/api/stream

# 4. Проверить журнал
curl http://localhost:8000/api/instances/{id}/events
```

### Сбой с компенсацией

```bash
# 1. Запустить инстанс
curl -X POST http://localhost:8000/api/instances -H "Content-Type: application/json" -d @instance.json

# 2. Сломать шаг оплаты
curl -X POST http://localhost:8000/api/chaos/fail-step \
  -H "Content-Type: application/json" \
  -d '{"instance_id":"{id}","step":"charge_payment","mode":"always"}'

# 3. Дождаться статуса COMPENSATED и проверить журнал компенсаций
curl http://localhost:8000/api/instances/{id}/steps
```

### Демонстрация восстановления после падения воркера

```bash
# 1. Запустить инстанс с долгим шагом
# 2. Остановить продление lease
curl -X POST http://localhost:8000/api/chaos/stop-lease \
  -H "Content-Type: application/json" -d '{"instance_id":"{id}","duration_seconds":40}'

# 3. Через LEASE_TTL_SECONDS проверить, что lease_owner сменился
curl http://localhost:8000/api/instances/{id}
```