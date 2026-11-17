"""Progress events for a management run (M8 ticket 04, spec R5/R10).

A manager run must be legible while it runs, not only when it returns: the
decomposed sub-questions, each analysis starting and finishing with its SQL,
rows and answer, the inspect decision, synthesis, the groundedness verdict and
the terminal outcome. These tests drive the real manager graph (and the real
``run_manager``) with a recording sink and assert:

* the manager stages arrive in pipeline order,
* ``seq`` is one monotonic sequence across the manager *and* every nested
  analyst run — no sub-run mints its own counter,
* nested analyst events and per-analysis events are attributable to their
  sub-question (index, question, follow-up flag), in completion order,
* a degraded inspect stage and a groundedness violation each emit their own
  event with the reason,
* every failure mode ends with a terminal failure carrying its reason,
* a raising sink changes nothing (R10), including from nested runs,
* a run that raises emits no terminal event: its stream simply ends (the
  documented contract).

Hermetic and fast: ``FakeLLM``/``StubCapabilities`` for the analyst graph,
``FakeManagerLLM`` for the manager, and explicit barriers (never sleeps) where
a test needs the fan-out to overlap.
"""

import asyncio
import json
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest

from app.agent.capabilities import Capabilities
from app.agent.graph import AgentServices, build_graph
from app.agent.llm import FakeLLM, LLMError
from app.agent.state import AgentState
from app.manager import entrypoint
from app.manager.graph import ManagerServices, build_manager_graph, manager_failure_reason
from app.manager.llm import FakeManagerLLM
from app.manager.state import ManagerState, ManagerStatus
from app.progress import ProgressEmitter, ProgressEvent, ProgressEventType
from tests.test_agent import StubCapabilities
from tests.test_manager_entrypoint import FakeCaps, FakeFullLLM
from tests.test_manager_graph import StubAnalyst, _failed_sub_state, _ok_sub_state

# The analyst vocabulary a nested sub-run emits for one clean, single-attempt
# run (M8 ticket 02), in pipeline order.
NESTED_ANALYST_TYPES = [
    ProgressEventType.RUN_STARTED,
    ProgressEventType.METADATA_RETRIEVED,
    ProgressEventType.SQL_GENERATED,
    ProgressEventType.SQL_VALIDATED,
    ProgressEventType.SQL_EXECUTED,
    ProgressEventType.ANALYSIS_COMPLETED,
    ProgressEventType.RUN_COMPLETED,
]

TERMINAL_TYPES = {ProgressEventType.RUN_COMPLETED, ProgressEventType.RUN_FAILED}


class RecordingSink:
    """Progress sink that keeps every event, in delivery order."""

    def __init__(self) -> None:
        self.events: list[ProgressEvent] = []

    def emit(self, event: ProgressEvent) -> None:
        self.events.append(event)

    def types(self) -> list[str]:
        return [event.type for event in self.events]

    def of_type(self, event_type: ProgressEventType) -> list[ProgressEvent]:
        return [event for event in self.events if event.type == event_type]

    def manager_stages(self) -> list[str]:
        """The manager's own pipeline events, in order (terminal included)."""
        return [
            event.type
            for event in self.events
            if event.type.startswith("manager.")
            or (event.type in TERMINAL_TYPES and event.index is None)
        ]


class RaisingSink:
    """Progress sink that fails on every event (an observability outage)."""

    def emit(self, event: ProgressEvent) -> None:
        raise RuntimeError("sink exploded")


def _emitting_runner(sql: str = "SELECT 1") -> Callable[..., Awaitable[AgentState]]:
    """An analyst runner that builds the real analyst graph on the child emitter.

    This mirrors the production composition (``app.manager.entrypoint``): the
    manager hands each sub-run the emitter it derived for it, and the sub-run's
    graph emits through that emitter instead of a counter of its own.
    """

    async def runner(
        question: str,
        capabilities: Capabilities | None = None,
        emitter: ProgressEmitter | None = None,
    ) -> AgentState:
        services = AgentServices(
            llm=FakeLLM(sql=sql, answer=f"answer for {question}"),
            capabilities=capabilities if capabilities is not None else StubCapabilities(),
            max_attempts=3,
            max_rows=100,
            emitter=emitter,
        )
        return await build_graph(services).ainvoke(
            {"question": question, "status": "planning", "attempts": 0}
        )

    return runner


