.DEFAULT_GOAL := check

# Адрес API для скриптов. Переопределяется на стенде, где порт 8000 занят:
#   make smoke API_URL=http://localhost:8001
API_URL ?= http://localhost:8000
INSTANCES ?= 300

.PHONY: help up down logs migrate api worker check check-backend check-frontend \
        test test-fast test-integration test-chaos test-full coverage \
        seed smoke bench chaos-demo prune numbers

help:  ## Список целей
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

up:  ## Поднять всё окружение (db, migrate, api, worker×2, frontend)
	docker compose up -d --build

down:  ## Остановить окружение
	docker compose down

logs:  ## Логи сервисов
	docker compose logs -f

migrate:  ## Применить миграции
	cd engine && alembic upgrade head

api:  ## Запустить API локально
	cd engine && uvicorn app.main:app --reload

worker:  ## Запустить воркер локально
	cd engine && python -m app.worker

check: check-backend check-frontend  ## Полный контур проверок

check-backend:  ## ruff + ruff format + mypy
	cd engine && ruff check . && ruff format --check . && mypy app

check-frontend:  ## eslint + tsc
	cd frontend && npm run lint && npm run typecheck

test:  ## Unit + integration с порогом покрытия 90% (нужна БД)
	cd engine && pytest -q -m "not chaos" --cov-fail-under=92

test-fast:  ## Только unit-тесты, без БД и без порога покрытия
	cd engine && pytest -q -m "not integration and not chaos" --no-cov

test-integration:  ## Интеграционные тесты (нужна БД)
	cd engine && pytest -q -m integration --no-cov

test-chaos:  ## Тесты отказоустойчивости (нужна БД)
	cd engine && pytest -q -m chaos --no-cov

test-full:  ## Все тесты с проверкой покрытия
	cd engine && pytest -q --cov-fail-under=92

coverage:  ## HTML-отчёт покрытия
	cd engine && coverage html && echo "engine/htmlcov/index.html"

seed:  ## Загрузить демонстрационные определения workflow
	cd engine && python -m scripts.seed

prune:  ## Показать, сколько журналов попадёт под уборку (ADR-0019)
	cd engine && python -m scripts.prune

prune-apply:  ## Убрать журналы старых архивных процессов (необратимо)
	cd engine && python -m scripts.prune --apply

numbers:  ## Числа для акта внедрения: модули, строки, миграции, ADR
	cd engine && python -m scripts.numbers

smoke:  ## Сквозная проверка работающей системы (нужен запущенный API)
	cd engine && python -m scripts.smoke --url $(API_URL)

bench:  ## Нагрузочный прогон и замеры (нужен запущенный API)
	cd engine && python -m scripts.load --url $(API_URL) --instances $(INSTANCES)

chaos-demo:  ## Показать автоматический откат: сбой на шаге оплаты
	cd engine && python -m scripts.smoke --url $(API_URL) --compensation-only
