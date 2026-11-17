"""Programmatic ``run_manager`` and the ``python -m app.manager`` CLI.

The manager composes grounded analyst runs (M7, D009): it connects to the
running MCP server through one shared read-only ``MCPCapabilities`` instance,
uses one ``ManagerLLMClient`` for both its own discrete calls and the analyst
sub-runs (the client extends ``LLMClient``, so it serves both protocols), and
runs the manager LangGraph workflow — decompose, sub-analyses, a bounded
follow-up round when the inspect stage requests one (M7.5), synthesize. The
sub-analyses (and the follow-up round) fan out as concurrent analyst runs
bounded by ``MANAGER_MAX_PARALLELISM`` (M8, D1); each run is handed its own
counting wrapper around the shared capability, so per-evidence call counts
stay exact under that concurrency (D2).
With Langfuse credentials the whole run
is traced under one manager trace: manager run → analyst sub-runs → nodes.
Tracing stays fail-open; a ``--out`` directory persists the report and the
evidence for inspection.

Failure scope worth knowing: the state-level failure modes (a groundedness
violation, exhausted retries, every sub-analysis failing) return a result that
still reports what the run cost. A run that *raises* — the MCP handshake or a
tool transport failing mid-flight, which the analyst graph deliberately does not
swallow — produces no result for the caller, but its ``--out`` artifacts are
still written before the exception propagates, carrying the calls it spent and
the exception text, so a crash remains diagnosable from disk alone.
"""

import asyncio
import dataclasses
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.agent.capabilities import Capabilities, CountingCapabilities, MCPCapabilities
from app.agent.graph import AgentServices, build_graph
from app.agent.state import AgentState
from app.agent.tracing import AgentTracer
from app.config import get_settings
from app.manager.evidence import EvidenceRecord
from app.manager.graph import (
    DEFAULT_MAX_PARALLELISM,
    ManagerServices,
    build_manager_graph,
)
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
    # M7.5: the bounded follow-up questions decided by the inspect stage
    # (empty when no follow-up was requested).
    follow_up_questions: list[str] = field(default_factory=list)
    # The rejected report text behind a groundedness violation (M7.3): the
    # run failed and no report was produced, but the attempt is kept for
    # inspection — callers can see exactly what the model fabricated.
    report_attempt: str | None = None
    error: str | None = None
    # MCP tool calls made by the run, per tool name. Counting is installed at
    # the composition root, so every caller sees the run's cost without
    # passing anything extra; the field is defaulted to keep older
    # constructions valid. Appended last on purpose: inserting it anywhere else
    # would shift ``error``'s position for a positional caller.
    tool_calls: dict[str, int] = field(default_factory=dict)


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
    manager_max_parallelism: int = DEFAULT_MAX_PARALLELISM,
) -> ManagerState:
    """Run one management request end-to-end with injected dependencies.

    The tracer config (fail-open) is shared by the manager graph and every
    analyst sub-run so a trace shows manager run → analyst sub-runs → nodes.

    ``capabilities`` is the shared read-only MCP surface. Every analyst
    sub-run gets its own counting decorator around it (installed by the
    manager graph, D2), so each evidence record owns exactly the calls its
    run spent even when runs overlap. When the caller installed a counter at
    this level too (``run_manager`` does), that outer wrapper additionally
    reports the whole run's total.
    """
    config = tracer.run_config(request) if tracer is not None else None

    async def run_analyst(question: str, surface: Capabilities | None = None) -> AgentState:
        # One analyst graph per sub-run: the capability surface is per-run
        # (a fresh counter), so the compiled graph cannot be shared. Graph
        # construction is pure wiring; the model and MCP calls dominate cost.
        analyst_services = AgentServices(
            llm=analyst_llm,
            capabilities=surface if surface is not None else capabilities,
            max_attempts=analyst_max_attempts,
            max_rows=max_rows,
        )
        analyst_graph = build_graph(analyst_services)
        return await analyst_graph.ainvoke(
            {"question": question, "status": "planning", "attempts": 0}, config=config
        )

    manager_services = ManagerServices(
        llm=llm,
        run_analyst=run_analyst,
        max_attempts=manager_max_attempts,
        max_parallelism=manager_max_parallelism,
        capabilities=capabilities,
    )
    manager_graph = build_manager_graph(manager_services)
    return await manager_graph.ainvoke({"request": request}, config=config)


