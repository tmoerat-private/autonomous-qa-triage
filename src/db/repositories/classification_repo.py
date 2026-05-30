from __future__ import annotations

from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.failure_classification import FailureClassification

logger = structlog.get_logger(__name__)


class ClassificationRepository:
    """Data access layer for FailureClassification records."""

    async def create(
        self,
        session: AsyncSession,
        test_failure_id: UUID,
        category: str,
        confidence: float,
        reasoning: str | None = None,
        model_used: str | None = None,
        tokens_used: int | None = None,
    ) -> FailureClassification:
        """Create and persist a FailureClassification for a given TestFailure.

        The caller controls the transaction; this method only flushes.
        """
        classification = FailureClassification(
            test_failure_id=test_failure_id,
            category=category,
            confidence=confidence,
            reasoning=reasoning,
            model_used=model_used,
            tokens_used=tokens_used,
        )
        session.add(classification)
        await session.flush()
        logger.info(
            "failure_classification.created",
            failure_classification_id=str(classification.id),
            test_failure_id=str(test_failure_id),
        )
        return classification

    async def get_by_failure_id(
        self,
        session: AsyncSession,
        test_failure_id: UUID,
    ) -> FailureClassification | None:
        """Return the FailureClassification for a TestFailure, or None if absent."""
        stmt = select(FailureClassification).where(
            FailureClassification.test_failure_id == test_failure_id
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    async def upsert(
        self,
        session: AsyncSession,
        test_failure_id: UUID,
        category: str,
        confidence: float,
        reasoning: str | None = None,
        model_used: str | None = None,
        tokens_used: int | None = None,
    ) -> FailureClassification:
        """Update an existing classification or create a new one.

        Non-None arguments overwrite the stored values on update.
        The caller controls the transaction; this method only flushes.
        """
        existing = await self.get_by_failure_id(session, test_failure_id)
        if existing is not None:
            existing.category = category
            existing.confidence = confidence
            if reasoning is not None:
                existing.reasoning = reasoning
            if model_used is not None:
                existing.model_used = model_used
            if tokens_used is not None:
                existing.tokens_used = tokens_used
            await session.flush()
            return existing

        return await self.create(
            session=session,
            test_failure_id=test_failure_id,
            category=category,
            confidence=confidence,
            reasoning=reasoning,
            model_used=model_used,
            tokens_used=tokens_used,
        )
