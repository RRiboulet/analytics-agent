"""Deterministic unit tests for the M4 analytics agent graph.

The whole graph is exercised with a deterministic ``FakeLLM`` and an in-memory
``StubCapabilities`` so the workflow logic (metadata retrieval, SQL validation,
execution, retry recovery, bounded attempts, status transitions) is covered
without any network, live model, or database.
"""

import pytest

from app.agent.capabilities import parse_tool_result
from app.agent.entrypoint import _run_question
from app.agent.graph import AgentServices, build_graph
from app.agent.llm import FakeLLM
from app.agent.state import AgentStatus


@pytest.mark.asyncio
async def test_parse_tool_result_extracts_json_text() -> None:
    payload = [{"type": "text", "text": '{"valid": true, "message": "ok", "entries": [1]}'}]
    assert parse_tool_result(payload) == {"valid": True, "message": "ok", "entries": [1]}


@pytest.mark.asyncio
async def test_parse_tool_result_falls_back_cleanly() -> None:
    assert parse_tool_result([]) == {"valid": False, "message": "Empty tool result.", "entries": []}
    assert parse_tool_result(None) == {
        "valid": False,
        "message": "Empty tool result.",
        "entries": [],
    }
    assert parse_tool_result([{"type": "text", "text": "not json"}])["valid"] is False
    assert parse_tool_result("literal")["valid"] is False


@pytest.mark.asyncio
async def test_run_question_returns_completed_state() -> None:
    llm = FakeLLM(sql="SELECT 1", answer="answer")
    out = await _run_question(
        "Q", llm=llm, capabilities=StubCapabilities(), max_attempts=3, max_rows=100, tracer=None
    )
    assert out["status"] == AgentStatus.COMPLETED
    assert out["answer"] == "answer"


class StubCapabilities:
    """In-memory MCP capability stub (mirrors the read-only tool results)."""

    def __init__(self, query_valid: bool = True, query_message: str = "") -> None:
        self.query_valid = query_valid
        self.query_message = query_message
        self.calls: list[tuple[str, dict | None]] = []

    async def call_tool(self, name, args=None) -> dict:
        self.calls.append((name, args))
        if name == "search_metadata":
            return {
                "valid": True,
                "message": "ok",
                "entries": [
                    {"entity_id": "column:order_items.price"},
                    {"entity_id": "table:orders"},
                ],
            }
        if name == "list_tables":
            return {
                "valid": True,
                "entries": [{"table_name": "orders"}, {"table_name": "order_items"}],
            }
        if name == "get_relationships":
            return {
                "valid": True,
                "entries": [
                    {
                        "child_table": "order_items",
                        "child_column": "order_id",
                        "parent_table": "orders",
                        "parent_column": "order_id",
                    }
                ],
            }
        if name == "query":
            if not self.query_valid:
                return {
                    "valid": False,
                    "message": self.query_message or "Query failed: bad column",
                    "entries": [],
                }
            return {"valid": True, "message": "ok", "entries": [{"order_status": "delivered"}]}
        return {"valid": True, "entries": []}

    async def close(self) -> None:
        pass


class SeqCapabilities(StubCapabilities):
    """Query returns a scripted sequence of valid/failed results."""

    def __init__(self, flags: list[bool]) -> None:
        super().__init__()
        self.flags = list(flags)
        self.i = 0

    async def call_tool(self, name, args=None) -> dict:
        if name == "query":
            ok = self.flags[min(self.i, len(self.flags) - 1)]
            self.i += 1
            return (
                {"valid": True, "message": "ok", "entries": [{"n": 1}]}
                if ok
                else {"valid": False, "message": "Query failed: bad column", "entries": []}
            )
        return await super().call_tool(name, args)


async def _run(services: AgentServices, question: str = "Which status is most common?"):
    graph = build_graph(services)
    return await graph.ainvoke({"question": question, "status": "planning", "attempts": 0})


