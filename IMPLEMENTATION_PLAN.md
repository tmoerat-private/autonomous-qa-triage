# Autonomous QA — Remaining Implementation Plan

> Generated: 2026-06-02  
> Project root: `C:\Users\Tino\Desktop\Autonomous QA`  
> Plan covers all remaining work after the initial skeleton + partial implementation phase.

---

## Audit Summary

The project is **~85% complete**. Core pipeline, all models, all repositories, all agent nodes, all integration clients, the full API surface, observability, and most tests are implemented. What remains falls into four buckets:

---

## GAP INVENTORY

### 🔴 Critical — Empty Source Files (6 files)

| File | Status | Impact |
|------|--------|--------|
| `src/agents/nodes/environment_health.py` | 0 lines | Node missing, not wired into graph |
| `src/agents/tools/github_tools.py` | 0 lines | LangChain tool functions absent |
| `src/agents/tools/jenkins_tools.py` | 0 lines | LangChain tool functions absent |
| `src/agents/tools/jira_tools.py` | 0 lines | LangChain tool functions absent |
| `src/agents/tools/log_tools.py` | 0 lines | LangChain tool functions absent |
| `src/agents/tools/slack_tools.py` | 0 lines | LangChain tool functions absent |

> The tools layer provides `@tool`-decorated functions for Claude tool_use invocation. Agent nodes currently call integration clients directly — these tools are a feature gap, not a crash risk.

### 🔴 Critical — Empty Test Files (3 files)

| File | Status | Gap |
|------|--------|-----|
| `tests/unit/services/test_triage_service.py` | 0 lines | `run_triage()` has zero coverage |
| `tests/unit/services/test_webhook_service.py` | 0 lines | `process_webhook()` has zero coverage |
| `tests/integration/test_failure_api.py` | 0 lines | Probable legacy stub — verify vs `test_failures_api.py` |

### 🟡 Needs Work — Infrastructure Deficiencies

| Item | Current State | Required State |
|------|--------------|----------------|
| `Dockerfile` | 8 lines, single-stage, runs as root | Multi-stage, non-root `appuser`, health check |
| `Makefile` | 19 lines | Add: `coverage`, `format`, `clean`, `seed-db`, `simulate-webhook`, `docker-build` |
| `.github/workflows/` | Only `ci.yml` | Add: `build.yml` (image push), `security.yml` (pip-audit + hadolint) |
| `tests/factories/pipeline_factory.py` | 17 lines | Full factory with all model fields |
| `tests/factories/failure_factory.py` | 24 lines | Full factory with sub-factories |

### 🟡 Needs Work — Dashboard Incomplete

| Gap | Description |
|-----|-------------|
| `Agents.jsx` page | Agent run history list + per-run timeline |
| `Releases.jsx` page | Release risk scoring dashboard |
| `Settings.jsx` page | Integration status display |
| Dark theme | `#0F172A` navy background — no global theme currently |
| TanStack Query | Raw axios calls; no caching, loading states, or refetch |
| `AgentTimeline.jsx` | 9-step vertical agent timeline in failure detail |
| Sidebar routing | Layout.jsx nav links incomplete |

---

## EXECUTION PLAN

### Phase A — Test Coverage (Sequential — do first, unblocks CI)

---

#### Task 1 → `testing-qa-expert`
**What:** Write `tests/unit/services/test_triage_service.py`

```
claude --agent testing-qa-expert
```

**Prompt:**
```
Write tests/unit/services/test_triage_service.py for src/services/triage_service.py.

The service has one async function: run_triage(pipeline_event_id: str) -> dict
It calls:
  1. initial_state(pipeline_event_id)  — from src.agents.state
  2. triage_graph.ainvoke(state)        — module-level singleton from src.agents.orchestrator
  3. Returns dict(result)

Tests needed:
1. test_run_triage_returns_final_state — mock triage_graph.ainvoke to return a known TriageState dict,
   assert run_triage returns it as a plain dict with expected keys (failure_ids, is_duplicate, errors)
2. test_run_triage_logs_completion — verify structlog output contains pipeline_event_id
3. test_run_triage_propagates_graph_errors — mock ainvoke to raise Exception, assert it propagates
4. test_run_triage_handles_duplicate_result — mock ainvoke returning is_duplicate=True, verify logged correctly

Use pytest-asyncio (mode: auto), AsyncMock for triage_graph.ainvoke.
Read src/agents/state.py first to understand TriageState fields.
Read tests/conftest.py for existing fixture patterns.
```

