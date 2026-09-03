"""LangGraph workflow for the analytics manager (M7 Stage A).

The manager composes analyst runs (D009): it decomposes one high-level
management request into at most 4 concrete sub-questions, runs each through a
full grounded, read-only analyst run, and accumulates the evidence. The manager
never touches the database itself — the decompose stage gets only a table-name
schema hint, and every evidence item is produced by an analyst run.

Failure policy (kept deliberately simple, per D009's fixed pipeline):

* ``LLMError`` on the decompose call (transient: timeout/transport/HTTP/rate
  limits — the common hosted-model failure mode) retries up to a bounded
  attempt count.
* ``DecompositionError`` (unusable model output) fails immediately: at
  temperature 0 the same input reproduces the same invalid output.
* A failing sub-analysis is recorded and the run continues; only when *all*
  sub-analyses fail does the manager fail.

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

# Node identifiers.
DECOMPOSE = "decompose"
RUN_SUB_ANALYSES = "run_sub_analyses"
RETRY = "retry"
FAIL = "fail"

# Edge identifiers returned by conditional routers.
SUB_ANALYSES_EDGE = "sub_analyses"
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

    def _route_after_decompose(state: ManagerState) -> str:
        if state.get("decomposition_error"):
            # Unusable model output: deterministic, retrying cannot help.
            return FAIL_EDGE
        if state.get("llm_error"):
            # Transient model/transport failure: bounded retry.
            return RETRY_EDGE if state.get("attempts", 1) < services.max_attempts else FAIL_EDGE
        return SUB_ANALYSES_EDGE

    g = StateGraph(ManagerState)
    g.add_node(DECOMPOSE, _decompose)
    g.add_node(RUN_SUB_ANALYSES, _run_sub_analyses)
    g.add_node(RETRY, _retry)
    g.add_node(FAIL, _fail)

    g.add_edge(START, DECOMPOSE)
    g.add_conditional_edges(
        DECOMPOSE,
        _route_after_decompose,
        {SUB_ANALYSES_EDGE: RUN_SUB_ANALYSES, RETRY_EDGE: RETRY, FAIL_EDGE: FAIL},
    )
    g.add_edge(RETRY, DECOMPOSE)
    g.add_edge(RUN_SUB_ANALYSES, END)
    g.add_edge(FAIL, END)

    return g.compile()
