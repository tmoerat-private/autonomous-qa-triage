from __future__ import annotations

from datetime import datetime, timezone

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.error_signature import ErrorSignature

logger = structlog.get_logger(__name__)


class ErrorSignatureRepository:
    """Data access layer for ErrorSignature records."""

    async def get_by_hash(
        self,
        session: AsyncSession,
        signature_hash: str,
    ) -> ErrorSignature | None:
        """Return an ErrorSignature by its hash, or None if not found."""
        stmt = select(ErrorSignature).where(
            ErrorSignature.signature_hash == signature_hash
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    async def create(
        self,
        session: AsyncSession,
        signature_hash: str,
        normalized_error: str,
    ) -> ErrorSignature:
        """Create and persist a new ErrorSignature with occurrence_count=1.

        The caller controls the transaction; this method only flushes.
        """
        signature = ErrorSignature(
            signature_hash=signature_hash,
            normalized_error=normalized_error,
            occurrence_count=1,
        )
        session.add(signature)
        await session.flush()
        logger.info(
            "error_signature.created",
            signature_hash=signature_hash,
        )
        return signature

    async def increment_occurrence(
        self,
        session: AsyncSession,
        signature: ErrorSignature,
    ) -> ErrorSignature:
        """Increment the occurrence counter and update last_seen_at.

        The caller controls the transaction; this method only flushes.
        """
        signature.occurrence_count += 1
        signature.last_seen_at = datetime.now(timezone.utc)
        await session.flush()
        logger.info(
            "error_signature.occurrence_incremented",
            signature_hash=signature.signature_hash,
            occurrence_count=signature.occurrence_count,
        )
        return signature

    async def update_embedding_id(
        self,
        session: AsyncSession,
        signature: ErrorSignature,
        embedding_id: str,
    ) -> ErrorSignature:
        """Persist the Qdrant point ID on an existing ErrorSignature.

        Called after a vector is stored in Qdrant so the DB row references the point.
        The caller controls the transaction; this method only flushes.
        """
        signature.embedding_id = embedding_id
        await session.flush()
        logger.info(
            "error_signature.embedding_id_set",
            signature_hash=signature.signature_hash,
            embedding_id=embedding_id,
        )
        return signature

    async def get_or_create(
        self,
        session: AsyncSession,
        signature_hash: str,
        normalized_error: str,
    ) -> tuple[ErrorSignature, bool]:
        """Return an existing ErrorSignature (incrementing its counter) or create a new one.

        Returns a (ErrorSignature, is_duplicate) tuple.
        is_duplicate is True when the signature already existed, False when newly created.
        The caller controls the transaction; this method only flushes.
        """
        existing = await self.get_by_hash(session, signature_hash)
        if existing is not None:
            existing = await self.increment_occurrence(session, existing)
            return existing, True

        new_signature = await self.create(session, signature_hash, normalized_error)
        return new_signature, False