---

#### Task 2 → `testing-qa-expert`
**What:** Write `tests/unit/services/test_webhook_service.py`

```
claude --agent testing-qa-expert
```

**Prompt:**
```
Write tests/unit/services/test_webhook_service.py for src/services/webhook_service.py.

Read the actual file first: src/services/webhook_service.py

Tests needed:
1. test_valid_github_signature_accepted — compute real HMAC-SHA256 using test secret, assert service accepts it
2. test_invalid_github_signature_rejected — pass wrong sig, assert HTTPException(401) or similar
3. test_missing_signature_rejected — no signature header, assert rejection
4. test_jenkins_signature_accepted — compute Jenkins HMAC using test secret
5. test_celery_task_dispatched_on_valid_webhook — mock celery task, assert run_triage_pipeline.delay() called
6. test_unknown_provider_rejected — pass provider="unknown", assert 422 or error response
7. test_payload_stored_as_pipeline_event — use db_session fixture, assert PipelineEvent created in DB

Use: pytest-asyncio (mode: auto), real PostgreSQL via db_session fixture (from tests/conftest.py),
mock Celery task with unittest.mock.patch.
Fixture: tests/fixtures/github_actions_webhook.json and jenkins_webhook.json for realistic payloads.
```

---

#### Task 3 → `testing-qa-expert`
**What:** Resolve `tests/integration/test_failure_api.py` (empty)

```
claude --agent testing-qa-expert
```

**Prompt:**
```
Audit tests/integration/test_failure_api.py (currently empty) against
tests/integration/test_failures_api.py (370 lines).

If they fully overlap: delete test_failure_api.py.
If there are gaps in test_failures_api.py: fill test_failure_api.py with the missing cases.

Key coverage gaps to look for:
- POST /api/v1/failures endpoint (if it exists)
- re-triage endpoint (PATCH or POST /api/v1/failures/{id}/retriage)
- bulk status update
- error cases (404 for unknown failure_id, 422 for invalid filter params)

Read src/api/routes/failures.py to see the complete route surface, then decide.
```

---

#### Task 4 → `testing-qa-expert`
**What:** Expand model factories

```
claude --agent testing-qa-expert
```

**Prompt:**
```
Expand the model factories in tests/factories/ to be production-quality.

Current state:
- tests/factories/pipeline_factory.py — 17 lines (very thin)
- tests/factories/failure_factory.py — 24 lines (very thin)

Read src/models/pipeline_event.py, src/models/test_failure.py,
src/models/failure_classification.py, src/models/error_signature.py,
src/models/triage_ticket.py, src/models/agent_run.py
to understand all fields on each model.

Requirements:
1. PipelineEventFactory — all fields with realistic defaults:
   - id: factory.LazyFunction(uuid4)
   - provider: factory.Iterator(["github_actions", "jenkins"])
   - provider_build_id: factory.Sequence(lambda n: f"run-{n}")
   - repository: "org/my-service"
   - branch: "main"
   - commit_sha: 40-char hex string via LazyFunction
   - pipeline_name: "CI"
   - status: "failure"
   - raw_payload: {} (JSONB)
   - received_at: factory.LazyFunction(datetime.utcnow)

2. TestFailureFactory — all fields:
   - id, pipeline_event_id, test_name, test_suite, test_file
   - error_message: realistic assertion error
   - stack_trace: multi-line realistic stack trace
   - duration_ms: factory.Faker("random_int", min=100, max=30000)
   - retry_count: 0
   - status: "new"

3. FailureClassificationFactory:
   - category: factory.Iterator(["product_bug", "flaky_test", "env_issue"])
   - confidence: factory.LazyFunction(lambda: round(random.uniform(0.7, 0.99), 2))
   - reasoning: "Test assertion failed in business logic"
   - model_used: "claude-sonnet-4-20250514"
   - tokens_used: factory.Faker("random_int", min=500, max=2000)

4. AgentRunFactory — for agent timeline tests

Use factory-boy with SubFactory for FK relationships where needed.
```

---

### Phase B — LangChain Tool Functions (Parallel — all 5 tasks can run simultaneously)

