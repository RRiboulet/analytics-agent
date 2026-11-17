"""Compose one web request into one run: the seam the web layer owns.

The page submits a request; this module turns it into a run and reports the
outcome in a shape the stream can serialize. Everything below it is the
existing system: a management request is the manager entrypoint
(``run_manager``), which consumes the MCP server over HTTP — the web layer
imports no database client and builds no SQL.

Three future tickets hook in here, which is why composition has exactly one
start point:

* ticket 07 (analyst mode) adds ``RunMode.ANALYST`` and its runner;
* ticket 08 (bounded visitors) wraps the single ``await runner(...)`` in
  ``RunService.run`` in the process-wide semaphore and emits a queued event;
* ticket 06 (rich page) reads the extra final-payload fields (per-record
  evidence, including each record's own call counts) without changing this
  contract.

The runners are injectable so the web tests stay hermetic: no MCP, no
database, no model.
"""

import dataclasses
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from app.progress import ProgressSink


class RunMode(StrEnum):
    """A kind of request the page can submit (spec D5).

    One flow, two modes eventually: ``ANALYST`` (one question, no manager
    stages) arrives with ticket 07. Until then manager-only is the honest
    surface, and the endpoint rejects the rest instead of pretending.
    """

    MANAGER = "manager"


@dataclass(frozen=True)
class RunOutcome:
    """What the page needs to know when a run ends.

    A concise projection of the entrypoint's result: the status and report, the
    run's actual MCP call counts (``ManagerRunResult.tool_calls``), and the
    failure text a rejected run reports. ``evidence`` is the per-sub-question
    record list (question, SQL, rows, answer, error, tool calls) that ticket 06
    renders in the pipeline view — serialized here so the wire contract does
    not need to change when that view lands.
    """

    status: str | None
    report: str | None
    error: str | None = None
    tool_calls: dict[str, int] = field(default_factory=dict)
    sub_questions: list[str] = field(default_factory=list)
    follow_up_questions: list[str] = field(default_factory=list)
    report_attempt: str | None = None
    evidence: list[dict[str, Any]] = field(default_factory=list)


# A runner starts one run and returns its outcome. The sink is the run's
# progress channel (synchronous, fail-open); the stream drains it.
RunRunner = Callable[[str, ProgressSink], Awaitable[RunOutcome]]


async def run_manager_request(request: str, sink: ProgressSink) -> RunOutcome:
    """Run one management request through the existing manager entrypoint.

    Imported lazily on purpose: the web process must start — and ``--check``
    must pass — without loading the model/MCP machinery, and this function is
    the only place the web package reaches a run entrypoint. ``run_manager``
    talks to the MCP server over its configured URL, so no database client is
    imported here or there.
    """
    from app.manager.entrypoint import run_manager  # noqa: PLC0415

    result = await run_manager(request, sink=sink)
    return RunOutcome(
        status=result.status,
        report=result.report,
        error=result.error,
        tool_calls=dict(result.tool_calls),
        sub_questions=list(result.sub_questions),
        follow_up_questions=list(result.follow_up_questions),
        report_attempt=result.report_attempt,
        # The manager's evidence records are plain dataclasses; flatten them
        # now so the stream module never needs to know their type.
        evidence=[dataclasses.asdict(record) for record in result.evidence],
    )


def default_runners() -> dict[RunMode, RunRunner]:
    """The production runners; tests inject their own mapping instead."""
    return {RunMode.MANAGER: run_manager_request}


class RunService:
    """Start one run for one mode — the only place a run begins.

    ``runners`` is the injection seam (the ASGI tests pass a fake and assert
    the frames it produces). Ticket 07 adds an analyst entry here; ticket 08
    wraps the ``await`` below in the process-wide ``asyncio.Semaphore`` and
    emits ``queued`` when the slot is not free, so neither ticket needs to
    touch the endpoint or the stream.
    """

    def __init__(self, runners: Mapping[RunMode, RunRunner] | None = None) -> None:
        self._runners: dict[RunMode, RunRunner] = (
            dict(runners) if runners is not None else default_runners()
        )

    def modes(self) -> tuple[RunMode, ...]:
        """The modes this process can actually run, in declaration order."""
        return tuple(self._runners)

    async def run(self, mode: RunMode, request: str, sink: ProgressSink) -> RunOutcome:
        """Compose and await one run (ticket 08's semaphore wraps this await)."""
        runner = self._runners.get(mode)
        if runner is None:
            raise ValueError(f"mode {mode.value!r} is not available")
        return await runner(request, sink)
