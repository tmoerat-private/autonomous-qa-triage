# Autonomous QA — Forward Roadmap
> Drafted: 2026-06-04  
> Baseline: All 14 tasks from IMPLEMENTATION_PLAN.md complete (57a73b7). 360 tests, 83% coverage, CI green.

---

## Current Status

The platform skeleton is complete and tested. Every agent node, integration client, FastAPI route, and Celery worker has been implemented and has unit test coverage. The gap between "well-tested skeleton" and "production-ready system that actually triages failures" is what this roadmap closes.

---

## Phase 1 — Production Hardening
> **Duration:** ~2 weeks  
> **Goal:** Close the gap so a real webhook can flow end-to-end: received → classified → Jira ticket → Slack message  
> **Owner agents:** `database-infrastructure-specialist`, `dev-ops-engineer`, `code-implementation-specialist`, `ai-agent-architect`, `testing-qa-expert`, `ui-design-specialist`

---

### Sprint 1.1 — Must-Haves (Week 1)

These three items must be done before a single real webhook can be processed end-to-end.

---

#### Task 1.1.1 → `database-infrastructure-specialist`

**What:** Qdrant collection auto-initialisation on startup  
**Why:** `duplicate_detector` raises `UnexpectedResponse` on every triage run until the `error_signatures` collection exists. No code currently creates it.  
**Files:**
- Create `src/db/qdrant_client.py` — init function that calls `recreate_collection()` if collection absent
- Modify `src/api/app.py` — call the init function inside the `lifespan()` context manager

**Acceptance:** App boots with a fresh Qdrant container without crashing; triage pipeline runs a duplicate check without error.

---

#### Task 1.1.2 → `dev-ops-engineer`

**What:** Automatic Alembic migration on container start  
**Why:** `make migrate` is currently a manual step. Every deploy leaves the DB on the old schema until an engineer runs it.  
**Files:**
- Create `docker-entrypoint.sh` — runs `alembic upgrade head`, then `exec uvicorn ...`
- Modify `Dockerfile` — change `CMD` from direct uvicorn to `["./docker-entrypoint.sh"]`

**Acceptance:** `docker compose up` on a fresh DB runs migrations before serving requests; existing data is unaffected.

---

#### Task 1.1.3 → `code-implementation-specialist`

**What:** Celery retry policy for `run_triage_pipeline`  
**Why:** A transient Claude API timeout silently drops a triage with no retry or dead-letter. The task has no `max_retries` or `autoretry_for`.  
**Files:**
- Modify `src/workers/tasks.py` — add `autoretry_for=(Exception,)`, `max_retries=3`, `default_retry_delay=30` to the task decorator
- Add `on_failure` handler that sets `PipelineEvent.status = "failed"` and logs the error

**Acceptance:** Simulate a failing Claude API call; Celery retries 3× with 30s delay, then marks the event as failed.

---

### Sprint 1.2 — Reliability & Polish (Week 2)

With the must-haves done, these items harden the system for sustained use.

---

#### Task 1.2.1 → `ai-agent-architect`

**What:** Tenacity rate-limit retry in LangChain tool functions  
**Why:** GitHub and Jira tool functions silently return error strings on HTTP 429s under load, causing silent triage failures.  
**Files:**
- Modify `src/agents/tools/github_tools.py` — wrap httpx calls with `@retry(wait=wait_exponential(...), retry=retry_if_exception(is_429))`
- Modify `src/agents/tools/jira_tools.py` — same pattern

**Acceptance:** Instrument a 429 response via respx; verify the tool retries with backoff and eventually returns the correct result.

---

#### Task 1.2.2 → `testing-qa-expert`

**What:** End-to-end smoke test  
**Why:** The pipeline is unit-tested node-by-node but never exercised as a whole chain. A full-stack regression has no automated catch.  
**Files:**
- Create `tests/integration/test_e2e_smoke.py` (or `scripts/e2e_smoke.py` if Docker-only)
  1. POST a synthetic GitHub Actions webhook to `POST /api/v1/webhooks/github_actions`
  2. Poll until `PipelineEvent.status` transitions from `"triaging"` → `"triaged"` (max 30s)
  3. Assert a `FailureClassification` row exists with non-null `category` and `confidence`

