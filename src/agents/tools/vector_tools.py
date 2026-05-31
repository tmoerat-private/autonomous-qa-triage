"""Vector embedding tools for error signature similarity detection.

Uses sentence-transformers all-MiniLM-L6-v2 (384-dimensional) for local
CPU-friendly embeddings with no external API key required.
Model is loaded lazily and cached as a module-level singleton.
"""
from __future__ import annotations

import asyncio
from functools import lru_cache
from typing import Any

import structlog
from sentence_transformers import SentenceTransformer

from src.config.settings import get_settings
from src.db.qdrant_client import QdrantManager, get_qdrant_manager

logger = structlog.get_logger(__name__)

_MODEL_NAME = "all-MiniLM-L6-v2"


@lru_cache(maxsize=1)
def _get_model() -> SentenceTransformer:
    """Load and cache the embedding model (called once per process)."""
    logger.info("embedding_model.loading", model=_MODEL_NAME)
    return SentenceTransformer(_MODEL_NAME)


def _embed_sync(text: str) -> list[float]:
    """Synchronous embedding — run inside asyncio.to_thread to avoid blocking."""
    model = _get_model()
    vector = model.encode(text, normalize_embeddings=True)
    return vector.tolist()


async def generate_embedding(text: str) -> list[float]:
    """Generate a 384-dimensional embedding for the given text.

    Runs the CPU-bound model.encode() in a thread pool to keep the event
    loop unblocked.
    """
    return await asyncio.to_thread(_embed_sync, text)


async def store_error_embedding(
    point_id: str,
    error_text: str,
    payload: dict[str, Any],
) -> list[float]:
    """Generate embedding for error_text and store it in Qdrant.

    Args:
        point_id: UUID string used as the Qdrant point ID.
        error_text: Normalized error text to embed and store.
        payload: Arbitrary metadata stored alongside the vector in Qdrant.

    Returns:
        The generated embedding vector (callers may need it for an immediate
        similarity search without re-encoding the same text).
    """
    vector = await generate_embedding(error_text)
    manager = get_qdrant_manager()
    await manager.ensure_collection(vector_size=len(vector))
    await manager.store_embedding(point_id=point_id, vector=vector, payload=payload)
    logger.info("vector_tools.embedding_stored", point_id=point_id)
    return vector


async def find_similar_errors(
    error_text: str,
    limit: int = 5,
    score_threshold: float = 0.85,
) -> list[dict]:
    """Find previously stored errors similar to error_text.

    Embeds error_text, queries Qdrant for nearest neighbours above
    score_threshold, and returns the raw Qdrant result dicts.

    Args:
        error_text: The error text to search for similar matches against.
        limit: Maximum number of results to return.
        score_threshold: Minimum cosine similarity score (0.0-1.0) for a
            result to be included.

    Returns:
        List of dicts: [{"id": str, "score": float, "payload": dict}, ...]
        Empty list if no matches exceed the threshold.
    """
    vector = await generate_embedding(error_text)
    manager = get_qdrant_manager()
    await manager.ensure_collection(vector_size=len(vector))
    results = await manager.find_similar(
        query_vector=vector,
        limit=limit,
        score_threshold=score_threshold,
    )
    logger.info(
        "vector_tools.similarity_search_complete",
        matches=len(results),
        threshold=score_threshold,
    )
    return results


def _get_outcomes_manager() -> QdrantManager:
    """Return a QdrantManager pointed at the triage_outcomes collection."""
    settings = get_settings()
    return QdrantManager(
        url=settings.qdrant_url,
        collection_name=settings.qdrant_outcomes_collection,
    )


async def store_outcome_embedding(
    point_id: str,
    error_text: str,
    payload: dict[str, Any],
) -> None:
    """Store a triage outcome embedding in the triage_outcomes Qdrant collection.

    Args:
        point_id: UUID string for the TestFailure record.
        error_text: Normalized error text to embed and store.
        payload: Metadata (test_name, category, confidence, reasoning, ticket_url, …).
    """
    vector = await generate_embedding(error_text)
    manager = _get_outcomes_manager()
    await manager.ensure_collection(vector_size=len(vector))
    await manager.store_embedding(point_id=point_id, vector=vector, payload=payload)
    logger.info("vector_tools.outcome_stored", point_id=point_id)


async def find_similar_outcomes(
    error_text: str,
    limit: int = 3,
    score_threshold: float = 0.80,
) -> list[dict]:
    """Search the triage_outcomes collection for similar past failure outcomes.

    Uses a slightly lower threshold than dedup (0.80 vs 0.85) to cast a wider
    net when retrieving few-shot examples.

    Returns:
        List of dicts: [{"id": str, "score": float, "payload": dict}, ...]
    """
    vector = await generate_embedding(error_text)
    manager = _get_outcomes_manager()
    await manager.ensure_collection(vector_size=len(vector))
    results = await manager.find_similar(
        query_vector=vector,
        limit=limit,
        score_threshold=score_threshold,
    )
    logger.info(
        "vector_tools.outcomes_search_complete",
        matches=len(results),
        threshold=score_threshold,
    )
    return results
