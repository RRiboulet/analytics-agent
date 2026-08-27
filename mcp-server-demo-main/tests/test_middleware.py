"""Tests for the structured content middleware."""

import json
from datetime import date, datetime, time
from decimal import Decimal

from mcp.types import CallToolResult, TextContent

from app.middleware import copy_structured_content_to_content


def _result(entries: list[dict]) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text="Query returned rows.")],
        structuredContent={"valid": True, "message": "Query returned rows.", "entries": entries},
    )


def test_copies_structured_content_to_content_as_json() -> None:
    modified = copy_structured_content_to_content(_result([{"id": 1, "name": "Item 1"}]))

    assert len(modified.content) == 1
    parsed = json.loads(modified.content[0].text)
    assert parsed == {
        "valid": True,
        "message": "Query returned rows.",
        "entries": [{"id": 1, "name": "Item 1"}],
    }


def test_preserves_structured_content() -> None:
    modified = copy_structured_content_to_content(_result([{"machine_code": "ASM-01"}]))

    assert modified.structuredContent["valid"] is True
    assert modified.structuredContent["entries"] == [{"machine_code": "ASM-01"}]


def test_serializes_decimal_rows() -> None:
    entries = [{"price": Decimal("19.99"), "quantity": Decimal("100")}]
    modified = copy_structured_content_to_content(_result(entries))

    parsed = json.loads(modified.content[0].text)
    assert parsed["entries"][0]["price"] == 19.99
    assert parsed["entries"][0]["quantity"] == 100.0


def test_serializes_datetime_rows() -> None:
    ts = datetime(2018, 1, 15, 12, 30, 0)
    entries = [{"order_purchase_timestamp": ts, "event_date": date(2018, 1, 15), "t": time(12, 30)}]
    modified = copy_structured_content_to_content(_result(entries))

    parsed = json.loads(modified.content[0].text)
    assert parsed["entries"][0] == {
        "order_purchase_timestamp": "2018-01-15T12:30:00",
        "event_date": "2018-01-15",
        "t": "12:30:00",
    }


def test_serializes_nested_datetime() -> None:
    entries = [{"nested": {"at": datetime(2020, 6, 1, 8, 0, 0)}}]
    modified = copy_structured_content_to_content(_result(entries))

    parsed = json.loads(modified.content[0].text)
    assert parsed["entries"][0]["nested"]["at"] == "2020-06-01T08:00:00"
    result = CallToolResult(
        content=[TextContent(type="text", text="Error")],
        structuredContent={"valid": False, "message": "错误发生：Unicode 测试"},  # noqa: RUF001
    )
    modified = copy_structured_content_to_content(result)

    parsed = json.loads(modified.content[0].text)
    assert parsed["message"] == "错误发生：Unicode 测试"  # noqa: RUF001


def test_none_structured_content_is_unchanged() -> None:
    result = CallToolResult(
        content=[TextContent(type="text", text="No data")],
        structuredContent=None,
    )
    modified = copy_structured_content_to_content(result)

    assert modified.content[0].text == "No data"
    assert modified.structuredContent is None


def test_empty_structured_content() -> None:
    modified = copy_structured_content_to_content(_result([]))

    parsed = json.loads(modified.content[0].text)
    assert parsed["entries"] == []