def _services(
    llm: FakeManagerLLM,
    analyst: Callable[..., Awaitable[AgentState]],
    *,
    max_attempts: int = 2,
    sink: RecordingSink | RaisingSink | None = None,
) -> ManagerServices:
    return ManagerServices(
        llm=llm,
        run_analyst=analyst,
        max_attempts=max_attempts,
        table_names=["orders"],
        sink=sink,
    )


async def _invoke(services: ManagerServices, request: str = "Summarize sales.") -> ManagerState:
    graph = build_manager_graph(services)
    return await graph.ainvoke({"request": request})


def _terminal(sink: RecordingSink) -> ProgressEvent:
    """The run's terminal event (the last event of a non-raising run)."""
    assert sink.events[-1].type in TERMINAL_TYPES
    return sink.events[-1]


# ---------------------------------------------------------------------------
# A successful run: stage order, one sequence, per-analysis attribution
# ---------------------------------------------------------------------------


async def test_successful_run_emits_the_pipeline_in_stage_order() -> None:
    questions = ["Revenue by category?", "Top sellers?"]
    llm = FakeManagerLLM(raw="\n".join(questions))
    sink = RecordingSink()
    state = await _invoke(_services(llm, _emitting_runner(), sink=sink))

    assert state["status"] is ManagerStatus.COMPLETED
    # The manager's own events, in pipeline order. The analyses fan out, so
    # their started/finished pairs may interleave (completion order, not
    # question order) — every other stage is strictly ordered around them.
    per_analysis = {ProgressEventType.ANALYSIS_STARTED, ProgressEventType.ANALYSIS_FINISHED}
    assert [
        event_type for event_type in sink.manager_stages() if event_type not in per_analysis
    ] == [
        ProgressEventType.REQUEST_ACCEPTED,
        ProgressEventType.SUB_QUESTIONS_DECOMPOSED,
        ProgressEventType.INSPECTION_DECIDED,
        ProgressEventType.SYNTHESIS_STARTED,
        ProgressEventType.GROUNDEDNESS_VERDICT,
        ProgressEventType.RUN_COMPLETED,
    ]
    # One started and one finished event per sub-question, and both analyses
    # are over before the inspect decision is taken.
    assert sink.manager_stages().count(ProgressEventType.ANALYSIS_STARTED) == len(questions)
    assert sink.manager_stages().count(ProgressEventType.ANALYSIS_FINISHED) == len(questions)
    assert sink.events.index(sink.of_type(ProgressEventType.ANALYSIS_FINISHED)[-1]) < (
        sink.events.index(sink.of_type(ProgressEventType.INSPECTION_DECIDED)[0])
    )
    for index in range(len(questions)):
        started = [e for e in sink.of_type(ProgressEventType.ANALYSIS_STARTED) if e.index == index]
        finished = [
            e for e in sink.of_type(ProgressEventType.ANALYSIS_FINISHED) if e.index == index
        ]
        assert len(started) == len(finished) == 1
        assert started[0].seq < finished[0].seq
        # The nested analyst events of that run sit between its own pair.
        nested = [
            event
            for event in sink.events
            if event.index == index and event.type in set(NESTED_ANALYST_TYPES)
        ]
        assert [event.seq for event in nested] == sorted(event.seq for event in nested)
        assert started[0].seq < nested[0].seq < nested[-1].seq < finished[0].seq
    # Request accepted carries the request; decomposed carries the questions.
    first = sink.events[0]
    assert first.type == ProgressEventType.REQUEST_ACCEPTED
    assert first.question == "Summarize sales."
    decomposed = sink.of_type(ProgressEventType.SUB_QUESTIONS_DECOMPOSED)[0]
    assert decomposed.questions == questions
    # The inspect decision with no follow-up requested is explicit, not silent.
    decision = sink.of_type(ProgressEventType.INSPECTION_DECIDED)[0]
    assert decision.status == "none"
    assert decision.questions == []
    # The verdict and the terminal event.
    assert sink.of_type(ProgressEventType.GROUNDEDNESS_VERDICT)[0].status == "grounded"
    terminal = _terminal(sink)
    assert terminal.type == ProgressEventType.RUN_COMPLETED
    assert terminal.status == "completed"
    assert terminal.report == state["report"]
    assert ProgressEventType.RUN_FAILED not in sink.types()


