"""Structured progress events for a run, as it happens.

A run is otherwise legible only when it is over: ``run_agent`` and
``run_manager`` return their result once, at the end. This module is the
vocabulary a run uses to say what it is doing while it does it, so a consumer
(the web surface, a CLI spinner, a test) can render the pipeline live.

It is deliberately tiny and dependency-free:

* ``ProgressEvent`` — one immutable event: a stable ``type`` name, a monotonic
  ``seq`` number (one sequence per emitter; ``child`` emitters share
  their run's), and the stage-relevant fields (``attempt``, ``sql``,
  ``row_count``, ``answer``, ``error``, ``status``, ``reason``, ``question``,
  ``index``, ``is_follow_up``, ``questions``, ``report``). Only the fields that
  apply to an event are set; ``as_dict`` drops the rest, which is the shape the
  web layer serialises.
* ``ProgressSink`` — the consumer's side: a *synchronous* ``emit(event)``.
  Synchronous by design: emitting never awaits, so a sink hands off and returns
  immediately (an SSE sink pushes onto an ``asyncio`` queue with
  ``put_nowait``). ``NullSink`` is the do-nothing implementation.
* ``ProgressEmitter`` — the producer's side: assigns the sequence number and
  hands the event to the sink, fail-open. ``ProgressEmitter.child`` derives the
  emitter of a nested producer (one analyst sub-run inside a manager run) that
  shares the parent's sink *and* sequence counter, so a whole run is one
  ordered stream.

One run is one stream. A manager run and every analyst sub-run it contains
share a single monotonically increasing ``seq``: a sub-run never mints its own
counter, it emits through a child emitter that stamps its events with the
sub-question's ``index``, ``question`` and ``is_follow_up`` flag. A consumer can
therefore order one run's events as they arrive, and every nested analyst event
(``run.started``, ``metadata.*``, ``sql.*``, ``analysis.completed``, and the
nested ``run.completed``/``run.failed``) is attributable to the analysis it
belongs to. The manager's own pipeline events use the ``manager.*`` types, and
the run's terminal event is a ``run.completed``/``run.failed`` carrying no
``index``.

Emission is fail-open, exactly like tracing (``app/agent/tracing.py``): a sink
that raises is swallowed and never changes the run's status, answer or error —
including events emitted from a nested run, since they go through the same
emitter. With no sink there is no emission and no cost — the emitter returns
before anything is constructed.

A stream has no guaranteed last event: a run that *raises* (an MCP transport
failure, for example) simply stops emitting, and a consumer treats the end of
the stream as terminal. Nobody wraps a run to invent an event it cannot honestly
report.

Event type names live in ``ProgressEventType`` so producers and consumers share
one spelling. The analyst graph was the first producer; the manager emits its
pipeline with the ``manager.*`` names below and nests analyst events under it;
the web layer consumes this same vocabulary.
"""

from collections.abc import Mapping
from contextlib import suppress
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any, Protocol


class ProgressEventType(StrEnum):
    """Event type names emitted while a run progresses.

    Names are dot-namespaced ``<area>.<what happened>`` and stable for
    consumers: adding an event type is additive, renaming one is a break.
    """

    # Run lifecycle. ``RUN_COMPLETED``/``RUN_FAILED`` are the terminal event of
    # whichever run ended — an analyst run (carrying ``answer``) or a manager
    # run (carrying ``report``) — and are always the last event of a run that
    # finishes without raising.
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
    # Manager pipeline, in stage order for one management request. The analyst
    # sub-runs it starts emit the analyst types above, stamped with their
    # sub-question ``index``.
    REQUEST_ACCEPTED = "manager.requested"
    SUB_QUESTIONS_DECOMPOSED = "manager.decomposed"
    ANALYSIS_STARTED = "manager.analysis_started"
    ANALYSIS_FINISHED = "manager.analysis_finished"
    INSPECTION_DECIDED = "manager.inspected"
    SYNTHESIS_STARTED = "manager.synthesis_started"
    GROUNDEDNESS_VERDICT = "manager.groundedness"


