"""Seed the database with realistic demo data for local development.

Creates pipeline events, test failures, and AI classifications spanning
the last 30 days so dashboard charts and tables are immediately populated.

Usage:
    uv run python scripts/seed_db.py
    uv run python scripts/seed_db.py --days 14 --failures 30
"""
from __future__ import annotations

import argparse
import asyncio
import random
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.config.settings import get_settings
from src.models import Base  # noqa: F401 — ensures all models are registered
from src.models.error_signature import ErrorSignature
from src.models.failure_classification import FailureClassification
from src.models.pipeline_event import PipelineEvent
from src.models.test_failure import TestFailure

# ---------------------------------------------------------------------------
# Seed corpus
# ---------------------------------------------------------------------------

REPOSITORIES = [
    "org/api-service",
    "org/frontend",
    "org/data-pipeline",
    "org/auth-service",
]

BRANCHES = ["main", "main", "main", "feature/payments", "feature/auth-v2", "fix/db-pool"]

PROVIDERS = ["jenkins", "github_actions"]

PIPELINES = {
    "jenkins": ["build-and-test", "integration-suite", "e2e-tests"],
    "github_actions": ["CI", "Deploy", "Nightly"],
}

# (test_name, error_message, stack_trace, category, confidence, reasoning)
FAILURE_CORPUS: list[tuple[str, str, str, str, float, str]] = [
    (
        "tests/auth/test_login.py::test_valid_credentials",
        "AssertionError: Expected status 200 but got 500",
        "File tests/auth/test_login.py, line 47\n  assert response.status_code == 200\nAssertionError: Expected status 200 but got 500",
        "product_bug", 0.91,
        "Test asserts a well-defined HTTP contract (200 OK on valid credentials) and receives 500, indicating an unhandled exception in the application.",
    ),
    (
        "tests/payments/test_checkout.py::test_concurrent_cart_updates",
        "TimeoutError: Timed out waiting for lock after 30s",
        "File tests/payments/test_checkout.py, line 88\n  result = await asyncio.wait_for(cart.lock(), timeout=30)\nasyncio.TimeoutError",
        "flaky_test", 0.78,
        "Lock-wait timeout that only manifests under concurrent load — classic flaky-test pattern caused by a race condition in test setup.",
    ),
    (
        "tests/db/test_session.py::test_connection_pool",
        "ConnectionRefusedError: [Errno 111] Connection refused",
        "sqlalchemy.exc.OperationalError: (asyncpg.exceptions.ConnectionFailureError)\nFile tests/db/test_session.py, line 23\n  await engine.connect()",
        "env_issue", 0.88,
        "Database process refused the connection — the service was not running on the CI agent at test time.",
    ),
    (
        "tests/api/test_bulk_import.py::test_import_10k_records",
        "TimeoutError: Test exceeded 120s timeout",
        "File tests/api/test_bulk_import.py, line 112\n  await asyncio.wait_for(importer.run(), timeout=120)\nasyncio.TimeoutError: Task exceeded time limit",
        "timeout", 0.85,
        "Test exceeded its configured 120s timeout processing 10k records — indicates either genuinely slow infrastructure or an unexpectedly large dataset path.",
    ),
    (
        "tests/infra/test_worker.py::test_celery_task_dispatch",
        "WorkerLostError: Worker exited prematurely",
        "celery.exceptions.WorkerLostError: Worker exited prematurely: exitcode 137\nFile tests/infra/test_worker.py, line 34\n  result = task.delay().get(timeout=60)",
        "infra_issue", 0.92,
        "Exit code 137 is SIGKILL — the container was OOM-killed by the CI runner, an infrastructure-level failure.",
    ),
    (
        "tests/config/test_env.py::test_required_secrets_present",
        "KeyError: 'STRIPE_API_KEY'",
        "File tests/config/test_env.py, line 18\n  assert os.environ['STRIPE_API_KEY']\nKeyError: 'STRIPE_API_KEY'",
        "config_error", 0.95,
        "A required environment variable is missing from the test configuration — this is a setup issue, not an application defect.",
    ),
    (
        "tests/integrations/test_stripe.py::test_payment_intent",
        "httpx.ConnectError: All connection attempts failed",
        "httpx.ConnectError: All connection attempts failed\n  File tests/integrations/test_stripe.py, line 56\n  response = await client.post('/v1/payment_intents')",
        "dependency_failure", 0.87,
        "External Stripe API was unreachable during the test run — dependency failure rather than application defect.",
    ),
    (
        "tests/cache/test_session_store.py::test_write_read_roundtrip",
        "redis.exceptions.ConnectionError: Error connecting to localhost:6379",
        "redis.exceptions.ConnectionError: Error 111 connecting to localhost:6379. Connection refused.\nFile tests/cache/test_session_store.py, line 23\n  client.set('key', 'value')",
        "env_issue", 0.90,
        "Redis process refused the connection, meaning the service never started on the CI agent.",
    ),
    (
        "tests/auth/test_token.py::test_jwt_refresh",
        "AssertionError: Token expiry mismatch: expected 3600 got 1800",
        "File tests/auth/test_token.py, line 67\n  assert token.expires_in == 3600\nAssertionError: Token expiry mismatch",
        "product_bug", 0.83,
        "Token expiry value returned by the endpoint differs from the contract — regression in the auth service configuration.",
    ),
    (
        "tests/api/test_search.py::test_full_text_search_ranking",
        "AssertionError: Expected result[0].id == 'doc-42', got 'doc-17'",
        "File tests/api/test_search.py, line 94\n  assert results[0]['id'] == 'doc-42'\nAssertionError: ranking order changed",
        "flaky_test", 0.72,
        "Search ranking is non-deterministic for equal-scoring documents — test assumes a specific tie-breaking order that is not guaranteed.",
    ),
    (
        "tests/jobs/test_report_generator.py::test_monthly_pdf",
        "MemoryError: Unable to allocate 2.4 GiB",
        "File tests/jobs/test_report_generator.py, line 45\n  pdf = generator.render(dataset)\nMemoryError: Unable to allocate array",
        "infra_issue", 0.88,
        "Memory allocation failure during PDF rendering — the CI runner ran out of available RAM, an infrastructure constraint.",
    ),
    (
        "tests/api/test_ratelimit.py::test_burst_requests",
        "AssertionError: Expected 429 but got 200 on request 101",
        "File tests/api/test_ratelimit.py, line 78\n  assert response.status_code == 429\nAssertionError",
        "product_bug", 0.86,
        "Rate limiting is not enforced at the configured threshold — the application is accepting requests beyond the limit.",
    ),
]


