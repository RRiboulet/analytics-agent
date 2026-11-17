"""Unit tests for the manager orchestration graph (M7.2).

Covers decomposition routing (happy path, transient LLM retry, unusable
output), sequential sub-analysis execution with evidence accumulation, and the
failure policy (partial failure recorded, all-failed fails the manager).
Fully hermetic: FakeManagerLLM plus a stub analyst runner; no network, no
database, no live model.
"""

from app.agent.capabilities import CountingCapabilities
from app.agent.llm import LLMError
from app.agent.state import AgentState, AgentStatus
from app.manager.decompose import MAX_SUB_QUESTIONS
from app.manager.graph import FAIL, SUB_ANALYSES_EDGE, ManagerServices, build_manager_graph
from app.manager.llm import FakeManagerLLM
from app.manager.state import ManagerState, ManagerStatus


def _ok_sub_state(answer: str = "found it") -> AgentState:
    return {
        "status": AgentStatus.COMPLETED,
        "bounded_sql": "SELECT 1 LIMIT 100",
        "result": [{"revenue": 10.5}],
        "answer": answer,
    }


def _failed_sub_state(error: str) -> AgentState:
    return {"status": AgentStatus.FAILED, "sql": "SELECT bogus", "llm_error": error}


class FlakyLLM(FakeManagerLLM):
    """Raises LLMError on the first decompose call, succeeds afterwards."""

    def __init__(self, raw: str) -> None:
        super().__init__(raw=raw)

    async def decompose(self, request: str, table_names: list[str]) -> str:
        self.calls.append((request, tuple(table_names)))
        if len(self.calls) == 1:
            raise LLMError("HTTP 429 too many requests")
        return self.raw


class StubAnalyst:
    """Scripted analyst runner: answers per sub-question, records calls."""

    def __init__(self, scripts: dict[str, AgentState]) -> None:
        self.scripts = scripts
        self.calls: list[str] = []

    async def __call__(self, question: str) -> AgentState:
        self.calls.append(question)
        return self.scripts[question]