async def test_every_event_is_one_monotonic_sequence_across_nested_runs() -> None:
    questions = ["Q0?", "Q1?", "Q2?"]
    llm = FakeManagerLLM(raw="\n".join(questions))
    sink = RecordingSink()
    await _invoke(_services(llm, _emitting_runner(), sink=sink))

    # One sequence for the whole run: manager events and the events of three
    # nested analyst runs share it, with no gap and no reset to 1.
    assert [event.seq for event in sink.events] == list(range(1, len(sink.events) + 1))
    # Every nested analyst event is attributable to its sub-question, and the
    # nested analyst vocabulary is complete for each index, in its own order.
    for index, question in enumerate(questions):
        nested = [
            event
            for event in sink.events
            if event.index == index and event.type in NESTED_ANALYST_TYPES
        ]
        assert [event.type for event in nested] == NESTED_ANALYST_TYPES
        assert {event.question for event in nested} == {question}
        assert all(event.is_follow_up is False for event in nested)
        # The nested terminal event carries its answer (attribution is not just
        # the label: the payload belongs to that run too).
        assert nested[-1].answer == f"answer for {question}"
    # Manager-level stage events carry no answer of their own.
    terminal = _terminal(sink)
    assert terminal.index is None
    assert terminal.answer is None


async def test_per_analysis_events_carry_their_sql_rows_and_answer() -> None:
    llm = FakeManagerLLM(raw="Revenue by category?\nTop sellers?")
    sink = RecordingSink()
    state = await _invoke(
        _services(llm, _emitting_runner("SELECT order_status FROM orders"), sink=sink)
    )

    started = sink.of_type(ProgressEventType.ANALYSIS_STARTED)
    finished = sink.of_type(ProgressEventType.ANALYSIS_FINISHED)
    assert [event.index for event in started] == [0, 1]
    assert [event.question for event in started] == ["Revenue by category?", "Top sellers?"]
    # Finished events arrive in completion order (which need not be question
    # order under the fan-out); each still names the analysis it belongs to.
    assert sorted(event.index for event in finished) == [0, 1]

    records = {record.sub_index: record for record in state["evidence"]}
    for event in finished:
        record = records[event.index]
        assert event.question == record.sub_question
        assert event.sql == record.sql
        assert "LIMIT 100" in (event.sql or "")  # the bounded query that ran
        assert event.row_count == len(record.rows)
        assert event.row_count == 1
        assert event.answer == record.answer == f"answer for {record.sub_question}"
        assert event.error is None
        assert event.is_follow_up is False


async def test_one_raising_sub_run_does_not_stop_the_other_analyses() -> None:
    """A failed analysis is recorded and reported; the others still finish."""
    scripts = {
        "Good question?": _ok_sub_state("fine"),
        "Bad question?": _failed_sub_state("LLM request timed out"),
    }
    llm = FakeManagerLLM(raw="Bad question?\nGood question?")
    sink = RecordingSink()
    state = await _invoke(_services(llm, StubAnalyst(scripts), sink=sink))

    assert state["status"] is ManagerStatus.COMPLETED  # partial failure, not fatal
    finished = sink.of_type(ProgressEventType.ANALYSIS_FINISHED)
    by_index = {event.index: event for event in finished}
    assert by_index[0].error == "LLM request timed out"
    assert by_index[0].answer is None
    assert by_index[0].row_count is None  # no rows: no row count is claimed
    assert by_index[1].error is None
    assert by_index[1].answer == "fine"


# ---------------------------------------------------------------------------
# Completion order and the follow-up round
# ---------------------------------------------------------------------------


