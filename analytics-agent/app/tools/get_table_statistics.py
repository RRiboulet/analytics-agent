from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent, ToolAnnotations

from app.data_sources.postgres import PostgresClient
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


def register(mcp: FastMCP, client: PostgresClient) -> None:
    @mcp.tool(
        name="get_table_statistics",
        description=(
            "Return the exact row count for every public table. "
            "Use this to judge data volume before aggregating or sampling."
        ),
        annotations=ToolAnnotations(
            title="Get table statistics",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    async def get_table_statistics() -> CallToolResult:
        try:
            statistics = await client.get_table_statistics()
        except Exception:
            return _result(False, "The database is temporarily unavailable.", [])
        return _result(True, f"Found statistics for {len(statistics)} table(s).", statistics)
