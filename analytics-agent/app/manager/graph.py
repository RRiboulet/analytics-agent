"""LangGraph workflow for the analytics manager (M7).

The manager composes analyst runs (D009): it decomposes one high-level
management request into at most 4 concrete sub-questions, runs each through a
full grounded, read-only analyst run, accumulates the evidence, then passes
the evidence through the inspect stage (M7.5), which may request one bounded
follow-up round (at most 2 questions, drill-down/anomaly investigation)
before synthesis. The manager never touches the database itself — the
decompose stage gets only a table-name schema hint, and every evidence item
is produced by an analyst run.

Failure policy (kept deliberately simple, per D009's fixed pipeline):

* ``LLMError`` on the decompose, inspect or synthesis call (transient:
  timeout/transport/HTTP/rate limits — the common hosted-model failure mode)
  retries up to a bounded attempt count. The attempt budget is shared by
  all retryable stages, so it stays a single hard bound.
* ``DecompositionError`` (unusable decompose output) fails immediately: at
  temperature 0 the same input reproduces the same invalid output.
* A failing sub-analysis is recorded and the run continues; only when *all*
  sub-analyses fail does the manager fail.
* The inspect stage is optional by intent (a successful run can need no
  follow-up): an ``LLMError`` that exhausts the retry budget, or unusable
  output (``FollowUpError``), degrades to no follow-up — recorded in
  ``inspect_error`` — and the run proceeds to synthesis. The sub-analysis
  evidence is complete, so a report is still achievable and never blocked
  on this enhancement stage.
* A groundedness violation in the synthesized report (a number that appears
  in no evidence result set) fails the run and the report is never stored —
  never ship a fabricated report. No retry: deterministic output at
  temperature 0 would reproduce the same violation.

Exceptions raised by ``run_analyst`` propagate (mirroring the agent's
``call_tool`` contract): the analyst graph reports its own failures in state.
When a sub-run does raise, every in-flight sibling is allowed to settle before
the first exception is re-raised, so no task is orphaned and the calls the
aborted round spent are still counted (M8, R4).

Parallelism (M8, D1): the sub-analyses and the single follow-up round fan out
as concurrent analyst runs under a semaphore of ``max_parallelism``; results
are collected in question order, so evidence indexing never depends on which
run finished first. Per-run call attribution is exact under that fan-out
because each run gets its own counting decorator around the shared capability
(D2) — a snapshot/delta over the shared counter would double-count once two
runs overlap.

Progress (M8, R5): given ``ManagerServices.sink``, the graph streams its
pipeline — request accepted, the decomposed sub-questions, each analysis
starting and finishing (in completion order, attributed to its sub-question by
``index``), the inspect decision, synthesis started, the groundedness verdict,
and the terminal event with the report or the failure reason. Each nested
analyst run emits through a child emitter derived from this run's emitter, so
the whole run is *one* sequence of events and a consumer can order it as it
arrives. Emission is fail-open and absent by default: without a sink nothing is
constructed and the run is exactly what it was before.

The terminal event is emitted by one place (the ``finish`` node) from the final
state, so every termination path reports the same way. A run that *raises* — an
MCP transport failure inside a sub-run, say — reaches no terminal node and
emits no terminal event: its stream just ends, and a consumer treats the end of
the stream as terminal.
"""

import asyncio
import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langgraph.graph import END, START, StateGraph

from app.agent.capabilities import Capabilities, CountingCapabilities
from app.agent.llm import LLMError
from app.agent.state import AgentState
from app.manager.decompose import DecompositionError, decompose_request
from app.manager.evidence import EvidenceRecord
from app.manager.inspect import FollowUpError, parse_follow_up_questions
from app.manager.llm import ManagerLLM
from app.manager.state import ManagerState, ManagerStatus
from app.manager.synthesize import format_evidence, groundedness_violation
from app.progress import ProgressEmitter, ProgressEventType, ProgressSink

# Node identifiers.
DECOMPOSE = "decompose"
RUN_SUB_ANALYSES = "run_sub_analyses"
INSPECT = "inspect"
RUN_FOLLOW_UPS = "run_follow_ups"
SYNTHESIZE = "synthesize"
RETRY = "retry"
FAIL = "fail"
FINISH = "finish"

