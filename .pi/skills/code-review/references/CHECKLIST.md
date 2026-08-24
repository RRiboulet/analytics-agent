# Code Review Checklist

Evaluate each changed unit against the rows below. Not every row applies to every change — mark N/A when not relevant. The point is uniformity, not exhaustive re-reading of files that did not move.

## Correctness & edge cases

- [ ] Does the logic handle the obvious happy path exactly once (no double side effects)?
- [ ] Are empty/no-op inputs handled explicitly? (`""`, `None`, empty list, whitespace-only)
- [ ] Are missing-data cases handled without crashing (table not found, zero rows, NULL)?
- [ ] Does any numeric/`Decimal` path risk overflow, precision loss, or divide-by-zero?
- [ ] Are string inputs bounded (length limits) and trimmed consistently?

## Error handling

- [ ] Are exceptions caught where a user/agent must see a message, and re-raised where a caller should decide?
- [ ] Is there a consistent error surface (same `{valid: bool, message, entries}` contract) rather than ad-hoc shapes?
- [ ] Are failures distinguishable from valid-but-empty results?

## Concurrency & lifecycle

- [ ] Async resources (pools, clients, connections) are created once and closed exactly once.
- [ ] Lifespan/pool management is not duplicated across layers (e.g., FastMCP `lifespan` vs. ASGI app lifespan).
- [ ] Timeouts exist for I/O and cover connect + query, not just one phase.
- [ ] No per-request connection/reconnect churn if a pool is available.

## Security

- [ ] Input that reaches SQL is validated (single statement, allowed dialect, no write keywords) — never just client-supplied strings.
- [ ] Parameters are passed as bind parameters, not string-interpolated, wherever data is sent to a query.
- [ ] Secrets never leak into logs, examples, or docs; `.env` stays untracked.
- [ ] Output has a hard row/byte cap, not an unbounded fetch.
- [ ] Read-only role/credentials are used by the app, not admin creds.

## API / contract stability

- [ ] Tool names, argument names, and response `structuredContent` shape stay backward compatible.
- [ ] Response messages and field names are consistent across tools.
- [ ] Changes to message text or field names are intentional and documented.

## Tests

- [ ] New behavior has a test that would fail if the behavior regressed.
- [ ] Test asserts the actual contract (structured content / content), not just "no exception".
- [ ] Literal SQL/dialect risks are covered with at least one integration-style case:
      - unsafe operation rejected (`DELETE`, `DROP`, multi-statement)
      - Decimal serialization round-trips to JSON
- [ ] Tests are not asserting implementation internals that are free to change.

## Performance & resource bounds

- [ ] No unbounded pagination/scan before a cap is applied.
- [ ] N+1 / repeated-query patterns avoided inside a request.
- [ ] Big payloads are truncated or summarized, not serialized whole without a limit.

## Docs & naming

- [ ] Naming matches the surrounding codebase and MCP conventions (`*_factory_*` tool names).
- [ ] Behavior-affecting choices are documented in the README or a dedicated doc, not only in the code comment.
- [ ] Deterministic sampling/seed data stays deterministic (no random values that erode reproducibility).

## No fill-in

Skip any row that does not apply. Do not add invented findings to look thorough.