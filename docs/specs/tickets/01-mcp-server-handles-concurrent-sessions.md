# 01: The MCP server handles concurrent analyst sessions

**Blocked by:** None (can start immediately)
**Status:** ready

## What to build
Four analyst sessions can hit the MCP server at once without the database
queueing up or the server's event loop stalling. The asyncpg pool size becomes
a setting, and metadata search's CPU embedding runs off the event loop under a
lock so concurrent searches neither block the loop nor race the embedder.

This is prefactoring: it changes nothing an agent sees, and it makes ticket 03's
parallel fan-out a latency win instead of a contention experiment.

## Scope
- Pool size configurable (`DB_POOL_MAX_SIZE`, default 10) instead of the
  hardcoded `max_size=5`, wired from settings through the data client.
- An async embedding entry point on the embedder wrapper: the sync fastembed
  call moves to a worker thread, serialized by an asyncio lock.
- Metadata search awaits the async entry point; behavior and error message
  unchanged.
- No change to the read-only SQL boundary, tool payloads or result shapes.

## Acceptance criteria
- [ ] The pool connects with the configured size, and a smaller configured
      value is honored (no silent clamping back to the old literal).
- [ ] A slow embedding call does not delay other coroutines on the same event
      loop (a second coroutine completes while the embedding is in flight).
- [ ] Two concurrent metadata searches produce correct results and do not
      interleave inside the embedder.
- [ ] Every existing test passes unchanged.

## Validation
New tests beside the metadata/tool tests: pool-size setting, non-blocking
embedding, concurrent search. Covers spec D7 and part of R9; unblocks the
acceptance measurement in ticket 03.