# Edge identifiers returned by conditional routers.
SUB_ANALYSES_EDGE = "sub_analyses"
INSPECT_EDGE = "inspect"
FOLLOW_UPS_EDGE = "follow_ups"
SYNTHESIZE_EDGE = "synthesize"
RETRY_EDGE = "retry"
FAIL_EDGE = "fail"
FINISH_EDGE = "finish"

# Version-controlled, curated table names — the DB-free schema hint for the
# decompose stage (the manager itself never queries the database, D009).
_SEED_PATH = Path(__file__).resolve().parents[1] / "metadata_seed.json"


def _table_names_from_seed() -> list[str]:
    """Derive the table-name schema hint from the curated metadata seed."""
    seed = json.loads(_SEED_PATH.read_text(encoding="utf-8"))
    return list(seed["tables"])


# Default fan-out width (spec D1); ``MANAGER_MAX_PARALLELISM`` overrides it at
# the composition root.
DEFAULT_MAX_PARALLELISM = 4

# One analyst run's outcome: its final state plus the MCP calls it spent.
_AnalysisOutcome = tuple[AgentState, dict[str, int]]


def manager_failure_reason(state: ManagerState) -> str | None:
    """Why a manager run failed, or ``None`` when nothing failed.

    The single definition of a run's failure reason: the terminal progress
    event and the caller-facing ``ManagerRunResult.error`` both come from here,
    so the live view and the caller can never disagree. Precedence follows the
    pipeline: a decomposition failure happened before anything ran, a
    groundedness violation before a report could be cleared, then a model
    failure, then the first sub-analysis error.
    """
    if state.get("decomposition_error"):
        return state["decomposition_error"]
    if state.get("groundedness_error"):
        return state["groundedness_error"]
    if state.get("llm_error"):
        return state["llm_error"]
    if state.get("status") is ManagerStatus.FAILED and state.get("sub_analysis_errors"):
        return state["sub_analysis_errors"][0]
    return None


@dataclass
class ManagerServices:
    """Runtime dependencies injected into the manager graph."""

    llm: ManagerLLM
    # Runs one grounded analyst sub-analysis and returns its final state.
    # The second argument is the capability surface for that sub-run: in
    # production a fresh counting decorator around ``capabilities``, so the
    # run's MCP calls are attributable without a snapshot/delta (D2). It is
    # ``None`` when no shared surface was injected (a caller that does not
    # care about cost). The third is the progress emitter for that sub-run —
    # a child of this run's emitter, so the nested events continue the run's
    # single sequence and are labelled with its sub-question (M8, R5). It is
    # never ``None`` from the graph, but a runner may ignore it.
    run_analyst: Callable[[str, Capabilities | None, ProgressEmitter], Awaitable[AgentState]]
    # Bounded retries for transient LLM failures; the budget is shared by
    # the retryable stages (decompose / inspect / synthesize).
    max_attempts: int = 2
    # How many analyst runs may be in flight at once (MANAGER_MAX_PARALLELISM).
    # 1 reproduces the old sequential behavior exactly.
    max_parallelism: int = DEFAULT_MAX_PARALLELISM
    # The shared read-only capability surface the fan-out runs through; each
    # run is wrapped in its own counter. Absent by default, so the graph — and
    # every caller that does not care about cost — behaves exactly as before.
    capabilities: Capabilities | None = None
    # Optional progress sink (M8, R5). Absent by default, so every existing
    # caller — CLI, library, tests — runs with no emission and no cost.
    sink: ProgressSink | None = None
    # Table-name schema hint; defaults to the curated metadata seed.
    table_names: list[str] = field(default_factory=_table_names_from_seed)


