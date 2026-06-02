.PHONY: setup dev test lint migrate docker-up docker-down worker coverage format clean seed-db simulate-webhook docker-build docker-prod-up logs check help

.DEFAULT_GOAL := help

setup:
	uv sync --all-extras
	uv run pre-commit install

dev:
	uv run uvicorn src.api.app:create_app --reload --port 8000 --factory

test:
	uv run pytest

lint:
	uv run ruff check src tests
	uv run mypy src

migrate:
	uv run alembic upgrade head

docker-up:
	docker compose up -d

docker-down:
	docker compose down

worker:
	uv run celery -A src.workers.celery_app worker --loglevel=info

coverage:
	uv run pytest --cov=src --cov-report=html --cov-report=term-missing
	@echo "HTML report: htmlcov/index.html"

format:
	uv run ruff format src tests
	uv run ruff check --fix src tests

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true
	rm -rf .coverage htmlcov coverage.xml .pytest_cache .mypy_cache .ruff_cache

seed-db:
	uv run python scripts/seed_db.py

simulate-webhook:
	uv run python scripts/simulate_webhook.py

docker-build:
	docker build -t autonomous-qa:local .

docker-prod-up:
	docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d

logs:
	docker compose logs -f app celery-worker

check: lint test
	@echo "All checks passed"

help:
	@grep -E '^[a-zA-Z_-]+:' Makefile | sort | awk -F: '{printf "  %-20s\n", $$1}'
