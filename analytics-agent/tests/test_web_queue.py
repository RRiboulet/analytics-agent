"""Bounded visitors (M8 ticket 08): queue, don't reject (spec R8, D6).

The web process admits ``WEB_MAX_CONCURRENT_RUNS`` runs at a time. A visitor
beyond the cap is not punished with an error and not left in silence: the stream
says ``queued`` immediately and the run starts when a slot frees.

Two levels are tested:

* the composition seam (``RunService`` + ``stream_run``) with gates the test
  opens by hand, so ordering is deterministic rather than timing-based — a fake
  runner holds its slot until released, and the frames are asserted exactly;
* the HTTP surface once, with two real concurrent requests through the ASGI
  app (``httpx.AsyncClient`` + ``ASGITransport`` on one event loop), to prove
  the endpoint wires the queue into the wire contract and that a queued client
  still receives a normal stream afterwards. Every wait there is bounded by
  ``asyncio.wait_for``, so a regression fails instead of hanging.

Every test is hermetic: no MCP, no database, no model. What these tests do *not*
prove: that a real manager run behaves under load (nothing here touches one).
"""

import asyncio
import json
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

import app.web.app as web_app
from app.config import Settings
from app.progress import ProgressEmitter, ProgressEventType
from app.web.app import create_app
from app.web.composition import (
    QUEUED_EVENT_TYPE,
    QueuedNotice,
    RunMode,
    RunOutcome,
    RunService,
)
from app.web.stream import QueueSink, stream_run

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TICK = 0.005


# ---------------------------------------------------------------------------
# Seams and fakes
# ---------------------------------------------------------------------------


def frames_of(events) -> list[tuple[str, dict]]:
    """``(event name, JSON payload)`` for the frames a stream yielded."""
    return [(event.event, json.loads(event.data)) for event in events]


class RecordingChannel(QueueSink):
    """A ``QueueSink`` that also records the queue notices it was given.

    Independent of any timing: ``queued`` fires the moment the service decides
    a request has to wait, which is the ordering the tests assert on.
    """

    def __init__(self) -> None:
        super().__init__()
        self.notices: list[QueuedNotice] = []
        self.queued = asyncio.Event()

    def emit_queued(self, notice: QueuedNotice) -> None:
        self.notices.append(notice)
        self.queued.set()
        super().emit_queued(notice)


class HeldRunner:
    """Fake run that occupies its slot until the test releases it.

    It emits the two events a real run would (started, then completed) so the
    frames can be checked for loss and duplication, and records starts and
    finishes in order so admission order is observable.
    """

    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.release = asyncio.Event()
        self.started: list[str] = []
        self.finished: list[str] = []
        self.cancelled = 0
        self.running = 0

    async def __call__(self, request: str, sink) -> RunOutcome:
        self.started.append(request)
        self.running += 1
        emitter = ProgressEmitter(sink)
        emitter.emit(ProgressEventType.RUN_STARTED, question=request)
        try:
            await self.release.wait()
            if self.error is not None:
                raise self.error
            emitter.emit(
                ProgressEventType.RUN_COMPLETED,
                status="completed",
                report=f"report: {request}",
            )
            return RunOutcome(status="completed", report=f"report: {request}")
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        finally:
            self.running -= 1
            self.finished.append(request)


class RaisingRunner:
    """Runner that raises on one request and succeeds on the others."""

    def __init__(self) -> None:
        self.requests: list[str] = []

    async def __call__(self, request: str, sink) -> RunOutcome:
        self.requests.append(request)
        if request == "boom":
            raise RuntimeError("mcp transport died")
        return RunOutcome(status="completed", report=f"report: {request}")


async def collect(stream) -> list:
    return [event async for event in stream]


