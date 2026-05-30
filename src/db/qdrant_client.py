"""Async Qdrant client wrapper for error signature vector storage and similarity search."""

from qdrant_client import AsyncQdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

from src.config.settings import get_settings


class QdrantManager:
    """Thin async wrapper around AsyncQdrantClient for error signature operations."""

    def __init__(self, url: str, collection_name: str) -> None:
        self._client = AsyncQdrantClient(url=url)
        self._collection = collection_name

    async def ensure_collection(self, vector_size: int = 384) -> None:
        """Create the collection if it does not already exist.

        Safe to call on every startup — idempotent.
        """
        collections = await self._client.get_collections()
        names = [c.name for c in collections.collections]
        if self._collection not in names:
            await self._client.create_collection(
                collection_name=self._collection,
                vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
            )

    async def store_embedding(
        self,
        point_id: str,
        vector: list[float],
        payload: dict,
    ) -> None:
        """Upsert a single vector point. Overwrites if point_id already exists."""
        await self._client.upsert(
            collection_name=self._collection,
            points=[PointStruct(id=point_id, vector=vector, payload=payload)],
        )

    async def find_similar(
        self,
        query_vector: list[float],
        limit: int = 5,
        score_threshold: float = 0.85,
    ) -> list[dict]:
        """Return up to `limit` similar points with score >= score_threshold.

        Each dict has keys: id (str), score (float), payload (dict).
        Returns empty list if no matches meet the threshold.
        """
        results = await self._client.search(
            collection_name=self._collection,
            query_vector=query_vector,
            limit=limit,
            score_threshold=score_threshold,
            with_payload=True,
        )
        return [
            {"id": str(r.id), "score": r.score, "payload": r.payload or {}}
            for r in results
        ]

    async def delete_point(self, point_id: str) -> None:
        """Delete a single point by ID. No-op if the point does not exist."""
        from qdrant_client.models import PointIdsList

        await self._client.delete(
            collection_name=self._collection,
            points_selector=PointIdsList(points=[point_id]),
        )

    async def close(self) -> None:
        await self._client.close()


def get_qdrant_manager() -> QdrantManager:
    """Return a QdrantManager configured from application settings."""
    settings = get_settings()
    return QdrantManager(
        url=settings.qdrant_url,
        collection_name=settings.qdrant_collection,
    )
