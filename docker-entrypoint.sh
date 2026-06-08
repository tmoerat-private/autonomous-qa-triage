#!/bin/sh
set -e

echo "Running Alembic migrations..."
alembic upgrade head

echo "Starting API server..."
exec uvicorn src.api.app:create_app --host 0.0.0.0 --port 8000 --factory
