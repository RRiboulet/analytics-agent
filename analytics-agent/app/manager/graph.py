"""LangGraph workflow for the analytics manager (M7 Stage A).

The manager composes analyst runs (D009): it decomposes one high-level
management request into at most 4 concrete sub-questions, runs each through a
full grounded, read-only analyst run, and accumulates the evidence. The manager
never touches the database itself — the decompose stage gets only a table-name
schema hint, and every evidence item is produced by an analyst run.

Failure policy (kept deliberately simple, per D009's fixed pipeline):

* ``LLMError`` on the decompose or synthesis call (transient:
  timeout/transport/HTTP/rate limits — the common hosted-model failure mode)
  retries up to a bounded attempt count. The attempt budget is shared by
  both retryable stages, so it stays a single hard bound.
* ``DecompositionError`` (unusable model output) fails immediately: at
  temperature 0 the same input reproduces the same invalid output.
* A failing sub-analysis is recorded and the run continues; only when *all*
  sub-analyses fail does the manager fail.
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
from app.manager.llm import ManagerLLM
from app.manager.state import ManagerState, ManagerStatus
from app.manager.synthesize import format_evidence, groundedness_violation

# Node identifiers.
DECOMPOSE = "decompose"
RUN_SUB_ANALYSES = "run_sub_analyses"
SYNTHESIZE = "synthesize"
RETRY = "retry"
FAIL = "fail"

# Edge identifiers returned by conditional routers.
SUB_ANALYSES_EDGE = "sub_analyses"
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
    # Bounded decompose retries for transient LLM failures.
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
            return {**update, "llm_error": str(error), "report": None}
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
        # grounded evidence reaches synthesis.
        return SYNTHESIZE_EDGE if state.get("status") is ManagerStatus.COMPLETED else END

    def _route_after_synthesize(state: ManagerState) -> str:
        if state.get("llm_error"):
            # Transient model/transport failure: bounded retry (the shared
            # attempt budget).
            return RETRY_EDGE if state.get("attempts", 1) < services.max_attempts else FAIL_EDGE
        # Both a completed report and a groundedness violation end the run
        # (the violation already set FAILED and withheld the report).
        return END

    def _route_after_retry(state: ManagerState) -> str:
        # Retry targets the stage that failed: synthesis once evidence
        # exists, decomposition otherwise.
        return SYNTHESIZE if state.get("evidence") else DECOMPOSE

    g = StateGraph(ManagerState)
    g.add_node(DECOMPOSE, _decompose)
    g.add_node(RUN_SUB_ANALYSES, _run_sub_analyses)
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
        {SYNTHESIZE_EDGE: SYNTHESIZE, END: END},
    )
    g.add_conditional_edges(
        SYNTHESIZE,
        _route_after_synthesize,
        {RETRY_EDGE: RETRY, FAIL_EDGE: FAIL, END: END},
    )
    g.add_conditional_edges(
        RETRY,
        _route_after_retry,
        {DECOMPOSE: DECOMPOSE, SYNTHESIZE: SYNTHESIZE},
    )
    g.add_edge(FAIL, END)

    return g.compile()
