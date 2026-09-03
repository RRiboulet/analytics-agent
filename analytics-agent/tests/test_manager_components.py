"""Unit tests for the analytics manager groundwork (M7.1).

Covers the manager state model, evidence records built from analyst run
states, the decompose LLM capability (real client against a stubbed transport
plus the deterministic fake) and the deterministic decomposition
parsing/validation. No network, no database, no live model.
"""

import json
from typing import Any

import httpx
import pytest

from app.agent.llm import LLMError
from app.agent.state import AgentState, AgentStatus
from app.manager.decompose import (
    MAX_SUB_QUESTIONS,
    DecompositionError,
    decompose_request,
    parse_sub_questions,
)
from app.manager.evidence import EvidenceRecord
from app.manager.llm import FakeManagerLLM, ManagerLLMClient
from app.manager.state import ManagerState, ManagerStatus

# ---------------------------------------------------------------------------
# Manager state model
# ---------------------------------------------------------------------------


def test_manager_status_values() -> None:
    assert {s.value for s in ManagerStatus} == {
        "decomposing",
        "running_sub_analyses",
        "synthesizing",
        "completed",
        "failed",
    }


def test_manager_state_holds_all_workflow_fields() -> None:
    state: ManagerState = {
        "request": "How did sales develop?",
        "sub_questions": ["Revenue per month?"],
        "decomposition_error": "",
        "evidence": [EvidenceRecord(sub_index=0, sub_question="Revenue per month?", rows=[])],
        "sub_analysis_errors": ["sub 1 failed"],
        "report": "# Report",
        "status": ManagerStatus.COMPLETED,
    }
    assert state["status"] is ManagerStatus.COMPLETED
    assert state["evidence"][0].sub_index == 0


# ---------------------------------------------------------------------------
# EvidenceRecord
# ---------------------------------------------------------------------------


def test_evidence_from_completed_agent_state() -> None:
    state: AgentState = {
        "status": AgentStatus.COMPLETED,
        "sql": "SELECT * FROM orders",
        "bounded_sql": "SELECT * FROM orders LIMIT 100",
        "result": [{"order_id": "o1", "revenue": 10.5}],
        "answer": "Revenue was 10.5.",
    }
    record = EvidenceRecord.from_agent_state(2, "What is the revenue?", state)
    assert record.sub_index == 2
    assert record.sub_question == "What is the revenue?"
    assert record.status == "completed"
    assert record.sql == "SELECT * FROM orders LIMIT 100"  # bounded query preferred
    assert record.rows == [{"order_id": "o1", "revenue": 10.5}]
    assert record.answer == "Revenue was 10.5."
    assert record.error is None


def test_evidence_from_failed_agent_state_error_precedence() -> None:
    state: AgentState = {
        "status": AgentStatus.FAILED,
        "sql": "SELECT bogus",
        "llm_error": "LLM request timed out",
        "query_error": "syntax error",
        "validation_error": "unsafe",
    }
    record = EvidenceRecord.from_agent_state(0, "q", state)
    assert record.status == "failed"
    assert record.sql == "SELECT bogus"  # no bounded_sql -> raw sql fallback
    assert record.rows is None
    assert record.answer is None
    assert record.error == "LLM request timed out"  # llm > query > validation


def test_evidence_error_falls_back_to_query_and_validation() -> None:
    record = EvidenceRecord.from_agent_state(
        0, "q", {"query_error": "syntax error", "validation_error": "unsafe"}
    )
    assert record.error == "syntax error"

    record = EvidenceRecord.from_agent_state(0, "q", {"validation_error": "unsafe"})
    assert record.error == "unsafe"

    record = EvidenceRecord.from_agent_state(0, "q", {})
    assert record.status is None
    assert record.error is None
    assert record.sql is None


# ---------------------------------------------------------------------------
# Decomposition parsing / validation
# ---------------------------------------------------------------------------


def test_parse_plain_lines() -> None:
    raw = "Which category has the most revenue?\nHow many orders per month?"
    assert parse_sub_questions(raw) == [
        "Which category has the most revenue?",
        "How many orders per month?",
    ]


def test_parse_strips_numbering_and_bullets() -> None:
    raw = "1. First question?\n2) Second question?\n- Third question?\n* Fourth question?"
    parsed = parse_sub_questions(raw)
    assert len(parsed) == 4
    assert parsed[0] == "First question?"
    assert parsed[1] == "Second question?"
    assert parsed[2] == "Third question?"
    assert parsed[3] == "Fourth question?"


