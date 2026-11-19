"""LangGraph workflow for the analytics agent.

The graph implements the V2 analytics loop from the project plan: understand
the question, retrieve relevant metadata, generate a candidate query, validate
it (read-only), execute it through MCP, recover from invalid/erroring queries up
to a bounded attempt limit, and produce an evidence-grounded answer.

The agent is intentionally tool-calling-free for determinism: SQL generation and
final analysis are discrete model calls, and database access happens strictly
through the injected ``Capabilities`` (the MCP read-only boundary). Tests swap in
a ``FakeLLM`` and a stub ``Capabilities`` for deterministic behaviour.
"""

import json
from dataclasses import dataclass
from typing import Any

from langgraph.graph import END, START, StateGraph

from app.agent.capabilities import Capabilities
from app.agent.llm import LLM, LLMError
from app.agent.state import AgentState, AgentStatus
from app.progress import ProgressEmitter, ProgressEventType, ProgressSink
from app.sql_safety import UnsafeQueryError, validate_and_bound_query

# Node identifiers.
PLAN = "plan"
RETRIEVE = "retrieve_metadata"
GENERATE = "generate_sql"
VALIDATE = "validate_sql"
EXECUTE = "execute_sql"
ANALYZE = "analyze_results"
RETRY = "retry"
FAIL = "fail"

# Edge identifiers returned by conditional routers.
EXECUTE_EDGE = "exec"
RETRY_EDGE = "retry"
FAIL_EDGE = "fail"
ANALYZE_EDGE = "analyze"


@dataclass
class AgentServices:
    """Runtime dependencies injected into the graph."""

    llm: LLM
    capabilities: Capabilities
    max_attempts: int = 3
    max_rows: int = 100
    # Optional progress sink (PLAN M8). Absent by default, so every existing
    # caller — CLI, library, tests — runs with no emission and no cost.
    sink: ProgressSink | None = None
    # An emitter derived from the run this one is nested in (a manager hands
    # one to each analyst sub-run). It already carries the parent's sink and
    # sequence counter, so the nested events continue the parent's single
    # stream instead of restarting at 1 — and they arrive stamped with the
    # sub-question they belong to.
    #
    # ``sink`` and ``emitter`` are mutually exclusive: the emitter already owns
    # a sink, so setting both would leave one of them silently ignored — and a
    # nested runner that passed ``sink=`` would start a second stream inside a
    # run that must have exactly one. ``build_graph`` rejects the combination.
    emitter: ProgressEmitter | None = None


def failure_reason(state: AgentState) -> str | None:
    """Why the run failed, or ``None`` when nothing failed.

    The caller-facing error of a run: a model, validation or execution
    failure, or a retrieval failure only when the schema itself was
    unavailable. A ``search_metadata`` failure alone degrades the run (it
    still completes from the schema) and is not an error.
    """
    reason = state.get("llm_error") or state.get("query_error") or state.get("validation_error")
    if reason:
        return reason
    if state.get("schema_unavailable"):
        retrieval_errors = state.get("retrieval_errors") or []
        if retrieval_errors:
            return retrieval_errors[0]
    return None


def _table_names_from(hits: list[dict[str, Any]]) -> list[str]:
    """Derive deduplicated table names from retrieved metadata entities."""
    names: list[str] = []
    for hit in hits:
        entity_id = str(hit.get("entity_id", ""))
        table = entity_id.split(".")[0].split(":", 1)[-1]
        if table and table not in names:
            names.append(table)
    return names


def _build_metadata_text(hits: list[dict[str, Any]]) -> str:
    return json.dumps(hits, indent=2, default=str)


def _build_schema_text(tables: list[dict[str, Any]], relationships: list[dict[str, Any]]) -> str:
    lines: list[str] = [
        "Available tables: " + ", ".join(sorted(t.get("table_name", "") for t in tables))
    ]
    if relationships:
        lines.append(
            "Relationship graph: "
            + "; ".join(
                f"{r.get('child_table')}.{r.get('child_column')} -> "
                f"{r.get('parent_table')}.{r.get('parent_column')}"
                for r in relationships
            )
        )
    return "\n".join(lines)


