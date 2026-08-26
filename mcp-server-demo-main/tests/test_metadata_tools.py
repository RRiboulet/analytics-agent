"""Unit tests for the M3 metadata layer (no live database required).

Covers the document builder, the embedder seam, and the search_metadata MCP
tool response contract. Live-database behavior is covered in
tests/test_metadata_database.py.
"""

import json
from typing import Any

import pytest

from app.data_sources.postgres import PostgresClient
from app.embedder import MetadataEmbedder, create_test_embedder
from app.metadata import build_metadata_documents
from app.tools.search_metadata import register as register_search_metadata


class FakeMCP:
    def __init__(self) -> None:
        self.tools: dict[str, Any] = {}
        self.annotations: dict[str, Any] = {}

    def tool(self, **kwargs: Any):
        def decorator(function: Any) -> Any:
            self.tools[function.__name__] = function
            self.annotations[function.__name__] = kwargs
            return function

        return decorator


class FakeClient(PostgresClient):
    def __init__(
        self,
        tables: list[str] | None = None,
        columns: dict[str, list[dict[str, Any]]] | None = None,
        relationships: list[dict[str, Any]] | None = None,
        search_hits: list[dict[str, Any]] | None = None,
        error: Exception | None = None,
    ) -> None:
        super().__init__("postgresql://unused", 1, 100)
        self.tables = tables or []
        self.columns = columns or {}
        self.relationships = relationships or []
        self.search_hits = search_hits or []
        self.error = error
        self.calls: list[str] = []

    async def list_tables(self) -> list[str]:
        self.calls.append("list_tables")
        if self.error is not None:
            raise self.error
        return self.tables

    async def describe_table(self, table_name: str) -> list[dict[str, Any]]:
        self.calls.append(f"describe_table:{table_name}")
        if self.error is not None:
            raise self.error
        return self.columns.get(table_name, [])

    async def get_relationships(self) -> list[dict[str, Any]]:
        self.calls.append("get_relationships")
        if self.error is not None:
            raise self.error
        return self.relationships

    async def search_metadata(self, query_vector: list[float], limit: int) -> list[dict[str, Any]]:
        self.calls.append(f"search_metadata:{limit}")
        if self.error is not None:
            raise self.error
        return self.search_hits[:limit]


COLUMNS = {
    "orders": [
        {"column_name": "order_id", "data_type": "text", "is_nullable": "NO"},
        {"column_name": "order_status", "data_type": "text", "is_nullable": "NO"},
    ],
    "order_items": [
        {"column_name": "price", "data_type": "numeric", "is_nullable": "NO"},
    ],
}

RELATIONSHIPS = [
    {
        "child_table": "order_items",
        "child_column": "order_id",
        "parent_table": "orders",
        "parent_column": "order_id",
        "constraint_name": "order_items_order_id_fkey",
    }
]

SEARCH_HITS = [
    {
        "id": "column:order_items.price",
        "entity_type": "column",
        "entity_id": "order_items.price",
        "title": "Column order_items.price",
        "content": "Column order_items.price ...",
        "doc_metadata": {"kind": "column"},
        "similarity": 0.61,
    }
]


def _register(mcp: FakeMCP, client: FakeClient, embedder: MetadataEmbedder) -> None:
    register_search_metadata(mcp, client, embedder)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Embedder
# ---------------------------------------------------------------------------


def test_stub_embedder_returns_fixed_vectors() -> None:
    embedder = create_test_embedder([[0.1, 0.2, 0.3]])
    assert embedder.embed(["a", "b"]) == [[0.1, 0.2, 0.3], [0.1, 0.2, 0.3]]
    assert embedder.embed_query("question") == [0.1, 0.2, 0.3]


def test_real_embedder_returns_finite_vectors_of_configured_dimension() -> None:
    from app.config import get_settings

    settings = get_settings()
    embedder = MetadataEmbedder(
        model_name=settings.embedding_model_name,
        cache_dir=settings.embedding_cache_dir,
    )
    vector = embedder.embed_query("Which product categories generate the most revenue?")
    assert len(vector) == settings.embedding_dimensions
    assert all(isinstance(v, float) for v in vector)
    assert all(v == v for v in vector)  # no NaN


# ---------------------------------------------------------------------------
# Document builder
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_metadata_documents_creates_table_column_relation_docs() -> None:
    client = FakeClient(
        tables=["orders", "order_items", "metadata_documents"],
        columns=COLUMNS,
        relationships=RELATIONSHIPS,
    )
    docs = await build_metadata_documents(client)

    ids = {d["id"] for d in docs}
    # metadata_documents (the pgvector table itself) is excluded.
    assert "table:metadata_documents" not in ids
    assert "table:orders" in ids
    assert "column:orders.order_status" in ids
    assert "column:order_items.price" in ids
    assert "relation:order_items.order_id -> orders.order_id" in ids
    assert client.calls == [
        "list_tables",
        "describe_table:orders",
        "describe_table:order_items",
        "get_relationships",
    ]


