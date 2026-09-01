from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import Field

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
        name="get_sample_rows",
        description=(
            "Return up to N arbitrary sample rows from one public table. "
            "Use this to inspect real values (statuses, ids, formats) before writing a query."
        ),
        annotations=ToolAnnotations(
            title="Get sample rows",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    async def get_sample_rows(
        table_name: str = Field(description="Exact public table name.", max_length=63),
        limit: int = Field(
            default=5, description="Maximum number of sample rows to return.", ge=1, le=100
        ),
    ) -> CallToolResult:
        if not table_name.strip():
            return _result(False, "The table name must not be blank.", [])
        try:
            rows = await client.get_sample_rows(table_name.strip(), limit)
        except Exception:
            return _result(False, "The database is temporarily unavailable.", [])
        if rows is None:
            return _result(False, f"Table '{table_name}' was not found.", [])
        return _result(True, f"Returned {len(rows)} sample row(s) from '{table_name}'.", rows)
