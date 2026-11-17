"""Compose one web request into one run: the seam the web layer owns.

The page submits a request; this module turns it into a run and reports the
outcome in a shape the stream can serialize. Everything below it is the
existing system: a management request is the manager entrypoint
(``run_manager``), a single question is the analyst entrypoint
(``run_agent``), and both consume the MCP server over HTTP — the web layer
imports no database client and builds no SQL.

Both modes share one start point, one concurrency limit and one wire contract:
``RunService`` dispatches by ``RunMode`` and a runner only has to return a
``RunOutcome``. That is why analyst mode needed no change to the queue or the
stream — the mode is data, not a second pipeline.

Concurrency (spec R8/D6) lives here too, because this is the one place a run
begins: ``RunService`` holds a process-wide semaphore sized by
``WEB_MAX_CONCURRENT_RUNS``. A request that finds every slot taken is *not*
rejected and not silently throttled — it is told it is queued, on the same
stream, and starts when a slot frees. The bound applies to both modes.

The runners are injectable so the web tests stay hermetic: no MCP, no
database, no model.
"""

import asyncio
import dataclasses
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from app.progress import ProgressEvent, ProgressSink


class RunMode(StrEnum):
    """A kind of request the page can submit (spec D5/R7).

    One flow, two modes: ``ANALYST`` answers a single question without any
    manager stage (the fast first interaction) and ``MANAGER`` runs the full
    decomposition/inspect/synthesis pipeline. Both stream through the same sink
    and end in the same final payload; the mode is the only difference.
    """

    ANALYST = "analyst"
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
    # The grounded answer of an analyst-mode run; ``None`` for the manager,
    # which reports a report instead. Kept separate from ``report`` so the page
    # can never present one as the other.
    answer: str | None = None
    tool_calls: dict[str, int] = field(default_factory=dict)
    sub_questions: list[str] = field(default_factory=list)
    follow_up_questions: list[str] = field(default_factory=list)
    report_attempt: str | None = None
    evidence: list[dict[str, Any]] = field(default_factory=list)


# A runner starts one run and returns its outcome. The sink is the run's
# progress channel (synchronous, fail-open); the stream drains it.
RunRunner = Callable[[str, ProgressSink], Awaitable[RunOutcome]]

# ``WEB_MAX_CONCURRENT_RUNS`` default: two visitors watch runs at once, a third
# waits. Each run is a whole manager run, which already fans out up to
# ``MANAGER_MAX_PARALLELISM`` analyst runs against the model and the database
# pool, so the bound stays small on purpose.
DEFAULT_MAX_CONCURRENT_RUNS = 2

# The ``type`` of the frame a queued visitor receives before its run starts.
QUEUED_EVENT_TYPE = "run.queued"


@dataclass(frozen=True)
class QueuedNotice:
    """Sent the moment a request has to wait for a slot (spec R8, D6).

    Deliberately not a ``ProgressEvent``: the run has not started, so this
    belongs to the web surface rather than to the run's own event sequence
    (which starts at 1 when the run begins). ``runs_ahead`` is what a visitor
    counts — how many runs must finish first — not a slot index.
    """

    runs_ahead: int
    limit: int


class RunChannel(Protocol):
    """Where a web run reports: progress events, plus its queue state.

    A ``ProgressSink`` is what a runner needs (it only emits events); the
    channel adds the one message that exists before the run does. Keeping it a
    separate protocol means the manager/analyst graphs never learn about
    ``WEB_MAX_CONCURRENT_RUNS``.
    """

    def emit(self, event: ProgressEvent) -> None:
        """Receive one progress event (the ``ProgressSink`` half)."""
        ...

    def emit_queued(self, notice: QueuedNotice) -> None:
        """Receive the notice that this run is waiting for a free slot."""
        ...


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


async def run_analyst_request(question: str, sink: ProgressSink) -> RunOutcome:
    """Run one single question through the existing analyst entrypoint.

    Imported lazily for the same reason the manager runner is: the web process
    starts (and ``--check`` passes) without loading the model/MCP machinery.
    ``run_agent`` builds a fresh graph per invocation (spec D10), so two
    concurrent analyst runs never share a sink or an event sequence.

    The outcome is the analyst's own result projected onto the same
    ``RunOutcome`` the manager uses. The single question is reported as one
    evidence record (index 0) in exactly the manager's evidence shape, so the
    page needs no second renderer: rows, SQL, answer, error and per-run call
    counts arrive with the terminal payload. A failed run still carries the
    last SQL it generated or executed, which is what the page shows instead of
    an empty answer.
    """
    from app.agent.entrypoint import run_agent  # noqa: PLC0415

    result = await run_agent(question, sink=sink)
    state = result.state
    # Same projection as ``EvidenceRecord.from_agent_state``: the bounded SQL
    # that actually ran when there is one, otherwise the last generated SQL.
    error = state.get("llm_error") or state.get("query_error") or state.get("validation_error")
    evidence = [
        {
            "sub_index": 0,
            "sub_question": question,
            "status": result.status,
            "sql": result.sql,
            "rows": state.get("result"),
            "answer": result.answer,
            "error": error,
            "is_follow_up": False,
            "tool_calls": dict(result.tool_calls),
        }
    ]
    return RunOutcome(
        status=result.status,
        report=None,
        error=result.error,
        answer=result.answer,
        tool_calls=dict(result.tool_calls),
        evidence=evidence,
    )