@pytest.mark.asyncio
async def test_build_metadata_documents_merges_curated_seed() -> None:
    client = FakeClient(tables=["order_items"], columns=COLUMNS, relationships=[])
    docs = await build_metadata_documents(client)

    price_doc = next(d for d in docs if d["id"] == "column:order_items.price")
    assert "revenue" in price_doc["content"]
    assert "price" in price_doc["content"]

    table_doc = next(d for d in docs if d["id"] == "table:order_items")
    assert "revenue" in table_doc["content"]


@pytest.mark.asyncio
async def test_build_metadata_documents_accepts_legacy_string_seed() -> None:
    """Plain-string seed entries (no keywords dict) are still supported."""
    import app.metadata as metadata_mod

    def legacy_seed() -> dict[str, Any]:
        return {
            "tables": {"orders": "A table of orders."},
            "columns": {"orders.order_status": "The lifecycle status."},
        }

    original = metadata_mod._load_seed
    metadata_mod._load_seed = legacy_seed  # type: ignore[assignment]
    try:
        client = FakeClient(
            tables=["orders"],
            columns={"orders": [{"column_name": "order_status", "data_type": "text"}]},
            relationships=[],
        )
        docs = await build_metadata_documents(client)
        table_doc = next(d for d in docs if d["id"] == "table:orders")
        col_doc = next(d for d in docs if d["id"] == "column:orders.order_status")
        assert "A table of orders" in table_doc["content"] or "orders" in table_doc["content"]
        assert "status" in col_doc["content"]
    finally:
        metadata_mod._load_seed = original  # type: ignore[assignment]


@pytest.mark.asyncio
async def test_build_metadata_documents_uses_defaults_for_unknown_entities() -> None:
    client = FakeClient(
        tables=["unknown_table"],
        columns={"unknown_table": [{"column_name": "col", "data_type": "integer"}]},
        relationships=[],
    )
    docs = await build_metadata_documents(client)
    table_doc = next(d for d in docs if d["id"] == "table:unknown_table")
    assert "analytical table" in table_doc["content"]
    col_doc = next(d for d in docs if d["id"] == "column:unknown_table.col")
    assert "integer" in col_doc["content"]


@pytest.mark.asyncio
async def test_replace_metadata_documents_empty_returns_zero() -> None:
    """Empty input short-circuits before touching the pool."""
    client = PostgresClient("postgresql://unused", 1, 100)
    assert await client.replace_metadata_documents([]) == 0


# ---------------------------------------------------------------------------
# search_metadata tool contract
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_metadata_is_read_only() -> None:
    mcp = FakeMCP()
    _register(mcp, FakeClient(), create_test_embedder([[0.1]]))

    annotations = mcp.annotations["search_metadata"]["annotations"]
    assert annotations.readOnlyHint is True
    assert annotations.destructiveHint is False


@pytest.mark.asyncio
async def test_search_metadata_returns_hits() -> None:
    mcp = FakeMCP()
    client = FakeClient(search_hits=SEARCH_HITS)
    _register(mcp, client, create_test_embedder([[0.1, 0.2]]))

    result = await mcp.tools["search_metadata"](question="revenue by category")

    assert result.structuredContent["valid"] is True
    assert result.structuredContent["entries"] == SEARCH_HITS
    assert client.calls == ["search_metadata:5"]
    parsed = json.loads(result.content[0].text)
    assert parsed["entries"] == SEARCH_HITS


@pytest.mark.asyncio
async def test_search_metadata_respects_top_k() -> None:
    mcp = FakeMCP()
    client = FakeClient(search_hits=SEARCH_HITS)
    _register(mcp, client, create_test_embedder([[0.1, 0.2]]))

    result = await mcp.tools["search_metadata"](question="revenue", top_k=3)

    assert result.structuredContent["valid"] is True
    assert client.calls == ["search_metadata:3"]


@pytest.mark.asyncio
async def test_search_metadata_rejects_blank_question() -> None:
    mcp = FakeMCP()
    client = FakeClient()
    _register(mcp, client, create_test_embedder([[0.1]]))

    result = await mcp.tools["search_metadata"](question="   ")

    assert result.structuredContent["valid"] is False
    assert "blank" in result.structuredContent["message"]
    assert client.calls == []


@pytest.mark.asyncio
async def test_search_metadata_rejects_invalid_top_k() -> None:
    mcp = FakeMCP()
    client = FakeClient()
    _register(mcp, client, create_test_embedder([[0.1]]))

    result = await mcp.tools["search_metadata"](question="revenue", top_k=0)

    assert result.structuredContent["valid"] is False
    assert "top_k" in result.structuredContent["message"]
    assert client.calls == []


@pytest.mark.asyncio
async def test_search_metadata_masks_infrastructure_error() -> None:
    mcp = FakeMCP()
    client = FakeClient(error=ConnectionError("refused"))
    _register(mcp, client, create_test_embedder([[0.1]]))

    result = await mcp.tools["search_metadata"](question="revenue")

    assert result.structuredContent["valid"] is False
    assert result.structuredContent["message"] == "The database is temporarily unavailable."
