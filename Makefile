.PHONY: setup dev test lint migrate docker-up docker-down

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