def test_parse_strips_markdown_fences_and_blank_lines() -> None:
    raw = "```text\n\nOne?\n\nTwo?\n\n```"
    assert parse_sub_questions(raw) == ["One?", "Two?"]


def test_parse_dedupes_preserving_order() -> None:
    raw = "A?\nB?\nA?\nC?\nB?\nD?"
    assert parse_sub_questions(raw) == ["A?", "B?", "C?", "D?"]


def test_parse_drops_punctuation_only_lines() -> None:
    raw = "A?\n-\n*\n1.\n1)\nB?"
    assert parse_sub_questions(raw) == ["A?", "B?"]


@pytest.mark.parametrize("raw", ["", "   \n  \n", "```\n```", "-", "*"])
def test_parse_empty_output_raises(raw: str) -> None:
    with pytest.raises(DecompositionError, match="no sub-questions"):
        parse_sub_questions(raw)


def test_parse_accepts_exactly_the_cap() -> None:
    raw = "\n".join(f"Question {i}?" for i in range(MAX_SUB_QUESTIONS))
    assert len(parse_sub_questions(raw)) == MAX_SUB_QUESTIONS


def test_parse_rejects_more_than_the_cap() -> None:
    raw = "\n".join(f"Question {i}?" for i in range(MAX_SUB_QUESTIONS + 1))
    with pytest.raises(DecompositionError, match="hard cap is 4"):
        parse_sub_questions(raw)


# ---------------------------------------------------------------------------
# decompose_request (LLM call + validation seam)
# ---------------------------------------------------------------------------


async def test_decompose_request_parses_and_records_the_call() -> None:
    llm = FakeManagerLLM(raw="- Revenue by category?\n- Revenue by category?\n2. Top sellers?")
    questions = await decompose_request(llm, "Summarize sales.", ["orders", "order_items"])
    assert questions == ["Revenue by category?", "Top sellers?"]
    assert llm.calls == [("Summarize sales.", ("orders", "order_items"))]


async def test_decompose_request_propagates_llm_errors_unchanged() -> None:
    llm = FakeManagerLLM(llm_error=LLMError("LLM request timed out"))
    with pytest.raises(LLMError, match="timed out"):
        await decompose_request(llm, "Summarize sales.", ["orders"])


async def test_decompose_request_rejects_unusable_output() -> None:
    llm = FakeManagerLLM(raw="```")
    with pytest.raises(DecompositionError, match="no sub-questions"):
        await decompose_request(llm, "Summarize sales.", ["orders"])


# ---------------------------------------------------------------------------
# FakeManagerLLM / ManagerLLMClient
# ---------------------------------------------------------------------------


async def test_fake_manager_llm_returns_raw_verbatim() -> None:
    llm = FakeManagerLLM(raw="1. One?\n2. Two?")
    assert await llm.decompose("req", ["t1"]) == "1. One?\n2. Two?"


async def test_fake_manager_llm_raises_configured_error() -> None:
    llm = FakeManagerLLM(llm_error=LLMError("boom"))
    with pytest.raises(LLMError, match="boom"):
        await llm.decompose("req", ["t1"])


def _decompose_transport(captured: dict[str, Any]) -> httpx.MockTransport:
    async def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = request.read()
        return httpx.Response(200, json={"choices": [{"message": {"content": "  One?  "}}]})

    return httpx.MockTransport(handler)


async def test_manager_llm_client_decompose_request_shape() -> None:
    captured: dict[str, Any] = {}
    client = ManagerLLMClient(
        base_url="http://llm/v1",
        model="gemma",
        timeout_seconds=5,
        max_tokens=4096,
        transport=_decompose_transport(captured),
    )
    raw = await client.decompose("Summarize sales.", ["orders", "order_items"])
    assert raw == "One?"  # whitespace stripped by the shared completion path
    payload = json.loads(captured["payload"])
    assert payload["model"] == "gemma"
    assert payload["max_tokens"] == 4096
    system, user = (m["content"] for m in payload["messages"])
    assert system.startswith("You are an analytics manager")
    assert "at most 4" in system  # the decompose system prompt is used
    assert "Summarize sales." in user
    assert "orders, order_items" in user  # table names as the schema hint
