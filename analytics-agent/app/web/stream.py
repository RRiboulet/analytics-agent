"""Server-Sent Events between a run's progress sink and the browser (spec D4).

A browser cannot use ``EventSource`` here because the run needs a POST body, so
the page reads the response with ``fetch`` and parses SSE frames itself.
``sse-starlette`` supplies ``EventSourceResponse``/``ServerSentEvent``.

Four frame names make up the contract:

* ``queued`` — the request is waiting for a free run slot (spec R8/D6): the
  process-wide limit is full, so the run has not started yet. Sent immediately,
  before the first ``progress`` frame, and only to a request that had to wait;
  a visitor waits rather than seeing an error, so silence would be wrong. It is
  not part of the run's own event sequence (``seq``): the run has not begun.
* ``progress`` — one ``ProgressEvent`` per frame, as ``ProgressEvent.as_dict``
  (``type``, ``seq`` and the fields that apply). The manager's own stages and
  every nested analyst stage arrive on this one ordered stream. The run's first
  progress frame also carries ``mode``, so a consumer knows which pipeline it is
  reading before the terminal payload (spec R5).
* ``final`` — the run ended with a result: mode, status, report, answer, elapsed
  wall time, the run's real MCP call counts and the per-question evidence. Both
  modes use this one payload shape: ``answer`` is set for an analyst run,
  ``report`` for a manager run, and the page chooses which to present from the
  ``mode`` rather than from whichever field happens to be non-empty.
* ``failed`` — the run *raised*. The manager emits no terminal progress event
  when it raises (a stream simply ends), so the stream itself plays that part
  and reports the exception text. ``final`` is never sent in this case.

``QueueSink`` is the bridge from the synchronous ``ProgressSink`` protocol:
``emit`` does ``put_nowait`` and returns, so observing a run never delays it
(spec D3/R10). The queue is unbounded and drained by the same event loop, so a
slow reader grows memory rather than blocking the run — the documented contract
is that a sink must not block; only the raising half is enforceable. The same
queue carries the queue notice, because ordering is part of the contract: the
``queued`` frame can never overtake the first ``progress`` frame.
"""

import asyncio
import json
import time
from collections.abc import AsyncIterator, Mapping
from contextlib import suppress
from typing import Any

from sse_starlette.sse import ServerSentEvent

from app.progress import ProgressEvent
from app.web.composition import (
    QUEUED_EVENT_TYPE,
    QueuedNotice,
    RunMode,
    RunOutcome,
    RunService,
)

# What one stream carries: the run's progress, plus the notice that it queued.
QueueItem = ProgressEvent | QueuedNotice


class QueueSink:
    """Progress channel that hands events to an ``asyncio`` queue, never blocking.

    Called from the run's event loop (same loop as the drain), so
    ``put_nowait`` is safe and an unbounded queue cannot raise ``QueueFull``.
    """

    def __init__(self) -> None:
        self._queue: asyncio.Queue[QueueItem] = asyncio.Queue()

    def emit(self, event: ProgressEvent) -> None:
        self._queue.put_nowait(event)

    def emit_queued(self, notice: QueuedNotice) -> None:
        self._queue.put_nowait(notice)

    async def get(self) -> QueueItem:
        return await self._queue.get()

    def get_nowait(self) -> QueueItem | None:
        try:
            return self._queue.get_nowait()
        except asyncio.QueueEmpty:
            return None


def _json(payload: Mapping[str, Any]) -> str:
    # ``default=str``: evidence rows can carry ``Decimal``/``date`` values from
    # the database. The page displays them; a frame must never fail to encode.
    return json.dumps(payload, default=str)


def _queued_frame(notice: QueuedNotice) -> ServerSentEvent:
    payload = {
        "type": QUEUED_EVENT_TYPE,
        "state": "queued",
        "runs_ahead": notice.runs_ahead,
        "limit": notice.limit,
    }
    return ServerSentEvent(event="queued", data=_json(payload))


