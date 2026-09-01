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
        name="get_column_statistics",
        description=(
            "Return statistics for one column: total rows, distinct non-null values, "
            "null count, and min/max values. Use this to understand value distribution "
            "and data quality before writing a query."
        ),
        annotations=ToolAnnotations(
            title="Get column statistics",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    async def get_column_statistics(
        table_name: str = Field(description="Exact public table name.", max_length=63),
        column_name: str = Field(description="Exact column name in the table.", max_length=63),
    ) -> CallToolResult:
        if not table_name.strip():
            return _result(False, "The table name must not be blank.", [])
        if not column_name.strip():
            return _result(False, "The column name must not be blank.", [])
        try:
            statistics = await client.get_column_statistics(table_name.strip(), column_name.strip())
        except Exception:
            return _result(False, "The database is temporarily unavailable.", [])
        if statistics is None:
            return _result(
                False, f"Table '{table_name}' or column '{column_name}' was not found.", []
            )
        return _result(True, f"Statistics for '{table_name}.{column_name}'.", [statistics])
