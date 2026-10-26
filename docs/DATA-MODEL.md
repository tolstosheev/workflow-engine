# Модель данных

Схема PostgreSQL, назначение таблиц, индексы и правила работы с ними.
Создаётся миграциями Alembic; версия схемы определяется версией кода.

---

## 1. Обзор

```mermaid
erDiagram
    workflow_definitions ||--o{ workflow_instances : "порождает"
    workflow_instances ||--o{ workflow_events : "журнал (только добавление)"
    workflow_instances ||--o{ step_runs : "выполнения шагов"
    workflow_instances ||--o{ timers : "ожидания"
    workflow_instances ||--o{ processed_operations : "идемпотентность"
    workflow_instances ||--o{ compensation_log : "попытки компенсаций"
    workflow_instances |o--o{ chaos_rules : "правила инъекций (без внешнего ключа)"
    step_runs ||--o{ compensation_log : "попытки компенсации"

    workflow_definitions {
        uuid id PK
        string name UK
        int version
        jsonb definition "определение процесса"
        bool is_active
        timestamptz created_at
    }

    workflow_instances {
        uuid id PK
        uuid definition_id FK
        string status "PENDING..FAILED"
        jsonb input
        jsonb variables "состояние из журнала"
        string current_step
        text error
        string lease_owner
        timestamptz lease_expires_at
        timestamptz heartbeat_at
        timestamptz compensation_not_before
        timestamptz journal_pruned_at
        timestamptz archived_at "убран из списка"
        timestamptz created_at
    }

    workflow_events {
        uuid id PK
        uuid instance_id FK
        int seq "строго монотонен"
        string type
        jsonb payload
        timestamptz created_at
    }

    step_runs {
        uuid id PK
        uuid instance_id FK
        string step_name
        int attempt
        string status "PENDING..COMPENSATED"
        jsonb input
        jsonb output
        text error
        bool compensated
        timestamptz started_at
        timestamptz finished_at
    }

    timers {
        uuid id PK
        uuid instance_id FK
        string step_name
        string kind "step / pause_deadline"
        timestamptz due_at
        bool fired
        jsonb payload
        timestamptz created_at
    }

    processed_operations {
        uuid id PK
        uuid instance_id FK
        string op_key "уникальный ключ операции"
        string step_name
        timestamptz created_at
    }

    compensation_log {
        uuid id PK
        uuid instance_id FK
        uuid step_run_id FK
        string compensation_step
        int attempt
        string status "RUNNING / COMPLETED / FAILED / INTERRUPTED"
        text error
        timestamptz started_at
        timestamptz finished_at
    }

    workers {
        uuid id PK
        string worker_id UK
        timestamptz started_at
        timestamptz heartbeat_at
        timestamptz stopped_at
        int cycles_completed
        int steps_executed
        int lease_lost
    }

    chaos_rules {
        uuid id PK
        uuid instance_id "пусто — любой процесс"
        string step_name "пусто — любой шаг"
        string handler_ref "адрес обработчика отката"
        string mode "once / always / timeout"
        text error
        int used
        timestamptz created_at
    }

    demo_inventory {
        uuid id PK
        string sku UK
        int available
        int reserved
    }

    demo_payments {
        uuid id PK
        uuid instance_id FK
        numeric amount
        string status "HELD / REFUNDED"
        timestamptz refunded_at
    }

    demo_shipments {
        uuid id PK
        uuid instance_id FK
        string status "CREATED / CANCELLED"
        string tracking_number
        timestamptz cancelled_at
    }
```

Источник — [`diagrams/er.mmd`](diagrams/er.mmd).

---

## 2. Таблицы

### 2.1 `workflow_definitions`

Дефинишены процессов — данные, а не код. Новый процесс добавляется без
изменений движка.

| Колонка | Тип | Назначение |
|---|---|---|
| `id` | `uuid` | Первичный ключ |
| `name` | `text UNIQUE` | Имя дефинишена |
| `version` | `integer` | Версия; `(name, version)` — уникальная пара |
| `definition` | `jsonb` | Граф шагов, компенсаций, переходов |
| `is_active` | `boolean` | Активна ли версия для запуска |
| `created_at` | `timestamptz` | Создание |

`jsonb`, а не отдельные таблицы на шаги: структура графа известна только в
момент запуска инстанса, а схема дефинишена может меняться приложением.
Нормализованные таблицы шагов дали бы пустую модель при изменении формата.

Индекс `GIN (definition)` — только если появится запрос поиска по шагам;
при текущем сценарии дефинишены читаются по имени.

### 2.2 `workflow_instances`