async def test_analysis_events_arrive_in_completion_order_and_stay_attributable() -> None:
    """Overlapping runs interleave; each event still names its sub-question."""
    questions = ["Q0?", "Q1?"]
    runner = _emitting_runner()
    barrier = asyncio.Barrier(len(questions))

    async def overlapping(
        question: str,
        capabilities: Capabilities | None = None,
        emitter: ProgressEmitter | None = None,
    ) -> AgentState:
        # Both runs must be in flight before either proceeds, so a serial loop
        # times out instead of passing quietly.
        await asyncio.wait_for(barrier.wait(), timeout=1.0)
        return await runner(question, capabilities, emitter)

    llm = FakeManagerLLM(raw="\n".join(questions))
    sink = RecordingSink()
    await _invoke(_services(llm, overlapping, sink=sink))

    assert [event.seq for event in sink.events] == list(range(1, len(sink.events) + 1))
    # Two overlapping runs: their nested events interleave in the stream, which
    # is exactly why the index (not the position) is what a consumer groups by.
    for index, question in enumerate(questions):
        nested = [
            event
            for event in sink.events
            if event.type in NESTED_ANALYST_TYPES and event.index is not None
        ]
        assert {event.index for event in nested} == {0, 1}
        labelled = [event for event in nested if event.index == index]
        assert [event.question for event in labelled] == [question] * len(NESTED_ANALYST_TYPES)
    # Each analysis still has exactly one started and one finished event.
    assert len(sink.of_type(ProgressEventType.ANALYSIS_STARTED)) == 2
    assert len(sink.of_type(ProgressEventType.ANALYSIS_FINISHED)) == 2


async def test_follow_up_round_events_are_marked_and_numbered_after() -> None:
    questions = ["Revenue by category?", "Top sellers?"]
    follow_ups = ["What drove the drop?", "Which seller?"]
    llm = FakeManagerLLM(
        raw="\n".join(questions),
        follow_up_raw="\n".join(follow_ups),
        report="ok",
    )
    sink = RecordingSink()
    state = await _invoke(_services(llm, _emitting_runner(), sink=sink))

    decision = sink.of_type(ProgressEventType.INSPECTION_DECIDED)[0]
    assert decision.status == "follow_ups"
    assert decision.questions == follow_ups
    started = sink.of_type(ProgressEventType.ANALYSIS_STARTED)
    # Two sub-analyses, then the follow-up round (numbered after them).
    assert [(event.index, event.is_follow_up) for event in started] == [
        (0, False),
        (1, False),
        (2, True),
        (3, True),
    ]
    assert [event.question for event in started[2:]] == follow_ups
    # Nested analyst events of the follow-up runs say so too.
    follow_up_nested = [
        event for event in sink.events if event.type in NESTED_ANALYST_TYPES and event.is_follow_up
    ]
    assert {event.index for event in follow_up_nested} == {2, 3}
    assert state["follow_up_rounds"] == 1
    assert [e.seq for e in sink.events] == list(range(1, len(sink.events) + 1))


# ---------------------------------------------------------------------------
# Degraded inspect stage and the groundedness verdict
# ---------------------------------------------------------------------------


class InspectFailingLLM(FakeManagerLLM):
    """Decompose and synthesize succeed; every inspect call raises ``LLMError``."""

    async def inspect(self, request: str, evidence: str) -> str:
        self.inspect_calls.append((request, evidence))
        raise LLMError("HTTP 503 service unavailable")


async def test_unusable_inspect_output_emits_a_degraded_decision_with_its_reason() -> None:
    over_cap = "\n".join(f"Q{i}?" for i in range(3))
    llm = FakeManagerLLM(raw="Revenue?", follow_up_raw=over_cap, report="ok")
    sink = RecordingSink()
    state = await _invoke(_services(llm, StubAnalyst({"Revenue?": _ok_sub_state()}), sink=sink))

    # Degraded, not failed: the run still reports.
    assert state["status"] is ManagerStatus.COMPLETED
    decision = sink.of_type(ProgressEventType.INSPECTION_DECIDED)[0]
    assert decision.status == "degraded"
    assert "hard cap is 2" in (decision.reason or "")
    assert "hard cap is 2" in (state["inspect_error"] or "")
    assert decision.questions is None  # a degradation decides nothing
    assert _terminal(sink).type == ProgressEventType.RUN_COMPLETED
    assert _terminal(sink).report == state["report"]