**Acceptance:** `make test` runs the smoke test against a local Docker Compose stack; it passes in < 30 seconds.

---

#### Task 1.2.3 → `ui-design-specialist`

**What:** Dashboard real-time polling + failures list pagination  
**Why:** TanStack Query is wired but `refetchInterval` is unset — users see stale data until they hard-refresh. The failures list also loads only the first page.  
**Files:**
- Modify `dashboard/src/pages/Dashboard.jsx` — add `refetchInterval: 30_000` to `summary` and `trends` queries
- Modify `dashboard/src/pages/Failures.jsx` — add `refetchInterval: 30_000`; add page/prev/next controls using `?page=N&limit=25` query params
- Modify `dashboard/src/api/client.js` — pass `page` and `limit` params to `getFailures()`

**Acceptance:** Failures page auto-refreshes every 30 seconds; navigating to page 2 loads the next 25 failures.

---

## Phase 2 — Enhanced Intelligence
> **Duration:** ~6 weeks (3 sprints × 2 weeks)  
> **Goal:** Upgrade the platform from a reactive triage tool to a learning, pattern-detecting system  
> **New features:** Flaky test detection, learning from history, vector-similarity deduplication, auth, trend dashboards

---

### Sprint 2.1 — Flaky Test Detection (Weeks 1–2)

---

#### Task 2.1.1 → `database-infrastructure-specialist`

**What:** `flaky_test_stats` table + Alembic migration  
**Files:**
- Create `src/models/flaky_test_stat.py`
  - `id` UUID PK, `test_name` VARCHAR, `test_suite` VARCHAR, `repository` VARCHAR
  - `run_count` INT, `fail_count` INT, `flakiness_score` FLOAT
  - `last_evaluated_at` TIMESTAMPTZ, `created_at` TIMESTAMPTZ
- Create Alembic migration: `alembic revision --autogenerate -m "add flaky_test_stats"`
- Create `src/db/repositories/flaky_stat_repo.py` — `upsert_stat()`, `get_by_test_name()`

---

#### Task 2.1.2 → `ai-agent-architect`

**What:** `src/agents/nodes/flaky_detector.py`  
**Why:** Flaky tests are the #1 source of false triage runs. Identifying them early lets the orchestrator short-circuit.  
**Files:**
- Create `src/agents/nodes/flaky_detector.py`
  - Reads `TriageState`: `test_name`, `repository`, `classification`
  - Fetches last 20 run outcomes via `get_recent_runs_for_test()` tool
  - Applies chi-squared test: if pass/fail variance is statistically significant with p < 0.05 → flaky
  - Writes: `is_flaky: bool`, `flakiness_score: float`, `flakiness_evidence: str`
- Update `src/agents/state.py` — add `is_flaky`, `flakiness_score`, `flakiness_evidence` fields
- Update `src/agents/orchestrator.py` — wire `flaky_detector` node after `log_analyzer`, before `duplicate_detector`; add conditional edge: if `is_flaky=True` → skip ticket creation, send "flaky test" Slack notification

---

#### Task 2.1.3 → `testing-qa-expert`

**What:** Unit tests for `flaky_detector` node  
**Files:**
- Create `tests/unit/agents/test_flaky_detector.py`
  - `test_flaky_test_detected` — mock 15 failures in 20 runs, assert `is_flaky=True`
  - `test_stable_test_not_flaky` — 1 failure in 20 runs, assert `is_flaky=False`
  - `test_short_circuit_on_insufficient_data` — fewer than 5 runs, assert conservative `is_flaky=False`
  - `test_flakiness_score_proportional_to_failure_rate`

---

### Sprint 2.2 — Learning & Memory + Vector Dedup (Weeks 3–4)

---

#### Task 2.2.1 → `ai-agent-architect`

