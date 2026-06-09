"""End-to-end smoke test for the full webhook to Celery to DB chain.

Exercises:
  1. Build a minimal, valid GitHub Actions webhook payload.
  2. HMAC-sign it with GITHUB_WEBHOOK_SECRET from env.
  3. POST to http://localhost:8000/api/v1/webhooks/github_actions.
  4. Extract pipeline_event_id from the 202 response.
  5. Poll the PostgreSQL DB directly every 2 s for up to 60 s until
     pipeline_events.status is no longer "pending".
  6. Print the final pipeline event status and any associated failure rows.
  7. Exit 0 on success (status="triaged"), 1 on timeout or unexpected status.

Usage:
    GITHUB_WEBHOOK_SECRET=my-secret python scripts/e2e_smoke.py
    python scripts/e2e_smoke.py --base-url http://localhost:8000 --secret my-secret
    python scripts/e2e_smoke.py --timeout 120

Requirements:
    - A running FastAPI server (make dev)
    - A running Celery worker (make worker)
    - PostgreSQL accessible at DATABASE_URL (or default localhost)
    - GITHUB_WEBHOOK_SECRET set (or pass --secret)
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
import os
import sys
import time

import httpx

# ---------------------------------------------------------------------------
# Payload — minimal but schema-valid GitHubActionsWebhookPayload
# ---------------------------------------------------------------------------

GITHUB_ACTIONS_PAYLOAD: dict = {
    "action": "completed",
    "workflow_run": {
        "id": 1111111111,
        "name": "e2e-smoke-test",
        "head_branch": "main",
        "head_sha": "e2esmoke0000000000000000000000000000000",
        "status": "completed",
        "conclusion": "failure",
        "html_url": "https://github.com/org/smoke-test/actions/runs/1111111111",
        "run_number": 1,
        "repository": {
            "id": 9999,
            "full_name": "org/smoke-test",
            "html_url": "https://github.com/org/smoke-test",
        },
    },
    "repository": {
        "id": 9999,
        "full_name": "org/smoke-test",
        "html_url": "https://github.com/org/smoke-test",
    },
}

# Pipeline event statuses that mean "still in progress" — keep polling
_TRANSITIONAL_STATUSES = {"pending", "triaging"}


def _sign(secret: str, body: bytes) -> str:
    """Compute the HMAC-SHA256 signature in GitHub's ``sha256=<hex>`` format."""
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _post_webhook(base_url: str, secret: str, payload: dict) -> str:
    """POST the webhook and return the ``pipeline_event_id`` from the 202 body."""
    body = json.dumps(payload).encode()
    sig = _sign(secret, body)
    endpoint = f"{base_url}/api/v1/webhooks/github_actions"

    print(f"POST {endpoint}")
    print(f"  X-Hub-Signature-256: {sig[:30]}...")

    resp = httpx.post(
        endpoint,
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": sig,
        },
        timeout=10.0,
    )

    if resp.status_code != 202:
        print(f"  ERROR — expected 202, got {resp.status_code}: {resp.text}")
        sys.exit(1)

    data = resp.json()
    event_id = data.get("pipeline_event_id")
    if not event_id:
        print(f"  ERROR — no pipeline_event_id in response: {data}")
        sys.exit(1)

    print(f"  pipeline_event_id = {event_id}")
    return event_id