async def test_persistent_inspect_failure_degrades_with_its_reason() -> None:
    llm = InspectFailingLLM(raw="Revenue?", report="ok")
    sink = RecordingSink()
    state = await _invoke(_services(llm, StubAnalyst({"Revenue?": _ok_sub_state()}), sink=sink))

    assert state["status"] is ManagerStatus.COMPLETED
    decision = sink.of_type(ProgressEventType.INSPECTION_DECIDED)[0]
    assert decision.status == "degraded"
    assert "inspect LLM call failed" in (decision.reason or "")
    assert "503" in (decision.reason or "")
    assert _terminal(sink).type == ProgressEventType.RUN_COMPLETED


async def test_groundedness_violation_emits_the_verdict_and_never_the_report() -> None:
    fabricated = "Revenue was 999.99."
    llm = FakeManagerLLM(raw="Revenue?", report=fabricated)
    sink = RecordingSink()
    state = await _invoke(_services(llm, StubAnalyst({"Revenue?": _ok_sub_state()}), sink=sink))

    assert state["status"] is ManagerStatus.FAILED
    assert state["report"] is None
    verdict = sink.of_type(ProgressEventType.GROUNDEDNESS_VERDICT)[0]
    assert verdict.status == "violated"
    assert "999.99" in (verdict.reason or "")
    assert verdict.report is None
    # Never ship a fabricated report — not to the caller, and not to a consumer
    # of the stream either.
    assert all(event.report is None for event in sink.events)
    terminal = _terminal(sink)
    assert terminal.type == ProgressEventType.RUN_FAILED
    assert terminal.reason == manager_failure_reason(state)
    assert "999.99" in (terminal.reason or "")


# ---------------------------------------------------------------------------
# The child emitter: one counter, authoritative labels
# ---------------------------------------------------------------------------


def test_child_emitter_continues_the_run_sequence_and_labels_its_events() -> None:
    sink = RecordingSink()
    parent = ProgressEmitter(sink)
    parent.emit(ProgressEventType.REQUEST_ACCEPTED, question="Summarize sales.")
    child = parent.child(index=2, question="Follow-up?", is_follow_up=True)

    child.emit(ProgressEventType.RUN_STARTED, question="Follow-up?", attempt=1)
    # A nested producer cannot relabel its own event: attribution is the point.
    child.emit(ProgressEventType.RUN_STARTED, question="wrong?", index=99)
    parent.emit(ProgressEventType.SYNTHESIS_STARTED)

    assert [event.seq for event in sink.events] == [1, 2, 3, 4]
    assert parent.seq == child.seq == 4
    nested = sink.events[1]
    assert (nested.index, nested.question, nested.is_follow_up) == (2, "Follow-up?", True)
    # The labels won over what the nested producer tried to set.
    assert (sink.events[2].index, sink.events[2].question) == (2, "Follow-up?")
    # A manager-level event keeps no index.
    assert sink.events[3].index is None


# ---------------------------------------------------------------------------
# Terminal failures: one event per failure mode, with its reason
# ---------------------------------------------------------------------------


async def test_decomposition_failure_emits_a_terminal_failure() -> None:
    llm = FakeManagerLLM(raw="```")  # parses to nothing -> DecompositionError
    sink = RecordingSink()
    state = await _invoke(_services(llm, StubAnalyst({}), max_attempts=3, sink=sink))

    assert sink.manager_stages() == [
        ProgressEventType.REQUEST_ACCEPTED,
        ProgressEventType.RUN_FAILED,
    ]
    terminal = _terminal(sink)
    assert terminal.status == "failed"
    assert terminal.reason == manager_failure_reason(state)
    assert "no sub-questions" in (terminal.reason or "")
    # Nothing ran, so no analysis or synthesis event was invented.
    assert sink.of_type(ProgressEventType.ANALYSIS_STARTED) == []
    assert sink.of_type(ProgressEventType.SUB_QUESTIONS_DECOMPOSED) == []