@pytest.mark.asyncio
async def test_successful_flow_grounds_answer_and_bounds_sql() -> None:
    sql = "SELECT order_status, COUNT(*) n FROM orders GROUP BY order_status"
    llm = FakeLLM(sql=sql, answer="delivered is most common")
    caps = StubCapabilities()
    out = await _run(AgentServices(llm=llm, capabilities=caps, max_attempts=3))

    assert out["status"] == AgentStatus.COMPLETED
    assert out["answer"] == "delivered is most common"
    assert out.get("validation_error") is None
    assert out.get("query_error") is None
    # The bounded query is what actually reaches the read-only boundary.
    assert "LIMIT 100" in out["bounded_sql"]
    # Tool discovery precedes query execution.
    names = [c[0] for c in caps.calls]
    assert names == ["search_metadata", "list_tables", "get_relationships", "query"]


@pytest.mark.asyncio
async def test_retries_on_invalid_sql_then_succeeds() -> None:
    llm = FakeLLM(sql_sequence=["DELETE FROM orders", "SELECT 1"], answer="ok")
    caps = StubCapabilities()
    state = await _run(AgentServices(llm=llm, capabilities=caps, max_attempts=3))

    assert state["status"] == AgentStatus.COMPLETED
    assert state["attempts"] == 2  # initial + one retry


@pytest.mark.asyncio
async def test_fails_after_max_invalid_sql_attempts() -> None:
    llm = FakeLLM(sql="DELETE FROM orders")
    state = await _run(AgentServices(llm=llm, capabilities=StubCapabilities(), max_attempts=2))

    assert state["status"] == AgentStatus.FAILED
    assert state["attempts"] == 2
    assert "Only SELECT" in state["validation_error"]


@pytest.mark.asyncio
async def test_retries_after_query_error_then_succeeds() -> None:
    llm = FakeLLM(sql="SELECT 1", answer="ok")
    caps = SeqCapabilities([False, True])
    state = await _run(AgentServices(llm=llm, capabilities=caps, max_attempts=3))

    assert state["status"] == AgentStatus.COMPLETED
    assert state["attempts"] == 2
    assert state.get("query_error") is None


@pytest.mark.asyncio
async def test_fails_after_persistent_query_error() -> None:
    llm = FakeLLM(sql="SELECT 1")
    state = await _run(
        AgentServices(llm=llm, capabilities=SeqCapabilities([False, False, False]), max_attempts=3)
    )

    assert state["status"] == AgentStatus.FAILED
    assert state["attempts"] == 3
    assert "bad column" in state["query_error"]
    assert state.get("answer") is None


@pytest.mark.asyncio
async def test_query_error_feedback_reaches_model_and_corrects() -> None:
    """A rejected query must be fed back to the model so it can self-correct."""

    seen_errors: list[str | None] = []
    calls = 0

    class FeedbackLLM(FakeLLM):
        async def generate_sql(
            self, question: str, metadata: str, schema: str, *, prior_error: str | None = None
        ) -> str:
            nonlocal calls
            calls += 1
            seen_errors.append(prior_error)
            # First (blind) generation references a column that does not exist; the
            # retry, seeing the prior error, drops the bad column.
            if calls == 1:
                return "SELECT SUM(t2.price * t2.quantity) x FROM order_items t2"
            return "SELECT SUM(t2.price) x FROM order_items t2"

    caps = SeqCapabilities([False, True])  # first query fails, corrected one succeeds
    state = await _run(
        AgentServices(llm=FeedbackLLM(answer="ok"), capabilities=caps, max_attempts=3)
    )

    assert state["status"] == AgentStatus.COMPLETED
    assert state["attempts"] == 2
    assert state.get("query_error") is None
    # The retry generation saw the previous execution error as feedback.
    assert seen_errors == [None, "Query failed: bad column"]


@pytest.mark.asyncio
async def test_transport_failure_propagates_never_fabricates() -> None:
    """A total capability outage raises rather than yielding a made-up answer."""

    class BoomCapabilities(StubCapabilities):
        async def call_tool(self, name, args=None) -> dict:
            raise ConnectionError("server down")

    llm = FakeLLM(sql="SELECT 1", answer="should not be used")
    with pytest.raises(ConnectionError):
        await _run(AgentServices(llm=llm, capabilities=BoomCapabilities(), max_attempts=3))
