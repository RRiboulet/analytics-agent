"""
Middleware for copying structuredContent to content in CallToolResult responses.

llama.cpp MCP clients expect structured data in the `content` field rather than
the separate `structuredContent` field. Every tool result is passed through
`copy_structured_content_to_content` so those clients receive a JSON string in
`content` while the original `structuredContent` is preserved.

See: https://modelcontextprotocol.io/specification/2024-11-05/server/implementation/http#structured-content
"""

import json
from datetime import date, time
from decimal import Decimal
from typing import Any

from mcp.types import CallToolResult, TextContent


def copy_structured_content_to_content(result: CallToolResult) -> CallToolResult:
    """
    Replace `content` with a JSON-encoded copy of `structuredContent`.

    Args:
        result: The CallToolResult to modify.

    Returns:
        The modified CallToolResult. `structuredContent` is preserved for
        standards-compliant clients; `content` carries the same data as a JSON
        string for llama.cpp clients. If `structuredContent` is None, the result
        is returned unchanged.
    """
    if result.structuredContent is None:
        return result

    content_text = json.dumps(_serialize_value(result.structuredContent), ensure_ascii=False)
    result.content = [TextContent(type="text", text=content_text)]
    return result


def _serialize_value(value: Any) -> Any:
    """
    Recursively convert Decimal to float for JSON serialization.

    PostgreSQL's asyncpg returns Decimal for numeric columns, which json.dumps
    cannot serialize by default.
    """
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, time)):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _serialize_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serialize_value(item) for item in value]
    return value
