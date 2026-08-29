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
    """Compile the LangGraph state machine."""

    async def _plan(state: AgentState) -> dict[str, Any]:
        return {"status": AgentStatus.RETRIEVING_METADATA, "attempts": 1}

    async def _retrieve(state: AgentState) -> dict[str, Any]:
        hits = (
            await services.capabilities.call_tool(
                "search_metadata", {"question": state["question"]}
            )
        ).get("entries", [])
        tables = (await services.capabilities.call_tool("list_tables", {})).get("entries", [])
        relationships = (await services.capabilities.call_tool("get_relationships", {})).get(
            "entries", []
        )
        return {
            "metadata": hits,
            "relevant_tables": _table_names_from(hits),
            "schema": tables,
            "relationships": relationships,
            "status": AgentStatus.GENERATING_SQL,
            "validation_error": state.get("validation_error"),
            "query_error": state.get("query_error"),
        }

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
        return {"bounded_sql": bounded, "validation_error": None}

    async def _execute(state: AgentState) -> dict[str, Any]:
        result = await services.capabilities.call_tool(
            "query", {"sql": state.get("bounded_sql", "")}
        )
        if not result.get("valid", False):
            return {"result": None, "query_error": result.get("message", "query failed")}
        return {
            "result": result.get("entries", []),
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
        return {"answer": answer, "status": AgentStatus.COMPLETED, "llm_error": None}

    async def _retry(state: AgentState) -> dict[str, Any]:
        return {"attempts": state.get("attempts", 0) + 1, "status": AgentStatus.RETRYING}

    async def _fail(state: AgentState) -> dict[str, Any]:
        return {"status": AgentStatus.FAILED}

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
    g.add_edge(RETRIEVE, GENERATE)
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
    g.add_edge(RETRY, GENERATE)
    g.add_edge(FAIL, END)

    return g.compile()
