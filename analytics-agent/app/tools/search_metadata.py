from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent, ToolAnnotations

from app.data_sources.postgres import PostgresClient
from app.embedder import MetadataEmbedder
from app.middleware import copy_structured_content_to_content

_DATA_SOURCE = "olist-postgres"


def _result(valid: bool, message: str, entries: list[dict[str, Any]]) -> CallToolResult:
    response = {"valid": valid, "message": message, "entries": entries}
    return copy_structured_content_to_content(
        CallToolResult(
            content=[TextContent(type="text", text=message)],
            structuredContent=response,
            _meta={"data_source": _DATA_SOURCE},
        )
    )


def register(mcp: FastMCP, client: PostgresClient, embedder: MetadataEmbedder) -> None:
    @mcp.tool(
        name="search_metadata",
        description=(
            "Semantically search the database metadata. Given a natural-language "
            "question about the e-commerce data, return the most relevant tables, "
            "columns and relationships so a query can be planned without reading "
            "the whole schema."
        ),
        annotations=ToolAnnotations(
            title="Search metadata",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    async def search_metadata(question: str, top_k: int | None = None) -> CallToolResult:
        if not question.strip():
            return _result(False, "The question must not be blank.", [])

        limit = top_k if top_k is not None else 5
        if limit < 1 or limit > 50:
            return _result(False, "top_k must be between 1 and 50.", [])

        try:
            query_vector = embedder.embed_query(question)
            hits = await client.search_metadata(query_vector, limit)
        except Exception:
            return _result(False, "The database is temporarily unavailable.", [])

        return _result(
            True,
            f"Found {len(hits)} metadata document(s) relevant to the question.",
            hits,
        )
