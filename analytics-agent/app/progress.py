"""Structured progress events for a run, as it happens.

A run is otherwise legible only when it is over: ``run_agent`` and
``run_manager`` return their result once, at the end. This module is the
vocabulary a run uses to say what it is doing while it does it, so a consumer
(the web surface, a CLI spinner, a test) can render the pipeline live.

It is deliberately tiny and dependency-free:

* ``ProgressEvent`` — one immutable event: a stable ``type`` name, a monotonic
  per-run ``seq`` number, and the stage-relevant fields (``attempt``, ``sql``,
  ``row_count``, ``answer``, ``status``, ``reason``, ``question``). Only the
  fields that apply to an event are set; ``as_dict`` drops the rest, which is
  the shape the web layer serialises.
* ``ProgressSink`` — the consumer's side: a *synchronous* ``emit(event)``.
  Synchronous by design: emitting never awaits, so a sink hands off and returns
  immediately (an SSE sink pushes onto an ``asyncio`` queue with
  ``put_nowait``). ``NullSink`` is the do-nothing implementation.
* ``ProgressEmitter`` — the producer's side: assigns the sequence number and
  hands the event to the sink, fail-open.

Emission is fail-open, exactly like tracing (``app/agent/tracing.py``): a sink
that raises is swallowed and never changes the run's status, answer or error.
With no sink there is no emission and no cost — the emitter returns before
anything is constructed.

Event type names live in ``ProgressEventType`` so producers and consumers share
one spelling. The analyst graph is the first producer; the manager and the web
layer reuse this same vocabulary.
"""

from contextlib import suppress
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any, Protocol


class ProgressEventType(StrEnum):
    """Event type names emitted while a run progresses.

    Names are dot-namespaced ``<area>.<what happened>`` and stable for
    consumers: adding an event type is additive, renaming one is a break.
    """

    # Run lifecycle.
    RUN_STARTED = "run.started"
    RUN_COMPLETED = "run.completed"
    RUN_FAILED = "run.failed"
    RUN_RETRYING = "run.retrying"
    # Analyst stages, in pipeline order (one event per stage per attempt).
    METADATA_RETRIEVED = "metadata.retrieved"
    SQL_GENERATED = "sql.generated"
    SQL_VALIDATED = "sql.validated"
    SQL_EXECUTED = "sql.executed"
    ANALYSIS_COMPLETED = "analysis.completed"


@dataclass(frozen=True)
class ProgressEvent:
    """One progress event: a type, a sequence number and its stage fields.

    ``seq`` is monotonic per emitter, starting at 1 with the run's first event,
    so a consumer can both order events and notice gaps. Fields that do not
    apply to a given event stay ``None`` and are omitted from ``as_dict``.

    Result *rows* are deliberately not carried: they can be large, and the
    manager's evidence records are the durable place for them. ``row_count`` is
    what a live view needs.
    """

    type: str
    seq: int
    question: str | None = None
    attempt: int | None = None
    sql: str | None = None
    row_count: int | None = None
    answer: str | None = None
    status: str | None = None
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """JSON-ready payload: the type and sequence number plus set fields."""
        return {name: value for name, value in asdict(self).items() if value is not None}


class ProgressSink(Protocol):
    """A consumer of progress events.

    ``emit`` is synchronous on purpose: it must not block or await. A sink that
    needs to hand events to an ``asyncio`` queue or a stream does that
    internally (``put_nowait``) and returns immediately. Sinks may raise —
    emission is fail-open, so the run continues untouched.
    """

    def emit(self, event: ProgressEvent) -> None:
        """Receive one event."""
        ...


class NullSink:
    """A sink that discards every event."""

    def emit(self, event: ProgressEvent) -> None:
        return None


class ProgressEmitter:
    """Assign sequence numbers to events and deliver them to an optional sink.

    One emitter belongs to one run (or one producer inside a run): it owns the
    sequence counter. With ``sink=None`` — the default for library and CLI
    callers — every ``emit`` returns immediately and costs nothing.

    Delivery is fail-open: a raising sink is swallowed. Only ``Exception`` is
    caught, so cancellation and other ``BaseException`` signals still propagate
    (a cancelled run must not be silently swallowed).
    """

    def __init__(self, sink: ProgressSink | None = None) -> None:
        self._sink = sink
        self._seq = 0

    @property
    def enabled(self) -> bool:
        """True when a sink is attached (emitting will do work)."""
        return self._sink is not None

    @property
    def seq(self) -> int:
        """The sequence number of the last event emitted (0 before the first)."""
        return self._seq

    def emit(self, event_type: ProgressEventType | str, **fields: Any) -> ProgressEvent | None:
        """Emit one event, returning it (or ``None`` when no sink is attached)."""
        if self._sink is None:
            return None
        self._seq += 1
        event = ProgressEvent(type=str(event_type), seq=self._seq, **fields)
        # Fail-open, like AgentTracer.flush: observability can never break the
        # run it observes. The counter still advanced, so the next event's
        # sequence number stays ahead of the swallowed one.
        with suppress(Exception):
            self._sink.emit(event)
        return event