async def test_exhausted_decompose_retries_emit_retry_then_terminal_failure() -> None:
    llm = FakeManagerLLM(raw="Revenue?", llm_error=LLMError("HTTP 429 too many requests"))
    sink = RecordingSink()
    state = await _invoke(_services(llm, StubAnalyst({}), max_attempts=2, sink=sink))

    assert len(llm.calls) == 2
    # One run start (not one per attempt), one retry with its reason, one
    # terminal failure with the same reason.
    assert sink.types().count(ProgressEventType.REQUEST_ACCEPTED) == 1
    retry = sink.of_type(ProgressEventType.RUN_RETRYING)[0]
    assert retry.reason == "HTTP 429 too many requests"
    assert retry.attempt == 1
    terminal = _terminal(sink)
    assert terminal.type == ProgressEventType.RUN_FAILED
    assert terminal.reason == "HTTP 429 too many requests"
    assert terminal.reason == manager_failure_reason(state)


async def test_all_sub_analyses_failing_emits_a_terminal_failure() -> None:
    scripts = {
        "One?": _failed_sub_state("query failed"),
        "Two?": _failed_sub_state("syntax error"),
    }
    llm = FakeManagerLLM(raw="One?\nTwo?")
    sink = RecordingSink()
    state = await _invoke(_services(llm, StubAnalyst(scripts), sink=sink))

    assert state["status"] is ManagerStatus.FAILED
    # Both analyses reported their own error before the run failed.
    assert [event.error for event in sink.of_type(ProgressEventType.ANALYSIS_FINISHED)] == [
        "query failed",
        "syntax error",
    ]
    terminal = _terminal(sink)
    assert terminal.type == ProgressEventType.RUN_FAILED
    assert terminal.reason == manager_failure_reason(state)
    assert "sub-question 0 (One?)" in (terminal.reason or "")
    # No synthesis or groundedness event: the run never got that far.
    assert sink.of_type(ProgressEventType.SYNTHESIS_STARTED) == []
    assert sink.of_type(ProgressEventType.GROUNDEDNESS_VERDICT) == []


# ---------------------------------------------------------------------------
# Fail-open (R10) and the no-terminal-on-crash contract
# ---------------------------------------------------------------------------


async def test_raising_sink_leaves_the_run_unchanged() -> None:
    questions = ["Revenue by category?", "Top sellers?"]
    llm = FakeManagerLLM(raw="\n".join(questions))
    analyst = _emitting_runner()
    plain = await _invoke(_services(llm, analyst))
    guarded = await _invoke(
        _services(FakeManagerLLM(raw="\n".join(questions)), analyst, sink=RaisingSink())
    )

    assert (guarded["status"], guarded["report"], guarded.get("groundedness_error")) == (
        plain["status"],
        plain["report"],
        plain.get("groundedness_error"),
    )
    assert [record.answer for record in guarded["evidence"]] == [
        record.answer for record in plain["evidence"]
    ]
    assert [record.tool_calls for record in guarded["evidence"]] == [
        record.tool_calls for record in plain["evidence"]
    ]


async def test_a_raising_sub_run_emits_no_terminal_event() -> None:
    """The documented contract: a run that raises ends its stream, nothing more."""

    async def exploding(
        question: str,
        capabilities: Capabilities | None = None,
        emitter: ProgressEmitter | None = None,
    ) -> AgentState:
        raise RuntimeError("analyst transport failed")

    llm = FakeManagerLLM(raw="Revenue?")
    sink = RecordingSink()
    graph = build_manager_graph(_services(llm, exploding, sink=sink))
    with pytest.raises(RuntimeError, match="analyst transport failed"):
        await graph.ainvoke({"request": "Summarize sales."})

    # The request was accepted and the analysis started; nothing claims the run
    # finished, because it did not.
    assert sink.types()[0] == ProgressEventType.REQUEST_ACCEPTED
    assert sink.types()[-1] == ProgressEventType.ANALYSIS_STARTED
    assert ProgressEventType.RUN_COMPLETED not in sink.types()
    assert ProgressEventType.RUN_FAILED not in sink.types()
    # The stream is still one ordered sequence up to where it stopped.
    assert [event.seq for event in sink.events] == list(range(1, len(sink.events) + 1))


