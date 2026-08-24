from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import Field

from app.data_sources.postgres import PostgresClient
from app.middleware import copy_structured_content_to_content

_DATA_SOURCE = "sample-factory-postgres"


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
        name="describe_factory_table",
        description="Describe columns for one public table in the sample factory database.",
        annotations=ToolAnnotations(
            title="Describe factory table",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    async def describe_factory_table(
        table_name: str = Field(description="Exact public table name.", max_length=63),
    ) -> CallToolResult:
        if not table_name.strip():
            return _result(False, "The table name must not be blank.", [])
        try:
            columns = await client.describe_table(table_name.strip())
        except Exception:
            return _result(False, "The factory database is temporarily unavailable.", [])
        if not columns:
            return _result(False, f"Table '{table_name}' was not found.", [])
        return _result(True, f"Found {len(columns)} column(s) in '{table_name}'.", columns)
