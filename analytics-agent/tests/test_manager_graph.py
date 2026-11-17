"""Unit tests for the manager orchestration graph (M7.2, M8).

Covers decomposition routing (happy path, transient LLM retry, unusable
output), bounded parallel sub-analysis execution with evidence accumulation,
exact per-run call attribution under that concurrency, and the failure policy
(partial failure recorded, all-failed fails the manager, a raising run settles
its siblings before propagating).
Fully hermetic: FakeManagerLLM plus a stub analyst runner; no network, no
database, no live model. Overlap is proved with explicit events/barriers, not
sleeps, so the tests stay fast and non-flaky.
"""

import asyncio
from collections import Counter, defaultdict
from collections.abc import Awaitable, Callable

import pytest
from pydantic import ValidationError

from app.agent.capabilities import Capabilities, CountingCapabilities
from app.agent.llm import LLMError
from app.agent.state import AgentState, AgentStatus
from app.config import Settings
from app.manager.decompose import MAX_SUB_QUESTIONS
from app.manager.graph import (
    DEFAULT_MAX_PARALLELISM,
    FAIL,
    SUB_ANALYSES_EDGE,
    ManagerServices,
    build_manager_graph,
)
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
    """Scripted analyst runner: answers per sub-question, records calls.

    The second argument is the capability surface for that sub-run, which the
    manager hands over per run (M8, D2); ``None`` when no shared surface was
    injected.
    """

    def __init__(self, scripts: dict[str, AgentState]) -> None:
        self.scripts = scripts
        self.calls: list[str] = []
        self.surfaces: list[Capabilities | None] = []

    async def __call__(self, question: str, capabilities: Capabilities | None = None) -> AgentState:
        self.calls.append(question)
        self.surfaces.append(capabilities)
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
    The calls go through the per-run capability surface the manager supplied,
    exactly as they do in production; that surface must delegate to the shared
    counter the run total is read from.
    """

    def __init__(
        self,
        scripts: dict[str, AgentState],
        counter: CountingCapabilities,
        spend: dict[str, list[dict[str, int]]],
    ) -> None:
        super().__init__(scripts)
        self.shared = counter
        self.spend = spend
        self._runs: dict[str, int] = {}

    async def __call__(self, question: str, capabilities: Capabilities | None = None) -> AgentState:
        assert capabilities is not None, "an attributed run needs a capability surface"
        before = sum(self.shared.snapshot().values())
        run = self._runs.get(question, 0)
        self._runs[question] = run + 1
        for name, count in self.spend[question][run].items():
            for _ in range(count):
                await capabilities.call_tool(name)
        # The per-run surface wraps the shared counter: what a run spends must
        # stay visible to the run total.
        assert sum(self.shared.snapshot().values()) > before
        return await super().__call__(question, capabilities)


class RoundAnalyst:
    """Analyst stub that waits for its whole round to be in flight.

    ``rounds`` maps a question to its round id, so each round rendezvouses on
    its own event: a round with several questions cannot proceed unless the
    manager started them concurrently, and a serial loop times out instead of
    hanging the suite. No sleeps are involved, so the test is deterministic.

    ``events`` records ``start:``/``finish:`` per question and ``max_active``
    the peak concurrency per round, which is what the overlap and serial-
    equivalence assertions read.
    """

    def __init__(
        self, rounds: dict[str, int], *, spend: dict[str, dict[str, int]] | None = None
    ) -> None:
        self._round_of = dict(rounds)
        self._expected = Counter(rounds.values())
        self._gates = {round_id: asyncio.Event() for round_id in self._expected}
        self._active: Counter[int] = Counter()
        self.max_active: dict[int, int] = defaultdict(int)
        self.events: list[str] = []
        self.spend = spend or {}
        self.timeout = 0.5

    async def __call__(self, question: str, capabilities: Capabilities | None = None) -> AgentState:
        round_id = self._round_of[question]
        self.events.append(f"start:{question}")
        self._active[round_id] += 1
        self.max_active[round_id] = max(self.max_active[round_id], self._active[round_id])
        if self._active[round_id] >= self._expected[round_id]:
            self._gates[round_id].set()
        try:
            await asyncio.wait_for(self._gates[round_id].wait(), timeout=self.timeout)
            if capabilities is not None:
                for name, count in self.spend.get(question, {}).items():
                    for _ in range(count):
                        await capabilities.call_tool(name)
                        await asyncio.sleep(0)  # interleave with siblings
        finally:
            self._active[round_id] -= 1
        self.events.append(f"finish:{question}")
        return _ok_sub_state(f"{question} answer")


class ExplodingAnalyst:
    """Analyst stub whose ``boom`` question raises while siblings are in flight.

    The survivor questions spend one tool call each and then block until the
    test releases them *after* the crash, so a manager that cancels its
    siblings on failure (rather than letting them settle) leaves ``finished``
    empty and loses their counts.
    """

    def __init__(self, boom: str, expected: int) -> None:
        self.boom = boom
        self.expected = expected
        self.started: list[str] = []
        self.finished: list[str] = []
        self.all_started = asyncio.Event()
        self.exploded = asyncio.Event()
        self.release = asyncio.Event()

    async def __call__(self, question: str, capabilities: Capabilities | None = None) -> AgentState:
        self.started.append(question)
        if len(self.started) >= self.expected:
            self.all_started.set()
        await asyncio.wait_for(self.all_started.wait(), timeout=0.5)
        if question == self.boom:
            self.exploded.set()
            raise RuntimeError("analyst transport failed")
        assert capabilities is not None
        await capabilities.call_tool("query")
        # Still in flight when the sibling raises; the manager must let this
        # run settle instead of cancelling it.
        await asyncio.wait_for(self.release.wait(), timeout=1.0)
        self.finished.append(question)
        return _ok_sub_state(f"{question} answer")


def _services(
    llm: FakeManagerLLM,
    analyst: Callable[[str, Capabilities | None], Awaitable[AgentState]] | None = None,
    *,
    max_attempts: int = 2,
    max_parallelism: int = DEFAULT_MAX_PARALLELISM,
    table_names: list[str] | None = None,
    capabilities: CountingCapabilities | None = None,
) -> ManagerServices:
    return ManagerServices(
        llm=llm,
        run_analyst=analyst or StubAnalyst({}),
        max_attempts=max_attempts,
        max_parallelism=max_parallelism,
        table_names=table_names or ["orders", "order_items"],
        # The shared capability surface; the manager wraps it in a fresh
        # counting decorator per analyst run, so per-record counts stay exact.
        capabilities=capabilities,
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
    # Both runs start in sub-question order (the fan-out creates them in order).
    assert analyst.calls == ["Revenue by category?", "Top sellers?"]
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
    state = await _invoke(_services(llm, analyst, capabilities=counter))

    records = state["evidence"]
    assert [r.tool_calls for r in records] == [
        {"query": 2, "list_tables": 1},
        {"query": 1},
    ]
    # Attribution is complete: nothing the run spent is left unattributed.
    assert sum(sum(r.tool_calls.values()) for r in records) == sum(counter.snapshot().values())
    # Each run was handed its own counting surface (D2), never the shared one.
    assert len(analyst.surfaces) == 2
    assert all(surface is not counter for surface in analyst.surfaces)
    assert len({id(surface) for surface in analyst.surfaces}) == 2


async def test_failed_sub_analysis_still_carries_its_tool_calls() -> None:
    counter = _counter()
    analyst = CountingAnalyst(
        {"Bad question?": _failed_sub_state("LLM request timed out")},
        counter,
        {"Bad question?": [{"query": 3}]},
    )
    llm = FakeManagerLLM(raw="Bad question?")
    state = await _invoke(_services(llm, analyst, capabilities=counter))

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
# Parallel fan-out (M8, R1/R2) and exact per-run attribution (R3)
# ---------------------------------------------------------------------------


async def test_four_sub_analyses_run_concurrently() -> None:
    questions = ["Q0?", "Q1?", "Q2?", "Q3?"]
    # A run only finishes when all four are in flight, so a sequential loop
    # times out here instead of passing slowly.
    analyst = RoundAnalyst(dict.fromkeys(questions, 0))
    llm = FakeManagerLLM(raw="\n".join(questions))
    state = await _invoke(_services(llm, analyst))

    assert analyst.max_active[0] == len(questions)  # overlapping, not serial
    assert analyst.events[:4] == [f"start:{question}" for question in questions]
    # Evidence stays indexed by sub-question position.
    assert [r.sub_question for r in state["evidence"]] == questions
    assert [r.sub_index for r in state["evidence"]] == [0, 1, 2, 3]
    assert [r.answer for r in state["evidence"]] == [f"{q} answer" for q in questions]


async def test_parallelism_of_one_reproduces_serial_runs() -> None:
    questions = ["Q0?", "Q1?", "Q2?"]
    analyst = RoundAnalyst({question: index for index, question in enumerate(questions)})
    llm = FakeManagerLLM(raw="\n".join(questions))
    state = await _invoke(_services(llm, analyst, max_parallelism=1))

    # Never two runs in flight, and each run finishes before the next starts:
    # exactly the pre-M8 sequential loop.
    assert max(analyst.max_active.values()) == 1
    assert analyst.events == [
        event for question in questions for event in (f"start:{question}", f"finish:{question}")
    ]
    assert [r.sub_question for r in state["evidence"]] == questions


async def test_evidence_order_does_not_depend_on_completion_order() -> None:
    questions = ["Q0?", "Q1?", "Q2?"]
    gates = {question: asyncio.Event() for question in questions}
    all_started = asyncio.Event()
    started: list[str] = []
    finished: list[str] = []

    async def analyst(question: str, capabilities: Capabilities | None = None) -> AgentState:
        started.append(question)
        if len(started) == len(questions):
            all_started.set()
        await asyncio.wait_for(gates[question].wait(), timeout=1.0)
        finished.append(question)
        return _ok_sub_state(f"{question} answer")

    llm = FakeManagerLLM(raw="\n".join(questions))
    graph = build_manager_graph(_services(llm, analyst))

    run = asyncio.create_task(graph.ainvoke({"request": "Summarize sales."}))
    await asyncio.wait_for(all_started.wait(), timeout=1.0)
    for question in reversed(questions):  # release in reverse completion order
        gates[question].set()
    state = await asyncio.wait_for(run, timeout=1.0)

    assert finished == list(reversed(questions))  # runs really finished in reverse
    assert [r.sub_question for r in state["evidence"]] == questions
    assert [r.sub_index for r in state["evidence"]] == [0, 1, 2]
    assert [r.answer for r in state["evidence"]] == [f"{q} answer" for q in questions]


async def test_per_record_counts_are_exact_when_runs_interleave() -> None:
    questions = ["Q0?", "Q1?", "Q2?"]
    # The production call pattern: a flat four MCP calls per analyst run.
    pattern = {
        "search_metadata": 1,
        "list_tables": 1,
        "get_relationships": 1,
        "query": 1,
    }
    raw = StubCapability()
    shared = CountingCapabilities(raw)
    analyst = RoundAnalyst(dict.fromkeys(questions, 0), spend=dict.fromkeys(questions, pattern))
    llm = FakeManagerLLM(raw="\n".join(questions))
    state = await _invoke(_services(llm, analyst, capabilities=shared))

    records = state["evidence"]
    assert analyst.max_active[0] == len(questions)  # the runs really overlapped
    # Each record owns exactly its own run's calls — a snapshot/delta over the
    # shared counter would blur these because the calls interleave.
    assert [record.tool_calls for record in records] == [pattern for _ in questions]
    assert all(sum(record.tool_calls.values()) == 4 for record in records)
    attributed = sum(sum(record.tool_calls.values()) for record in records)
    assert attributed == 12
    assert attributed == sum(shared.snapshot().values()) == len(raw.calls)  # zero unattributed


async def test_follow_up_round_fans_out_under_the_same_limit() -> None:
    analyst = RoundAnalyst(
        {
            "Revenue by category?": 0,
            "What drove the drop?": 1,
            "Which seller?": 1,
        }
    )
    llm = FakeManagerLLM(
        raw="Revenue by category?",
        follow_up_raw="What drove the drop?\nWhich seller?",
        report="ok",
    )
    state = await _invoke(_services(llm, analyst))

    assert analyst.max_active[0] == 1  # the single sub-analysis round
    assert analyst.max_active[1] == 2  # the follow-up round overlapped too
    follow_ups = [record for record in state["evidence"] if record.is_follow_up]
    assert [record.sub_question for record in follow_ups] == [
        "What drove the drop?",
        "Which seller?",
    ]
    assert [record.sub_index for record in follow_ups] == [1, 2]  # numbered after it
    assert state["follow_up_rounds"] == 1


async def test_raising_sub_run_settles_siblings_then_propagates() -> None:
    questions = ["Q0?", "Q1?", "Q2?"]
    raw = StubCapability()
    shared = CountingCapabilities(raw)
    analyst = ExplodingAnalyst("Q1?", expected=len(questions))
    llm = FakeManagerLLM(raw="\n".join(questions))
    graph = build_manager_graph(_services(llm, analyst, capabilities=shared))

    run = asyncio.create_task(graph.ainvoke({"request": "Summarize sales."}))
    await asyncio.wait_for(analyst.exploded.wait(), timeout=1.0)
    # The survivors are still in flight when the exception is raised; release
    # them so they can settle (a cancelled sibling would never record this).
    analyst.release.set()
    with pytest.raises(RuntimeError, match="analyst transport failed"):
        await asyncio.wait_for(run, timeout=1.0)

    # Settled, not orphaned: both survivors ran to completion after the crash,
    # and the calls they spent are still in the run total the artifacts read.
    assert sorted(analyst.finished) == ["Q0?", "Q2?"]
    assert len(raw.calls) == 2
    assert sum(shared.snapshot().values()) == 2


# ---------------------------------------------------------------------------
# MANAGER_MAX_PARALLELISM setting
# ---------------------------------------------------------------------------


def test_manager_max_parallelism_defaults_to_four(monkeypatch) -> None:
    monkeypatch.delenv("MANAGER_MAX_PARALLELISM", raising=False)
    assert Settings(_env_file=None).manager_max_parallelism == DEFAULT_MAX_PARALLELISM == 4


def test_manager_max_parallelism_is_read_from_the_environment(monkeypatch) -> None:
    monkeypatch.setenv("MANAGER_MAX_PARALLELISM", "2")
    assert Settings(_env_file=None).manager_max_parallelism == 2


def test_manager_max_parallelism_must_allow_one_run(monkeypatch) -> None:
    monkeypatch.setenv("MANAGER_MAX_PARALLELISM", "0")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


async def test_run_manager_composes_the_parallelism_setting(monkeypatch) -> None:
    from app.config import get_settings  # noqa: PLC0415
    from app.manager import entrypoint  # noqa: PLC0415

    captured: dict = {}

    class _StubGraph:
        async def ainvoke(self, state, config=None):
            return {"status": ManagerStatus.COMPLETED}

    def _capture(services):
        captured["services"] = services
        return _StubGraph()

    monkeypatch.setenv("MANAGER_MAX_PARALLELISM", "2")
    monkeypatch.setattr(entrypoint, "create_manager_llm", FakeManagerLLM)
    monkeypatch.setattr(entrypoint, "build_manager_graph", _capture)
    get_settings.cache_clear()
    try:
        await entrypoint.run_manager("Summarize sales.")
    finally:
        get_settings.cache_clear()

    assert captured["services"].max_parallelism == 2


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
