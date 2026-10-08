# Разработка

Настройка окружения, запуск, проверки, отладка и соглашения о коде.

---

## 1. Требования

| Инструмент | Версия |
|---|---|
| Python | 3.13 |
| Node.js | 22 |
| Docker Desktop | 27+ |
| PostgreSQL | 16 (только через Docker) |
| Git | 2.40+ |

Версии Python и Node проходят проверку в CI, поэтому отклонение от них
приводит к расхождению результатов.

---

## 2. Запуск через Docker

Самый быстрый путь — всё окружение в контейнерах.

```bash
cp .env.example .env
docker compose up -d --build
```

| Сервис | Адрес |
|---|---|
| `db` | `localhost:5433` |
| `api` | http://localhost:8000 |
| `frontend` | http://localhost:5173 |
| Swagger UI | http://localhost:8000/docs |

Миграции выполняет сервис `migrate`; `api` и оба воркера стартуют только
после его успешного завершения.

```bash
docker compose ps             # состояние сервисов
docker compose logs -f worker # логи воркера
docker compose down           # остановить
docker compose down -v        # остановить вместе с данными
```

---

## 3. Локальная разработка

Локальный запуск удобен для отладки и быстрых циклов правки.

### Бэкенд

```bash
cd engine
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env

docker compose up -d db           # только БД
alembic upgrade head

uvicorn app.main:app --reload     # терминал 1
python -m app.worker              # терминал 2 (вторая реплика — третий)
```

### Фронтенд

```bash
cd frontend
npm install
npm run dev
```

Vite проксирует `/api` на бэкенд, поэтому из браузера всё доступно с одного
origin — CORS в разработке не мешает.

---

## 4. Проверки

```bash
make check              # всё: ruff, mypy, eslint, tsc
make check-backend      # ruff check, ruff format --check, mypy
make check-frontend     # eslint, tsc
make test               # тесты с порогом покрытия 90%
make test-fast          # тесты без проверки покрытия
make coverage           # HTML-отчёт: engine/htmlcov/index.html
```

### Настройки линтеров

| Инструмент | Настройка |
|---|---|
| ruff | `line-length = 100`, `target = py313`, правила `E,F,W,I,UP,B,SIM,ASYNC` |
| mypy | `disallow_untyped_defs`, `warn_unused_ignores`, плагин `pydantic.mypy` |
| eslint | `recommended` + `react-hooks`, `react-refresh` |
| tsc | `strict`, `noUnusedLocals`, `noUnusedParameters` |

`disallow_untyped_defs` означает, что **все** функции аннотированы. Это не
формальность: неаннотированная функция не проверяется mypy, а ядро движка
должно проверяться полностью.

---

## 5. Тестирование

### Структура

```
engine/tests/
├── conftest.py         # общие фикстуры
├── unit/               # чистая логика, БД не нужна
├── integration/        # реальный PostgreSQL
└── chaos/              # падение воркера, таймауты, недоступность сервиса
```

### Маркеры

| Маркер | Что проверяет | Нужна БД |
|---|---|---|
| без маркера | Unit: конфигурация, ошибки, replay, планировщик переходов | нет |
| `integration` | Транзакции, журнал, lease на PostgreSQL | да |
| `chaos` | Падение воркера, таймаут шага, недоступность сервиса | да |

```bash
make test-integration
make test-chaos
make test-full           # всё вместе с порогом покрытия
```

### Покрытие

Порог — **90% branch-покрытия** (`--cov-branch --cov-fail-under=90`).
Line-only не считается: `if` без `else` даёт 100% по строкам при
непроверенной ветке.

Исключены из измерения: `app/main.py` (точка входа), `alembic/` (миграции),
`app/models/base.py` (метаданные без логики).

Понизить порог или добавить `# pragma: no cover` можно только с объяснением в
теле коммита — это повод для ревью всего коммита.

### Правила написания тестов