| Колонка | Тип | Назначение |
|---|---|---|
| `id` | `uuid` | Первичный ключ |
| `definition_id` | `uuid FK` | Ссылка на дефинишен |
| `status` | `text` | Состояние машины |
| `input` | `jsonb` | Входные данные инстанса |
| `variables` | `jsonb` | Результат replay — кэш, не источник истины |
| `current_step` | `text` | Шаг, готовый к исполнению |
| `error` | `text` | Причина терминального провала |
| `lease_owner` | `text` | Идентификатор воркера-арендатора |
| `lease_expires_at` | `timestamptz` | Момент истечения аренды |
| `heartbeat_at` | `timestamptz` | Последнее продление |
| `compensation_not_before` | `timestamptz` | Пауза перед повтором отката |
| `journal_pruned_at` | `timestamptz` | Отметка об уборке журнала (ADR-0019) |
| `archived_at` | `timestamptz` | Отметка архива: процесс убран из списка (ADR-0017) |
| `created_at` | `timestamptz` | Создание |

**`archived_at` — не удаление.** Убранный из списка процесс остаётся в базе,
его журнал цел, а состояние по-прежнему выводится повтором событий. Удаление
запрещено схемой: `workflow_events.instance_id` ссылается на инстанс с
`ondelete="RESTRICT"`. Проставляется только терминальным процессам —
`PAUSED` и `COMPENSATION_FAILED` ждут человека, и спрятать их значило бы
спрятать незакрытое.

**`variables` — не источник истины.** Это денормализованный результат replay,
ускоряющий выбор следующего шага. Восстановление читает журнал, а не
`variables`: иначе рассинхронизация кэша и журнала становятся источником
тихих ошибок. Инвариант §3.1 `AGENTS.md` требует, чтобы статус менялся
только записью события — прямое `UPDATE status` в обход журнала запрещено.

**`compensation_not_before` — пауза, а не счётчик попыток.** Проваленная
компенсация повторяется исполнителем, но не в следующем цикле: интервал растёт
с числом провалов, чтобы недоступная служба не получала запрос раз в секунду.
Времени достаточно, потому что лимита попыток у отката нет — процесс остаётся
в `COMPENSATION_FAILED` с видимым ручным разбором, пока откат не удастся или
оператор не повторит его вручную.

**`journal_pruned_at` — отметка, без которой пустая лента лжёт.** Уборка
архивных журналов (ADR-0019) удаляет события терминального процесса старше
месяца, а отсутствие событий читалось бы как «журнала не было вовсе». Пометка
отделяет одно от другого: страница процесса объявляет уборку и не предлагает
сверку состояния, потому что сверять больше не с чем.

### 2.2a `workers`

| Колонка | Тип | Назначение |
|---|---|---|
| `worker_id` | `text` unique | Идентификатор воркера — тот же, что в `lease_owner` и в логах |
| `started_at` | `timestamptz` | Момент подъёма |
| `heartbeat_at` | `timestamptz` | Последнее продление, раз за интервал опроса |
| `stopped_at` | `timestamptz` | Штатная остановка; у павшего остаётся пустым |
| `cycles_completed` | `integer` | Счётчик циклов подбора |
| `steps_executed` | `integer` | Счётчик исполненных шагов |
| `lease_lost` | `integer` | Циклы, оборвавшиеся потерей аренды |

**Реестр — единственное место в интерфейсе, где состояние не выводится из
журнала.** Воркер не часть бизнес-процесса: его появление не меняет состояние
инстанса, поэтому и события о нём в журнал не пишутся. Упавший воркер остаётся
строкой с устаревшим сердцем — по той же причине, по которой протухшая аренда
не исчезает (§3.3).

### 2.2b `chaos_rules`

Правила неисправностей — инструмент проверки, а не часть предметной области.
Они лежат в базе, потому что ставятся через HTTP в процессе API, а применяются
исполнителем шага, то есть в другом процессе; правило в памяти не пережило бы
и перезапуска.

| Колонка | Тип | Назначение |
|---|---|---|
| `instance_id` | `uuid` | Адресат; пусто — правило подходит любому процессу |
| `step_name` | `text` | Шаг; пусто — любой шаг процесса |
| `handler_ref` | `text` | Обработчик отката (ADR-0015); пусто для правил шага |
| `mode` | `text` | `once`, `always` или `timeout` |
| `error` | `text` | Текст отказа, который увидит обработчик |
| `used` | `integer` | Сколько раз сработало правило `once` |
| `created_at` | `timestamptz` | Создание; задаёт порядок выбора |

