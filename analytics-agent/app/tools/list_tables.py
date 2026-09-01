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
        name="list_tables",
        description="List the public tables available in the PostgreSQL database.",
        annotations=ToolAnnotations(
            title="List tables",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    async def list_tables() -> CallToolResult:
        try:
            tables = await client.list_tables()
        except Exception:
            return _result(False, "The database is temporarily unavailable.", [])
        entries = [{"table_name": table} for table in tables]
        return _result(True, f"Found {len(entries)} table(s).", entries)