| Правило | Почему |
|---|---|
| Тест падает до исправления | Тест, который и так проходит, ничего не проверяет |
| Один механизм — один тест | Иначе при падении непонятно, что сломалось |
| На каждый механизм ядра есть тест | Отсутствие теста на восстановление означает недоделанную фазу |
| Без БД — unit, с БД — `integration` | `make test` должен проходить без поднятой базы |
| Проверять поведение, а не реализацию | Тест, повторяющий структуру кода, ломается при любом рефакторинге |

---

## 6. Отладка

### Логи

```bash
docker compose logs -f worker    # воркер
docker compose logs -f api       # API
```

Формат переключается переменной `LOG_JSON`: `false` — человекочитаемый,
`true` — JSON для агрегатора.

События воркера структурированы:

```json
{"ts": "...", "level": "INFO", "logger": "app.worker",
 "message": "worker.started", "worker_id": "worker-a1b2c3d4", "pid": 42}
```

Тексты ошибок и тела внешних запросов не логируются: секреты и персональные
данные в логах — утечка.

### Точки останова

`--reload` перезапускает API при изменении кода, но не сбрасывает точки.
Отладка с точками требует запуска без `--reload`:

```bash
uvicorn app.main:app --reload  # обычная работа
python -m debugpy --listen 5678 -m uvicorn app.main:app --port 8000  # отладка
```

### SQLAlchemy

Включить вывод SQL без перезапуска окружения:

```bash
export DB_ECHO=true
```

Постоянно включать не нужно: вывод SQL на порядок замедляет воркер.

### Изучение состояния инстанса

```bash
# Журнал событий — источник истины
curl http://localhost:8000/api/instances/{id}/events

# Тайминги шагов
curl http://localhost:8000/api/instances/{id}/steps

# Кто владеет lease
curl http://localhost:8000/api/instances/{id}
```

---

## 7. Работа с миграциями

```bash
cd engine

alembic revision --autogenerate -m "описание изменения"
alembic upgrade head
alembic downgrade -1
alembic current
alembic history
```

Правила:

- Миграция только вперёд и только на `head`. Переписывание применённой миграции
  ломает окружения, где она уже применена.
- `alembic.ini` не содержит URL: он берётся из `app.core.config`, чтобы
  локальный запуск, compose и тесты читали одну настройку.
- Таблица `workflow_events` не пересоздаётся: потеря журнала означает потерю
  возможности восстановления.

---

## 8. Соглашения о коде

### Python

- ruff: `line-length = 100`, `target = py313`, импорты сортируются автоматически.
- mypy: `disallow_untyped_defs = true` — все функции аннотированы.
- `async def` для всего I/O; блокирующие вызовы — через `asyncio.to_thread`.
- `Decimal` для денег, `float` не используется.
- Имена: `snake_case` для функций и переменных, `PascalCase` для классов,
  `_`-префикс для приватных.

### Комментарии

Комментарий объясняет **почему**, а не что. Пересказ строк кода — это шум,
который устаревает раньше, чем код.

```python
# Плохо: увеличиваем счётчик
counter += 1

# Хорошо: счётчик хранит число запусков, а не число попыток:
# повтор после ретрая не должен его обнулять.
counter += 1
```

Ссылка на инвариант уместна там, где код рискует его нарушить:

```python
# Транзакция закрывается здесь: удерживать её во время await на внешний
# сервис запрещено (§3.2 AGENTS.md) — блокировки держатся минуты вместо
# миллисекунд.
```

### React и TypeScript

- Функциональные компоненты, хуки, `strict`.
- Tailwind-классы, без CSS-in-JS; тёмная тема — через CSS-переменные.
- Имена файлов: компоненты — `PascalCase.tsx`, страницы — `lowercase.tsx`.
- Иконки — `lucide-react`.
- Данные приходят через слой `src/api/`, компоненты не знают URL.

---

## 9. Работа с git

Полное описание — [`GIT_WORKFLOW.md`](GIT_WORKFLOW.md).

```bash
git switch develop
git switch -c feature/<область>-<описание>
# работа
make check && make test
git add -A
git commit
```

Один коммит — одна область. Смешанные изменения дробятся на серию коммитов.