def default_runners() -> dict[RunMode, RunRunner]:
    """The production runners; tests inject their own mapping instead."""
    return {
        RunMode.ANALYST: run_analyst_request,
        RunMode.MANAGER: run_manager_request,
    }


class RunService:
    """Start one run for one mode — the only place a run begins.

    ``runners`` is the injection seam (the ASGI tests pass a fake and assert
    the frames it produces). ``max_concurrent_runs`` is the process-wide cap
    (``WEB_MAX_CONCURRENT_RUNS``): one ``RunService`` per app, one app per
    process. The same slot pool admits manager and analyst runs, so the limit
    bounds the process rather than one mode.
    """

    def __init__(
        self,
        runners: Mapping[RunMode, RunRunner] | None = None,
        *,
        max_concurrent_runs: int = DEFAULT_MAX_CONCURRENT_RUNS,
    ) -> None:
        if max_concurrent_runs < 1:
            # Settings already validate this; a direct caller (a test, a future
            # embedding) gets the same guarantee rather than a deadlock.
            raise ValueError("max_concurrent_runs must be at least 1")
        self._runners: dict[RunMode, RunRunner] = (
            dict(runners) if runners is not None else default_runners()
        )
        self._limit = max_concurrent_runs
        self._slots: asyncio.Semaphore | None = None
        self._slots_loop: asyncio.AbstractEventLoop | None = None
        # Counters for the queue notice's position. The event loop is the only
        # mutator and there is no ``await`` between a counter change and the
        # matching semaphore change, so they cannot disagree.
        self._active = 0
        self._waiting = 0

    def modes(self) -> tuple[RunMode, ...]:
        """The modes this process can actually run, in declaration order."""
        return tuple(self._runners)

    @property
    def max_concurrent_runs(self) -> int:
        """The process-wide cap on in-flight runs."""
        return self._limit

    def _slots_for_running_loop(self) -> asyncio.Semaphore:
        """The process-wide semaphore, created on the loop that uses it.

        ``asyncio`` primitives belong to an event loop, and the app is built
        outside one (``create_app``), so the semaphore is created at first use
        — under uvicorn that is the server's single loop, for the process's
        lifetime. Re-creating it when the loop changes only ever happens in
        tests, which drive one app from several short-lived loops.
        """
        loop = asyncio.get_running_loop()
        if self._slots is None or self._slots_loop is not loop:
            self._slots = asyncio.Semaphore(self._limit)
            self._slots_loop = loop
            self._active = 0
            self._waiting = 0
        return self._slots

    async def run(self, mode: RunMode, request: str, channel: RunChannel) -> RunOutcome:
        """Compose and await one run, inside the process-wide limit.

        The slot is acquired before the runner is awaited and released in a
        ``finally``, so every outcome frees it: a returned result, a run that
        fails at the state level, a runner that raises, and cancellation (the
        browser going away) which unwinds through the same ``finally``. A
        request that has to wait says so first, on its own stream, instead of
        streaming silence.
        """
        runner = self._runners.get(mode)
        if runner is None:
            raise ValueError(f"mode {mode.value!r} is not available")

        slots = self._slots_for_running_loop()
        if slots.locked():
            # ``locked`` is the public "cannot be acquired now" test: every
            # slot is taken, or earlier waiters are ahead of us in FIFO order.
            channel.emit_queued(
                QueuedNotice(runs_ahead=self._active + self._waiting, limit=self._limit)
            )
            self._waiting += 1
            try:
                await slots.acquire()
            finally:
                # Cancelled while queued: nobody held a slot for us, so there
                # is nothing to release — only our place in line to give up
                # (the semaphore's own ``acquire`` does that bookkeeping).
                self._waiting -= 1
        else:
            # An uncontended ``acquire`` never suspends, so the queued decision
            # above and this acquire cannot interleave with another request.
            await slots.acquire()

        self._active += 1
        try:
            return await runner(request, channel)
        finally:
            self._active -= 1
            slots.release()