async def _poll_pipeline_event(
    database_url: str,
    pipeline_event_id: str,
    poll_interval: float,
    timeout_seconds: float,
) -> dict | None:
    """Poll ``pipeline_events`` in PostgreSQL until status leaves transitional states.

    Returns a dict with the final event row and its associated test_failures,
    or None if the poll times out.

    Uses asyncpg directly so the script has no dependency on the app's
    SQLAlchemy session machinery.
    """
    import asyncpg  # type: ignore[import]

    # asyncpg uses the ``postgres://`` scheme; strip any SQLAlchemy driver prefix
    dsn = database_url.replace("postgresql+asyncpg://", "postgresql://")

    conn = await asyncpg.connect(dsn=dsn)
    try:
        deadline = time.monotonic() + timeout_seconds
        attempt = 0

        while time.monotonic() < deadline:
            attempt += 1

            row = await conn.fetchrow(
                "SELECT id, status, provider, repository, branch"
                " FROM pipeline_events WHERE id = $1",
                pipeline_event_id,
            )
            if row is None:
                print(f"  poll attempt {attempt}: pipeline event not found yet")
                await asyncio.sleep(poll_interval)
                continue

            status = row["status"]
            print(f"  poll attempt {attempt}: status={status!r}")

            if status not in _TRANSITIONAL_STATUSES:
                # Fetch associated test_failure rows
                failures = await conn.fetch(
                    "SELECT id, test_name, status FROM test_failures WHERE pipeline_event_id = $1",
                    pipeline_event_id,
                )
                return {
                    "event": dict(row),
                    "failures": [dict(f) for f in failures],
                }

            await asyncio.sleep(poll_interval)

    finally:
        await conn.close()

    return None  # timed out


def _print_summary(result: dict) -> None:
    """Print a human-readable summary of the triage result."""
    event = result["event"]
    failures = result["failures"]

    print()
    print("=" * 60)
    print(f"  Pipeline Event : {event['id']}")
    print(f"  Provider       : {event.get('provider', '-')}")
    print(f"  Repository     : {event.get('repository', '-')}")
    print(f"  Branch         : {event.get('branch', '-')}")
    print(f"  Status         : {event['status']}")
    print(f"  Failures       : {len(failures)}")
    for f in failures:
        print()
        print(f"    Failure ID   : {f['id']}")
        print(f"    Test Name    : {f.get('test_name', '<unknown>')}")
        print(f"    Status       : {f.get('status', '<unknown>')}")
    print("=" * 60)


async def _run(args: argparse.Namespace) -> int:
    """Async entry-point; returns the process exit code."""
    # Step 1-4: POST the webhook synchronously (httpx sync is fine here)
    pipeline_event_id = _post_webhook(args.base_url, args.secret, GITHUB_ACTIONS_PAYLOAD)

    # Step 5: Poll the DB
    print(
        f"\nPolling PostgreSQL for pipeline event status every {args.poll_interval}s "
        f"(timeout: {args.timeout}s)..."
    )

    result = await _poll_pipeline_event(
        database_url=args.database_url,
        pipeline_event_id=pipeline_event_id,
        poll_interval=args.poll_interval,
        timeout_seconds=args.timeout,
    )

    if result is None:
        print(
            f"\nTIMEOUT: pipeline event {pipeline_event_id} did not leave "
            f"transitional state within {args.timeout}s."
        )
        return 1

    # Step 6: Print summary
    _print_summary(result)

    # Step 7: Determine exit code
    final_status = result["event"]["status"]
    if final_status == "triaged":
        print("\nSUCCESS: pipeline event reached 'triaged' status.")
        return 0
    else:
        print(f"\nFAILURE: pipeline event ended with status={final_status!r}.")
        return 1


def main() -> None:
    parser = argparse.ArgumentParser(
        description="End-to-end smoke test for the webhook to Celery to DB chain."
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("APP_BASE_URL", "http://localhost:8000"),
        help="Base URL of the running server (default: http://localhost:8000)",
    )
    parser.add_argument(
        "--secret",
        default=os.getenv("GITHUB_WEBHOOK_SECRET", ""),
        help="HMAC secret (default: $GITHUB_WEBHOOK_SECRET env var)",
    )
    parser.add_argument(
        "--database-url",
        default=os.getenv(
            "DATABASE_URL",
            "postgresql+asyncpg://postgres:postgres@localhost:5432/autonomous_qa",
        ),
        help="PostgreSQL DSN (default: $DATABASE_URL env var)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=60.0,
        help="Maximum seconds to poll for completion (default: 60)",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=2.0,
        help="Seconds between poll attempts (default: 2)",
    )
    args = parser.parse_args()

    if not args.secret:
        print(
            "ERROR: No webhook secret provided. "
            "Set GITHUB_WEBHOOK_SECRET env var or pass --secret."
        )
        sys.exit(1)

    exit_code = asyncio.run(_run(args))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