**What:** Upgrade `duplicate_detector` to vector-similarity search  
**Why:** The current SHA-256 exact-match misses near-duplicate errors where only the line number or memory address differs (even after normalization). Qdrant nearest-neighbor search catches these.  
**Files:**
- Modify `src/agents/nodes/duplicate_detector.py`
  - After exact hash check, generate embedding for normalized error via `httpx` call to an embedding model
  - Search Qdrant `error_signatures` collection for cosine similarity ≥ `settings.DUPLICATE_THRESHOLD` (default: 0.85)
  - If vector match found: treat as duplicate (with `duplicate_source: "vector"`)
- Add `DUPLICATE_THRESHOLD: float = 0.85` to `src/config/settings.py`
- Add `EMBEDDING_MODEL: str = "text-embedding-3-small"` to settings

---

#### Task 2.2.2 → `ai-agent-architect`

**What:** `src/agents/nodes/learner.py` — triage outcome learning  
**Why:** Every resolved triage is a labeled training example. The learner stores these in Qdrant and uses them to dynamically improve prompts with few-shot examples.  
**Files:**
- Create `src/agents/nodes/learner.py`
  - Runs after `notifier` (final node); reads full `TriageState`
  - Upserts the normalized error + classification into Qdrant with metadata (category, confidence, resolution)
  - Exports top-5 similar historical examples as `few_shot_examples: list[dict]` in state
- Update `src/agents/nodes/failure_classifier.py` — read `few_shot_examples` from state and inject into prompt if available
- Update `src/agents/orchestrator.py` — add `learner` as the final node after `notifier`

---

#### Task 2.2.3 → `code-implementation-specialist`

**What:** `POST /api/v1/failures/{id}/feedback` endpoint  
**Why:** Human reviewers can correct AI classifications. This feedback feeds the learning loop.  
**Files:**
- Create/modify `src/api/routes/failures.py` — add feedback endpoint
  - Body: `{ "correct_category": str, "resolution": str, "notes": str }`
  - Updates `FailureClassification.human_category`
  - Triggers `learner.update_outcome()` async
- Update `src/schemas/failure_schemas.py` — add `FeedbackRequest` and `FeedbackResponse`

---

#### Task 2.2.4 → `testing-qa-expert`

**What:** Tests for learner node + upgraded duplicate detector  
**Files:**
- Create `tests/unit/agents/test_learner.py`
  - Mock Qdrant upsert; verify correct embedding + metadata stored
  - Test few-shot injection into classifier state
- Update `tests/unit/agents/test_duplicate_detector.py`
  - Add test for vector-match path (mock Qdrant search result)
  - Add test for fallback to exact hash when Qdrant returns no results

---

### Sprint 2.3 — Auth + Trend Dashboards (Weeks 5–6)

---

#### Task 2.3.1 → `code-implementation-specialist`

**What:** JWT auth middleware + login endpoint  
**Why:** The dashboard is currently open to anyone. Before real-world deployment, even basic auth is required.  
**Files:**
- Modify `src/api/middleware.py` — add `JWTMiddleware` that validates `Authorization: Bearer <token>` on all `/api/v1/*` routes except `/health` and `/readiness`
- Add `POST /api/v1/auth/login` — accepts `{ email, password }`, returns signed JWT (HS256, 24h expiry)
- Add `AUTH_SECRET_KEY: str` to `src/config/settings.py`
- Add a seeded admin user to `scripts/seed_db.py`

---

#### Task 2.3.2 → `ui-design-specialist`

**What:** Login page + protected routes in React  
**Files:**
- Create `dashboard/src/pages/Login.jsx` — email/password form, stores JWT in `localStorage`
- Modify `dashboard/src/api/client.js` — attach `Authorization: Bearer` header to all requests
- Modify `dashboard/src/App.jsx` — add `<ProtectedRoute>` wrapper; redirect `/login` if no token
- Create `dashboard/src/components/ProtectedRoute.jsx`

---

#### Task 2.3.3 → `code-implementation-specialist`

