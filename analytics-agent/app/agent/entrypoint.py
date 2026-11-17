"""Programmatic ``run_agent`` and the ``python -m app.agent`` CLI.

The agent is a standalone consumable: it connects to the running MCP server as
its database boundary and to the configured LLM (local llama.cpp or hosted
OpenRouter, selected by ``LLM_PROVIDER``), runs the LangGraph
workflow, and returns the grounded answer. Tracing is fail-open (Langfuse only
when a public key is configured).
"""

import asyncio
import json
import sys
from dataclasses import dataclass
from typing import Any

from app.agent.capabilities import MCPCapabilities
from app.agent.graph import AgentServices, build_graph, failure_reason
from app.agent.llm import create_llm
from app.agent.state import AgentState
from app.agent.tracing import AgentTracer
from app.config import get_settings
from app.progress import ProgressSink


@dataclass
class RunResult:
    """Summary returned to the caller."""

    answer: str | None
    status: str | None
    sql: str | None
    attempts: int
    state: AgentState
    error: str | None = None


async def _run_question(
    question: str,
    *,
    llm: Any,
    capabilities: Any,
    max_attempts: int,
    max_rows: int,
    tracer: AgentTracer | None,
    sink: ProgressSink | None = None,
) -> AgentState:
    services = AgentServices(
        llm=llm,
        capabilities=capabilities,
        max_attempts=max_attempts,
        max_rows=max_rows,
        sink=sink,
    )
    graph = build_graph(services)
    # Fail-open observability: with Langfuse credentials the whole run is
    # traced (nodes, LLM calls, MCP tool calls, retries, final answer);
    # without them the graph is invoked with no config at all.
    config = tracer.run_config(question) if tracer is not None else None
    return await graph.ainvoke(
        {"question": question, "status": "planning", "attempts": 0}, config=config
    )


async def run_agent(question: str, *, sink: ProgressSink | None = None) -> RunResult:
    """Run the analytics agent end-to-end and return a concise result.

    ``sink`` is the optional progress channel: when supplied the run emits its
    stage transitions, retries and terminal outcome through it, fail-open. With
    no sink the run is exactly what it was before (no emission, no cost).
    """
    settings = get_settings()
    llm = create_llm()
    capabilities = MCPCapabilities()
    tracer = AgentTracer()
    try:
        state = await _run_question(
            question,
            llm=llm,
            capabilities=capabilities,
            max_attempts=settings.agent_max_attempts,
            max_rows=settings.max_rows,
            tracer=tracer,
            sink=sink,
        )
    finally:
        await capabilities.close()
        # Export what was collected even if the run crashed mid-flight.
        tracer.flush()
    return RunResult(
        answer=state.get("answer"),
        status=state.get("status"),
        sql=state.get("bounded_sql") or state.get("sql"),
        attempts=state.get("attempts", 0),
        state=state,
        # A successful run is never an error: this is the same reason the
        # failure event carries, so the caller and the live view agree.
        error=failure_reason(state),
    )


def _parse_args(argv: list[str]) -> tuple[str, bool]:
    if argv and argv[0] == "--json":
        return " ".join(argv[1:]), True
    return " ".join(argv), False


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    if not args:
        raise SystemExit("Usage: python -m app.agent [--json] '<question>'")
    question, as_json = _parse_args(args)
    try:
        result = asyncio.run(run_agent(question))
    except ValueError as error:
        # Configuration errors (e.g. LLM_PROVIDER=openrouter without an API
        # key) fail fast with a clean message instead of a traceback.
        raise SystemExit(f"Configuration error: {error}") from error
    if as_json:
        print(
            json.dumps(
                {
                    "answer": result.answer,
                    "status": result.status,
                    "sql": result.sql,
                    "attempts": result.attempts,
                    "error": result.error,
                },
                indent=2,
            )
        )
        return
    print(f"Status: {result.status}")
    print(f"Attempts: {result.attempts}")
    if result.error:
        print(f"Error: {result.error}")
    if result.sql:
        print(f"SQL: {result.sql}")
    print(f"Answer: {result.answer}")


if __name__ == "__main__":
    main()