def build_manager_graph(services: ManagerServices) -> Any:
    """Compile the manager state machine.

    The compiled graph serves exactly one run: its progress sink and event
    sequence counter bind here, at construction. Build a graph per invocation
    (D10) — a graph cached across invocations would fuse two runs into one
    indistinguishable stream (nested analyst events included) and the second
    run's events would never reach its own consumer.
    """
    # One emitter per manager run: it owns the run's sequence counter and is a
    # no-op when no sink was injected. Every nested analyst run emits through a
    # child of it (same sink, same counter), so the run is one ordered stream.
    progress = ProgressEmitter(services.sink)

    async def _decompose(state: ManagerState) -> dict[str, Any]:
        update: dict[str, Any] = {"status": ManagerStatus.DECOMPOSING}
        if not state.get("attempts"):
            # The run's first event, emitted once: a retry re-enters this node.
            progress.emit(ProgressEventType.REQUEST_ACCEPTED, question=state["request"])
        try:
            questions = await decompose_request(
                services.llm, state["request"], services.table_names
            )
        except LLMError as error:
            # No decomposed event: the failure surfaces as the terminal event
            # with its reason, after the retry budget is spent.
            return {
                **update,
                "sub_questions": [],
                "llm_error": str(error),
                "decomposition_error": None,
                "retry_stage": DECOMPOSE,
            }
        except DecompositionError as error:
            return {
                **update,
                "sub_questions": [],
                "decomposition_error": str(error),
                "llm_error": None,
            }
        progress.emit(
            ProgressEventType.SUB_QUESTIONS_DECOMPOSED,
            questions=list(questions),
            attempt=state.get("attempts", 1),
        )
        return {
            **update,
            "sub_questions": questions,
            "llm_error": None,
            "decomposition_error": None,
        }

    async def _analyse(question: str, emitter: ProgressEmitter) -> _AnalysisOutcome:
        """Run one analyst sub-analysis, attributing exactly its MCP calls.

        Each run gets a fresh counting decorator around the shared capability
        surface, so the record owns precisely the calls its own run spent —
        including calls from any retry inside it. That stays exact when runs
        overlap, which a snapshot/delta over the shared counter would not:
        two concurrent runs would each absorb the other's calls.

        Without a shared surface (a library caller that does not care about
        cost) the attribution is simply empty. ``emitter`` is the child of this
        run's emitter, so the nested analyst events land on the same stream.
        """
        shared = services.capabilities
        surface = CountingCapabilities(shared) if shared is not None else None
        sub_state = await services.run_analyst(question, surface, emitter)
        return sub_state, surface.snapshot() if surface is not None else {}

    async def _analyse_all(
        questions: Sequence[str], *, start_index: int = 0, is_follow_up: bool = False
    ) -> list[EvidenceRecord]:
        """Fan the questions out, bounded, and return records in input order.

        ``asyncio.gather`` is the fan-out: every run is started before any is
        awaited, and it returns one record per question in *input* order, so
        evidence indexing cannot depend on completion order.

        Each run emits its own ``analysis.started`` and ``analysis.finished``
        events as it starts and completes — completion order, deliberately,
        because that is what a live view is watching — stamped with the
        sub-question's ``index``, so an order-independent consumer can still
        put each event where it belongs.

        Exceptions are collected (``return_exceptions=True``) rather than
        cancelling siblings: every in-flight run settles, then the first
        exception — by question order, so it is deterministic — is re-raised.
        A crash therefore still propagates (the ``run_analyst`` contract)
        without orphaning a task or discarding the calls already spent. The
        crashing run reports no ``analysis.finished`` event: its stream, and
        the run's, simply ends without a terminal event.
        """
        # A semaphore of 0 would deadlock, so clamp the floor here too rather
        # than trusting every constructor (the setting already validates it).
        semaphore = asyncio.Semaphore(max(1, services.max_parallelism))

        async def _bounded(index: int, question: str) -> EvidenceRecord:
            async with semaphore:
                # The child emitter stamps the nested analyst events with this
                # sub-question and shares the run's counter, so nested events
                # cannot lose their attribution or restart numbering.
                child = progress.child(index=index, question=question, is_follow_up=is_follow_up)
                progress.emit(
                    ProgressEventType.ANALYSIS_STARTED,
                    index=index,
                    question=question,
                    is_follow_up=is_follow_up,
                )
                sub_state, tool_calls = await _analyse(question, child)
                record = EvidenceRecord.from_agent_state(
                    index, question, sub_state, is_follow_up=is_follow_up, tool_calls=tool_calls
                )
                progress.emit(
                    ProgressEventType.ANALYSIS_FINISHED,
                    index=index,
                    question=question,
                    is_follow_up=is_follow_up,
                    sql=record.sql,
                    row_count=len(record.rows) if record.rows is not None else None,
                    answer=record.answer,
                    error=record.error,
                )
                return record

        outcomes = await asyncio.gather(
            *(
                _bounded(start_index + offset, question)
                for offset, question in enumerate(questions)
            ),
            return_exceptions=True,
        )
        records: list[EvidenceRecord] = []
        for outcome in outcomes:
            if isinstance(outcome, BaseException):
                raise outcome
            records.append(outcome)
        return records

    async def _run_sub_analyses(state: ManagerState) -> dict[str, Any]:
        questions = list(state["sub_questions"])
        # Parallel by D1; evidence still indexed by sub-question position.
        records = await _analyse_all(questions)
        errors = [
            f"sub-question {record.sub_index} ({record.sub_question}): {record.error}"
            for record in records
            if record.error
        ]
        all_failed = bool(records) and all(record.error is not None for record in records)
        return {
            "evidence": records,
            "sub_analysis_errors": errors,
            "status": ManagerStatus.FAILED if all_failed else ManagerStatus.COMPLETED,
        }

    async def _inspect(state: ManagerState) -> dict[str, Any]:
        """Decide the single bounded follow-up round (M7.5).

        The model reviews the accumulated evidence and may request 0..2
        follow-up questions; the decision is validated deterministically by
        ``parse_follow_up_questions``. A transient ``LLMError`` retries
        bounded (shared budget); exhausting the budget, or unusable output,
        degrades to no follow-up (recorded in ``inspect_error``) instead of
        failing the run — the report stays achievable from the sub-analysis
        evidence, and this stage is optional by intent (D009).
        """
        update: dict[str, Any] = {"status": ManagerStatus.INSPECTING}
        try:
            raw = await services.llm.inspect(
                state["request"], format_evidence(state.get("evidence", []))
            )
        except LLMError as error:
            if state.get("attempts", 1) < services.max_attempts:
                return {
                    **update,
                    "llm_error": str(error),
                    "retry_stage": INSPECT,
                }
            reason = f"inspect LLM call failed: {error}"
            # Degraded, not failed: the follow-up round is an enhancement and
            # the evidence is already complete. The event carries the reason,
            # and an explicit empty ``questions`` so a consumer can read the
            # decision uniformly (no follow-ups) on every branch.
            progress.emit(
                ProgressEventType.INSPECTION_DECIDED,
                status="degraded",
                reason=reason,
                questions=[],
                attempt=state.get("attempts", 1),
            )
            return {
                **update,
                "llm_error": None,
                "inspect_error": reason,
                "follow_up_questions": [],
                "retry_stage": INSPECT,
            }
        try:
            questions = parse_follow_up_questions(raw)
        except FollowUpError as error:
            # Unusable output: deterministic at temperature 0, retry cannot
            # help. Degrade to no follow-up — the caps are hard (D009) and
            # guessing which questions to keep is not the manager's job.
            progress.emit(
                ProgressEventType.INSPECTION_DECIDED,
                status="degraded",
                reason=str(error),
                questions=[],
                attempt=state.get("attempts", 1),
            )
            return {
                **update,
                "inspect_error": str(error),
                "follow_up_questions": [],
                "llm_error": None,
            }
        progress.emit(
            ProgressEventType.INSPECTION_DECIDED,
            status="follow_ups" if questions else "none",
            questions=list(questions),
            attempt=state.get("attempts", 1),
        )
        return {
            **update,
            "follow_up_questions": questions,
            "inspect_error": None,
            "llm_error": None,
            "retry_stage": INSPECT,
        }

    async def _run_follow_ups(state: ManagerState) -> dict[str, Any]:
        """Run the single follow-up round's questions through the analyst.

        Exactly one round by construction (the graph never loops back to
        inspect), fanned out under the same bounded parallelism as the
        sub-analyses (R2). Follow-up records are appended to the evidence,
        marked ``is_follow_up``, and number after the sub-analyses; a
        follow-up failure is recorded and the round still proceeds to
        synthesis.
        """
        questions = list(state.get("follow_up_questions", []))
        evidence = list(state.get("evidence", []))
        start_index = len(evidence)
        records = await _analyse_all(questions, start_index=start_index, is_follow_up=True)
        evidence.extend(records)
        errors = [
            f"follow-up {record.sub_index - start_index} ({record.sub_question}): {record.error}"
            for record in records
            if record.error
        ]
        return {
            "evidence": evidence,
            "follow_up_errors": errors,
            "follow_up_rounds": 1,
            "status": ManagerStatus.COMPLETED,
        }

    async def _retry(state: ManagerState) -> dict[str, Any]:
        # The retry carries the reason the attempt was rejected, so a consumer
        # sees why the pipeline went around again instead of guessing.
        progress.emit(
            ProgressEventType.RUN_RETRYING,
            attempt=state.get("attempts", 1),
            reason=manager_failure_reason(state),
        )
        return {"attempts": state.get("attempts", 1) + 1, "status": ManagerStatus.RETRYING}

    async def _fail(state: ManagerState) -> dict[str, Any]:
        return {"status": ManagerStatus.FAILED}

    async def _finish(state: ManagerState) -> dict[str, Any]:
        """Emit the run's terminal event from its final state.

        One place for every termination path, so the completed case (the
        report) and the failed cases (decomposition error, exhausted retries,
        every sub-analysis failing, a groundedness violation) report through
        the same two event types with the same reason definition. A run that
        *raises* never reaches this node, so it emits no terminal event at all —
        the stream simply ends.
        """
        status = state.get("status")
        if status is ManagerStatus.FAILED:
            progress.emit(
                ProgressEventType.RUN_FAILED,
                status=str(ManagerStatus.FAILED),
                reason=manager_failure_reason(state),
            )
        else:
            progress.emit(
                ProgressEventType.RUN_COMPLETED,
                status=str(status),
                report=state.get("report"),
            )
        return {}

    async def _synthesize(state: ManagerState) -> dict[str, Any]:
        update: dict[str, Any] = {"status": ManagerStatus.SYNTHESIZING}
        progress.emit(
            ProgressEventType.SYNTHESIS_STARTED,
            attempt=state.get("attempts", 1),
        )
        evidence = state.get("evidence", [])
        evidence_text = format_evidence(evidence)
        report = None
        violation = None
        first_violating_report = None
        # One correction round: a groundedness violation feeds the offending
        # number back to the model so it can rephrase (e.g. drop a structural
        # constant) instead of failing on a deterministic first draft. The
        # gate itself stays strict; this is a rephrase aid, not relaxation.
        for _attempt in range(2):
            prompt_evidence = evidence_text
            if violation is not None:
                prompt_evidence = (
                    evidence_text
                    + "\n\nGroundedness correction: "
                    + violation
                    + " Rewrite the report without that number "
                    + "(rephrase the condition/period qualitatively) and keep "
                    + "every other value exactly as recorded."
                )
            try:
                report = await services.llm.synthesize(state["request"], prompt_evidence)
            except LLMError as error:
                return {
                    **update,
                    "llm_error": str(error),
                    "report": None,
                    "retry_stage": SYNTHESIZE,
                }
            violation = groundedness_violation(report, evidence, task=state.get("request", ""))
            if violation is None:
                progress.emit(ProgressEventType.GROUNDEDNESS_VERDICT, status="grounded")
                return {
                    **update,
                    "status": ManagerStatus.COMPLETED,
                    "report": report,
                    "groundedness_error": None,
                    "llm_error": None,
                }
            if first_violating_report is None:
                first_violating_report = report
        # Never ship a fabricated report: store the last rejected attempt for
        # inspection only. The verdict event carries the violation, never the
        # rejected text — no event ever exposes it as the run's report.
        progress.emit(
            ProgressEventType.GROUNDEDNESS_VERDICT,
            status="violated",
            reason=violation,
        )
        return {
            **update,
            "status": ManagerStatus.FAILED,
            "groundedness_error": violation,
            "report": None,
            "report_attempt": first_violating_report or report,
            "llm_error": None,
        }

    def _route_after_decompose(state: ManagerState) -> str:
        if state.get("decomposition_error"):
            # Unusable model output: deterministic, retrying cannot help.
            return FAIL_EDGE
        if state.get("llm_error"):
            # Transient model/transport failure: bounded retry.
            return RETRY_EDGE if state.get("attempts", 1) < services.max_attempts else FAIL_EDGE
        return SUB_ANALYSES_EDGE

    def _route_after_sub_analyses(state: ManagerState) -> str:
        # All-failing sub-analyses already set FAILED; only a run with
        # grounded evidence reaches the inspect stage (M7.5). Either way the
        # run finishes through the terminal node.
        return INSPECT_EDGE if state.get("status") is ManagerStatus.COMPLETED else FINISH_EDGE

    def _route_after_synthesize(state: ManagerState) -> str:
        if state.get("llm_error"):
            # Transient model/transport failure: bounded retry (the shared
            # attempt budget).
            return RETRY_EDGE if state.get("attempts", 1) < services.max_attempts else FAIL_EDGE
        # Both a completed report and a groundedness violation end the run
        # (the violation already set FAILED and withheld the report).
        return FINISH_EDGE

    def _route_after_inspect(state: ManagerState) -> str:
        if state.get("llm_error"):
            # Transient model/transport failure: bounded retry (the shared
            # attempt budget). On budget exhaustion the node already degraded
            # to no follow-up, so this path means a retry is still available.
            return RETRY_EDGE
        # Follow-ups (when requested) run exactly once, then synthesis;
        # an empty decision skips the round and goes straight to synthesis.
        return FOLLOW_UPS_EDGE if state.get("follow_up_questions") else SYNTHESIZE_EDGE

    def _route_after_follow_ups(state: ManagerState) -> str:
        # The single follow-up round is spent; always synthesize now.
        return SYNTHESIZE_EDGE

    def _route_after_retry(state: ManagerState) -> str:
        # Retry targets the stage that failed, recorded by the failing node;
        # decomposition is the default before anything has run.
        return state.get("retry_stage", DECOMPOSE)

    g = StateGraph(ManagerState)
    g.add_node(DECOMPOSE, _decompose)
    g.add_node(RUN_SUB_ANALYSES, _run_sub_analyses)
    g.add_node(INSPECT, _inspect)
    g.add_node(RUN_FOLLOW_UPS, _run_follow_ups)
    g.add_node(SYNTHESIZE, _synthesize)
    g.add_node(RETRY, _retry)
    g.add_node(FAIL, _fail)
    # The terminal node: every path that ends the run passes through it, so the
    # terminal progress event is emitted in exactly one place.
    g.add_node(FINISH, _finish)

    g.add_edge(START, DECOMPOSE)
    g.add_conditional_edges(
        DECOMPOSE,
        _route_after_decompose,
        {SUB_ANALYSES_EDGE: RUN_SUB_ANALYSES, RETRY_EDGE: RETRY, FAIL_EDGE: FAIL},
    )
    g.add_conditional_edges(
        RUN_SUB_ANALYSES,
        _route_after_sub_analyses,
        {INSPECT_EDGE: INSPECT, FINISH_EDGE: FINISH},
    )
    g.add_conditional_edges(
        INSPECT,
        _route_after_inspect,
        {
            FOLLOW_UPS_EDGE: RUN_FOLLOW_UPS,
            SYNTHESIZE_EDGE: SYNTHESIZE,
            RETRY_EDGE: RETRY,
        },
    )
    g.add_conditional_edges(
        RUN_FOLLOW_UPS,
        _route_after_follow_ups,
        {SYNTHESIZE_EDGE: SYNTHESIZE},
    )
    g.add_conditional_edges(
        SYNTHESIZE,
        _route_after_synthesize,
        {RETRY_EDGE: RETRY, FAIL_EDGE: FAIL, FINISH_EDGE: FINISH},
    )
    g.add_conditional_edges(
        RETRY,
        _route_after_retry,
        {DECOMPOSE: DECOMPOSE, INSPECT: INSPECT, SYNTHESIZE: SYNTHESIZE},
    )
    g.add_edge(FAIL, FINISH)
    g.add_edge(FINISH, END)

    return g.compile()