@dataclass(frozen=True)
class ProgressEvent:
    """One progress event: a type, a sequence number and its stage fields.

    ``seq`` starts at 1 with the run's first event and increases by one for
    every event of *that run* — including events emitted by an analyst sub-run
    nested inside a manager run, which shares the manager's counter (see
    ``ProgressEmitter.child``). Fields that do not apply to a given event stay
    ``None`` and are omitted from ``as_dict``.

    ``index``/``is_follow_up`` mark an event with the sub-question it belongs
    to — set on every event of a nested analyst run, and on the manager's
    per-analysis started/finished events. ``questions`` carries a decision's
    question list, ``report`` the report of a completed manager run — never a
    report that failed the groundedness check.

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
    # Why this stage produced no answer (a failed analysis reports its error
    # instead), or why a run failed.
    error: str | None = None
    status: str | None = None
    reason: str | None = None
    # Manager pipeline (M8): the sub-question a nested analyst event belongs
    # to, whether that sub-question is a follow-up, a decision's question list.
    index: int | None = None
    is_follow_up: bool | None = None
    questions: list[str] | None = None
    report: str | None = None

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


class _Sequence:
    """The shared, mutable sequence counter one run's emitters advance.

    Shared by reference between an emitter and the child emitters it derives,
    which is what makes a run's events one sequence instead of several.
    Increment and delivery happen without an ``await`` in between, so under one
    ``asyncio`` event loop two concurrent producers can never take the same
    number or reorder an event's number against its delivery.
    """

    __slots__ = ("value",)

    def __init__(self) -> None:
        self.value = 0


class ProgressEmitter:
    """Assign sequence numbers to events and deliver them to an optional sink.

    One emitter belongs to one run: it owns the run's sequence counter. With
    ``sink=None`` — the default for library and CLI callers — every ``emit``
    returns immediately and costs nothing.

    ``child`` derives the emitter of a producer nested inside the run (one
    analyst sub-run of a manager run). The child shares this emitter's sink and
    counter, so the nested events continue the run's single sequence and are
    labelled with the child's fields. A caller never builds a second counter
    for a nested run.

    Delivery is fail-open: a raising sink is swallowed. Only ``Exception`` is
    caught, so cancellation and other ``BaseException`` signals still propagate
    (a cancelled run must not be silently swallowed).
    """

    def __init__(
        self,
        sink: ProgressSink | None = None,
        *,
        _sequence: _Sequence | None = None,
        _labels: Mapping[str, Any] | None = None,
    ) -> None:
        self._sink = sink
        self._sequence = _sequence if _sequence is not None else _Sequence()
        self._labels: dict[str, Any] = dict(_labels or {})

    @property
    def enabled(self) -> bool:
        """True when a sink is attached (emitting will do work)."""
        return self._sink is not None

    @property
    def seq(self) -> int:
        """The sequence number of the last event emitted (0 before the first)."""
        return self._sequence.value

    def child(self, **labels: Any) -> "ProgressEmitter":
        """Derive the emitter of a nested producer, on the same stream.

        The child shares this emitter's sink and sequence counter, so nothing
        nested can restart numbering, and every event it emits is stamped with
        ``labels`` (for an analyst sub-run: ``index``, ``question``,
        ``is_follow_up``). Labels win over whatever the nested producer sets,
        because attributing a nested event to its sub-question is the whole
        point and must not be overridable by accident.
        """
        return ProgressEmitter(
            self._sink, _sequence=self._sequence, _labels={**self._labels, **labels}
        )

    def emit(self, event_type: ProgressEventType | str, **fields: Any) -> ProgressEvent | None:
        """Emit one event, returning it (or ``None`` when no sink is attached)."""
        # Build the event before consulting the sink: a malformed event (an
        # unknown field, a bad type) is a producer bug, and it must raise the
        # same way in runs with a sink and in runs without one, instead of
        # hiding until the day someone attaches a consumer. Labels are merged
        # here too, so a nested producer cannot overwrite its own attribution.
        event = ProgressEvent(
            type=str(event_type), seq=self._sequence.value + 1, **{**fields, **self._labels}
        )
        if self._sink is None:
            return None
        self._sequence.value += 1
        # Fail-open, like AgentTracer.flush: observability can never break the
        # run it observes. The counter still advanced, so the next event's
        # sequence number stays ahead of the swallowed one.
        with suppress(Exception):
            self._sink.emit(event)
        return event