---

#### Task 5 → `ai-agent-architect`
**What:** Implement `src/agents/tools/log_tools.py`

```
claude --agent ai-agent-architect
```

**Prompt:**
```
Implement src/agents/tools/log_tools.py — LangChain tool functions for log analysis.

Tools to implement:
1. normalize_error_signature(raw_error: str) -> str
   Pipeline: strip ANSI → strip timestamps → strip memory addresses (0x[0-9a-f]+)
   → strip line numbers (line \d+) → strip UUIDs → SHA-256 hash
   Returns the hex digest string.

2. extract_stack_frames(stack_trace: str) -> list[dict]
   Parse a Python stack trace into structured frames:
   [{"file": "src/checkout.py", "line": 42, "function": "process_payment", "code": "..."}]

3. classify_error_type(error_message: str) -> str
   Rule-based pre-classifier:
   - "AssertionError" → "assertion_failure"
   - "TimeoutError" | "timed out" → "timeout"
   - "ConnectionError" | "refused" → "network_error"
   - "ImportError" | "ModuleNotFound" → "import_error"
   - Otherwise → "unknown"

4. extract_test_names_from_log(log_text: str) -> list[str]
   Parse pytest/junit output to extract FAILED test names.
   Handles: "FAILED tests/test_foo.py::test_bar - AssertionError"

Each function:
- Must use @tool decorator from langchain_core.tools
- Must have a clear docstring (Claude reads this to decide when to call it)
- Must be pure (no DB calls, no I/O)
- Type-annotated throughout

Read src/agents/nodes/log_analyzer.py to understand usage context.
Read CLAUDE.md for project conventions.
```

---

#### Task 6 → `ai-agent-architect`
**What:** Implement `src/agents/tools/github_tools.py`

```
claude --agent ai-agent-architect
```

**Prompt:**
```
Implement src/agents/tools/github_tools.py — LangChain @tool functions for GitHub Actions.

Read src/integrations/github_actions/client.py first to see the existing async client.

Tools to implement:
1. get_workflow_run_logs(run_id: int, repository: str) -> str
   Fetches full log text for a workflow run.

2. get_failed_jobs(run_id: int, repository: str) -> list[dict]
   Returns [{"job_name": str, "conclusion": str, "steps_failed": list[str]}]

3. get_commit_diff(commit_sha: str, repository: str) -> str
   Returns the unified diff for a commit (truncated to 4000 chars if too long).

4. get_recent_runs_for_test(test_name: str, repository: str, limit: int = 10) -> list[dict]
   Returns recent run outcomes: [{"run_id": int, "status": str, "created_at": str}]
   (Used by flaky_detector for pass/fail history)

Implementation notes:
- Each tool creates a GitHubActionsClient from settings (use get_settings())
- Handle httpx.HTTPStatusError gracefully — return error string on 404/403
- Detailed docstrings required — Claude reads them to decide when to invoke each tool

Read src/config/settings.py for credential fields.
Read CLAUDE.md for conventions.
```

---

#### Task 7 → `ai-agent-architect`
**What:** Implement `src/agents/tools/jenkins_tools.py`

```
claude --agent ai-agent-architect
```

**Prompt:**
```
Implement src/agents/tools/jenkins_tools.py — LangChain @tool functions for Jenkins.

Read src/integrations/jenkins/client.py first.

Tools to implement:
1. get_build_console_log(job_name: str, build_number: int) -> str
   Returns the full console text for a Jenkins build.

2. get_build_test_report(job_name: str, build_number: int) -> dict
   Returns: {"total": int, "failed": int, "cases": [{"name": str, "status": str, "error": str}]}

3. get_build_parameters(job_name: str, build_number: int) -> dict
   Returns build parameters (branch, commit SHA, etc.) as a dict.

4. get_recent_build_history(job_name: str, limit: int = 10) -> list[dict]
   Returns: [{"number": int, "result": str, "timestamp": int}]

5. trigger_build_rerun(job_name: str, build_number: int) -> dict
   Triggers a rebuild. Returns {"triggered": bool, "new_build_url": str}

Implementation notes:
- Wrap JenkinsClient from src.integrations.jenkins.client
- Handle httpx errors gracefully
- Detailed docstrings on every tool
- Read src/config/settings.py for JENKINS_URL, JENKINS_USER, JENKINS_TOKEN
```

