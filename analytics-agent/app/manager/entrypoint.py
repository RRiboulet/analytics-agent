"""Programmatic ``run_manager`` and the ``python -m app.manager`` CLI.

The manager composes grounded analyst runs (M7, D009): it connects to the
running MCP server through one shared read-only ``MCPCapabilities`` instance,
uses one ``ManagerLLMClient`` for both its own discrete calls and the analyst
sub-runs (the client extends ``LLMClient``, so it serves both protocols), and
runs the manager LangGraph workflow. With Langfuse credentials the whole run
is traced under one manager trace: manager run → analyst sub-runs → nodes.
Tracing stays fail-open; a ``--out`` directory persists the report and the
evidence for inspection.
"""

import asyncio
import dataclasses
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.agent.capabilities import MCPCapabilities
from app.agent.graph import AgentServices, build_graph
from app.agent.state import AgentState
from app.agent.tracing import AgentTracer
from app.config import get_settings
from app.manager.evidence import EvidenceRecord
from app.manager.graph import ManagerServices, build_manager_graph
from app.manager.llm import create_manager_llm
from app.manager.state import ManagerState, ManagerStatus


@dataclass
class ManagerRunResult:
    """Summary returned to the caller."""

    report: str | None
    status: str | None
    sub_questions: list[str]
    evidence: list[EvidenceRecord]
    attempts: int
    state: ManagerState
    error: str | None = None


def _run_error(state: ManagerState) -> str | None:
    """Caller-facing error for a manager run (None when nothing failed)."""
    if state.get("decomposition_error"):
        return state["decomposition_error"]
    if state.get("groundedness_error"):
        return state["groundedness_error"]
    if state.get("llm_error"):
        return state["llm_error"]
    if state.get("status") is ManagerStatus.FAILED and state.get("sub_analysis_errors"):
        return state["sub_analysis_errors"][0]
    return None


async def _run_request(
    request: str,
    *,
    llm: Any,
    analyst_llm: Any,
    capabilities: Any,
    manager_max_attempts: int,
    analyst_max_attempts: int,
    max_rows: int,
    tracer: AgentTracer | None,
) -> ManagerState:
    """Run one management request end-to-end with injected dependencies.

    The tracer config (fail-open) is shared by the manager graph and every
    analyst sub-run so a trace shows manager run → analyst sub-runs → nodes.
    """
    config = tracer.run_config(request) if tracer is not None else None

    analyst_services = AgentServices(
        llm=analyst_llm,
        capabilities=capabilities,
        max_attempts=analyst_max_attempts,
        max_rows=max_rows,
    )
    analyst_graph = build_graph(analyst_services)

    async def run_analyst(question: str) -> AgentState:
        return await analyst_graph.ainvoke(
            {"question": question, "status": "planning", "attempts": 0}, config=config
        )

    manager_services = ManagerServices(
        llm=llm, run_analyst=run_analyst, max_attempts=manager_max_attempts
    )
    manager_graph = build_manager_graph(manager_services)
    return await manager_graph.ainvoke({"request": request}, config=config)


async def run_manager(request: str, out_dir: str | Path | None = None) -> ManagerRunResult:
    """Run the analytics manager end-to-end and return a concise result."""
    settings = get_settings()
    llm = create_manager_llm()
    capabilities = MCPCapabilities()
    tracer = AgentTracer()
    try:
        state = await _run_request(
            request,
            llm=llm,
            analyst_llm=llm,  # ManagerLLMClient extends LLMClient: one shared client
            capabilities=capabilities,
            manager_max_attempts=settings.manager_max_attempts,
            analyst_max_attempts=settings.agent_max_attempts,
            max_rows=settings.max_rows,
            tracer=tracer,
        )
    finally:
        await capabilities.close()
        # Export what was collected even if the run crashed mid-flight.
        tracer.flush()
    if out_dir is not None:
        _write_artifacts(Path(out_dir), request, state)
    return ManagerRunResult(
        report=state.get("report"),
        status=str(state["status"]) if state.get("status") is not None else None,
        sub_questions=state.get("sub_questions", []),
        evidence=state.get("evidence", []),
        # Only the retry node writes 'attempts'; a clean run makes exactly 1
        # attempt round for each retryable stage.
        attempts=state.get("attempts", 1),
        state=state,
        error=_run_error(state),
    )


def _write_artifacts(out_dir: Path, request: str, state: ManagerState) -> None:
    """Persist ``report.md`` (when a report exists) and ``evidence.json``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    report = state.get("report")
    if report:
        (out_dir / "report.md").write_text(report, encoding="utf-8")
    payload = {
        "request": request,
        "status": str(state["status"]) if state.get("status") is not None else None,
        "attempts": state.get("attempts", 0),
        "sub_questions": state.get("sub_questions", []),
        "sub_analysis_errors": state.get("sub_analysis_errors", []),
        "decomposition_error": state.get("decomposition_error"),
        "groundedness_error": state.get("groundedness_error"),
        "evidence": [dataclasses.asdict(record) for record in state.get("evidence", [])],
    }
    (out_dir / "evidence.json").write_text(
        json.dumps(payload, indent=2, default=str), encoding="utf-8"
    )


def _parse_args(argv: list[str]) -> tuple[str, bool, Path | None]:
    import argparse  # noqa: PLC0415 (local: keeps module import light like the agent CLI)

    parser = argparse.ArgumentParser(
        prog="python -m app.manager",
        description="Run the autonomous analytics manager on a management request.",
    )
    parser.add_argument("request", help="The high-level management request to analyze.")
    parser.add_argument("--json", action="store_true", help="Print the result as JSON.")
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Optional directory to write report.md and evidence.json into.",
    )
    args = parser.parse_args(argv)
    return args.request, args.json, args.out


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    request, as_json, out_dir = _parse_args(args)
    try:
        result = asyncio.run(run_manager(request, out_dir))
    except ValueError as error:
        # Configuration errors (e.g. LLM_PROVIDER=openrouter without an API
        # key) fail fast with a clean message instead of a traceback.
        raise SystemExit(f"Configuration error: {error}") from error
    if as_json:
        print(
            json.dumps(
                {
                    "report": result.report,
                    "status": result.status,
                    "sub_questions": result.sub_questions,
                    "attempts": result.attempts,
                    "error": result.error,
                },
                indent=2,
            )
        )
        return
    print(f"Status: {result.status}")
    print(f"Attempts: {result.attempts}")
    print(f"Sub-analyses: {len(result.evidence)}")
    for question in result.sub_questions:
        print(f"  - {question}")
    if result.error:
        print(f"Error: {result.error}")
    print()
    print(result.report if result.report else "(no report produced)")


if __name__ == "__main__":
    main()
