import json
from typing import Any

import pytest

from app.data_sources.postgres import PostgresClient
from app.tools.get_column_statistics import register as register_column_statistics
from app.tools.get_relationships import register as register_relationships
from app.tools.get_sample_rows import register as register_sample_rows
from app.tools.get_table_statistics import register as register_table_statistics


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
        relationships: list[dict[str, Any]] | None = None,
        sample_rows: list[dict[str, Any]] | None = None,
        table_statistics: list[dict[str, Any]] | None = None,
        column_statistics: dict[str, Any] | None = None,
        error: Exception | None = None,
    ) -> None:
        super().__init__("postgresql://unused", 1, 100)
        self.relationships = relationships
        self.sample_rows = sample_rows
        self.table_statistics = table_statistics
        self.column_statistics = column_statistics
        self.error = error
        self.calls: list[str] = []

    async def get_relationships(self) -> list[dict[str, Any]]:
        self.calls.append("get_relationships")
        if self.error is not None:
            raise self.error
        return self.relationships or []

    async def get_sample_rows(self, table_name: str, limit: int) -> list[dict[str, Any]] | None:
        self.calls.append(f"get_sample_rows:{table_name}:{limit}")
        if self.error is not None:
            raise self.error
        if table_name == "missing":
            return None
        return self.sample_rows or []

    async def get_table_statistics(self) -> list[dict[str, Any]]:
        self.calls.append("get_table_statistics")
        if self.error is not None:
            raise self.error
        return self.table_statistics or []

    async def get_column_statistics(
        self, table_name: str, column_name: str
    ) -> dict[str, Any] | None:
        self.calls.append(f"get_column_statistics:{table_name}.{column_name}")
        if self.error is not None:
            raise self.error
        if table_name == "missing" or column_name == "missing":
            return None
        return self.column_statistics or {}


RELATIONSHIPS = [
    {
        "child_table": "order_items",
        "child_column": "order_id",
        "parent_table": "orders",
        "parent_column": "order_id",
        "constraint_name": "order_items_order_id_fkey",
    }
]

SAMPLE_ROWS = [
    {
        "order_id": "a1",
        "order_status": "delivered",
        "order_purchase_timestamp": "2018-01-01 00:00:00",
    }
]

TABLE_STATISTICS = [
    {"table_name": "orders", "row_count": 99441},
    {"table_name": "geolocation", "row_count": 1000163},
]

COLUMN_STATISTICS = {
    "table_name": "orders",
    "column_name": "order_status",
    "data_type": "text",
    "total_rows": 99441,
    "distinct_count": 8,
    "null_count": 0,
    "min_value": "approved",
    "max_value": "unavailable",
}


def _register_all(mcp: FakeMCP, client: FakeClient) -> None:
    register_relationships(mcp, client)  # type: ignore[arg-type]
    register_sample_rows(mcp, client)  # type: ignore[arg-type]
    register_table_statistics(mcp, client)  # type: ignore[arg-type]
    register_column_statistics(mcp, client)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_analytics_tools_are_read_only() -> None:
    mcp = FakeMCP()
    _register_all(mcp, FakeClient())

    for name, kwargs in mcp.annotations.items():
        annotations = kwargs["annotations"]
        assert annotations.readOnlyHint is True, f"{name} must be read-only"
        assert annotations.destructiveHint is False, f"{name} must not be destructive"


@pytest.mark.asyncio
async def test_get_relationships_returns_structured_data() -> None:
    mcp = FakeMCP()
    client = FakeClient(relationships=RELATIONSHIPS)
    _register_all(mcp, client)

    result = await mcp.tools["get_relationships"]()

    assert result.structuredContent["valid"] is True
    assert result.structuredContent["entries"] == RELATIONSHIPS
    assert client.calls == ["get_relationships"]
    parsed = json.loads(result.content[0].text)
    assert parsed["entries"] == RELATIONSHIPS


@pytest.mark.asyncio
async def test_get_relationships_masks_infrastructure_error() -> None:
    mcp = FakeMCP()
    client = FakeClient(error=ConnectionError("refused"))
    _register_all(mcp, client)

    result = await mcp.tools["get_relationships"]()

    assert result.structuredContent["valid"] is False
    assert result.structuredContent["message"] == "The database is temporarily unavailable."


@pytest.mark.asyncio
async def test_get_sample_rows_returns_rows() -> None:
    mcp = FakeMCP()
    client = FakeClient(sample_rows=SAMPLE_ROWS)
    _register_all(mcp, client)

    result = await mcp.tools["get_sample_rows"](table_name="orders", limit=3)

    assert result.structuredContent["valid"] is True
    assert result.structuredContent["entries"] == SAMPLE_ROWS
    assert client.calls == ["get_sample_rows:orders:3"]