---

#### Task 8 → `ai-agent-architect`
**What:** Implement `src/agents/tools/jira_tools.py` and `src/agents/tools/slack_tools.py`

```
claude --agent ai-agent-architect
```

**Prompt:**
```
Implement two tool files:

--- FILE 1: src/agents/tools/jira_tools.py ---
Read src/integrations/jira/client.py and src/integrations/jira/mapper.py first.

Tools:
1. create_jira_ticket(title: str, description: str, priority: str, labels: list[str]) -> dict
   Returns {"ticket_id": str, "url": str}

2. link_duplicate_ticket(source_ticket_id: str, duplicate_ticket_id: str) -> bool
   Creates a "is duplicate of" link between two Jira tickets.

3. get_ticket_status(ticket_id: str) -> dict
   Returns {"status": str, "assignee": str, "resolution": str}

4. search_similar_tickets(error_signature: str, project_key: str, limit: int = 5) -> list[dict]
   JQL text search. Returns [{"id": str, "title": str, "status": str, "url": str}]

--- FILE 2: src/agents/tools/slack_tools.py ---
Read src/integrations/slack/client.py and src/integrations/slack/message_builder.py first.

Tools:
1. post_failure_notification(channel_id: str, failure_summary: dict) -> str
   Posts a Block Kit message. Returns message_ts (thread ID).

2. post_thread_reply(channel_id: str, thread_ts: str, message: str) -> bool
   Posts a follow-up in a thread.

3. update_notification_with_ticket(channel_id: str, message_ts: str, ticket_url: str) -> bool
   Updates an existing Slack message to add the Jira ticket link.

For both files:
- Use @tool from langchain_core.tools
- Wrap existing integration clients
- Full docstrings on every tool
- Read src/config/settings.py for credentials
- Read CLAUDE.md for conventions
```

---

#### Task 9 → `ai-agent-architect`
**What:** Implement `src/agents/nodes/environment_health.py` and wire into orchestrator

```
claude --agent ai-agent-architect
```

**Prompt:**
```
Implement src/agents/nodes/environment_health.py and wire it into the LangGraph orchestrator.

Read these files first:
- src/agents/state.py (TriageState fields)
- src/agents/nodes/log_analyzer.py (pattern to follow)
- src/agents/nodes/failure_classifier.py (Claude structured output pattern)
- src/agents/orchestrator.py (where to wire it in)

TriageState fields this node reads:
  - classification: str (from failure_classifier)
  - error_message: str
  - stack_trace: str
  - raw_log: str

TriageState fields this node writes:
  - environment_healthy: bool
  - environment_issues: list[str]  (e.g. ["DB connection refused", "Redis timeout"])

Implementation:
1. Rule-based pre-check (before calling Claude) for obvious patterns:
   - "connection refused" → env issue
   - "timeout" + "database" → env issue
   - "502 Bad Gateway" | "503 Service" → env issue
   - "OOMKilled" → env issue

2. Call Claude with structured output (tool use, NOT free-text):
   EnvironmentHealthResult(is_healthy: bool, issues: list[str], confidence: float)

3. Short-circuit: if classification is already "env_issue", mark environment_healthy=False
   without calling Claude (saves tokens).

Then update src/agents/orchestrator.py:
- Import environment_health_node
- Add as a node AFTER "visual_analyzer" and BEFORE "duplicate_detector"
- Update the graph edge accordingly

Add environment_healthy: bool and environment_issues: list[str] to TriageState
in src/agents/state.py if not already present.
```

---

### Phase C — Infrastructure Hardening (Parallel — can run alongside Phase B)

---

#### Task 10 → `dev-ops-engineer`
**What:** Fix Dockerfile + add `build.yml` and `security.yml` workflows

```
claude --agent dev-ops-engineer
```