class StubCapability:
    """Minimal read-only capability stub: accepts any call, returns a valid result."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def call_tool(self, name: str, args=None) -> dict:
        self.calls.append(name)
        return {"valid": True, "message": "ok", "entries": []}


def _counter() -> CountingCapabilities:
    """The production counter over a stub capability (no second implementation)."""
    return CountingCapabilities(StubCapability())


class CountingAnalyst(StubAnalyst):
    """Analyst stub that spends a scripted number of tool calls per run.

    ``spend`` maps a question to the calls spent by each successive run of it,
    so a follow-up question can cost something different from the first run.
    The calls go through the real counter, exactly as they do in production.
    """

    def __init__(
        self,
        scripts: dict[str, AgentState],
        counter: CountingCapabilities,
        spend: dict[str, list[dict[str, int]]],
    ) -> None:
        super().__init__(scripts)
        self.counter = counter
        self.spend = spend
        self._runs: dict[str, int] = {}

    async def __call__(self, question: str) -> AgentState:
        run = self._runs.get(question, 0)
        self._runs[question] = run + 1
        for name, count in self.spend[question][run].items():
            for _ in range(count):
                await self.counter.call_tool(name)
        return await super().__call__(question)


def _services(
    llm: FakeManagerLLM,
    analyst: StubAnalyst | None = None,
    *,
    max_attempts: int = 2,
    table_names: list[str] | None = None,
    tool_call_counter: CountingCapabilities | None = None,
) -> ManagerServices:
    return ManagerServices(
        llm=llm,
        run_analyst=analyst or StubAnalyst({}),
        max_attempts=max_attempts,
        table_names=table_names or ["orders", "order_items"],
        tool_call_counter=tool_call_counter,
    )


async def _invoke(services: ManagerServices, request: str = "Summarize sales.") -> ManagerState:
    graph = build_manager_graph(services)
    return await graph.ainvoke({"request": request})


# ---------------------------------------------------------------------------
# Happy path / evidence accumulation
# ---------------------------------------------------------------------------


async def test_happy_path_accumulates_ordered_evidence() -> None:
    analyst = StubAnalyst(
        {
            "Revenue by category?": _ok_sub_state("revenue answer"),
            "Top sellers?": _ok_sub_state("sellers answer"),
        }
    )
    llm = FakeManagerLLM(raw="Revenue by category?\nTop sellers?")
    state = await _invoke(_services(llm, analyst))

    assert state["status"] is ManagerStatus.COMPLETED
    assert analyst.calls == ["Revenue by category?", "Top sellers?"]  # sequential, in order
    assert [r.sub_question for r in state["evidence"]] == [
        "Revenue by category?",
        "Top sellers?",
    ]
    assert [r.sub_index for r in state["evidence"]] == [0, 1]
    assert state["evidence"][0].answer == "revenue answer"
    assert state["evidence"][0].rows == [{"revenue": 10.5}]
    assert state["sub_analysis_errors"] == []
    assert state["decomposition_error"] is None
    assert state["llm_error"] is None


async def test_evidence_records_carry_each_runs_tool_calls() -> None:
    counter = _counter()
    analyst = CountingAnalyst(
        {"Revenue by category?": _ok_sub_state(), "Top sellers?": _ok_sub_state()},
        counter,
        {
            "Revenue by category?": [{"query": 2, "list_tables": 1}],
            "Top sellers?": [{"query": 1}],
        },
    )
    llm = FakeManagerLLM(raw="Revenue by category?\nTop sellers?")
    state = await _invoke(_services(llm, analyst, tool_call_counter=counter))

    records = state["evidence"]
    assert [r.tool_calls for r in records] == [
        {"query": 2, "list_tables": 1},
        {"query": 1},
    ]
    # Attribution is complete: nothing the run spent is left unattributed.
    assert sum(sum(r.tool_calls.values()) for r in records) == sum(counter.snapshot().values())


async def test_failed_sub_analysis_still_carries_its_tool_calls() -> None:
    counter = _counter()
    analyst = CountingAnalyst(
        {"Bad question?": _failed_sub_state("LLM request timed out")},
        counter,
        {"Bad question?": [{"query": 3}]},
    )
    llm = FakeManagerLLM(raw="Bad question?")
    state = await _invoke(_services(llm, analyst, tool_call_counter=counter))

    assert state["evidence"][0].error is not None
    assert state["evidence"][0].tool_calls == {"query": 3}


async def test_records_carry_no_counts_without_a_counter() -> None:
    analyst = StubAnalyst({"One?": _ok_sub_state()})
    llm = FakeManagerLLM(raw="One?")
    state = await _invoke(_services(llm, analyst))

    assert state["evidence"][0].tool_calls == {}


async def test_partial_failure_is_recorded_and_run_continues() -> None:
    analyst = StubAnalyst(
        {
            "Good question?": _ok_sub_state(),
            "Bad question?": _failed_sub_state("LLM request timed out"),
        }
    )
    llm = FakeManagerLLM(raw="Bad question?\nGood question?")
    state = await _invoke(_services(llm, analyst))

    assert state["status"] is ManagerStatus.COMPLETED  # some evidence exists
    assert len(state["evidence"]) == 2  # failure is recorded, not skipped
    assert state["evidence"][0].error == "LLM request timed out"
    assert state["evidence"][1].error is None
    assert state["sub_analysis_errors"] == ["sub-question 0 (Bad question?): LLM request timed out"]


async def test_all_sub_analyses_failing_fails_the_manager() -> None:
    analyst = StubAnalyst(
        {
            "One?": _failed_sub_state("query failed"),
            "Two?": _failed_sub_state("syntax error"),
        }
    )
    llm = FakeManagerLLM(raw="One?\nTwo?")
    state = await _invoke(_services(llm, analyst))

    assert state["status"] is ManagerStatus.FAILED
    assert len(state["evidence"]) == 2  # evidence still recorded for the report path
    assert len(state["sub_analysis_errors"]) == 2


async def test_table_hint_comes_from_services() -> None:
    analyst = StubAnalyst({"One?": _ok_sub_state()})
    llm = FakeManagerLLM(raw="One?")
    await _invoke(_services(llm, analyst, table_names=["orders", "customers"]))
    assert llm.calls == [("Summarize sales.", ("orders", "customers"))]


async def test_default_table_names_derived_from_metadata_seed() -> None:
    services = ManagerServices(llm=FakeManagerLLM(), run_analyst=StubAnalyst({}))
    assert "orders" in services.table_names
    assert "order_items" in services.table_names
    assert len(services.table_names) == 9  # the full curated Olist schema


# ---------------------------------------------------------------------------
# Decompose failure routing
# ---------------------------------------------------------------------------


async def test_transient_llm_error_is_retried_bounded_then_recovers() -> None:
    analyst = StubAnalyst({"One?": _ok_sub_state()})
    flaky = FlakyLLM("One?")
    state = await _invoke(_services(flaky, analyst, max_attempts=2))

    assert len(flaky.calls) == 2  # first attempt failed, second succeeded
    assert state["attempts"] == 2
    assert state["status"] is ManagerStatus.COMPLETED
    assert state["llm_error"] is None  # cleared on the successful decompose
    assert analyst.calls == ["One?"]


async def test_persistent_llm_error_fails_after_bounded_attempts() -> None:
    llm = FakeManagerLLM(raw="One?", llm_error=LLMError("HTTP 429"))
    state = await _invoke(_services(llm, max_attempts=3))

    assert len(llm.calls) == 3  # bounded, no infinite loop
    assert state["status"] is ManagerStatus.FAILED
    assert state["llm_error"] == "HTTP 429"
    assert state["sub_questions"] == []
    assert state.get("evidence") is None  # never reached sub-analyses


async def test_unusable_output_fails_immediately_without_retry() -> None:
    llm = FakeManagerLLM(raw="```")  # parses to nothing -> DecompositionError
    state = await _invoke(_services(llm, max_attempts=3))

    assert len(llm.calls) == 1  # deterministic output: no retry
    assert state["status"] is ManagerStatus.FAILED
    assert "no sub-questions" in state["decomposition_error"]
    assert state["llm_error"] is None


async def test_over_cap_output_fails_immediately() -> None:
    llm = FakeManagerLLM(raw="\n".join(f"Q{i}?" for i in range(MAX_SUB_QUESTIONS + 1)))
    state = await _invoke(_services(llm, max_attempts=3))

    assert len(llm.calls) == 1
    assert state["status"] is ManagerStatus.FAILED
    assert "hard cap is 4" in state["decomposition_error"]


# ---------------------------------------------------------------------------
# Graph structure
# ---------------------------------------------------------------------------


def test_manager_graph_structure_mirrors_documented_edges() -> None:
    graph = build_manager_graph(_services(FakeManagerLLM()))
    nodes = set(graph.get_graph().nodes)
    assert {"decompose", "run_sub_analyses", "retry", "fail"} <= nodes


def test_module_level_constants_are_stable() -> None:
    # Guard the exported node/edge names: M7.4's entrypoint and tracing tests
    # reference them.
    assert SUB_ANALYSES_EDGE == "sub_analyses"
    assert FAIL == "fail"