def build_graph(services: AgentServices) -> Any:
    """Compile the LangGraph state machine.

    The compiled graph serves exactly one run: its progress sink and event
    sequence counter bind here, at construction. Build a graph per invocation
    (D10) — a graph cached across invocations would fuse two runs into one
    indistinguishable stream and the second run's events would never reach its
    own consumer.
    """
    if services.emitter is not None and services.sink is not None:
        raise ValueError(
            "AgentServices takes either an emitter or a sink, not both: the "
            "emitter already carries the sink of the run it is nested in, so "
            "passing both would silently drop one of them."
        )
    # One emitter per graph (i.e. per run): it owns the run's sequence numbers
    # and is a no-op when no sink was injected. A nested run reuses the emitter
    # its parent derived for it, so the run's numbering stays one sequence.
    progress = services.emitter if services.emitter is not None else ProgressEmitter(services.sink)

    async def _plan(state: AgentState) -> dict[str, Any]:
        progress.emit(ProgressEventType.RUN_STARTED, question=state["question"], attempt=1)
        return {"status": AgentStatus.RETRIEVING_METADATA, "attempts": 1}

    async def _retrieve(state: AgentState) -> dict[str, Any]:
        # Every discovery call's validity is surfaced in state: a failed call
        # (masked infrastructure error, unknown tool, empty result) must not be
        # silently degraded to an empty schema — the run retries retrieval
        # (bounded) or fails cleanly instead of generating SQL blind.
        discovery_args: dict[str, dict[str, Any]] = {
            "search_metadata": {"question": state["question"]},
            "list_tables": {},
            "get_relationships": {},
        }
        results: dict[str, dict[str, Any]] = {}
        retrieval_errors: list[str] = []
        schema_unavailable = False
        for name, args in discovery_args.items():
            result = await services.capabilities.call_tool(name, args)
            results[name] = result
            if not result.get("valid", False):
                retrieval_errors.append(f"{name}: {result.get('message', 'failed')}")
                if name != "search_metadata":
                    # Without the table/FK graph no grounded SQL is possible.
                    schema_unavailable = True
        hits = results["search_metadata"].get("entries", [])
        tables = results["list_tables"].get("entries", [])
        relationships = results["get_relationships"].get("entries", [])
        update: dict[str, Any] = {
            "metadata": hits,
            "relevant_tables": _table_names_from(hits),
            "schema": tables,
            "relationships": relationships,
            "validation_error": state.get("validation_error"),
            "query_error": state.get("query_error"),
            # Cleared on a clean pass so the shared retry router can tell a
            # retrieval retry (schema unavailable) from an SQL retry.
            "retrieval_errors": retrieval_errors,
            "schema_unavailable": schema_unavailable,
            "status": (
                AgentStatus.RETRIEVING_METADATA
                if schema_unavailable
                else AgentStatus.GENERATING_SQL
            ),
        }
        if not schema_unavailable:
            # A completed retrieval is reported even when search_metadata failed
            # (the run degrades to schema-only), but never as healthy: the
            # failure travels as the event's reason. A broken schema discovery
            # reports nothing here; it surfaces through the retry/failure event
            # that carries its reason.
            progress.emit(
                ProgressEventType.METADATA_RETRIEVED,
                attempt=state.get("attempts", 1),
                reason=retrieval_errors[0] if retrieval_errors else None,
            )
        return update

    async def _generate(state: AgentState) -> dict[str, Any]:
        metadata_text = _build_metadata_text(state.get("metadata", []))
        schema_text = _build_schema_text(state.get("schema", []), state.get("relationships", []))
        # If a previous attempt was rejected (unsafe or errored at execution),
        # feed that error back to the model so it corrects its SQL instead of
        # blindly repeating the same query. Otherwise it regenerates identically
        # and burns the attempt budget (PLAN item 7: recover from errors).
        prior_error = state.get("query_error") or state.get("validation_error")
        try:
            candidate = await services.llm.generate_sql(
                state["question"], metadata_text, schema_text, prior_error=prior_error
            )
        except LLMError as error:
            # The model call failed (timeout/transport/HTTP). This is a
            # transient, retryable step — the graph recovers up to the attempt
            # limit and fails cleanly afterwards instead of crashing the run.
            return {
                "sql": "",
                "llm_error": str(error),
                "validation_error": None,
                "query_error": None,
            }
        progress.emit(
            ProgressEventType.SQL_GENERATED,
            attempt=state.get("attempts", 1),
            sql=candidate,
        )
        return {
            "sql": candidate,
            "status": AgentStatus.VALIDATING_SQL,
            "validation_error": None,
            "query_error": None,
            "llm_error": None,
        }

    async def _validate(state: AgentState) -> dict[str, Any]:
        sql = state.get("sql", "")
        try:
            bounded = validate_and_bound_query(sql, max_rows=services.max_rows)
        except UnsafeQueryError as error:
            return {"bounded_sql": "", "validation_error": str(error)}
        progress.emit(
            ProgressEventType.SQL_VALIDATED,
            attempt=state.get("attempts", 1),
            sql=bounded,
        )
        return {"bounded_sql": bounded, "validation_error": None}

    async def _execute(state: AgentState) -> dict[str, Any]:
        result = await services.capabilities.call_tool(
            "query", {"sql": state.get("bounded_sql", "")}
        )
        if not result.get("valid", False):
            return {"result": None, "query_error": result.get("message", "query failed")}
        # A null/absent entries payload must not raise inside the node: the
        # rows are counted and serialized below, so treat it as no rows.
        rows = result.get("entries") or []
        progress.emit(
            ProgressEventType.SQL_EXECUTED,
            attempt=state.get("attempts", 1),
            sql=state.get("bounded_sql", ""),
            row_count=len(rows),
        )
        return {
            "result": rows,
            "query_error": None,
            "status": AgentStatus.ANALYZING_RESULTS,
        }

    async def _analyze(state: AgentState) -> dict[str, Any]:
        result_text = json.dumps(state.get("result", []), default=str)
        try:
            answer = await services.llm.generate_answer(
                state["question"], state.get("bounded_sql", ""), result_text
            )
        except LLMError as error:
            # Never fabricate: on a model failure the answer stays unset and the
            # run retries (bounded) or fails cleanly with the error recorded.
            return {"answer": None, "llm_error": str(error)}
        progress.emit(
            ProgressEventType.ANALYSIS_COMPLETED,
            attempt=state.get("attempts", 1),
            answer=answer,
        )
        progress.emit(
            ProgressEventType.RUN_COMPLETED,
            attempt=state.get("attempts", 1),
            status=str(AgentStatus.COMPLETED),
            answer=answer,
        )
        return {"answer": answer, "status": AgentStatus.COMPLETED, "llm_error": None}

    async def _retry(state: AgentState) -> dict[str, Any]:
        # The retry carries the reason the attempt was rejected, so a consumer
        # sees why the pipeline went around again instead of guessing.
        progress.emit(
            ProgressEventType.RUN_RETRYING,
            attempt=state.get("attempts", 1),
            reason=failure_reason(state),
        )
        return {"attempts": state.get("attempts", 0) + 1, "status": AgentStatus.RETRYING}

    async def _fail(state: AgentState) -> dict[str, Any]:
        progress.emit(
            ProgressEventType.RUN_FAILED,
            attempt=state.get("attempts", 1),
            status=str(AgentStatus.FAILED),
            reason=failure_reason(state),
        )
        return {"status": AgentStatus.FAILED}

    def _route_after_retrieve(state: AgentState) -> str:
        # A search_metadata failure alone degrades the run to schema-only
        # retrieval (recorded in retrieval_errors); only a broken schema
        # discovery (list_tables / get_relationships) blocks SQL generation.
        if state.get("schema_unavailable"):
            return RETRY_EDGE if state.get("attempts", 1) < services.max_attempts else FAIL_EDGE
        return GENERATE

    def _route_after_retry(state: AgentState) -> str:
        # A retry caused by failed schema discovery re-runs retrieval; an SQL
        # validation/execution/LLM retry goes straight back to generation (the
        # retrieved schema is still valid in that case).
        return RETRIEVE if state.get("schema_unavailable") else GENERATE

    def _route_after_validate(state: AgentState) -> str:
        if state.get("validation_error"):
            return RETRY_EDGE if state.get("attempts", 1) < services.max_attempts else FAIL_EDGE
        return EXECUTE_EDGE

    def _route_after_execute(state: AgentState) -> str:
        if state.get("query_error"):
            return RETRY_EDGE if state.get("attempts", 1) < services.max_attempts else FAIL_EDGE
        return ANALYZE_EDGE

    def _route_after_generate(state: AgentState) -> str:
        if state.get("llm_error"):
            return RETRY_EDGE if state.get("attempts", 1) < services.max_attempts else FAIL_EDGE
        return VALIDATE

    def _route_after_analyze(state: AgentState) -> str:
        if state.get("llm_error"):
            return RETRY_EDGE if state.get("attempts", 1) < services.max_attempts else FAIL_EDGE
        return END

    g = StateGraph(AgentState)
    g.add_node(PLAN, _plan)
    g.add_node(RETRIEVE, _retrieve)
    g.add_node(GENERATE, _generate)
    g.add_node(VALIDATE, _validate)
    g.add_node(EXECUTE, _execute)
    g.add_node(ANALYZE, _analyze)
    g.add_node(RETRY, _retry)
    g.add_node(FAIL, _fail)

    g.add_edge(START, PLAN)
    g.add_edge(PLAN, RETRIEVE)
    g.add_conditional_edges(
        RETRIEVE,
        _route_after_retrieve,
        {GENERATE: GENERATE, RETRY_EDGE: RETRY, FAIL_EDGE: FAIL},
    )
    # LLM failures during SQL generation are recoverable: retry (bounded) or
    # fail cleanly instead of crashing the run with a raw httpx traceback.
    g.add_conditional_edges(
        GENERATE, _route_after_generate, {VALIDATE: VALIDATE, RETRY_EDGE: RETRY, FAIL_EDGE: FAIL}
    )
    g.add_conditional_edges(
        VALIDATE, _route_after_validate, {EXECUTE_EDGE: EXECUTE, RETRY_EDGE: RETRY, FAIL_EDGE: FAIL}
    )
    g.add_conditional_edges(
        EXECUTE, _route_after_execute, {RETRY_EDGE: RETRY, FAIL_EDGE: FAIL, ANALYZE_EDGE: ANALYZE}
    )
    # Same recovery for an LLM failure during the final answer step.
    g.add_conditional_edges(
        ANALYZE, _route_after_analyze, {END: END, RETRY_EDGE: RETRY, FAIL_EDGE: FAIL}
    )
    g.add_conditional_edges(RETRY, _route_after_retry, {RETRIEVE: RETRIEVE, GENERATE: GENERATE})
    g.add_edge(FAIL, END)

    return g.compile()
