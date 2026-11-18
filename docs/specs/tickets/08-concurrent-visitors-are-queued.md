# 08: Concurrent visitors are queued, not rejected

**Blocked by:** 05
**Status:** ready

## What to build
The web process admits at most a configured number of concurrent runs (default
2). A visitor beyond the cap sees an explicit queued state and starts when a
slot frees — the demo never punishes a second visitor with an error, and the
database and model never see more fan-out than the host can serve.

## Scope
- A process-wide limit (`WEB_MAX_CONCURRENT_RUNS`, default 2) around run
  execution, with slot release on every outcome (success, state-level failure,
  crash, client disconnect).
- A queued event (with position or just the state) followed by normal events
  once admitted.
- A disconnecting client releases its slot and does not leave a run pinned.

## Acceptance criteria
- [ ] With the limit at 1, two simultaneous requests produce one running stream
      and one queued stream that starts after the first finishes.
- [ ] Both requests complete correctly; no events are lost or duplicated.
- [ ] A run that raises still releases its slot.
- [ ] The configured limit is honored (limit 2 admits two, queues the third).
- [ ] Slots are released when the client disappears.

## Validation
ASGI tests using fake runners that hold a slot until released. Covers spec R8
and D6.