**Prompt:**
```
Three infrastructure tasks:

--- TASK 1: Fix Dockerfile ---
Read the current Dockerfile (8 lines). Replace with multi-stage production build:

Stage 1 (builder):
  FROM python:3.12-slim AS builder
  WORKDIR /app
  RUN pip install --no-cache-dir uv
  COPY pyproject.toml uv.lock* ./
  RUN uv sync --no-dev --frozen

Stage 2 (runtime):
  FROM python:3.12-slim
  RUN groupadd -r appuser && useradd -r -g appuser appuser
  WORKDIR /app
  COPY --from=builder /app/.venv /app/.venv
  COPY src/ src/
  COPY alembic.ini .
  ENV PATH="/app/.venv/bin:$PATH"
  USER appuser
  EXPOSE 8000
  HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8000/health').raise_for_status()"
  CMD ["uvicorn", "src.api.app:create_app", "--host", "0.0.0.0", "--port", "8000", "--factory"]

--- TASK 2: .github/workflows/build.yml ---
Docker build-and-push workflow:
- Triggers: push to main only (workflow_run: ci.yml completed successfully)
- Uses: docker/login-action, docker/build-push-action
- Pushes to GitHub Container Registry (ghcr.io/${{ github.repository }})
- Tags: latest + short SHA (${{ github.sha }})
- Read .github/workflows/ci.yml for the existing style (actions/checkout version, etc.)

--- TASK 3: .github/workflows/security.yml ---
Security scanning workflow:
- Triggers: push to main, pull_request, weekly schedule (cron: "0 9 * * 1")
- Jobs:
  1. dependency-audit: uv run pip-audit
  2. dockerfile-scan: hadolint/hadolint-action@v3.1.0
  3. secret-scan: trufflesecurity/trufflehog-actions-scan@main

Verify pip-audit is in pyproject.toml dev dependencies; add it if missing.
```

---

#### Task 11 → `dev-ops-engineer`
**What:** Improve `Makefile`

```
claude --agent dev-ops-engineer
```

**Prompt:**
```
Expand the Makefile (currently 19 lines). Read it first. Keep all existing targets, add:

.DEFAULT_GOAL := help

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
```

---

### Phase D — Dashboard Completion (Sequential — run after Phase B)

---

#### Task 12 → `ui-design-specialist`
**What:** Add missing pages + dark theme

```
claude --agent ui-design-specialist
```

**Prompt:**
```
Add three missing pages and a dark theme to the React dashboard at dashboard/src/.

Read these files first:
- dashboard/src/App.jsx (routing)
- dashboard/src/components/Layout.jsx (sidebar)
- dashboard/src/pages/Dashboard.jsx (example page pattern)
- dashboard/src/api/client.js (available API calls)
- dashboard/package.json (react, recharts, axios, react-router-dom)

Design system (dark theme):
  Background: #0F172A | Surface cards: #1E293B | Teal accent: #0D9488
  Teal light: #5EEAD4 | Text primary: #F1F5F9 | Text muted: #94A3B8
  Success: #22C55E | Warning: #F59E0B | Error: #EF4444

--- PAGE 1: dashboard/src/pages/Agents.jsx ---
Agent run history:
- Table of recent runs (GET /api/v1/agents/runs?limit=50)
- Columns: Agent Name, Status badge, Duration, Tokens Used, Failure ID (link), Timestamp
- Click row → expand input_summary / output_summary
- Status badges: running (blue), completed (teal), failed (red), skipped (gray)
- Filter bar: by agent_name, by status

--- PAGE 2: dashboard/src/pages/Releases.jsx ---
Release risk scoring:
- Summary cards: Latest Score (circle gauge 0–10), Total Failures, Critical Count
- Score circle: green <4, amber 4–7, red >7
- Table: commit SHA, repo, branch, score, recommendation (PASS/BLOCK), date
- Click row → expand breakdown by classification category
- Use getRecentReleaseScores() and getReleaseScore() from api/client.js

--- PAGE 3: dashboard/src/pages/Settings.jsx ---
Read-only config display:
- Integration status: Jenkins ✓/✗, GitHub ✓/✗, Jira ✓/✗, Slack ✓/✗
- Use GET /health to determine status
- No forms — display only

--- THEME ---
- Add CSS variables to dashboard/src/index.css
- Update Layout.jsx sidebar to navy/teal colors
- All pages use dark backgrounds with card surfaces

--- ROUTING ---
Update App.jsx routes:
- /agents → Agents.jsx
- /releases → Releases.jsx
- /settings → Settings.jsx

Update Layout.jsx sidebar nav to include all 6 sections:
Dashboard, Failures, Agents, Releases, Settings
```

---