**What:** Trend API endpoints  
**Files:**
- Add to `src/api/routes/dashboard.py`:
  - `GET /api/v1/trends/daily?days=30` — failure counts + classification breakdown per day
  - `GET /api/v1/trends/weekly?weeks=12` — same, weekly granularity
  - `GET /api/v1/trends/top-failures?limit=10` — most-repeated test names
- Add queries to `src/db/repositories/failure_repo.py`

---

#### Task 2.3.4 → `ui-design-specialist`

**What:** Historical trend charts in React dashboard  
**Files:**
- Create `dashboard/src/pages/Trends.jsx`
  - Line chart: failure count over time (Recharts `LineChart`)
  - Stacked bar: breakdown by classification category
  - Table: top 10 most-failing tests with flakiness badge
- Update `dashboard/src/components/Layout.jsx` — add "Trends" nav link
- Update `dashboard/src/App.jsx` — add `/trends` route
- Update `dashboard/src/api/client.js` — add `getDailyTrends()`, `getWeeklyTrends()`, `getTopFailures()`

---

## Phase 3 — Predictive & Autonomous
> **Duration:** ~6 weeks (3 sprints × 2 weeks)  
> **Goal:** Move beyond reactive triage to proactive risk management and autonomous remediation  
> **New features:** Visual regression, release risk scoring, autonomous CI reruns, self-healing suggestions, Slack interactivity

---

### Sprint 3.1 — Visual Analysis Agent (Weeks 1–2)

---

#### Task 3.1.1 → `code-implementation-specialist`

**What:** Screenshot attachment support in webhook payloads  
**Files:**
- Modify `src/schemas/webhook_payloads.py` — add optional `screenshots: list[str]` (base64) field
- Create `src/integrations/storage/` package with a `screenshot_store.py` that saves images to local disk (or S3 if `S3_BUCKET` is configured)
- Modify webhook handlers to extract screenshots and store them, returning a list of file paths / URLs

---

#### Task 3.1.2 → `ai-agent-architect`

**What:** `src/agents/nodes/visual_analyzer.py`  
**Why:** UI test failures with screenshots need visual inspection — log analysis alone can't detect layout regressions.  
**Files:**
- Create `src/agents/nodes/visual_analyzer.py`
  - Reads `TriageState`: `screenshot_urls: list[str]`
  - Calls Claude with vision: passes screenshots as base64 image blocks
  - Prompt: "Identify visual regressions, layout breaks, or missing elements compared to a passing test"
  - Writes: `visual_regression: bool`, `visual_diff_summary: str`
- Update `src/agents/state.py` — add `screenshot_urls`, `visual_regression`, `visual_diff_summary`
- Update `src/agents/orchestrator.py` — add `visual_analyzer` node; only executed when `screenshot_urls` is non-empty

---

#### Task 3.1.3 → `testing-qa-expert`

**What:** Tests for visual_analyzer node  
**Files:**
- Create `tests/unit/agents/test_visual_analyzer.py`
  - Mock Claude vision response with and without regression detected
  - Verify node short-circuits (returns `visual_regression=False`) when no screenshots present
  - Test with a real base64-encoded test image fixture

---

### Sprint 3.2 — Release Risk Scoring (Weeks 3–4)

---

#### Task 3.2.1 → `database-infrastructure-specialist`

**What:** `release_scores` table + Alembic migration  
**Files:**
- Create `src/models/release_score.py`
  - `id` UUID PK, `commit_sha` VARCHAR(40), `repository` VARCHAR, `branch` VARCHAR
  - `score` FLOAT (0–10), `recommendation` VARCHAR(10) (PASS/CAUTION/BLOCK)
  - `breakdown` JSONB (per-category counts), `computed_at` TIMESTAMPTZ
- Create Alembic migration
- Create `src/db/repositories/release_score_repo.py`

---

#### Task 3.2.2 → `ai-agent-architect`

