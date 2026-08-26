"""Integration tests for the M4 analytics agent against the live MCP server.

These run against a reachable analytics MCP server and the live Olist database
and skip themselves when either is unavailable (mirroring the M1/M2/M3
integration tests). The agent consumes the real MCP tools end-to-end with a
deterministic FakeLLM so the SQL/answer step is not network/model-dependent —
the goal is to verify the graph drives the read-only MCP boundary correctly.
"""

import asyncio

import httpx
import pytest

from app.agent.capabilities import MCPCapabilities
from app.agent.graph import AgentServices, build_graph
from app.agent.llm import FakeLLM
from app.config import get_settings


async def _server_ready(url: str, retries: int = 10, delay: float = 1.0) -> bool:
    base = url.removesuffix("/mcp")
    for attempt in range(retries):
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(f"{base}/ready")
            if resp.status_code == 200 and resp.json().get("status") == "ready":
                return True
        except Exception:
            pass
        if attempt == retries - 1:
            return False
        await asyncio.sleep(delay)
    return False


@pytest.mark.asyncio
async def test_agent_drives_live_mcp_boundary() -> None:
    settings = get_settings()
    if not await _server_ready(settings.mcp_url):
        pytest.skip("no live MCP server")

    llm = FakeLLM(
        sql=(
            "SELECT o.order_status, COUNT(*) AS order_count "
            "FROM orders o GROUP BY o.order_status ORDER BY order_count DESC"
        ),
        answer="The delivered status has the most orders.",
    )
    caps = MCPCapabilities(settings.mcp_url)
    services = AgentServices(llm=llm, capabilities=caps, max_attempts=settings.agent_max_attempts)
    graph = build_graph(services)
    state = await graph.ainvoke({"question": "Which order status is most common?"})
    result = state["result"] if state.get("result") else []
    assert result, "query returned no rows"
    assert state.get("query_error") is None
    assert state["status"] == "completed"
    # The real server executed a bounded, read-only query.
    assert "LIMIT" in state["bounded_sql"]
    assert state["attempts"] == 1