async def run_manager(request: str, out_dir: str | Path | None = None) -> ManagerRunResult:
    """Run the analytics manager end-to-end and return a concise result."""
    settings = get_settings()
    llm = create_manager_llm()
    # One counting wrapper around the single capability instance: CLI, library
    # and harness callers all get real per-tool counts through the result,
    # while the manager package stays free of evaluation imports. The manager
    # nests a per-run counter inside it, so this outer count is the run total
    # and each evidence record carries its own share of it.
    capabilities = CountingCapabilities(MCPCapabilities())
    tracer = AgentTracer()
    state: ManagerState = {}
    tool_calls: dict[str, int] = {}
    crash: str | None = None
    try:
        state = await _run_request(
            request,
            llm=llm,
            analyst_llm=llm,  # ManagerLLMClient extends LLMClient: one shared client
            capabilities=capabilities,
            manager_max_attempts=settings.manager_max_attempts,
            analyst_max_attempts=settings.agent_max_attempts,
            max_rows=settings.max_rows,
            manager_max_parallelism=settings.manager_max_parallelism,
            tracer=tracer,
        )
    except Exception as error:
        # Keep the failure text for the artifact below, then let it propagate:
        # a crash is still a crash for the caller.
        crash = f"{type(error).__name__}: {error}"
        raise
    finally:
        # Snapshot before closing, and do it here rather than on the happy path
        # only: the state-level failures and a crashed run both have to report
        # what they spent.
        tool_calls = capabilities.snapshot()
        await capabilities.close()
        # Export what was collected even if the run crashed mid-flight.
        tracer.flush()
        if out_dir is not None:
            try:
                _write_artifacts(Path(out_dir), request, state, tool_calls, crash=crash)
            except OSError as artifact_error:
                # Artifacts are diagnostics: never mask the run's own outcome
                # with a disk error, but say so rather than hiding it.
                print(
                    f"warning: could not write artifacts to {out_dir}: {artifact_error}",
                    file=sys.stderr,
                )
    return ManagerRunResult(
        report=state.get("report"),
        status=str(state["status"]) if state.get("status") is not None else None,
        sub_questions=state.get("sub_questions", []),
        evidence=state.get("evidence", []),
        # Only the retry node writes 'attempts'; a clean run makes exactly 1
        # attempt round for each retryable stage.
        attempts=state.get("attempts", 1),
        follow_up_questions=state.get("follow_up_questions", []),
        report_attempt=state.get("report_attempt"),
        tool_calls=tool_calls,
        state=state,
        error=_run_error(state),
    )


def _write_artifacts(
    out_dir: Path,
    request: str,
    state: ManagerState,
    tool_calls: dict[str, int],
    *,
    crash: str | None = None,
) -> None:
    """Persist ``report.md`` (when a report exists) and ``evidence.json``.

    ``tool_calls`` is required on purpose: a caller that forgets it would write
    a genuine-looking zero for a run that made calls. ``crash`` carries the
    exception text when the run died before producing a state-level error, so a
    crashed run leaves the counts it spent and the reason next to them.
    Run-level totals sit at the top level; each evidence record carries its own
    share, and the ``unattributed_tool_calls`` mirror makes a coverage gap
    visible at runtime rather than only in the tests that assert the invariant.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    report = state.get("report")
    if report:
        (out_dir / "report.md").write_text(report, encoding="utf-8")
    records = state.get("evidence", [])
    attributed = sum(sum(record.tool_calls.values()) for record in records)
    payload = {
        "request": request,
        "status": str(state["status"]) if state.get("status") is not None else None,
        "attempts": state.get("attempts", 0),
        "sub_questions": state.get("sub_questions", []),
        "tool_calls": dict(tool_calls),
        "total_tool_calls": sum(tool_calls.values()),
        "unattributed_tool_calls": sum(tool_calls.values()) - attributed,
        "sub_analysis_errors": state.get("sub_analysis_errors", []),
        "decomposition_error": state.get("decomposition_error"),
        "groundedness_error": state.get("groundedness_error"),
        "report_attempt": state.get("report_attempt"),
        "follow_up_questions": state.get("follow_up_questions", []),
        "follow_up_errors": state.get("follow_up_errors", []),
        "follow_up_rounds": state.get("follow_up_rounds", 0),
        "inspect_error": state.get("inspect_error"),
        # The run's own failure, or the exception that killed it before it had
        # a state-level error to report.
        "error": crash or _run_error(state),
        "evidence": [dataclasses.asdict(record) for record in records],
    }
    (out_dir / "evidence.json").write_text(
        json.dumps(payload, indent=2, default=str), encoding="utf-8"
    )


def _format_tool_calls(tool_calls: dict[str, int]) -> str:
    """One readable CLI line: ``MCP calls: 9 (query 6, list_tables 3)``."""
    total = sum(tool_calls.values())
    if not tool_calls:
        return f"MCP calls: {total}"
    detail = ", ".join(
        f"{name} {count}"
        for name, count in sorted(tool_calls.items(), key=lambda item: (-item[1], item[0]))
    )
    return f"MCP calls: {total} ({detail})"


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
                    "follow_up_questions": result.follow_up_questions,
                    "attempts": result.attempts,
                    "tool_calls": result.tool_calls,
                    "report_attempt": result.report_attempt,
                    "error": result.error,
                },
                indent=2,
            )
        )
        return
    print(f"Status: {result.status}")
    print(f"Attempts: {result.attempts}")
    print(f"Sub-analyses: {len(result.evidence)}")
    print(_format_tool_calls(result.tool_calls))
    for question in result.sub_questions:
        print(f"  - {question}")
    if result.follow_up_questions:
        print(f"Follow-ups: {len(result.follow_up_questions)}")
        for question in result.follow_up_questions:
            print(f"  - {question}")
    if result.error:
        print(f"Error: {result.error}")
    print()
    print(result.report if result.report else "(no report produced)")


if __name__ == "__main__":
    main()