async def seed(days: int, num_failures: int) -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    now = datetime.now(UTC)
    inserted_events = 0
    inserted_failures = 0
    inserted_classifications = 0

    print(f"Seeding {num_failures} failures spread over {days} days...")

    async with session_factory() as session:
        for i in range(num_failures):
            # Spread events across the lookback window with more recent ones more common
            age_days = random.betavariate(1.5, 4) * days
            created_at = now - timedelta(days=age_days, hours=random.randint(0, 23))

            provider = random.choice(PROVIDERS)
            repository = random.choice(REPOSITORIES)
            branch = random.choice(BRANCHES)

            # Pipeline event
            event = PipelineEvent(
                id=uuid.uuid4(),
                provider=provider,
                provider_build_id=str(random.randint(100, 9999)),
                repository=repository,
                branch=branch,
                commit_sha=uuid.uuid4().hex[:40],
                pipeline_name=random.choice(PIPELINES[provider]),
                status="failure",
                raw_payload={"seeded": True, "index": i},
                received_at=created_at,
                created_at=created_at,
                updated_at=created_at,
            )
            session.add(event)
            await session.flush()
            inserted_events += 1

            # Pick a failure from the corpus (weighted: product_bug most common)
            corpus_entry = random.choice(FAILURE_CORPUS)
            test_name, error_msg, stack_trace, category, confidence, reasoning = corpus_entry

            # Vary test names slightly so the top-failing chart has variety
            if random.random() < 0.4:
                test_name = test_name.replace("::", f"_{random.randint(1,5)}::")

            failure = TestFailure(
                id=uuid.uuid4(),
                pipeline_event_id=event.id,
                test_name=test_name,
                test_suite=test_name.split("/")[1] if "/" in test_name else "unknown",
                test_file=test_name.split("::")[0] if "::" in test_name else test_name,
                error_message=error_msg,
                stack_trace=stack_trace,
                duration_ms=random.randint(100, 30000),
                retry_count=random.randint(0, 3),
                status=random.choice(["triaged", "triaged", "triaged", "resolved", "new"]),
                created_at=created_at,
                updated_at=created_at,
            )
            session.add(failure)
            await session.flush()
            inserted_failures += 1

            # Classification
            # Add slight confidence jitter
            jitter = random.uniform(-0.05, 0.05)
            classification = FailureClassification(
                id=uuid.uuid4(),
                test_failure_id=failure.id,
                category=category,
                confidence=round(max(0.5, min(1.0, confidence + jitter)), 3),
                reasoning=reasoning,
                model_used="claude-sonnet-4-20250514",
                tokens_used=random.randint(800, 2400),
                created_at=created_at,
                updated_at=created_at,
            )
            session.add(classification)

            # Error signature — always unique (uuid salt prevents cross-run collisions)
            import hashlib
            sig_hash = hashlib.sha256(f"{error_msg}{uuid.uuid4()}".encode()).hexdigest()
            signature = ErrorSignature(
                id=uuid.uuid4(),
                signature_hash=sig_hash,
                normalized_error=error_msg,
                occurrence_count=1,
                first_seen_at=created_at,
                last_seen_at=created_at,
                created_at=created_at,
                updated_at=created_at,
            )
            session.add(signature)
            inserted_classifications += 1

        await session.commit()

    await engine.dispose()

    print(f"OK: Inserted {inserted_events} pipeline events")
    print(f"OK: Inserted {inserted_failures} test failures")
    print(f"OK: Inserted {inserted_classifications} classifications")
    print("\nRefresh the dashboard at http://localhost:5173 to see data.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the database with demo failure data.")
    parser.add_argument("--days", type=int, default=30, help="Spread failures over this many days (default: 30)")
    parser.add_argument("--failures", type=int, default=50, help="Total number of failures to insert (default: 50)")
    args = parser.parse_args()
    asyncio.run(seed(args.days, args.failures))


if __name__ == "__main__":
    main()