async def wait_until(predicate, *, timeout: float = 2.0) -> None:
    """Await a condition the fakes make true, bounded (never hang the suite)."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("condition was not reached in time")
        await asyncio.sleep(TICK)


# ---------------------------------------------------------------------------
# One running stream, one queued stream (limit 1)
# ---------------------------------------------------------------------------


async def test_limit_one_runs_the_first_and_queues_the_second() -> None:
    runner = HeldRunner()
    service = RunService({RunMode.MANAGER: runner}, max_concurrent_runs=1)
    first, second = RecordingChannel(), RecordingChannel()

    first_task = asyncio.create_task(collect(stream_run(service, RunMode.MANAGER, "first", first)))
    await wait_until(lambda: runner.started == ["first"])

    second_task = asyncio.create_task(
        collect(stream_run(service, RunMode.MANAGER, "second", second))
    )
    # The second visitor is told it is queued *before* the first run ends...
    await asyncio.wait_for(second.queued.wait(), timeout=2)
    assert first_task.done() is False
    assert runner.started == ["first"], "the second run must not start yet"
    assert second.notices == [QueuedNotice(runs_ahead=1, limit=1)]
    assert first.notices == [], "a request that got a slot at once is not queued"

    # ...and starts, and completes normally, once the slot frees.
    runner.release.set()
    first_frames = frames_of(await asyncio.wait_for(first_task, timeout=2))
    second_frames = frames_of(await asyncio.wait_for(second_task, timeout=2))
    assert runner.finished == ["first", "second"]

    assert [name for name, _ in first_frames] == ["progress", "progress", "final"]
    assert [name for name, _ in second_frames] == [
        "queued",
        "progress",
        "progress",
        "final",
    ]
    assert second_frames[0][1] == {
        "type": QUEUED_EVENT_TYPE,
        "state": "queued",
        "runs_ahead": 1,
        "limit": 1,
    }

    # Both streams carry their run's events exactly once, in order, and a
    # correct final payload — the queued client gets a normal stream.
    for frames, request in ((first_frames, "first"), (second_frames, "second")):
        progress = [payload for name, payload in frames if name == "progress"]
        assert [event["seq"] for event in progress] == [1, 2]
        assert [event["type"] for event in progress] == [
            ProgressEventType.RUN_STARTED,
            ProgressEventType.RUN_COMPLETED,
        ]
        assert progress[0]["question"] == request
        final = frames[-1][1]
        assert final["mode"] == "manager"
        assert final["status"] == "completed"
        assert final["report"] == f"report: {request}"
        assert final["error"] is None


async def test_limit_two_admits_two_and_queues_the_third() -> None:
    runner = HeldRunner()
    service = RunService({RunMode.MANAGER: runner}, max_concurrent_runs=2)
    channels = [RecordingChannel() for _ in range(3)]

    tasks = [
        asyncio.create_task(collect(stream_run(service, RunMode.MANAGER, f"r{index}", channel)))
        for index, channel in enumerate(channels[:2])
    ]
    await wait_until(lambda: len(runner.started) == 2)

    tasks.append(
        asyncio.create_task(collect(stream_run(service, RunMode.MANAGER, "r2", channels[2])))
    )
    await asyncio.wait_for(channels[2].queued.wait(), timeout=2)
    assert sorted(runner.started) == ["r0", "r1"]
    assert channels[2].notices == [QueuedNotice(runs_ahead=2, limit=2)]
    assert channels[0].notices == [] and channels[1].notices == []

    runner.release.set()
    per_request = await asyncio.wait_for(asyncio.gather(*tasks), timeout=2)
    assert runner.finished[-1] == "r2", "the queued run starts last"
    assert [name for name, _ in frames_of(per_request[2])] == [
        "queued",
        "progress",
        "progress",
        "final",
    ]
    assert [frames_of(frames)[-1][1]["report"] for frames in per_request] == [
        "report: r0",
        "report: r1",
        "report: r2",
    ]


# ---------------------------------------------------------------------------
# Every outcome frees the slot
# ---------------------------------------------------------------------------


async def test_a_raising_run_releases_its_slot() -> None:
    runner = RaisingRunner()
    service = RunService({RunMode.MANAGER: runner}, max_concurrent_runs=1)

    boom = RecordingChannel()
    frames = frames_of(
        await asyncio.wait_for(
            collect(stream_run(service, RunMode.MANAGER, "boom", boom)), timeout=2
        )
    )
    assert [name for name, _ in frames] == ["failed"]
    assert frames[-1][1]["error"] == "RuntimeError: mcp transport died"

    after = RecordingChannel()
    frames = frames_of(
        await asyncio.wait_for(
            collect(stream_run(service, RunMode.MANAGER, "after", after)), timeout=2
        )
    )
    # Admitted immediately: the raised run freed the only slot.
    assert after.notices == []
    assert frames[-1][1]["report"] == "report: after"
    assert runner.requests == ["boom", "after"]


async def test_a_state_level_failure_releases_its_slot() -> None:
    class FailedOutcomeRunner:
        async def __call__(self, request: str, sink) -> RunOutcome:
            if request == "rejected":
                return RunOutcome(status="failed", report=None, error="groundedness violation")
            return RunOutcome(status="completed", report="ok")

    runner = FailedOutcomeRunner()
    service = RunService({RunMode.MANAGER: runner}, max_concurrent_runs=1)

    rejected = RecordingChannel()
    frames = frames_of(
        await asyncio.wait_for(
            collect(stream_run(service, RunMode.MANAGER, "rejected", rejected)), timeout=2
        )
    )
    assert frames[-1][1]["status"] == "failed"

    after = RecordingChannel()
    await asyncio.wait_for(collect(stream_run(service, RunMode.MANAGER, "after", after)), timeout=2)
    assert after.notices == []


# ---------------------------------------------------------------------------
# A disappeared client neither pins a slot nor leaves a run behind
# ---------------------------------------------------------------------------


async def test_disconnect_releases_the_slot_and_cancels_the_run() -> None:
    runner = HeldRunner()
    service = RunService({RunMode.MANAGER: runner}, max_concurrent_runs=1)
    channel = RecordingChannel()

    stream = stream_run(service, RunMode.MANAGER, "abandoned", channel)
    first = await asyncio.wait_for(anext(stream), timeout=2)
    assert first.event == "progress"
    assert runner.started == ["abandoned"]

    # The browser went away: the response generator is closed, exactly what a
    # disconnect does.
    await stream.aclose()

    assert runner.cancelled == 1, "the run must be cancelled, not left draining"
    assert runner.running == 0, "no run may still be in flight after a disconnect"

    # The next visitor's run is not gated, so admission is the only thing that
    # can make it slow: if the abandoned run had leaked its slot, this hangs.
    runner.release.set()
    after = RecordingChannel()
    frames = frames_of(
        await asyncio.wait_for(
            collect(stream_run(service, RunMode.MANAGER, "next", after)), timeout=2
        )
    )
    assert after.notices == [], "the freed slot admits the next visitor at once"
    assert frames[-1][1]["report"] == "report: next"
    assert runner.started == ["abandoned", "next"]


async def test_a_request_cancelled_while_queued_leaves_no_phantom_waiter() -> None:
    runner = HeldRunner()
    service = RunService({RunMode.MANAGER: runner}, max_concurrent_runs=1)

    first = RecordingChannel()
    first_task = asyncio.create_task(collect(stream_run(service, RunMode.MANAGER, "first", first)))
    await wait_until(lambda: runner.started == ["first"])

    waiting = RecordingChannel()
    waiting_task = asyncio.create_task(
        collect(stream_run(service, RunMode.MANAGER, "waiting", waiting))
    )
    await asyncio.wait_for(waiting.queued.wait(), timeout=2)

    waiting_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting_task
    assert runner.started == ["first"], "the cancelled request never started a run"

    runner.release.set()
    await asyncio.wait_for(first_task, timeout=2)

    # A later visitor is admitted immediately rather than inheriting the
    # cancelled request's place in line.
    after = RecordingChannel()
    frames = frames_of(
        await asyncio.wait_for(
            collect(stream_run(service, RunMode.MANAGER, "next", after)), timeout=2
        )
    )
    assert after.notices == []
    assert frames[-1][1]["report"] == "report: next"
    assert runner.started == ["first", "next"]


# ---------------------------------------------------------------------------
# The setting: default, validation, documentation, and the app wiring
# ---------------------------------------------------------------------------


def test_limit_setting_defaults_validates_and_is_documented(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert Settings(_env_file=None).web_max_concurrent_runs == 2

    monkeypatch.setenv("WEB_MAX_CONCURRENT_RUNS", "3")
    assert Settings(_env_file=None).web_max_concurrent_runs == 3

    for invalid in ("0", "-1", "not-a-number"):
        monkeypatch.setenv("WEB_MAX_CONCURRENT_RUNS", invalid)
        with pytest.raises(ValidationError):
            Settings(_env_file=None)

    # Documented where an operator looks, next to the other web settings.
    assert "WEB_MAX_CONCURRENT_RUNS=2" in (PROJECT_ROOT / ".env.example").read_text()


def test_service_rejects_a_limit_that_could_never_admit_a_run() -> None:
    with pytest.raises(ValueError, match="max_concurrent_runs"):
        RunService({RunMode.MANAGER: RaisingRunner()}, max_concurrent_runs=0)


def test_app_uses_the_configured_limit() -> None:
    app = create_app(
        settings=Settings(_env_file=None, web_max_concurrent_runs=3),
        runners={RunMode.MANAGER: RaisingRunner()},
    )
    assert app.state.run_service.max_concurrent_runs == 3


# ---------------------------------------------------------------------------
# The endpoint, end to end: two concurrent visitors over HTTP
# ---------------------------------------------------------------------------


class HeldOverHttp(HeldRunner):
    """``HeldRunner`` plus the signal that its first run is really inside."""

    def __init__(self) -> None:
        super().__init__()
        self.entered = asyncio.Event()

    async def __call__(self, request: str, sink) -> RunOutcome:
        self.entered.set()
        return await super().__call__(request, sink)


def parse_frames(body: str) -> list[tuple[str, dict]]:
    """Parse an SSE body into ``(event name, payload)`` pairs."""
    frames: list[tuple[str, dict]] = []
    for block in body.replace("\r\n", "\n").strip().split("\n\n"):
        name = "message"
        data: list[str] = []
        for line in block.splitlines():
            if line.startswith("event:"):
                name = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data.append(line[len("data:") :].strip())
        if data:
            frames.append((name, json.loads("\n".join(data))))
    return frames


async def test_second_visitor_over_the_endpoint_is_queued_then_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The endpoint wires the queue into the wire contract (limit 1, spec R8).

    Two requests are in flight on one event loop through the real ASGI app.
    Every wait is bounded so a regression fails here instead of hanging.
    """
    queued = asyncio.Event()
    notices: list[QueuedNotice] = []

    class NotifyingSink(QueueSink):
        """The app's sink, with a signal the moment a request queues."""

        def emit_queued(self, notice: QueuedNotice) -> None:
            notices.append(notice)
            queued.set()
            super().emit_queued(notice)

    monkeypatch.setattr(web_app, "QueueSink", NotifyingSink)
    runner = HeldOverHttp()
    app = create_app(
        settings=Settings(_env_file=None, web_max_concurrent_runs=1),
        runners={RunMode.MANAGER: runner},
    )

    async def post(client, request: str):
        return await client.post("/api/run", json={"request": request, "mode": "manager"})

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first_task = asyncio.create_task(post(client, "first"))
        second_task = None
        try:
            await asyncio.wait_for(runner.entered.wait(), timeout=5)
            second_task = asyncio.create_task(post(client, "second"))

            # Deterministic ordering, no sleeps: the second request has already
            # decided to wait while the only slot is provably taken.
            await asyncio.wait_for(queued.wait(), timeout=5)
            assert runner.started == ["first"]
            assert notices == [QueuedNotice(runs_ahead=1, limit=1)]

            runner.release.set()
            first, second = await asyncio.wait_for(
                asyncio.gather(first_task, second_task), timeout=5
            )
        finally:
            runner.release.set()
            pending = [
                task for task in (first_task, second_task) if task is not None and not task.done()
            ]
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), timeout=5)

    for response in (first, second):
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")

    first_frames = parse_frames(first.text)
    second_frames = parse_frames(second.text)

    assert [name for name, _ in first_frames] == ["progress", "progress", "final"]
    assert [name for name, _ in second_frames] == [
        "queued",
        "progress",
        "progress",
        "final",
    ]
    assert second_frames[0][1] == {
        "type": QUEUED_EVENT_TYPE,
        "state": "queued",
        "runs_ahead": 1,
        "limit": 1,
    }
    for frames, request in ((first_frames, "first"), (second_frames, "second")):
        assert frames[-1][1]["status"] == "completed"
        assert frames[-1][1]["report"] == f"report: {request}"
        assert frames[-1][1]["error"] is None


