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
"""

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langgraph.graph import END, START, StateGraph

from app.agent.llm import LLMError
from app.agent.state import AgentState
from app.manager.decompose import DecompositionError, decompose_request
from app.manager.evidence import EvidenceRecord
from app.manager.inspect import FollowUpError, parse_follow_up_questions
from app.manager.llm import ManagerLLM
from app.manager.state import ManagerState, ManagerStatus
from app.manager.synthesize import format_evidence, groundedness_violation

# Node identifiers.
DECOMPOSE = "decompose"
RUN_SUB_ANALYSES = "run_sub_analyses"
INSPECT = "inspect"
RUN_FOLLOW_UPS = "run_follow_ups"
SYNTHESIZE = "synthesize"
RETRY = "retry"
FAIL = "fail"

# Edge identifiers returned by conditional routers.
SUB_ANALYSES_EDGE = "sub_analyses"
INSPECT_EDGE = "inspect"
FOLLOW_UPS_EDGE = "follow_ups"
SYNTHESIZE_EDGE = "synthesize"
RETRY_EDGE = "retry"
FAIL_EDGE = "fail"

# Version-controlled, curated table names — the DB-free schema hint for the
# decompose stage (the manager itself never queries the database, D009).
_SEED_PATH = Path(__file__).resolve().parents[1] / "metadata_seed.json"


def _table_names_from_seed() -> list[str]:
    """Derive the table-name schema hint from the curated metadata seed."""
    seed = json.loads(_SEED_PATH.read_text(encoding="utf-8"))
    return list(seed["tables"])


@dataclass
class ManagerServices:
    """Runtime dependencies injected into the manager graph."""

    llm: ManagerLLM
    # Runs one grounded analyst sub-analysis and returns its final state.
    # Actual wiring (shared capabilities, LLM, tracer config) is M7.4's job.
    run_analyst: Callable[[str], Awaitable[AgentState]]
    # Bounded retries for transient LLM failures; the budget is shared by
    # the retryable stages (decompose / inspect / synthesize).
    max_attempts: int = 2
    # Table-name schema hint; defaults to the curated metadata seed.
    table_names: list[str] = field(default_factory=_table_names_from_seed)


def build_manager_graph(services: ManagerServices) -> Any:
    """Compile the manager state machine."""

    async def _decompose(state: ManagerState) -> dict[str, Any]:
        update: dict[str, Any] = {"status": ManagerStatus.DECOMPOSING}
        try:
            questions = await decompose_request(
                services.llm, state["request"], services.table_names
            )
        except LLMError as error:
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
        return {
            **update,
            "sub_questions": questions,
            "llm_error": None,
            "decomposition_error": None,
        }

    async def _run_sub_analyses(state: ManagerState) -> dict[str, Any]:
        evidence: list[EvidenceRecord] = []
        errors: list[str] = []
        # Sequential by D009 (the model server is the bottleneck; parallelism
        # is a pure optimization that can be added without state changes).
        for index, question in enumerate(state["sub_questions"]):
            sub_state = await services.run_analyst(question)
            record = EvidenceRecord.from_agent_state(index, question, sub_state)
            evidence.append(record)
            if record.error:
                errors.append(f"sub-question {index} ({question}): {record.error}")
        all_failed = bool(evidence) and all(record.error is not None for record in evidence)
        return {
            "evidence": evidence,
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
            return {
                **update,
                "llm_error": None,
                "inspect_error": f"inspect LLM call failed: {error}",
                "follow_up_questions": [],
                "retry_stage": INSPECT,
            }
        try:
            questions = parse_follow_up_questions(raw)
        except FollowUpError as error:
            # Unusable output: deterministic at temperature 0, retry cannot
            # help. Degrade to no follow-up — the caps are hard (D009) and
            # guessing which questions to keep is not the manager's job.
            return {
                **update,
                "inspect_error": str(error),
                "follow_up_questions": [],
                "llm_error": None,
            }
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
        inspect). Follow-up records are appended to the evidence, marked
        ``is_follow_up``, and number after the sub-analyses; a follow-up
        failure is recorded and the round still proceeds to synthesis.
        """
        questions = state.get("follow_up_questions", [])
        evidence = list(state.get("evidence", []))
        errors: list[str] = []
        start_index = len(evidence)
        for offset, question in enumerate(questions):
            sub_state = await services.run_analyst(question)
            record = EvidenceRecord.from_agent_state(
                start_index + offset, question, sub_state, is_follow_up=True
            )
            evidence.append(record)
            if record.error:
                errors.append(f"follow-up {offset} ({question}): {record.error}")
        return {
            "evidence": evidence,
            "follow_up_errors": errors,
            "follow_up_rounds": 1,
            "status": ManagerStatus.COMPLETED,
        }

    async def _retry(state: ManagerState) -> dict[str, Any]:
        return {"attempts": state.get("attempts", 1) + 1, "status": ManagerStatus.RETRYING}

    async def _fail(state: ManagerState) -> dict[str, Any]:
        return {"status": ManagerStatus.FAILED}

    async def _synthesize(state: ManagerState) -> dict[str, Any]:
        update: dict[str, Any] = {"status": ManagerStatus.SYNTHESIZING}
        try:
            report = await services.llm.synthesize(
                state["request"], format_evidence(state["evidence"])
            )
        except LLMError as error:
            return {
                **update,
                "llm_error": str(error),
                "report": None,
                "retry_stage": SYNTHESIZE,
            }
        violation = groundedness_violation(report, state["evidence"], task=state.get("request", ""))
        if violation:
            # Never ship a fabricated report: on a violation the report is
            # not stored and the run fails. Deterministic output at
            # temperature 0 would reproduce the same violation, so no retry.
            return {
                **update,
                "status": ManagerStatus.FAILED,
                "groundedness_error": violation,
                "report": None,
                "llm_error": None,
            }
        return {
            **update,
            "status": ManagerStatus.COMPLETED,
            "report": report,
            "groundedness_error": None,
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
        # grounded evidence reaches the inspect stage (M7.5).
        return INSPECT_EDGE if state.get("status") is ManagerStatus.COMPLETED else END

    def _route_after_synthesize(state: ManagerState) -> str:
        if state.get("llm_error"):
            # Transient model/transport failure: bounded retry (the shared
            # attempt budget).
            return RETRY_EDGE if state.get("attempts", 1) < services.max_attempts else FAIL_EDGE
        # Both a completed report and a groundedness violation end the run
        # (the violation already set FAILED and withheld the report).
        return END

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

    g.add_edge(START, DECOMPOSE)
    g.add_conditional_edges(
        DECOMPOSE,
        _route_after_decompose,
        {SUB_ANALYSES_EDGE: RUN_SUB_ANALYSES, RETRY_EDGE: RETRY, FAIL_EDGE: FAIL},
    )
    g.add_conditional_edges(
        RUN_SUB_ANALYSES,
        _route_after_sub_analyses,
        {INSPECT_EDGE: INSPECT, END: END},
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
        {RETRY_EDGE: RETRY, FAIL_EDGE: FAIL, END: END},
    )
    g.add_conditional_edges(
        RETRY,
        _route_after_retry,
        {DECOMPOSE: DECOMPOSE, INSPECT: INSPECT, SYNTHESIZE: SYNTHESIZE},
    )
    g.add_edge(FAIL, END)

    return g.compile()
