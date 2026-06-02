from __future__ import annotations

import structlog
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from src.agents.state import TriageState
from src.config.settings import get_settings

logger = structlog.get_logger(__name__)

# ── Rule-based pattern definitions ────────────────────────────────────────────

_RULES: list[tuple[list[str], str]] = [
    # All tokens in the list must appear (case-insensitive) for the rule to fire.
    (["connection refused"], "Service connection refused"),
    (["timeout", "database"], "Database/cache timeout"),
    (["timeout", "redis"], "Database/cache timeout"),
    (["timeout", "postgres"], "Database/cache timeout"),
    (["502 bad gateway"], "Upstream service unavailable"),
    (["503 service"], "Upstream service unavailable"),
    (["oomkilled"], "Out of memory / OOMKilled"),
    (["out of memory"], "Out of memory / OOMKilled"),
]

# ── Pydantic schema for Claude structured output ───────────────────────────────


class EnvironmentHealthResult(BaseModel):
    """Structured output returned by Claude for environment health analysis."""

    is_healthy: bool = Field(
        ...,
        description=(
            "True if the test failure appears to be caused by a code bug rather than "
            "an environment problem. False if infrastructure, networking, dependency, "
            "or configuration issues are detected."
        ),
    )
    issues: list[str] = Field(
        default_factory=list,
        description=(
            "Human-readable descriptions of detected environment problems. "
            "Empty list when is_healthy=True."
        ),
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Confidence score from 0.0 to 1.0 for this assessment.",
    )


# ── System prompt ──────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are a senior DevOps / SRE engineer performing automated CI/CD failure triage.

Your task is to determine whether a test failure was caused by an ENVIRONMENT PROBLEM
rather than a code defect.  Environment problems include:

- Infrastructure failures (unreachable services, pod evictions, OOMKilled containers)
- Network issues (connection refused, DNS failures, TLS errors, timeouts to external services)
- Dependency failures (missing packages, broken pip installs, NPM registry errors)
- Configuration errors (missing env vars, wrong secrets, misconfigured service URLs)
- Database/cache unavailability (Postgres down, Redis timeout, connection pool exhausted)
- Upstream service degradation (5xx from third-party APIs, internal micro-services)

Set is_healthy=False and populate issues[] when you detect any of the above.
Set is_healthy=True and leave issues[] empty when the failure looks like a product bug,
flaky test, or assertion failure unrelated to infrastructure.

Be conservative: prefer is_healthy=True if you are unsure.
"""


# ── Helper ─────────────────────────────────────────────────────────────────────


def _apply_rules(combined_text: str) -> list[str]:
    """Return a deduplicated list of issue labels matched by the rule set.

    Each rule fires when every token in its token list appears in *combined_text*
    (case-insensitive).  Duplicate labels (e.g. two timeout rules both matching)
    are collapsed into a single entry.
    """
    lower = combined_text.lower()
    matched: list[str] = []
    seen: set[str] = set()
    for tokens, label in _RULES:
        if all(token in lower for token in tokens) and label not in seen:
            matched.append(label)
            seen.add(label)
    return matched


def _extract_text_from_state(state: TriageState) -> tuple[str, str, str]:
    """Pull error_message, stack_trace, and raw_log out of the real TriageState fields.

    The state does not have top-level ``error_message`` / ``stack_trace`` fields; those
    live inside the ``current_failure`` dict that was populated by pipeline_monitor.
    ``raw_logs`` (plural) is the full console output stored by pipeline_monitor.
    """
    current_failure: dict = state.get("current_failure") or {}  # type: ignore[assignment]
    error_message: str = current_failure.get("error_message") or ""
    stack_trace: str = current_failure.get("stack_trace") or ""
    raw_log: str = state.get("raw_logs") or ""  # type: ignore[assignment]
    return error_message, stack_trace, raw_log


# ── Node ───────────────────────────────────────────────────────────────────────


async def environment_health_node(state: TriageState) -> dict:
    """Assess whether the current failure was caused by an environment problem.

    Decision flow
    -------------
    1. Short-circuit: if classifier already labelled this as ``env_issue``, mark
       unhealthy immediately — no need to call Claude.
    2. Rule-based pre-check: scan error text for well-known infrastructure
       keywords.  If any rule fires, return without calling Claude (fast path).
    3. Claude structured output: ask Claude to assess environment health and
       return an ``EnvironmentHealthResult``.
    4. Error handling: if the Claude call fails, default to
       ``environment_healthy=True`` and log a warning so the graph can continue.

    Writes to state
    ---------------
    - ``environment_healthy`` (bool)
    - ``environment_issues`` (list[str])
    """
    log = logger.bind(
        node="environment_health",
        pipeline_event_id=state["pipeline_event_id"],
    )
    log.info("environment_health.started")

    # ── 1. Short-circuit on pre-existing env_issue classification ──────────────
    classification: dict | None = state.get("classification")  # type: ignore[assignment]
    category = classification.get("category", "") if isinstance(classification, dict) else ""

    if category == "env_issue":
        log.info("environment_health.short_circuit", reason="classification=env_issue")
        return {
            "environment_healthy": False,
            "environment_issues": ["Classification indicates environment issue"],
        }

    # ── 2. Rule-based pre-check ────────────────────────────────────────────────
    error_message, stack_trace, raw_log = _extract_text_from_state(state)
    combined = "\n".join([error_message, stack_trace, raw_log])
    matched_issues = _apply_rules(combined)

    if matched_issues:
        log.info(
            "environment_health.rule_match",
            issues=matched_issues,
        )
        return {
            "environment_healthy": False,
            "environment_issues": matched_issues,
        }

    # ── 3. Claude structured output call ──────────────────────────────────────
    try:
        settings = get_settings()
        llm = ChatAnthropic(
            model=settings.default_model,
            api_key=settings.anthropic_api_key,
        )
        structured_llm = llm.with_structured_output(EnvironmentHealthResult)

        user_message = (
            f"Error message:\n{error_message[:2000]}\n\n"
            f"Stack trace:\n{stack_trace[:2000]}\n\n"
            f"Raw log (truncated):\n{raw_log[:2000]}"
        )

        result: EnvironmentHealthResult = await structured_llm.ainvoke(  # type: ignore[assignment]
            [
                SystemMessage(content=_SYSTEM_PROMPT),
                HumanMessage(content=user_message),
            ]
        )

        log.info(
            "environment_health.claude_result",
            is_healthy=result.is_healthy,
            issues=result.issues,
            confidence=result.confidence,
        )

        return {
            "environment_healthy": result.is_healthy,
            "environment_issues": result.issues,
        }

    except Exception as exc:
        log.warning(
            "environment_health.claude_error",
            error=str(exc),
        )
        # Graceful degradation: assume healthy so the graph can continue.
        return {
            "environment_healthy": True,
            "environment_issues": [],
        }