async def test_queued_analyst_run_is_queued_then_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same slot pool and wire contract admit an analyst run (spec R7/R8).

    Analyst mode is not a second queue: it waits on the one process-wide
    semaphore, gets the same ``queued`` frame, and then streams its own mode's
    final payload.
    """

    class HeldAnalyst(HeldOverHttp):
        async def __call__(self, request: str, sink) -> RunOutcome:
            await super().__call__(request, sink)
            return RunOutcome(status="completed", report=None, answer=f"answer: {request}")

    queued = asyncio.Event()
    notices: list[QueuedNotice] = []

    class NotifyingSink(QueueSink):
        def emit_queued(self, notice: QueuedNotice) -> None:
            notices.append(notice)
            queued.set()
            super().emit_queued(notice)

    monkeypatch.setattr(web_app, "QueueSink", NotifyingSink)
    runner = HeldAnalyst()
    app = create_app(
        settings=Settings(_env_file=None, web_max_concurrent_runs=1),
        runners={RunMode.ANALYST: runner},
    )

    async def post(client, request: str):
        return await client.post("/api/run", json={"request": request, "mode": "analyst"})

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first_task = asyncio.create_task(post(client, "first"))
        second_task = None
        try:
            await asyncio.wait_for(runner.entered.wait(), timeout=5)
            second_task = asyncio.create_task(post(client, "second"))
            await asyncio.wait_for(queued.wait(), timeout=5)
            assert runner.started == ["first"]
            assert notices == [QueuedNotice(runs_ahead=1, limit=1)]

            runner.release.set()
            first, second = await asyncio.wait_for(
                asyncio.gather(first_task, second_task), timeout=5
            )
        finally:
            runner.release.set()
            pending = [
                task for task in (first_task, second_task) if task is not None and not task.done()
            ]
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), timeout=5)

    second_frames = parse_frames(second.text)
    assert [name for name, _ in second_frames] == [
        "queued",
        "progress",
        "progress",
        "final",
    ]
    assert second_frames[0][1] == {
        "type": QUEUED_EVENT_TYPE,
        "state": "queued",
        "runs_ahead": 1,
        "limit": 1,
    }
    for frames, request in (
        (parse_frames(first.text), "first"),
        (second_frames, "second"),
    ):
        progress = [payload for name, payload in frames if name == "progress"]
        assert progress[0]["mode"] == "analyst"
        final = frames[-1][1]
        assert final["mode"] == "analyst"
        assert final["status"] == "completed"
        assert final["answer"] == f"answer: {request}"
