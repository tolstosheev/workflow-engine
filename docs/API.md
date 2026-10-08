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

Метрики Prometheus в текстовом формате. Порт отдельный не выделяется:
эндпоинт закрыт на уровне сети в эксплуатации, в разработке доступен свободно.

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

### `PUT /api/definitions/{id}`

Обновление дефинишена. Действующие инстансы не затрагиваются: они ссылаются на
конкретную версию.

### `POST /api/definitions/{id}/validate`

Проверка без сохранения.

```json
{ "valid": true, "errors": [], "warnings": ["шаг 'confirm_delivery' не имеет компенсации — при откате он выполнит побочные эффекты безвозвратно"] }
```

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
  "offset": 0
}
```

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

Выполнения шагов с таймингами — по таблице `step_runs`.

### `GET /api/instances/{id}/timeline`

Агрегированное представление для интерфейса: шаги, события и компенсации в
одном потоке, упорядоченном по времени.

### `POST /api/instances/{id}/cancel`

Запрос отмены. Инстанс переходит в `CANCELLING`, затем в `COMPENSATING`.

```bash
curl -X POST http://localhost:8000/api/instances/b2c3d4e5-f6a7-4b8c-9d0e-1f2a3b4c5d6e/cancel
```

`409`, если инстанс уже в терминальном состоянии.

### `POST /api/instances/{id}/complete`

Форсированное завершение без компенсаций. Требует `{"reason": "..."}`: приёмка
в обход компенсаций оставляет побочные эффекты, и это должно быть отражено
в данных.

```bash
curl -X POST http://localhost:8000/api/instances/{id}/complete \
  -H "Content-Type: application/json" \
  -d '{"reason": "клиент подтвердил вручную"}'
```

### `POST /api/instances/{id}/compensation/retry`

Повтор компенсации после `COMPENSATION_FAILED`.

```json
{ "status": "COMPENSATING", "message": "компенсация release_inventory поставлена на повтор" }
```

---

## 5. Живые обновления (SSE)

### `GET /api/stream`

Поток событий всех инстансов.

```bash
curl -N http://localhost:8000/api/stream
```

```
event: instance_status
data: {"instance_id":"b2c3d4e5-...","status":"RUNNING","current_step":"charge_payment"}

event: step_event
data: {"instance_id":"b2c3d4e5-...","seq":7,"type":"STEP_COMPLETED","payload":{"step":"charge_payment"}}
```

| Тип события | Когда |
|---|---|
| `instance_status` | Изменился статус инстанса |
| `step_event` | Новое событие в журнале |
| `compensation` | Изменилось состояние компенсации |
| `heartbeat` | Служебное, раз в `SSE_HEARTBEAT_SECONDS` |

Heartbeat нужен, чтобы прокси и балансировщики не закрывали соединение по
таймауту простоя.

Фронтенд получает поток через `EventSource`. Прокси настроен только на путь
`/api`: `EventSource` не позволяет задавать заголовки, поэтому CORS
настраивается на конкретные origins вместо заголовка `Authorization`.

---

## 6. Инъекция отказов

Эндпоинты для демонстрации отказоустойчивости. Не для эксплуатации.

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
долгого шага и не протухает.

### `POST /api/chaos/fail-service`

Отключает обработчики предметной области: `{"service": "payment"}`.

### `POST /api/chaos/stop-lease`

Останавливает продление lease для инстанса — имитация зависшего воркера.

```bash
curl -X POST http://localhost:8000/api/chaos/stop-lease \
  -H "Content-Type: application/json" \
  -d '{"instance_id": "b2c3d4e5-...", "duration_seconds": 40}'
```

Через `LEASE_TTL_SECONDS` другой воркер перехватит инстанс — это и есть
демонстрация восстановления.

### `DELETE /api/chaos`

Снимает все активные флаги.

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