"""Integration tests for the M3 pgvector metadata layer.

These run against a reachable PostgreSQL instance and skip themselves when one
is not available (mirroring the M1/M2 integration tests).
"""

import asyncio

import pytest

from app.config import get_settings
from app.data_sources.postgres import PostgresClient
from app.embedder import get_embedder
from app.metadata import build_metadata_documents


async def _connect(client: PostgresClient, retries: int = 10, delay: float = 1.0) -> bool:
    for attempt in range(retries):
        try:
            await client.connect()
            return True
        except Exception:
            if attempt == retries - 1:
                return False
            await asyncio.sleep(delay)
    return False


async def _seed_metadata() -> None:
    """Run the seeding pipeline with the admin role, as scripts/seed_metadata does."""
    settings = get_settings()
    admin = PostgresClient(
        settings.admin_database_url, settings.query_timeout_seconds, settings.max_rows
    )
    await admin.connect()
    try:
        await admin.ensure_metadata_schema()
        documents = await build_metadata_documents(admin)
        embedder = get_embedder()
        for doc in documents:
            doc["embedding"] = embedder.embed_query(doc["content"])
        await admin.replace_metadata_documents(documents)
    finally:
        await admin.close()


async def _count_documents(client: PostgresClient) -> int:
    rows = await client.fetch_many("SELECT count(*) AS n FROM metadata_documents")
    return int(rows[0]["n"])


@pytest.mark.asyncio
async def test_vector_extension_and_metadata_table_present() -> None:
    settings = get_settings()
    c = PostgresClient(settings.database_url, 5.0, 100)
    try:
        if not await _connect(c):
            pytest.skip("no live database")
        ext = await c.fetch_many("SELECT extname FROM pg_extension WHERE extname = 'vector'")
        assert ext and ext[0]["extname"] == "vector"
        # Querying the table succeeds (raises if it does not exist).
        await c.fetch_many("SELECT id FROM metadata_documents LIMIT 1")
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_internal_metadata_table_hidden_from_analytics_tools() -> None:
    """The pgvector table must not surface as a queryable analytics source."""
    settings = get_settings()
    c = PostgresClient(settings.database_url, 5.0, 100)
    try:
        if not await _connect(c):
            pytest.skip("no live database")
        assert "metadata_documents" not in await c.list_tables()
        assert await c.describe_table("metadata_documents") == []
        assert await c.get_sample_rows("metadata_documents", 3) is None
        assert await c.get_column_statistics("metadata_documents", "id") is None
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_seeding_is_idempotent_and_readonly_can_read() -> None:
    settings = get_settings()
    c = PostgresClient(settings.database_url, 30.0, 5_000_000)
    try:
        await c.connect()
    except Exception:
        pytest.skip("no live database")
    try:
        await _seed_metadata()
        first = await _count_documents(c)
        assert first > 0
        # Read-only role can SELECT from metadata_documents.
        rows = await c.fetch_many("SELECT id, entity_type FROM metadata_documents LIMIT 1")
        assert rows and rows[0]["id"]

        await _seed_metadata()
        second = await _count_documents(c)
        assert second == first
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_semantic_search_returns_relevant_metadata() -> None:
    settings = get_settings()
    c = PostgresClient(settings.database_url, 30.0, 5_000_000)
    try:
        await c.connect()
    except Exception:
        pytest.skip("no live database")
    try:
        await _seed_metadata()
        embedder = get_embedder()
        query = "Which product categories generate the most revenue?"
        vector = embedder.embed_query(query)
        hits = await c.search_metadata(vector, 8)

        assert hits, "expected at least one metadata hit"
        # The monetary measure and the category join must rank in the top 8.
        ids = [h["entity_id"] for h in hits]
        assert any("order_items" in i for i in ids), f"expected order_items in {ids}"
        assert any("product_category" in i for i in ids), f"expected category in {ids}"
        # Sorted by descending similarity.
        sims = [h["similarity"] for h in hits]
        assert sims == sorted(sims, reverse=True)
    finally:
        await c.close()
