"""CLI tool for sending test webhooks to the local dev server.

Usage:
    python scripts/simulate_webhook.py --provider jenkins
    python scripts/simulate_webhook.py --provider github_actions
    python scripts/simulate_webhook.py --provider jenkins --url http://localhost:8000 --secret my-secret
"""

import argparse
import hashlib
import hmac
import json
import os

import httpx

# ---------------------------------------------------------------------------
# Fixture payloads
# ---------------------------------------------------------------------------

JENKINS_PAYLOAD: dict = {
    "name": "my-pipeline",
    "url": "job/my-pipeline/",
    "build": {
        "full_url": "http://jenkins:8080/job/my-pipeline/42/",
        "number": 42,
        "status": "FAILURE",
        "url": "job/my-pipeline/42/",
        "scm": {
            "url": "https://github.com/org/my-service",
            "branch": "main",
            "commit": "abc123def456",
        },
        "artifacts": {},
    },
}

GITHUB_ACTIONS_PAYLOAD: dict = {
    "action": "completed",
    "workflow_run": {
        "id": 9876543210,
        "name": "CI",
        "head_branch": "main",
        "head_sha": "abc123def456",
        "status": "completed",
        "conclusion": "failure",
        "html_url": "https://github.com/org/my-service/actions/runs/9876543210",
        "run_number": 42,
        "repository": {
            "id": 1234,
            "full_name": "org/my-service",
            "html_url": "https://github.com/org/my-service",
        },
    },
    "repository": {
        "id": 1234,
        "full_name": "org/my-service",
        "html_url": "https://github.com/org/my-service",
    },
}

# Map provider name → (payload dict, fixture file path, signature header name)
_PROVIDERS: dict[str, tuple[dict, str, str]] = {
    "jenkins": (
        JENKINS_PAYLOAD,
        "tests/fixtures/jenkins_webhook.json",
        "X-Jenkins-Signature",
    ),
    "github_actions": (
        GITHUB_ACTIONS_PAYLOAD,
        "tests/fixtures/github_actions_webhook.json",
        "X-Hub-Signature-256",
    ),
}


def _write_fixture_if_empty(fixture_path: str, payload: dict) -> None:
    """Write payload JSON to the fixture file if it is currently empty."""
    if os.path.exists(fixture_path) and os.path.getsize(fixture_path) < 10:
        with open(fixture_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        print(f"Wrote fixture: {fixture_path}")


def _compute_signature(secret: str, body: bytes) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Send a test webhook to the local server.")
    parser.add_argument(
        "--provider",
        required=True,
        choices=list(_PROVIDERS.keys()),
        help="CI/CD provider to simulate",
    )
    parser.add_argument(
        "--url",
        default="http://localhost:8000",
        help="Base URL of the running server (default: http://localhost:8000)",
    )
    parser.add_argument(
        "--secret",
        default="test-secret",
        help="Webhook HMAC secret (default: test-secret)",
    )
    args = parser.parse_args()

    payload, fixture_path, sig_header_name = _PROVIDERS[args.provider]

    # Write fixture file if currently empty
    _write_fixture_if_empty(fixture_path, payload)

    # Serialise payload
    body: bytes = json.dumps(payload).encode()

    # Compute HMAC signature
    sig = _compute_signature(args.secret, body)

    # Build request
    endpoint = f"{args.url}/api/v1/webhooks/{args.provider}"
    headers = {
        "Content-Type": "application/json",
        sig_header_name: sig,
    }

    print(f"POST {endpoint}")
    print(f"Signature header: {sig_header_name}: {sig}")

    response = httpx.post(endpoint, content=body, headers=headers)

    print(f"Status: {response.status_code}")
    print(f"Response: {response.text}")


if __name__ == "__main__":
    main()
