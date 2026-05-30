# Autonomous QA Failure Triage Agent

## Project Overview
AI-powered platform that autonomously analyzes, classifies, and triages CI/CD test failures using LangGraph agent orchestration.

## Tech Stack
- **Language:** Python 3.12+
- **Package manager:** uv
- **Web framework:** FastAPI (async)
- **Task queue:** Celery + Redis
- **Agent framework:** LangGraph
- **Primary LLM:** Claude Sonnet via langchain-anthropic
- **Database:** PostgreSQL 16 (SQLAlchemy async + Alembic migrations)
- **Vector DB:** Qdrant
- **Observability:** OpenTelemetry + Prometheus + Grafana + Jaeger

## Commands
- `make setup` — install dependencies and pre-commit hooks
- `make dev` — run FastAPI dev server with hot reload
- `make test` — run pytest with coverage
- `make lint` — run ruff + mypy
- `make migrate` — run Alembic migrations
- `make docker-up` / `make docker-down` — start/stop infrastructure services
- `make worker` — start Celery worker

## Architecture
- `src/agents/orchestrator.py` — LangGraph graph definition (core triage pipeline)
- `src/agents/state.py` — shared TriageState TypedDict flowing through all agent nodes
- `src/agents/nodes/` — individual agent nodes (classifier, log analyzer, ticket creator, etc.)
- `src/integrations/` — CI/CD and tool integrations (Jenkins, GitHub Actions, Jira, Slack)
- `src/api/routes/webhooks.py` — webhook entry points for CI/CD platforms
- `src/services/triage_service.py` — orchestrates the triage pipeline via Celery

## Conventions
- Use async/await throughout (async SQLAlchemy sessions, httpx for HTTP)
- Pydantic models for all API schemas and webhook payloads
- Structured logging via structlog (no print statements)
- All secrets via environment variables loaded through pydantic-settings
- Classification uses Claude's structured output (tool use), not free-text parsing
- Each CI/CD provider has its own integration package under `src/integrations/`
- Error signature normalization: strip ANSI → timestamps → memory addresses → line numbers → UUIDs → SHA-256 hash

## Database
- PostgreSQL with async SQLAlchemy ORM
- Migrations via Alembic
- Core tables: pipeline_events, test_failures, failure_classifications, error_signatures, triage_tickets, agent_runs, notifications
