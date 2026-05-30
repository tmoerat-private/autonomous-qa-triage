"""Integration-test conftest — Qdrant cleanup fixture.

Qdrant is a persistent external service whose vector state survives between
pytest runs.  Without cleanup, a freshly-seeded error signature is detected
as a duplicate of a vector stored by a *previous* test run, breaking the
``is_duplicate is False`` assertions.

This module provides an autouse fixture that deletes the configured Qdrant
collection before every integration test so each test starts with an empty
vector store.  The ``QdrantManager.ensure_collection()`` call inside the
production code recreates the collection on demand.
"""
from __future__ import annotations

import pytest_asyncio
from qdrant_client import AsyncQdrantClient

from src.config.settings import get_settings


@pytest_asyncio.fixture(autouse=True)
async def clean_qdrant_collection():
    """Delete the Qdrant collection before each integration test.

    Production code calls ``ensure_collection()`` which re-creates the
    collection on first use, so deleting it here is safe and idempotent.

    Failures are silently ignored so that tests run even when Qdrant is
    unavailable (the duplicate-detector already has fail-open behaviour).
    """
    settings = get_settings()
    client = AsyncQdrantClient(url=settings.qdrant_url)
    try:
        collections = await client.get_collections()
        existing = {c.name for c in collections.collections}
        if settings.qdrant_collection in existing:
            await client.delete_collection(settings.qdrant_collection)
    except Exception:
        pass
    finally:
        await client.close()

    yield