**Правило адресует одно действие.** `step_name` и `handler_ref` не смешиваются:
у обработчика отката нет имени шага, и правило без этой развилки совпадало бы с
каждым шагом. Режим `used == 0` делает `once` одноразовым: без него «сломать
один раз» ломало бы все последующие шаги.

### 2.3 `workflow_events`

| Колонка | Тип | Назначение |
|---|---|---|
| `id` | `uuid` | Первичный ключ |
| `instance_id` | `uuid FK` | Владелец события |
| `seq` | `integer` | Порядковый номер внутри инстанса |
| `type` | `text` | Тип события |
| `payload` | `jsonb` | Данные события |
| `created_at` | `timestamptz` | Момент записи |

Ограничения:

- `UNIQUE (instance_id, seq)` — гарантирует монотонность без пропусков;
- `CHECK (seq >= 0)`;
- **`UPDATE` и `DELETE` запрещены** на уровне приложения; при необходимости —
  правило в БД, отклоняющее их.

`ON DELETE CASCADE` для `instance_id` **не используется**: удаление инстанса
уничтожило бы журнал, а вместе с ним возможность восстановления.

Индексы:

| Индекс | Обоснование |
|---|---|
| `UNIQUE (instance_id, seq)` | Порядок replay'а; защита от дубликатов при гонке |
| `(instance_id, created_at)` | Выборка событий инстанса за период в UI |
| `(type, created_at)` | Метрики по типам событий |

### 2.4 `step_runs`

Дополняет журнал техническими деталями исполнения: тайминги, попытки,
вход и выход шага.

| Колонка | Тип | Назначение |
|---|---|---|
| `id` | `uuid` | Первичный ключ |
| `instance_id` | `uuid FK` | Владелец |
| `step_name` | `text` | Имя шага |
| `attempt` | `integer` | Номер попытки, с 1 |
| `status` | `text` | `PENDING`…`COMPLETED`/`FAILED`/`COMPENSATED` |
| `input` / `output` | `jsonb` | Данные шага |
| `error` | `text` | Текст ошибки последней попытки |
| `compensated` | `boolean` | Компенсация выполнена |
| `started_at` / `finished_at` | `timestamptz` | Тайминги |

Разделение таблиц осознанное: журнал отвечает на вопрос «что произошло»,
`step_runs` — «сколько заняло и с каким результатом». Смешивать их означало бы
записывать инженерные детали в append-only поток.

Индексы: `(instance_id, step_name)`, `(instance_id, status, finished_at DESC)`
— последний обслуживает выбор шагов к компенсации в обратном порядке.

### 2.5 `timers`

| Колонка | Тип | Назначение |
|---|---|---|
| `id` | `uuid` | Первичный ключ |
| `instance_id` | `uuid FK` | Владелец |
| `step_name` | `text` | Шаг после срабатывания |
| `due_at` | `timestamptz` | Момент срабатывания |
| `fired` | `boolean` | Сработал ли |
| `payload` | `jsonb` | Данные ожидания |

Индекс: `(fired, due_at)` — выборка сработавших таймеров идёт постоянно.

### 2.6 `processed_operations`

| Колонка | Тип | Назначение |
|---|---|---|
| `id` | `uuid` | Первичный ключ |
| `instance_id` | `uuid FK` | Владелец |
| `op_key` | `varchar(256)` | Ключ операции |
| `step_name` | `varchar(128)` | Шаг — подпись, а не часть ключа |
| `created_at` | `timestamptz` | Момент применения |

**`UNIQUE (instance_id, op_key)`** — механизм идемпотентности. Конфликт при
вставке означает «эффект уже применён», и обработчик завершается успешно без
повторного действия.

Разделение колонок важно и было дефектом: у компенсации ключ длиннее имени шага
(префикс `compensate:` и идентификатор прогона), и запись ключа в `step_name`
переполняла `varchar(128)` — исключение уходило из отката. Идентичность живёт в
`op_key`, подпись записывает имя шага.

### 2.7 `compensation_log`

История попыток компенсаций отдельно от `step_runs`: компенсация — это
отдельная операция со своей попыткой, своим временем и своим результатом.

| Колонка | Тип | Назначение |
|---|---|---|
| `id` | `uuid` | Первичный ключ |
| `instance_id` | `uuid FK` | Владелец |
| `step_run_id` | `uuid FK` | Компенсируемый шаг |
| `compensation_step` | `text` | Имя компенсации |
| `attempt` | `integer` | Номер попытки |
| `status` | `text` | `RUNNING`/`COMPLETED`/`FAILED`/`INTERRUPTED` |
| `error` | `text` | Текст ошибки |
| `started_at` / `finished_at` | `timestamptz` | Тайминги |