#### Task 13 → `ui-design-specialist`
**What:** Add `AgentTimeline.jsx` component + TanStack Query (run after Task 12)

```
claude --agent ui-design-specialist
```

**Prompt:**
```
Two additions to the dashboard — run after the new pages are created (Task 12).

--- TASK 1: dashboard/src/components/AgentTimeline.jsx ---
Vertical timeline component for the failure detail right sidebar.
Data: GET /api/v1/agents/runs?failure_id={id}

Each timeline item:
- Agent name (e.g., "failure_classifier")
- Status icon: ✓ completed (teal), ✗ failed (red), ○ skipped (gray), ⟳ running (blue spinner)
- Duration badge (e.g., "1.2s")
- Click to expand: shows output_summary text

Visual:
- Vertical connecting line between nodes
- Timestamps on right
- Total elapsed time at bottom
- Dark theme: #1E293B cards, #0D9488 teal for completed

Import and render in FailureDetail.jsx right sidebar.

--- TASK 2: Add TanStack Query ---
1. Add to dashboard/package.json: "@tanstack/react-query": "^5.0.0"
   (update package.json only — do not run npm install)

2. Wrap app in QueryClientProvider in dashboard/src/main.jsx

3. Convert Dashboard.jsx to useQuery:
   useQuery({ queryKey: ['summary'], queryFn: getSummary })
   useQuery({ queryKey: ['trends'], queryFn: getTrends })
   Show loading skeletons (gray placeholder divs) while loading
   Show error message on failure

4. Convert FailureDetail.jsx to useQuery for failure data + agent runs

Keep api/client.js functions unchanged — wrap them in useQuery only.
```

---

### Phase E — Final Verification (After all phases complete)

---

#### Task 14 → `testing-qa-expert`
**What:** Final test audit — fill any remaining gaps

```
claude --agent testing-qa-expert
```

**Prompt:**
```
Final test audit for the Autonomous QA project after the implementation sprint.

New modules that need test coverage (recently implemented — may have no tests yet):
1. src/agents/nodes/environment_health.py — needs unit tests with mocked Claude
2. src/agents/tools/log_tools.py — needs parametrized unit tests (pure functions, no mocks needed)
3. src/agents/tools/github_tools.py — needs respx mock tests
4. src/agents/tools/jenkins_tools.py — needs respx mock tests
5. src/agents/tools/jira_tools.py — needs respx mock tests
6. src/agents/tools/slack_tools.py — needs respx mock tests

For each missing test file:
- Create tests/unit/agents/test_environment_health.py
- Create tests/unit/tools/test_log_tools.py (etc.)
- Write at least 3 tests per module

Follow patterns in:
- tests/unit/agents/test_failure_classifier.py (mock Claude via AsyncMock)
- tests/unit/integrations/test_jenkins_parser.py (respx for HTTP mocking)

Verify:
- tests/factories/ supports all integration tests
- tests/conftest.py has all needed fixtures

Coverage target: 80%+ overall.
```

---

## Execution Order Summary

```
Phase A — Sequential (do first, unblocks CI)
  Task 1: test_triage_service.py
  Task 2: test_webhook_service.py
  Task 3: audit/resolve test_failure_api.py
  Task 4: expand factories

Phase B — Parallel (all 5 can run at the same time)
  Task 5: log_tools.py
  Task 6: github_tools.py
  Task 7: jenkins_tools.py
  Task 8: jira_tools.py + slack_tools.py
  Task 9: environment_health.py + orchestrator update

Phase C — Parallel (can run alongside Phase B)
  Task 10: Dockerfile + build.yml + security.yml
  Task 11: Makefile

Phase D — Sequential (after Phase B)
  Task 12: Agents.jsx + Releases.jsx + Settings.jsx + dark theme
  Task 13: AgentTimeline.jsx + TanStack Query

Phase E — After all phases
  Task 14: Final test audit
```

---

## Estimated Remaining Work

| Phase | Tasks | Estimated Time |
|-------|-------|----------------|
| A — Test Coverage | 4 | 2–3 hours |
| B — Tool Functions | 5 | 3–4 hours |
| C — Infrastructure | 2 | 1–2 hours |
| D — Dashboard | 2 | 3–4 hours |
| E — Verification | 1 | 30 min |
| **Total** | **14** | **~10–13 hours** |
