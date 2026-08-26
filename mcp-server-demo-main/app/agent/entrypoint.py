"""Programmatic ``run_agent`` and the ``python -m app.agent`` CLI.

The agent is a standalone consumable: it connects to the running MCP server as
its database boundary and to the configured local LLM, runs the LangGraph
workflow, and returns the grounded answer. Tracing is fail-open (Langfuse only
when a public key is configured).
"""

import asyncio
import json
from dataclasses import dataclass
from typing import Any

from app.agent.capabilities import MCPCapabilities
from app.agent.graph import AgentServices, build_graph
from app.agent.llm import LLMClient
from app.agent.state import AgentState
from app.agent.tracing import AgentTracer, callbacks_for
from app.config import get_settings


@dataclass
class RunResult:
    """Summary returned to the caller."""

    answer: str | None
    status: str | None
    sql: str | None
    attempts: int
    state: AgentState


async def _run_question(
    question: str,
    *,
    llm: Any,
    capabilities: Any,
    max_attempts: int,
    max_rows: int,
    tracer: AgentTracer | None,
) -> AgentState:
    services = AgentServices(
        llm=llm, capabilities=capabilities, max_attempts=max_attempts, max_rows=max_rows
    )
    graph = build_graph(services)
    callbacks = callbacks_for(tracer)
    config = {"callbacks": callbacks} if callbacks is not None else None
    return await graph.ainvoke(
        {"question": question, "status": "planning", "attempts": 0}, config=config
    )


async def run_agent(question: str) -> RunResult:
    """Run the analytics agent end-to-end and return a concise result."""
    settings = get_settings()
    llm = LLMClient()
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
        )
    finally:
        await capabilities.close()
    return RunResult(
        answer=state.get("answer"),
        status=state.get("status"),
        sql=state.get("bounded_sql") or state.get("sql"),
        attempts=state.get("attempts", 0),
        state=state,
    )


def _parse_args(argv: list[str]) -> tuple[str, bool]:
    if argv and argv[0] == "--json":
        return " ".join(argv[1:]), True
    return " ".join(argv), False


def main(argv: list[str] | None = None) -> None:
    import sys

    args = sys.argv[1:] if argv is None else argv
    if not args:
        raise SystemExit("Usage: python -m app.agent [--json] '<question>'")
    question, as_json = _parse_args(args)
    result = asyncio.run(run_agent(question))
    if as_json:
        print(
            json.dumps(
                {
                    "answer": result.answer,
                    "status": result.status,
                    "sql": result.sql,
                    "attempts": result.attempts,
                },
                indent=2,
            )
        )
        return
    print(f"Status: {result.status}")
    print(f"Attempts: {result.attempts}")
    if result.sql:
        print(f"SQL: {result.sql}")
    print(f"Answer: {result.answer}")


if __name__ == "__main__":
    main()
