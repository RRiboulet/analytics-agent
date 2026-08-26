"""MCP capabilities consumed by the agent.

The agent never touches PostgreSQL directly — it reaches the database only
through the read-only MCP tools (PLAN decisions D005/D006, section 7). This
module wraps the running MCP server, indexed by the Streamable HTTP connection,
and exposes a narrow ``call_tool`` surface the graph nodes use. Tools are loaded
once and reused; each ``ainvoke`` creates its own MCP session per the adapter.
"""

import json
from typing import Any, Protocol

from app.config import get_settings


def parse_tool_result(content: Any) -> dict[str, Any]:
    """Extract the structured payload from a LangChain MCP tool result.

    The server returns structured data as a JSON string inside the text content
    (see app/middleware.py). We parse that back into a plain dict so the graph
    can read ``valid`` / ``message`` / ``entries`` deterministically.
    """
    text: str | None = None
    if isinstance(content, list):
        for block in content:
            item = block if isinstance(block, dict) else {}
            if item.get("type") == "text":
                text = item.get("text")
                break
    elif isinstance(content, dict) and "text" in content:
        text = content["text"]
    if text is None:
        return {"valid": False, "message": "Empty tool result.", "entries": []}
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        return {"valid": False, "message": text, "entries": []}
    if not isinstance(parsed, dict):
        return {"valid": False, "message": text, "entries": []}
    return parsed


class Capabilities(Protocol):
    """The subset of MCP capabilities the agent graph depends on."""

    async def call_tool(self, name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        """Invoke an MCP tool and return its parsed structured result."""
        ...


class MCPCapabilities:
    """Call MCP tools on the configured analytics server."""

    def __init__(self, url: str | None = None) -> None:
        settings = get_settings()
        self.url = url or settings.mcp_url
        self._client: Any = None
        self._tools: dict[str, Any] = {}

    async def _ensure_connected(self) -> None:
        if self._client is not None:
            return
        from langchain_mcp_adapters.client import MultiServerMCPClient

        client = MultiServerMCPClient(
            {"analytics": {"transport": "streamable_http", "url": self.url}}
        )
        tools = await client.get_tools()
        self._client = client
        self._tools = {tool.name: tool for tool in tools}

    async def call_tool(self, name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        await self._ensure_connected()
        tool = self._tools.get(name)
        if tool is None:
            return {"valid": False, "message": f"Unknown tool: {name}", "entries": []}
        try:
            content = await tool.ainvoke(args or {})
        except Exception as error:  # surfaced to the agent for self-correction
            return {"valid": False, "message": f"Tool error: {error}", "entries": []}
        return parse_tool_result(content)

    async def close(self) -> None:
        self._tools = {}
        self._client = None