Индекс: `(instance_id, status)` — выборка незакрытых компенсаций для
ручного разбора.

Индексы по `step_run_id`:

- `uq_compensation_log_step_run` — **частичный уникальный**, действует там, где
  `finished_at IS NULL`: одновременно открытая попытка на выполнение шага ровно
  одна. Он защищает от двух параллельных возвратов одного резерва, и это же
  означает, что незакрытая попытка блокирует следующую. Поэтому `start_attempt`
  закрывает осиротевшие попытки того же прогона как `INTERRUPTED` (а не
  `FAILED`: вердикта обработчика не было, и штрафовать процесс паузой за
  собственное восстановление нельзя) до вставки новой — иначе падение
  исполнителя в этом окне запирало бы откат навсегда, при пустом списке ручного
  разбора;
- `ix_compensation_log_step_run` — без условия: партиальный уникальный искал бы
  попытки только среди невыполненных, а чтение идёт по всем.

### 2.8 Таблицы предметной области

`demo_inventory`, `demo_payments`, `demo_shipments` — данные демонстрационного
процесса. Отдельный префикс `demo_`, чтобы при необходимости подключить
предметную область из задания, не смешивая её с таблицами движка.

Таблицы не декоративные: в них пишут обработчики, и компенсации отменяют эти
записи. Проверка отката опирается на них — резерв возвращается к исходному
значению, платёж переходит из `HELD` в `REFUNDED`.

Ограничения на складе:

| Ограничение | Что защищает |
|---|---|
| `available >= 0` | Отрицательный остаток — ошибка расчёта |
| `reserved >= 0` | Отмена не должна уходить в минус |
| `reserved <= available` | Заказ больше, чем есть на складе, падает как шаг |

Последнее ограничение добавлено миграцией `b7e4c1a9d206` как `NOT VALID` с
последующей валидацией: на работающей базе падать на старых данных было бы
нечестно. В обработчике осталась и своя проверка — она даёт внятную причину
оператору, а не сообщение СУБД.

Денежные суммы — `numeric`, никогда `float`. Двоичная плавающая точка даёт
ошибку накопления там, где сравнение с нулём должно быть точным.

---

## 3. Типы данных

| Тип | Где | Почему |
|---|---|---|
| `uuid` | Первичные ключи | Идентификаторы создаются на клиенте и в разных процессах; `SERIAL` создаёт скрытую последовательность как новую точку отказа |
| `jsonb` | `definition`, `payload`, `input`, `output`, `variables` | Структура задаётся приложением; `jsonb` даёт индексы и не требует миграции при изменении формата |
| `numeric` | Деньги | `float` теряет точность на сравнении с нулём |
| `timestamptz` | Все даты | `timestamp` без зоны теряет смысл при нескольких воркерах в разных зонах |
| `text` + `CHECK` | Статусы | Простой `enum` требует миграции при добавлении значения; `text` с ограничением — тоже, но без блокировки таблицы |

---

## 4. Транзакционные границы

Одна транзакция = одно событие журнала плюс изменение состояния.

```sql
BEGIN;
INSERT INTO workflow_events (instance_id, seq, type, payload) VALUES (...);
UPDATE workflow_instances SET current_step = $1 WHERE id = $2;
COMMIT;
```

**Транзакция не пересекает внешний вызов.** Инвариант §3.2 `AGENTS.md`: если
кажется, что нужен длинный транзакционный блок, — это сигнал, что нужен
чекпоинт в журнале, а не блокировка.

---

## 5. Правила и права

| Правило | Применение |
|---|---|
| Запрет `UPDATE`/`DELETE` на `workflow_events` | Приложение; при необходимости — правило в БД |
| `SELECT ... FOR UPDATE SKIP LOCKED` | Подбор задач воркерами |
| `SELECT ... FOR UPDATE` на строке инстанса | Выделение следующего `seq` |
| `INSERT ... ON CONFLICT DO NOTHING` | `processed_operations` |
| Таймзона `UTC` | Все `timestamptz` |

---

## 6. Миграции

```bash
cd engine
alembic revision --autogenerate -m "описание изменения"
alembic upgrade head
alembic downgrade -1
```

Правила:

- Миграция только вперёд и только на каталог `head`. Переписывание применённой
  миграции ломает окружения, где она уже применена.
- `sqlalchemy.url` берётся из конфигурации приложения, а не из `alembic.ini`:
  один источник правды для локального запуска, compose и тестов.
- Производные таблицы (`workflow_events`) не пересоздаются: потеря журнала
  означает потерю возможности восстановления.