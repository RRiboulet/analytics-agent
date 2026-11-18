# 05: A browser can submit a request and read the report

**Blocked by:** 04
**Status:** ready

## What to build
`python -m app.web` serves one page on loopback. Submitting a management request
streams progress to the page and ends with the grounded report, its status,
elapsed wall time and the real MCP call counts. This is the first slice a
visitor can actually use; the live pipeline view follows in ticket 06.

## Scope
- A web package: a Starlette app with one POST endpoint that streams
  server-sent events and ends with a final payload, one static page, a uvicorn
  entry point, and a `--check` mode that starts, serves the page, and exits 0.
- Declare `starlette`, `sse-starlette` and `uvicorn` as direct dependencies
  (already resolved in the lockfile).
- Run composition reuses the existing manager/analyst entrypoints; the web layer
  never imports the database client and never builds SQL.
- Bind `127.0.0.1` by default; an optional shared token via env, off by default.
- Page shell with a request box, a status line and the final report; theme- and
  framework-free, no build step.

## Acceptance criteria
- [ ] A POST to the run endpoint returns an event stream ending with a final
      payload containing status, report, elapsed time and per-tool call counts.
- [ ] The page loads and submits a request without a bundler, and renders the
      final payload.
- [ ] `python -m app.web --check` exits 0 without running a request.
- [ ] A failing run streams a failure event and the page shows it.
- [ ] With the token set, a request without it is rejected; with it, accepted.
- [ ] The ASGI test drives the app with an injected fake runner (no MCP, no
      database, no model).

## Validation
New web-app tests via an ASGI client plus a manual browser check. Covers spec R6
(final state), R11 (entrypoints unchanged); parts of D4/D5.