**What:** `src/agents/nodes/release_scorer.py`  
**Why:** Engineering managers need a single signal per release — not 50 individual failure triages.  
**Files:**
- Create `src/agents/nodes/release_scorer.py`
  - Queries all `TestFailure` records for the current `commit_sha`
  - Scoring formula:
    - Base: `product_bug` failures × 2.0 pts, `flaky_test` × 0.3 pts, `env_issue` × 1.0 pt
    - Modifiers: critical severity × 1.5, high flaky rate on new tests × 0.5
    - Clamp to 0–10
    - Recommendation: < 4 → PASS, 4–7 → CAUTION, > 7 → BLOCK
  - Writes score to `release_scores` table
- Update `src/agents/state.py` — add `release_score: float`, `release_recommendation: str`

---

#### Task 3.2.3 → `code-implementation-specialist`

**What:** Release score API endpoints  
**Files:**
- Add to `src/api/routes/` (new file `releases.py`):
  - `GET /api/v1/releases/{commit_sha}/score` — returns score, recommendation, breakdown
  - `GET /api/v1/releases/recent?limit=20` — list of recent scored releases
  - `POST /api/v1/releases/{commit_sha}/score` — manually trigger score computation for a commit
- Register router in `src/api/app.py`

---

### Sprint 3.3 — Autonomous Reruns + Self-Healing (Weeks 5–6)

---

#### Task 3.3.1 → `ai-agent-architect`

**What:** `src/agents/nodes/auto_rerun.py`  
**Why:** When a test is classified as flaky, the fastest resolution is a re-run — not a Jira ticket.  
**Files:**
- Create `src/agents/nodes/auto_rerun.py`
  - Only executes when `is_flaky=True` and `retry_count < 2`
  - Calls `trigger_build_rerun()` tool for Jenkins or GitHub re-run API
  - Rate guard: max 2 reruns per `test_name` per calendar day (tracked in Redis via `SET NX EX 86400`)
  - Writes: `rerun_triggered: bool`, `rerun_url: str`
- Update orchestrator: after `flaky_detector`, if `is_flaky=True` → route to `auto_rerun` instead of `ticket_creator`
- Update `src/agents/state.py` — add `rerun_triggered`, `rerun_url`

---

#### Task 3.3.2 → `ai-agent-architect`

**What:** `src/agents/nodes/fix_suggester.py`  
**Why:** For known failure patterns (assertion errors, import failures, config errors), Claude can generate a targeted fix suggestion that the Slack notification includes.  
**Files:**
- Create `src/agents/nodes/fix_suggester.py`
  - Reads `TriageState`: `classification`, `error_message`, `stack_trace`, `commit_diff` (if available)
  - Calls Claude with structured output: `FixSuggestion(has_suggestion: bool, suggestion: str, confidence: float)`
  - Only invokes Claude if `classification` is `product_bug` or `config_error`
  - Writes: `suggested_fix: str | None`
- Update `src/agents/nodes/notifier.py` — include `suggested_fix` block in Slack message if present
- Update `src/agents/state.py` — add `suggested_fix`

---

#### Task 3.3.3 → `code-implementation-specialist`

**What:** Slack interactive buttons — "Resolve" / "Re-triage" callbacks  
**Why:** Developers should be able to close a triage or kick off a re-triage directly from the Slack notification without opening the dashboard.  
**Files:**
- Add `POST /api/v1/slack/interactive` route in `src/api/routes/webhooks.py` (or new file)
  - Verifies Slack request signature
  - Dispatches `action_id` to appropriate handler:
    - `resolve_failure` → sets `TestFailure.status = "resolved"`
    - `retriage_failure` → enqueues `run_triage_pipeline.delay(pipeline_event_id)`
- Modify `src/integrations/slack/message_builder.py` — add actions block with Resolve and Re-triage buttons
- Add `SLACK_INTERACTIVE_ENDPOINT` to settings

---

#### Task 3.3.4 → `dev-ops-engineer`

**What:** Custom Grafana dashboards  
**Why:** Prometheus metrics are emitted but Grafana has no pre-built panels — the oncall engineer has nothing to look at.  
**Files:**
- Create `grafana/dashboards/triage_overview.json`
  - Panel 1: Triage throughput (failures/hour) — `rate(failures_received_total[1h])`
  - Panel 2: Classification distribution — pie chart from `classification_distribution` counter
  - Panel 3: Agent latency p50/p95 — histogram from `triage_duration_seconds`
  - Panel 4: Error rate (failed triages / total) — ratio panel
  - Panel 5: Flaky test count over time