def _progress_frame(event: ProgressEvent, *, mode: RunMode | None = None) -> ServerSentEvent:
    payload = event.as_dict()
    if mode is not None:
        # Only the run's first progress frame: the mode belongs to the stream,
        # not to each event, and repeating it on every frame would bloat it.
        payload["mode"] = mode.value
    return ServerSentEvent(event="progress", data=_json(payload))


def _frame(item: QueueItem, *, mode: RunMode | None = None) -> ServerSentEvent:
    """One queue item, as the frame name the page dispatches on."""
    if isinstance(item, QueuedNotice):
        return _queued_frame(item)
    return _progress_frame(item, mode=mode)


def _final_frame(mode: RunMode, outcome: RunOutcome, elapsed: float) -> ServerSentEvent:
    payload = {
        "mode": mode.value,
        "status": outcome.status,
        "report": outcome.report,
        "answer": outcome.answer,
        "error": outcome.error,
        "elapsed_seconds": round(elapsed, 3),
        "tool_calls": dict(outcome.tool_calls),
        "tool_call_total": sum(outcome.tool_calls.values()),
        "sub_questions": list(outcome.sub_questions),
        "follow_up_questions": list(outcome.follow_up_questions),
        "report_attempt": outcome.report_attempt,
        "evidence": outcome.evidence,
    }
    return ServerSentEvent(event="final", data=_json(payload))


def _failed_frame(mode: RunMode, error: BaseException, elapsed: float) -> ServerSentEvent:
    payload = {
        "mode": mode.value,
        "status": "failed",
        # Same shape the run's own failure events use: the exception class is
        # part of the message, so a transport error is not mistaken for a
        # model-reported failure.
        "error": f"{type(error).__name__}: {error}",
        "elapsed_seconds": round(elapsed, 3),
    }
    return ServerSentEvent(event="failed", data=_json(payload))


async def stream_run(
    service: RunService,
    mode: RunMode,
    request: str,
    sink: QueueSink,
) -> AsyncIterator[ServerSentEvent]:
    """Run one request and yield its frames in arrival order.

    The run starts as a task so the generator can await the queue and the run
    at once. Every event emitted before the run finishes is guaranteed to be in
    the queue by the time the task completes (``emit`` is synchronous), so a
    completed run is drained fully before the terminal frame. One ordered
    stream: events are yielded as they arrive, and ``seq`` orders them. The
    first progress frame names the mode (spec R5); the terminal frame repeats
    it with the outcome.

    ``elapsed`` starts here, before the queue check, so a queued request's final
    frame reports the wait it saw — the wall time the visitor actually spent.
    """
    started = time.perf_counter()
    run_task = asyncio.create_task(service.run(mode, request, sink))
    first_progress = True
    try:
        while True:
            getter = asyncio.create_task(sink.get())
            done, _ = await asyncio.wait({getter, run_task}, return_when=asyncio.FIRST_COMPLETED)
            if getter in done:
                # A progress event, or the ``queued`` notice the service emits
                # before it waits for a slot.
                item = getter.result()
                yield _frame(item, mode=mode if first_progress else None)
                if not isinstance(item, QueuedNotice):
                    first_progress = False
                continue
            # The run finished first. It emits synchronously, so anything it
            # emitted is already queued: cancel the pending read and drain.
            getter.cancel()
            with suppress(asyncio.CancelledError):
                await getter
            while (item := sink.get_nowait()) is not None:
                yield _frame(item, mode=mode if first_progress else None)
                if not isinstance(item, QueuedNotice):
                    first_progress = False
            break

        elapsed = time.perf_counter() - started
        try:
            outcome = run_task.result()
        except Exception as error:
            # A raising run has no result and emitted no terminal event; the
            # stream supplies the terminal frame with the exception text.
            yield _failed_frame(mode, error, elapsed)
        else:
            yield _final_frame(mode, outcome, elapsed)
    finally:
        # The browser went away (or the response errored) mid-run: stop the
        # run rather than burning model calls for a page nobody is reading.
        # The run's own cleanup (capabilities, artifacts) runs on cancellation.
        if not run_task.done():
            run_task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await run_task