@pytest.mark.asyncio
async def test_get_sample_rows_rejects_blank_table() -> None:
    mcp = FakeMCP()
    client = FakeClient()
    _register_all(mcp, client)

    result = await mcp.tools["get_sample_rows"](table_name="   ")

    assert result.structuredContent["valid"] is False
    assert "blank" in result.structuredContent["message"]
    assert client.calls == []


@pytest.mark.asyncio
async def test_get_sample_rows_reports_unknown_table() -> None:
    mcp = FakeMCP()
    client = FakeClient()
    _register_all(mcp, client)

    result = await mcp.tools["get_sample_rows"](table_name="missing")

    assert result.structuredContent["valid"] is False
    assert "not found" in result.structuredContent["message"]


@pytest.mark.asyncio
async def test_get_sample_rows_masks_infrastructure_error() -> None:
    mcp = FakeMCP()
    client = FakeClient(error=ConnectionError("refused"))
    _register_all(mcp, client)

    result = await mcp.tools["get_sample_rows"](table_name="orders")

    assert result.structuredContent["valid"] is False
    assert result.structuredContent["message"] == "The database is temporarily unavailable."


@pytest.mark.asyncio
async def test_get_table_statistics_returns_counts() -> None:
    mcp = FakeMCP()
    client = FakeClient(table_statistics=TABLE_STATISTICS)
    _register_all(mcp, client)

    result = await mcp.tools["get_table_statistics"]()

    assert result.structuredContent["valid"] is True
    assert result.structuredContent["entries"] == TABLE_STATISTICS
    assert client.calls == ["get_table_statistics"]


@pytest.mark.asyncio
async def test_get_table_statistics_masks_infrastructure_error() -> None:
    mcp = FakeMCP()
    client = FakeClient(error=ConnectionError("refused"))
    _register_all(mcp, client)

    result = await mcp.tools["get_table_statistics"]()

    assert result.structuredContent["valid"] is False
    assert result.structuredContent["message"] == "The database is temporarily unavailable."


@pytest.mark.asyncio
async def test_get_table_statistics_handles_empty_database() -> None:
    """When no public tables exist, the client returns an empty list without touching the pool."""
    client = PostgresClient("postgresql://unused", 1, 100)

    async def no_tables() -> list[str]:
        return []

    client.list_tables = no_tables  # type: ignore[method-assign]

    assert await client.get_table_statistics() == []


@pytest.mark.asyncio
async def test_get_column_statistics_returns_stats() -> None:
    mcp = FakeMCP()
    client = FakeClient(column_statistics=COLUMN_STATISTICS)
    _register_all(mcp, client)

    result = await mcp.tools["get_column_statistics"](
        table_name="orders", column_name="order_status"
    )

    assert result.structuredContent["valid"] is True
    assert result.structuredContent["entries"] == [COLUMN_STATISTICS]
    assert client.calls == ["get_column_statistics:orders.order_status"]


@pytest.mark.asyncio
async def test_get_column_statistics_rejects_blank_inputs() -> None:
    mcp = FakeMCP()
    client = FakeClient()
    _register_all(mcp, client)

    blank_table = await mcp.tools["get_column_statistics"](
        table_name=" ", column_name="order_status"
    )
    assert blank_table.structuredContent["valid"] is False
    assert "blank" in blank_table.structuredContent["message"]

    blank_column = await mcp.tools["get_column_statistics"](table_name="orders", column_name=" ")
    assert blank_column.structuredContent["valid"] is False
    assert "blank" in blank_column.structuredContent["message"]
    assert client.calls == []


@pytest.mark.asyncio
async def test_get_column_statistics_reports_unknown_table_or_column() -> None:
    mcp = FakeMCP()
    client = FakeClient()
    _register_all(mcp, client)

    result = await mcp.tools["get_column_statistics"](
        table_name="missing", column_name="order_status"
    )

    assert result.structuredContent["valid"] is False
    assert "not found" in result.structuredContent["message"]


@pytest.mark.asyncio
async def test_get_column_statistics_masks_infrastructure_error() -> None:
    mcp = FakeMCP()
    client = FakeClient(error=ConnectionError("refused"))
    _register_all(mcp, client)

    result = await mcp.tools["get_column_statistics"](
        table_name="orders", column_name="order_status"
    )

    assert result.structuredContent["valid"] is False
    assert result.structuredContent["message"] == "The database is temporarily unavailable."