- Modify `docker-compose.yml` — mount `grafana/dashboards/` as a provisioned dashboard source

---

#### Task 3.3.5 → `testing-qa-expert`

**What:** Final Phase 3 test audit  
**Files:**
- Create `tests/unit/agents/test_auto_rerun.py` — verify Redis rate guard, verify rerun not triggered when `retry_count >= 2`
- Create `tests/unit/agents/test_fix_suggester.py` — mock Claude structured output, verify suggestion injected into notifier state
- Create `tests/integration/test_slack_interactive.py` — POST mock Slack action payload, verify status update

---

## Phase Summary

| Phase | Sprints | Weeks | Key Deliverables |
|-------|---------|-------|------------------|
| **Phase 1: Production Hardening** | 1.1, 1.2 | 2 | Qdrant init, migration entrypoint, Celery retries, E2E smoke test, dashboard polling |
| **Phase 2: Enhanced Intelligence** | 2.1, 2.2, 2.3 | 6 | Flaky detection, vector dedup, learning loop, auth, trend dashboards |
| **Phase 3: Predictive & Autonomous** | 3.1, 3.2, 3.3 | 6 | Visual analysis, release scoring, auto-rerun, fix suggestions, Slack interactivity |

**Total remaining:** ~14 weeks to full Phase 3 completion.

---

## Dependency Graph

```
Phase 1 (must complete first)
  ├─ 1.1.1 Qdrant init         ← unblocks any real triage run
  ├─ 1.1.2 Migration entrypoint ← unblocks Docker deploy
  └─ 1.1.3 Celery retries      ← unblocks reliable processing
       │
       ▼
Phase 2
  ├─ Sprint 2.1: Flaky Detection
  │    └─ 2.1.1 DB table → 2.1.2 node → 2.1.3 tests
  ├─ Sprint 2.2: Learning & Vector Dedup (parallel with 2.1)
  │    ├─ 2.2.1 Vector dedup upgrade
  │    ├─ 2.2.2 Learner node (depends on 2.2.1 Qdrant collection)
  │    └─ 2.2.3 Feedback endpoint
  └─ Sprint 2.3: Auth + Trends (parallel with 2.1/2.2)
       ├─ 2.3.1 JWT middleware
       ├─ 2.3.2 Login page (depends on 2.3.1)
       ├─ 2.3.3 Trend API endpoints
       └─ 2.3.4 Trend charts (depends on 2.3.3)

Phase 3 (requires Phase 2 complete)
  ├─ Sprint 3.1: Visual Analysis (independent)
  ├─ Sprint 3.2: Release Scoring (depends on flaky data from Sprint 2.1)
  └─ Sprint 3.3: Auto-Rerun + Self-Healing (depends on 3.1 + 3.2)
```

---

## Verification Checkpoints

**After Phase 1:**
1. `docker compose up -d` + `make migrate` — no manual steps needed
2. POST a real GitHub Actions webhook — verify it flows through to Jira + Slack
3. E2E smoke test passes: `uv run pytest tests/integration/test_e2e_smoke.py`
4. Simulate Claude API 503 — verify Celery retries 3× and marks event as failed

**After Phase 2:**
1. Submit 5 identical errors — duplicate detector catches them via vector similarity
2. After 20 runs of a flaky test — `is_flaky=True` classified, no Jira ticket created
3. Login page works; `/api/v1/failures` returns 401 without JWT
4. Trends page shows 30-day breakdown by classification category

**After Phase 3:**
1. Submit a flaky test failure — auto-rerun triggered; no duplicate rerun within same day
2. Submit a webhook with screenshot — Slack notification includes visual regression summary
3. New commit with 8 product_bug failures → release score > 7 → recommendation = BLOCK
4. Click "Resolve" in Slack — failure status updates to resolved in dashboard
