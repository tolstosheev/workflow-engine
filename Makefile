.DEFAULT_GOAL := check

.PHONY: help up down logs migrate api worker check check-backend check-frontend \
        test test-fast test-integration test-chaos test-full coverage seed

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

test:  ## Unit + integration + chaos с порогом покрытия 90%
	cd engine && pytest -q -m "not integration and not chaos" --cov-fail-under=90

test-fast:  ## Тесты без порога покрытия (быстрый цикл разработки)
	cd engine && pytest -q -m "not integration and not chaos" --no-cov

test-integration:  ## Интеграционные тесты (нужна БД)
	cd engine && pytest -q -m integration --no-cov

test-chaos:  ## Тесты отказоустойчивости (нужна БД)
	cd engine && pytest -q -m chaos --no-cov

test-full:  ## Все тесты с проверкой покрытия
	cd engine && pytest -q --cov-fail-under=90

coverage:  ## HTML-отчёт покрытия
	cd engine && coverage html && echo "engine/htmlcov/index.html"

seed:  ## Загрузить демонстрационные определения workflow
	cd engine && python -m scripts.seed
