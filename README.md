# Workflow Engine

Распределённый движок выполнения workflow с поддержкой долгих транзакций
(long-running transactions) и компенсаций (Saga).

Движок исполняет бизнес-процессы, описанные в JSON: последовательные шаги,
ветвления, параллельные ветки, ожидания и компенсации. Ключевая идея — транзакция
уровня бизнес-процесса не выражается блокировкой строк, а набирается из
последовательности коротких коммитов, записанных в append-only журнал событий.
Поэтому процесс может жить минуты и часы, переживать падение воркера и
перезапускаться с последнего durable-чекпоинта.

---

## Возможности

- **Долгие транзакции.** Транзакция БД открыта на один короткий шаг; состояние
  инстанса восстанавливается replay'ом журнала событий.
- **Saga и компенсации.** У каждого шага объявлена компенсация; откат идёт в
  обратном порядке, ошибка компенсации переводит инстанс в `COMPENSATION_FAILED`.
- **Отказоустойчивость.** Воркеры конкурируют за инстансы через
  `SELECT ... FOR UPDATE SKIP LOCKED` и lease с heartbeat — падение воркера не
  теряет и не дублирует работу.
- **Идемпотентность.** Доставка at-least-once компенсируется таблицей
  `processed_operations`: уже применённый эффект не применяется дважды.
- **Durable-таймеры.** Ожидания хранятся в БД, а не в памяти процесса, поэтому
  переживают рестарт.
- **Наблюдаемость.** Полный журнал событий, тайминги шагов, live-обновление
  интерфейса через SSE, метрики Prometheus.

---

## Стек

| Слой | Технология |
|---|---|
| Язык | Python 3.13 |
| API | FastAPI, Uvicorn |
| ORM и миграции | SQLAlchemy 2.0 (async), Alembic |
| БД | PostgreSQL 16 |
| Фронтенд | React 19, TypeScript, Vite, Tailwind CSS 4, zustand |
| Инфраструктура | Docker Compose |
| Проверки | pytest, ruff, mypy, eslint, tsc |

Обоснование выбора и отказ от альтернатив — в [`docs/adr/`](docs/adr/).

---

## Быстрый старт

### Docker Compose (рекомендуется)

```bash
cp .env.example .env
docker compose up -d --build
```

Поднимется:

| Сервис | Адрес | Назначение |
|---|---|---|
| `db` | `localhost:5433` | PostgreSQL |
| `api` | http://localhost:8000 | REST API, документация OpenAPI на `/docs` |
| `worker`, `worker-2` | — | Две реплики воркера исполнения |
| `frontend` | http://localhost:5173 | Интерфейс |

Миграции применяет сервис `migrate` до старта API и воркеров.

```bash
docker compose logs -f worker      # логи исполнения
docker compose down                # остановить
```

### Локальная разработка

```bash
cd engine
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env

docker compose up -d db            # только БД
alembic upgrade head
uvicorn app.main:app --reload      # терминал 1
python -m app.worker               # терминал 2
```

```bash
cd frontend
npm install
npm run dev
```

---

## Проверки качества

```bash
make check              # ruff + ruff format + mypy + eslint + tsc
make test               # тесты бэкенда
make test-integration   # интеграционные (нужна БД)
make test-chaos         # отказоустойчивость
```

---

## Структура репозитория

```
workflow-engine/
├── engine/     # Python-бэкенд: ядро движка, REST+SSE API, воркер, демо-область
├── frontend/   # React SPA: дашборд инстансов, таймлайн, редактор дефинишенов
├── docs/       # техническая документация и ADR
└── docker-compose.yml
```

---

## Документация

| Документ | Содержание |
|---|---|
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Подсистемы, компонентные схемы, взаимодействия |
| [`docs/ENGINE.md`](docs/ENGINE.md) | Механизмы ядра: журнал, интерпретатор, lease, saga, идемпотентность |
| [`docs/DATA-MODEL.md`](docs/DATA-MODEL.md) | Схема БД, ER-диаграмма, индексы, миграции |
| [`docs/API.md`](docs/API.md) | REST-эндпоинты, SSE-события, примеры запросов |
| [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md) | Окружение, запуск, отладка, тесты |
| [`docs/OPERATIONS.md`](docs/OPERATIONS.md) | Эксплуатация, наблюдаемость, типовые сбои |
| [`docs/adr/`](docs/adr/) | Architecture Decision Records |
| [`AGENTS.md`](AGENTS.md) | Инварианты движка и правила работы над проектом |

---

## Демонстрационная предметная область

Заказ → резерв товара → списание средств → создание отправления → подтверждение
получения. У каждого шага есть компенсация: отмена резерва, возврат средств,
отмена доставки. Контрольный сценарий — сбой на шаге оплаты: движок сам
откатывает выполненные шаги в обратном порядке.

Разработка разделена на фазы в разделе «Roadmap» файла [`AGENTS.md`](AGENTS.md).