async def test_run_without_a_sink_emits_nothing() -> None:
    """The library/CLI path pays nothing: no sink, no events, no cost."""
    seen: list[ProgressEmitter] = []

    async def capturing(
        question: str,
        capabilities: Capabilities | None = None,
        emitter: ProgressEmitter | None = None,
    ) -> AgentState:
        assert emitter is not None
        seen.append(emitter)
        return _ok_sub_state()

    state = await _invoke(_services(FakeManagerLLM(raw="Revenue?"), capturing))

    assert state["status"] is ManagerStatus.COMPLETED
    # The sub-run's emitter is the child of a disabled manager emitter: nothing
    # is constructed, numbered or delivered.
    assert len(seen) == 1
    assert seen[0].enabled is False
    assert seen[0].emit(ProgressEventType.ANALYSIS_COMPLETED, answer="x") is None
    assert seen[0].seq == 0


# ---------------------------------------------------------------------------
# The entrypoint: run_manager(..., sink=...) streams the whole composition
# ---------------------------------------------------------------------------


@pytest.fixture
def _fake_composition(monkeypatch: pytest.MonkeyPatch) -> None:
    """Install the deterministic manager composition used by the entrypoint."""
    monkeypatch.setattr(entrypoint, "create_manager_llm", FakeFullLLM)
    monkeypatch.setattr(entrypoint, "MCPCapabilities", FakeCaps)


async def test_run_manager_streams_the_pipeline_as_one_sequence(_fake_composition) -> None:
    sink = RecordingSink()
    result = await entrypoint.run_manager("Summarize sales.", sink=sink)

    assert result.status == "completed"
    assert sink.events[0].type == ProgressEventType.REQUEST_ACCEPTED
    assert [event.seq for event in sink.events] == list(range(1, len(sink.events) + 1))
    # The real composition (manager graph → analyst graph) put the nested
    # analyst events on the same stream, labelled with their sub-question.
    nested = [
        event
        for event in sink.events
        if event.type in NESTED_ANALYST_TYPES and event.index is not None
    ]
    assert {event.type for event in nested} == set(NESTED_ANALYST_TYPES)
    assert [event.index for event in nested] == [0] * len(nested)
    assert {event.question for event in nested} == set(result.sub_questions)
    # And the run still ends with the terminal event carrying the report.
    terminal = sink.events[-1]
    assert terminal.type == ProgressEventType.RUN_COMPLETED
    assert terminal.index is None
    assert terminal.report == result.report


async def test_run_manager_with_a_raising_sink_matches_no_sink(_fake_composition) -> None:
    plain = await entrypoint.run_manager("Summarize sales.")
    guarded = await entrypoint.run_manager("Summarize sales.", sink=RaisingSink())

    assert (guarded.status, guarded.report, guarded.error, guarded.report_attempt) == (
        plain.status,
        plain.report,
        plain.error,
        plain.report_attempt,
    )
    assert guarded.sub_questions == plain.sub_questions
    assert guarded.tool_calls == plain.tool_calls
    assert [record.answer for record in guarded.evidence] == [
        record.answer for record in plain.evidence
    ]


async def test_run_manager_artifacts_are_unchanged_by_a_raising_sink(
    _fake_composition, tmp_path: Path
) -> None:
    plain_dir = tmp_path / "plain"
    guarded_dir = tmp_path / "guarded"
    await entrypoint.run_manager("Summarize sales.", plain_dir)
    await entrypoint.run_manager("Summarize sales.", guarded_dir, sink=RaisingSink())

    for name in ("report.md", "evidence.json"):
        assert (guarded_dir / name).read_bytes() == (plain_dir / name).read_bytes()
    # The artifact is still a valid record with full call attribution.
    payload = json.loads((guarded_dir / "evidence.json").read_text(encoding="utf-8"))
    assert payload["status"] == "completed"
    assert payload["unattributed_tool_calls"] == 0


def test_cli_output_is_unchanged_by_a_raising_sink(
    _fake_composition, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    entrypoint.main(["Summarize sales."])
    plain = capsys.readouterr().out

    real_run_manager = entrypoint.run_manager

    async def with_a_broken_sink(
        request: str, out_dir: str | Path | None = None, **kwargs: object
    ) -> object:
        return await real_run_manager(request, out_dir, sink=RaisingSink())

    monkeypatch.setattr(entrypoint, "run_manager", with_a_broken_sink)
    entrypoint.main(["Summarize sales."])
    guarded = capsys.readouterr().out

    assert guarded == plain
    assert "Status: completed" in guarded
