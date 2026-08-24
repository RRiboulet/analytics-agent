from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import Field

from app.data_sources.postgres import PostgresClient
from app.middleware import copy_structured_content_to_content
from app.sql_safety import UnsafeQueryError, validate_and_bound_query

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
        name="query_factory_data",
        description=(
            "Run one read-only SELECT query against the sample factory database. "
            "Results are capped by the server; query only the documented public tables."
        ),
        annotations=ToolAnnotations(
            title="Query factory data",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    async def query_factory_data(
        sql: str = Field(description="One read-only SELECT query.", max_length=10_000),
    ) -> CallToolResult:
        try:
            bounded_query = validate_and_bound_query(sql, client.max_rows)
        except UnsafeQueryError as error:
            return _result(False, str(error), [])
        try:
            rows = await client.query(bounded_query)
        except Exception:
            return _result(False, "The factory database is temporarily unavailable.", [])
        return _result(True, f"Query returned {len(rows)} row(s).", rows)
