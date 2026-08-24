import json
from typing import Any

import pytest

from app.data_sources.postgres import PostgresClient
from app.tools.query_factory_data import register
from app.tools.registry import register_tools


class FakeMCP:
    def __init__(self) -> None:
        self.tools: dict[str, Any] = {}

    def tool(self, **_kwargs: Any):
        def decorator(function: Any) -> Any:
            self.tools[function.__name__] = function
            return function

        return decorator


class FakeClient(PostgresClient):
    def __init__(self) -> None:
        super().__init__("postgresql://unused", 1, 100)
        self.queries: list[str] = []

    async def list_tables(self) -> list[str]:
        return ["machines"]

    async def describe_table(self, table_name: str) -> list[dict[str, Any]]:
        return [{"column_name": "machine_id", "table_name": table_name}]

    async def query(self, sql: str) -> list[dict[str, Any]]:
        self.queries.append(sql)
        return [{"machine_code": "ASM-01"}]


@pytest.mark.asyncio
async def test_query_tool_returns_structured_data() -> None:
    mcp = FakeMCP()
    client = FakeClient()
    register(mcp, client)  # type: ignore[arg-type]

    result = await mcp.tools["query_factory_data"]("SELECT machine_code FROM machines")

    assert result.structuredContent["valid"] is True
    assert result.structuredContent["entries"] == [{"machine_code": "ASM-01"}]
    assert client.queries[0].endswith("LIMIT 100")

    # llama.cpp clients read structured data from `content`; assert it carries JSON.
    parsed_content = json.loads(result.content[0].text)
    assert parsed_content["entries"] == [{"machine_code": "ASM-01"}]


@pytest.mark.asyncio
async def test_query_tool_rejects_writes_without_database_access() -> None:
    mcp = FakeMCP()
    client = FakeClient()
    register(mcp, client)  # type: ignore[arg-type]

    result = await mcp.tools["query_factory_data"]("DELETE FROM machines")

    assert result.structuredContent["valid"] is False
    assert client.queries == []


def test_registry_registers_factory_tools_with_shared_client() -> None:
    mcp = FakeMCP()
    register_tools(mcp, FakeClient())  # type: ignore[arg-type]

    assert set(mcp.tools) == {
        "list_factory_tables",
        "describe_factory_table",
        "query_factory_data",
    }
