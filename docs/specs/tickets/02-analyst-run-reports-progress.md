# 02: An analyst run reports its own progress

**Blocked by:** None (can start immediately)
**Status:** ready

## What to build
One analyst run can hand back what it is doing while it happens: retrieving
metadata, generating SQL, validating, executing, analyzing, retrying, and how it
finished (or why it failed). The report is optional, additive, and costs nothing
when nobody is listening.

## Scope
- A small progress package: an event type with a type name and a monotonic
  sequence number, a sink protocol with a synchronous `emit`, and a no-op
  implementation.
- The agent services gain an optional sink; the agent graph emits stage
  transitions, attempt/retry information and the terminal outcome (answer, or
  the error that ended the run). Per-attempt SQL and row counts ride along.
- `run_agent` keeps its signature and behavior; no sink means no emission.
- Emission is fail-open: a raising sink is swallowed and never changes the
  run's status, answer or error.
- Not in scope: the manager's events (ticket 04), any HTTP surface.

## Acceptance criteria
- [ ] A successful run emits, in order: run accepted, retrieval, SQL
      generation, validation, execution, analysis, completion — each with its
      sequence number.
- [ ] A failed run (invalid/unsafe SQL, query error, or exhausted retries)
      emits the retry and the failure with a reason; a successful run emits no
      failure event.
- [ ] With a sink that raises on every event, the run's status, answer and
      error are identical to a run with no sink.
- [ ] A run with no sink behaves exactly as before; the existing suite passes
      unchanged.

## Validation
New tests with a recording sink around the existing fake model and stub
capabilities. Covers spec R5 (analyst portion) and R10; provides the event
vocabulary ticket 04 reuses.
