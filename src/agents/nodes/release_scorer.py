from __future__ import annotations

import uuid

import structlog
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy import select

from src.agents.nodes.run_tracking import record_agent_runs
from src.agents.prompts.release_scorer_prompt import RELEASE_SCORER_SYSTEM_PROMPT
from src.agents.state import TriageState
from src.config.constants import AgentRunStatus
from src.config.settings import get_settings
from src.db.repositories.pipeline_repo import PipelineEventRepository
from src.db.repositories.release_score_repo import ReleaseScoreRepository
from src.db.session import get_session_factory
from src.models.failure_classification import FailureClassification
from src.models.pipeline_event import PipelineEvent
from src.models.test_failure import TestFailure

logger = structlog.get_logger(__name__)


def _template_summary(
    total: int, bugs: int, flaky: int, score: float, level: str
) -> str:
    return (
        f"This commit has {total} test failure(s) including {bugs} product bug(s) "
        f"and {flaky} flaky test(s). "
        f"Risk score: {score:.0f}/100 ({level}). "
        + (
            "Recommend holding release for investigation."
            if level in ("high", "critical")
            else "Release appears safe to proceed."
        )
    )


async def release_scorer_node(state: TriageState) -> dict:
    """Compute a release risk score for the commit associated with the current pipeline event.

    Steps:
      1. Load the PipelineEvent to extract commit_sha and repository.
      2. Query all TestFailures for that (commit_sha, repository) pair across all runs.
      3. Load FailureClassification for each failure and compute weighted counts.
      4. Derive a 0-100 risk score and risk_level label.
      5. Generate a human-readable risk_summary (via Claude or template fallback).
      6. Upsert the result into the release_scores table.

    Returns a partial state dict with 'release_score' set to a summary dict,
    or None if the event has no commit_sha or an error occurs.
    """
    log = logger.bind(
        node="release_scorer",
        pipeline_event_id=state["pipeline_event_id"],
    )
    log.info("release_scorer.started")

    settings = get_settings()
    session_factory = get_session_factory()
    errors: list[str] = list(state["errors"])

    try:
        # --- Step 1: Load PipelineEvent ---
        async with session_factory() as session:
            event = await PipelineEventRepository().get_by_id(
                session, uuid.UUID(state["pipeline_event_id"])
            )
            if event is None or not event.commit_sha:
                log.warning("release_scorer.skipped", reason="no_commit_sha")
                await record_agent_runs(
                    session_factory,
                    state["failure_ids"],
                    agent_name="release_scorer",
                    status=AgentRunStatus.SKIPPED,
                    output_summary="Skipped: pipeline event has no commit_sha",
                )
                return {"release_score": None, "errors": errors}

            commit_sha = event.commit_sha
            repository = event.repository or ""

            # --- Step 2: All TestFailures for this (commit_sha, repository) ---
            stmt = (
                select(TestFailure)
                .join(PipelineEvent, TestFailure.pipeline_event_id == PipelineEvent.id)
                .where(
                    PipelineEvent.commit_sha == commit_sha,
                    PipelineEvent.repository == repository,
                )
            )
            result = await session.execute(stmt)
            all_failures = list(result.scalars().all())

            # --- Step 3: Load FailureClassification for each failure ---
            failure_classifications: list[tuple[TestFailure, FailureClassification | None]] = []
            for failure in all_failures:
                clf_stmt = select(FailureClassification).where(
                    FailureClassification.test_failure_id == failure.id
                )
                clf_result = await session.execute(clf_stmt)
                clf = clf_result.scalar_one_or_none()
                failure_classifications.append((failure, clf))

        # --- Step 4: Compute counts and score ---
        total_failures = len(all_failures)
        product_bug_count = sum(
            1 for _, c in failure_classifications if c and c.category == "product_bug"
        )
        flaky_count = sum(
            1 for _, c in failure_classifications if c and c.category == "flaky_test"
        )
        env_issue_count = sum(
            1
            for _, c in failure_classifications
            if c and c.category in ("env_issue", "config_error", "dependency_failure")
        )
        infra_count = sum(
            1
            for _, c in failure_classifications
            if c and c.category in ("infra_issue", "timeout")
        )
        # Approximate resolved/known duplicates by status
        duplicate_count = sum(
            1 for f, _ in failure_classifications if f.status in ("resolved",)
        )

        confidences = [c.confidence for _, c in failure_classifications if c is not None]
        avg_confidence = sum(confidences) / len(confidences) if confidences else None

        raw_score = 0.0
        raw_score += min(product_bug_count * 20.0, 40.0)
        raw_score += min(infra_count * 10.0, 20.0)
        raw_score += min(env_issue_count * 5.0, 10.0)
        raw_score -= flaky_count * 5.0
        raw_score -= duplicate_count * 3.0
        if avg_confidence is not None:
            raw_score *= avg_confidence
        score = max(0.0, min(100.0, raw_score))

        if score < 20.0:
            risk_level = "low"
        elif score < 50.0:
            risk_level = "medium"
        elif score < 80.0:
            risk_level = "high"
        else:
            risk_level = "critical"

        log.info(
            "release_scorer.scored",
            commit_sha=commit_sha[:8],
            score=score,
            risk_level=risk_level,
            total_failures=total_failures,
        )

        # --- Step 5: Generate risk_summary ---
        if settings.release_score_claude_enabled:
            try:
                llm = ChatAnthropic(
                    model=settings.default_model,
                    api_key=settings.anthropic_api_key,
                )
                user_prompt = (
                    f"Commit {commit_sha[:8]} in {repository} has {total_failures} test failures "
                    f"(product bugs: {product_bug_count}, flaky: {flaky_count}, "
                    f"env/config: {env_issue_count}, "
                    f"infra/timeout: {infra_count}). Risk score: {score:.0f}/100 ({risk_level}). "
                    "Provide a 2-4 sentence risk assessment for a release manager. "
                    "Be concise and specific."
                )
                response = await llm.ainvoke(
                    [
                        SystemMessage(content=RELEASE_SCORER_SYSTEM_PROMPT),
                        HumanMessage(content=user_prompt),
                    ]
                )
                risk_summary = (
                    response.content
                    if isinstance(response.content, str)
                    else str(response.content)
                )
            except Exception as exc:
                log.warning("release_scorer.claude_failed", error=str(exc))
                risk_summary = _template_summary(
                    total_failures, product_bug_count, flaky_count, score, risk_level
                )
        else:
            risk_summary = _template_summary(
                total_failures, product_bug_count, flaky_count, score, risk_level
            )

        # --- Step 6: Upsert ---
        async with session_factory() as session:
            await ReleaseScoreRepository().upsert(
                session,
                commit_sha=commit_sha,
                repository=repository,
                score=score,
                risk_level=risk_level,
                risk_summary=risk_summary,
                total_failures=total_failures,
                product_bug_count=product_bug_count,
                flaky_count=flaky_count,
                env_issue_count=env_issue_count,
                infra_count=infra_count,
                duplicate_count=duplicate_count,
                avg_confidence=avg_confidence,
            )
            await session.commit()

        await record_agent_runs(
            session_factory,
            state["failure_ids"],
            agent_name="release_scorer",
            status=AgentRunStatus.COMPLETED,
            output_summary=(
                f"score={score:.0f}/100 ({risk_level})\n{risk_summary}"
            ),
        )

        return {
            "release_score": {
                "score": score,
                "risk_level": risk_level,
                "risk_summary": risk_summary,
                "commit_sha": commit_sha,
                "repository": repository,
            },
            "errors": errors,
        }

    except Exception as exc:
        msg = f"release_scorer: error: {exc}"
        log.warning("release_scorer.error", error=str(exc))
        errors.append(msg)
        await record_agent_runs(
            session_factory,
            state["failure_ids"],
            agent_name="release_scorer",
            status=AgentRunStatus.FAILED,
            output_summary=str(exc),
        )
        return {"release_score": None, "errors": errors}
