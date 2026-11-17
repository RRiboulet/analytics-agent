# M8 — Demonstrable analytics agent: parallel manager + live web surface

Status: proposed (spec written from a confirmed grilling session; not committed)
Source of truth for project direction remains `PLAN.md`, which this milestone amends.

## Objective

M1–M7 are complete, but the system can only be demonstrated by starting two
process trees and watching a terminal. A management request takes 5–15 minutes
on the local model and ~6 minutes on a hosted flash model, and nothing
intermediate is visible while it runs: `run_manager()` returns once, at the end.

The next milestone makes the finished system demonstrable to someone who is not
its author — a visitor submits a question, watches the manager work, and reads a
report whose every number traces to an executed read-only query. Doing that
credibly requires two changes at once: the pipeline must be fast enough to
watch (bounded parallel analyst fan-out) and it must be observable while it runs
(progress events surfaced in a browser).

Why now: the project's value is currently invisible outside the repository.
Every remaining capability is optional; demonstrability is not.

## Current behavior

Evidence gathered while scoping this milestone (hosted provider,
`deepseek-v4.1-flash`, `opencode`):

- **A management request is serial.** The manager runs its sub-analyses in a
  `for` loop (`app/manager/graph.py`, `_run_sub_analyses`), one after another,
  each a full analyst run. The stated reason is D009: "the model server is the
  bottleneck; parallelism is a pure optimization that can be added without
  state changes".
- **The measured cost is model calls, not MCP calls.** Each analyst run makes
  exactly 4 MCP calls (`search_metadata`, `list_tables`, `get_relationships`,
  `query`); the M7.6 benchmark reports a flat 4.00 calls/run, 132 calls over 33
  analyst runs, mean 366.9 s per management scenario (`PLAN.md` §17). Two of
  those four calls are invariant per database.
- **There is no surface.** The deliverables are `python -m app.agent`,
  `python -m app.manager` (CLI + `run()`) and untracked `--out` artifacts.
  `pyproject.toml` has no web dependency; there is no HTTP endpoint and no page.
- **There is no progress channel.** `_run_request` performs a single
  `manager_graph.ainvoke(...)`; `ManagerRunResult` is only complete at the end.
  Tracing exists (Langfuse, fail-open) but is per-provider and not a UI feed.
- **Call attribution is snapshot-based.** `_analyse` snapshots the shared
  `CountingCapabilities`, awaits the run, and takes a delta. This is exact only
  because runs are serial: two concurrent runs would overlap their deltas and
  double-count.
- **The MCP server is sized for one client.** `PostgresClient.connect()` opens
  an asyncpg pool of `max_size=5`; `search_metadata` calls
  `embedder.embed_query(question)` synchronously inside the async tool, so
  concurrent metadata searches serialize on — and block — the server's event
  loop.
- **CI does not run the Python suite.** The only workflow checks the
  devcontainer pin; `ruff` + `pytest` are run by hand (noted, not in scope).

## Required behavior

R1. **Parallel fan-out.** Given a decomposition of N sub-questions (N ≤ 4), the
    manager starts all N analyst runs without waiting for a prior one to
    finish, subject to a configurable limit (`MANAGER_MAX_PARALLELISM`, default
    4). Evidence records are indexed by sub-question position regardless of
    completion order. Decomposition validation, budgets (≤4 sub-questions),
    all-sub-analyses-failed failure, and retry policy are unchanged.

R2. **The follow-up round is parallel too.** The single bounded follow-up round
    (≤1 round, ≤2 questions) fans out under the same limit, and its records
    keep their existing numbering (after the sub-analyses) and
    `is_follow_up` marking.

R3. **Call attribution stays exact under concurrency.** For every run,
    the sum of per-record `tool_calls` equals the run's total, with zero
    unattributed calls, for any completion order. The flat
    4-calls-per-analyst-run property must still be observable per record.

R4. **A crashing analyst run still propagates, without orphans.** If a
    sub-run raises (the documented "exceptions propagate" contract), every
    in-flight sub-run is allowed to settle before the exception reaches the
    caller, so no task is orphaned and the artifacts written on the way out
    report the calls actually spent.

R5. **A run emits an ordered progress stream.** From request acceptance to
    terminal outcome, a run emits events that let a consumer render the whole
    pipeline: run started (mode, request), decomposed sub-questions (or the
    decomposition failure), per-analysis started/finished (index, question,
    SQL, row count, answer, error), analyst stage transitions
    (retrieving/generating/validating/executing/analyzing, attempts), inspect
    decision (follow-up questions, or none, or degraded with its reason),
    synthesis started, groundedness verdict, terminal status with report.
    The terminal *event* carries the status and the report only: the first
    event names the mode, and elapsed wall time plus per-tool MCP call counts
    are composed by the consuming surface from the run result
    (`ManagerRunResult`), which is the only place they exist.

R6. **The web surface renders the live pipeline.** Serving on loopback, one
    page offers a mode choice (single question / management request), submits
    a request, and renders the pipeline from R5 as it arrives: the decomposed
    sub-questions, each analysis with its SQL and answer as the run proceeds,
    the follow-up round, the groundedness verdict, and finally the report with
    elapsed time and call counts. Result *rows* are not carried by live events
    (they can be large, and the analyst already bounds them): they arrive with
    the terminal payload's evidence records, and the page backfills each
    analysis card from them rather than duplicating it. A failed run shows the
    failure and, when one exists, the rejected report attempt.

R7. **A single question answers without the manager stages.** Analyst mode
    returns the grounded answer for one question through the same page flow,
    with no decomposition/inspect/synthesize stages shown.

R8. **Concurrent visitors are bounded.** The web process limits total
    in-flight runs (`WEB_MAX_CONCURRENT_RUNS`, default 2); a request beyond
    the limit receives an explicit queued event and starts when a slot frees,
    rather than being rejected or silently throttled.

R9. **The MCP server tolerates N concurrent analyst sessions.** Metadata
    search's CPU embedding runs off the server's event loop and is serialized
    so concurrent searches cannot race the embedder; the DB pool size is a
    setting (`DB_POOL_MAX_SIZE`, default 10) instead of a literal.

R10. **Observability failures are fail-open.** A raising progress sink never
    fails or corrupts a run — the same contract the Langfuse tracer already
    has — and a sink that blocks is a bug in the sink, not something the run
    can prevent: `emit` is synchronous by design (D3) and cannot preempt its
    caller, so the no-blocking rule is a documented contract on sinks and only
    the raising half is enforceable and tested. The CLI/library path works with
    no sink at all.

R11. **Existing surfaces keep working.** `run_manager(request, out_dir)` keeps
    its behavior (an optional sink is additive); `ManagerRunResult` keeps its
    fields; the CLI keeps its flags; `app/agent/` gains only optional
    emission. The only intentional break is the manager's internal
    `run_analyst` contract (see D2), which is covered by updated tests.

R12. **The improvement is measured, not asserted.** The manager and analyst
    benchmarks are re-run on the same hosted provider and the before/after
    numbers (p50 latency per management scenario, per-run call counts, analyst
    pass rate) are written into `PLAN.md`.

## Non-goals

- New analytical capabilities, new MCP tools, or changes to the metadata layer.
- Abandoning the discrete-model-call pattern for a tool-calling loop.
- New agent roles (critic/planner/verifier agents): "multi-agent" here means
  the existing manager→analyst fan-out only.
- Local-model (llama.cpp/Gemma) tuning, or making the local path fast.
- Langfuse changes, trace schema work, or replacing tracing with the sink.
- User accounts, multi-tenancy, persistence of run history, or a public
  deployment (TLS, DNS, proxy).
- Token/dollar cost display (no LLM usage extraction exists today).
- CI for the Python suite.

## Constraints

- The read-only SQL boundary and the MCP capability boundary are untouched. The
  web layer never imports the database client, never constructs SQL, and never
  reaches PostgreSQL except through the existing manager/analyst entrypoints.
- Groundedness enforcement, the never-ship-a-fabricated-report rule, and every
  D009 budget stay exactly as they are.
- Tests stay hermetic: no network, no live database, no live model; fakes and
  stubs at the existing seams. The full suite must pass.
- `ruff format .` and `ruff check .` clean; Python 3.13 via `uv`; no Node
  toolchain and no frontend build step.
- No new heavy dependency. `starlette`, `sse-starlette` and `uvicorn` already
  resolve in the lockfile as transitive MCP dependencies; they become declared
  direct dependencies, nothing more.
- The demo path uses hosted API models (`LLM_PROVIDER=opencode`); the local
  provider must keep working, and the parallel win is explicitly a
  hosted-model property — a serial local model server gains little.
- Exposure is loopback only (this host's policy is that SSH is its only open
  port). No secret value ever reaches the browser.
- Budget: roughly two weeks of work.

## Design decisions

**D1 — Bounded `asyncio` fan-out replaces the sequential loop.** Sub-analyses
and the follow-up round run as concurrent coroutines under a semaphore sized by
`MANAGER_MAX_PARALLELISM` (default 4); results are collected in input order so
evidence indexing does not depend on completion order. Rejected alternatives:
LangGraph parallel branches (harder to read, no benefit over `gather` in one
node), unbounded gather (the pool and model rate limits are real). Failure
semantics are preserved by collecting outcomes with `return_exceptions=True`,
settling everything, then re-raising the first exception — so a crash still
propagates (R4) while no task is orphaned.

**D2 — Per-run counting replaces snapshot/delta.** The manager's internal
`run_analyst` contract gains the capability surface for that sub-run: the
manager wraps the shared capability in a fresh counting decorator per analyst
run and attributes that run's calls directly. Snapshot/delta is exact only
while runs are serial, so leaving it in place under fan-out would silently
double-count. Rejected alternative: context-variable tagging inside the shared
counter (more machinery, same result, invisible coupling).

**D3 — A small explicit `ProgressSink` protocol, not LangGraph streaming.** A
new `app/progress.py` defines a sink with a synchronous `emit(event)`; the
manager and the analyst graph emit small typed events (type, sequence number,
and stage-relevant fields). The web layer implements a sink that pushes onto an
`asyncio` queue feeding Server-Sent Events. Rejected alternatives: routing
through `astream` (only per-node granularity, and analyst sub-runs happen inside
one manager node, so the interesting events would be invisible), and scraping
Langfuse (couples the demo to a SaaS and to trace lag).

**D4 — Starlette + `sse-starlette` + uvicorn; one page, one endpoint.** A single
POST endpoint streams SSE frames and ends with a final payload; the page is
static HTML/JS that reads the stream with `fetch` and renders it. No framework
beyond Starlette (already in the lockfile), no bundler, no `EventSource` (it
cannot POST). Rejected alternative: a FastAPI app (a new dependency for one
route) and Streamlit (fast to write, but fights structured streaming and looks
like every other demo).

**D5 — Two modes, one flow.** The same endpoint and page accept a single
question (analyst) or a management request (manager); the mode is a field in the
request and is visible in the UI. Analyst mode is the fast, satisfying first
interaction; the manager is the payoff.

**D6 — Queue, don't reject, beyond the concurrency cap.** The web process holds
an `asyncio` semaphore sized by `WEB_MAX_CONCURRENT_RUNS`; a request that finds
it exhausted emits a `queued` event and proceeds when a slot frees. Rejected
alternative: HTTP 429 (a demo that punishes the second visitor is a worse demo
than one that makes them wait).

**D7 — Make the MCP server concurrency-safe rather than hoping.** The embedder
call in metadata search moves to a worker thread under an `asyncio` lock (CPU
work off the event loop, model access still serialized), and the asyncpg pool
size becomes `DB_POOL_MAX_SIZE` (default 10). Without this, fan-out turns a
latency win into event-loop stalls and pool contention.

**D8 — Cost stays visible, dollars do not.** The UI shows elapsed wall time and
real MCP call counts (already exact and free). Token/dollar accounting is
deferred because no code reads model usage today; adding it would be a separate
decision.

**D9 — `PLAN.md` records the amendment.** The D009 sequential rule is replaced
by bounded parallel fan-out (with the reason: the manager is demo-critical and
the model server is either rate-limit-bound or hosted); the discrete-call
pattern, the budgets, and the groundedness rule are unchanged. A new M8 section
carries the before/after measurements.

**D10 — One compiled graph serves exactly one run.** The sink and the event
sequence counter bind at graph-construction time, so two concurrent invocations
of one compiled graph would interleave into a single indistinguishable stream
and a second request's events would never reach its own client. Surfaces build
one graph per invocation (as `run_manager`/`run_agent` already do) and the graph
builders say so in their docstrings. Rejected alternative: a per-run key in
context state, which is more machinery than a run-scoped object needs.

**D11 — No run history.** The page holds the current run; `--out` artifacts
remain the durable record. A history store is a product decision this milestone
does not need.

## Test seams

Seams are chosen to be the highest existing ones, so the suite stays hermetic
and new tests describe behavior rather than plumbing.

- **The manager's injected analyst runner** (`ManagerServices.run_analyst`, the
  seam `tests/test_manager_graph.py` already stubs). Parallelism is proved by a
  stub runner that overlaps its wait and records start/finish order; ordering,
  failure and all-failed behavior are asserted through the same seam.
- **The progress sink** as a recording fake injected into the manager services
  and the agent services. Asserts the event sequence (stage order strict,
  per-analysis events order-independent) for both graphs, and that a raising
  sink leaves the run's outcome untouched.
- **The web app through an ASGI client** with an injected fake run/runner, so
  the SSE frames, mode handling, queued behavior and final payload are tested
  without MCP, database or model.
- **The counting decorator**, unit-tested with N concurrent callers to pin R3
  (per-run exactness, zero unattributed).
- **The settings and the embedder wrapper**, for `DB_POOL_MAX_SIZE` and for
  "embedding does not block the event loop" (a slow stub embedder must not
  delay another coroutine on the same loop).
- Prior art: `tests/test_manager_graph.py` (stub analyst + fake LLM),
  `tests/test_manager_entrypoint.py` (composition, artifacts, CLI),
  `tests/test_analytics_tools.py` / `test_metadata_tools.py` (tool-level
  behavior with a fake database).

## Acceptance criteria

- [ ] R1 — a decomposition of 4 sub-questions produces overlapping analyst runs
      (proved by the stub runner), with evidence indexed by sub-question order,
      for any completion order.
- [ ] R2 — follow-up questions fan out under the same limit, numbered after the
      sub-analyses and marked as follow-ups.
- [ ] R3 — in a parallel run, per-record call counts sum to the run total with
      zero unattributed calls; the 4.00-calls-per-analyst-run property holds.
- [ ] R4 — a raising sub-run settles its siblings and then propagates; the
      artifacts written on the way out carry the calls actually spent.
- [ ] R5 — a full management run emits the event types of R5 in stage order,
      including a degraded-inspect event when follow-up output is unusable, and
      a groundedness-violation event when the report is rejected.
- [ ] R6 — the page renders a completed management run with, for every evidence
      record: question, SQL, result rows, answer (or error) and call counts;
      plus the final report and elapsed time.
- [ ] R7 — analyst mode returns an answer with no manager stages presented.
- [ ] R8 — a third concurrent request emits a `queued` event and completes
      after a slot frees.
- [ ] R9 — with `DB_POOL_MAX_SIZE` set, the client connects with that size; a
      slow embedding does not block other coroutines on the MCP server's loop.
- [ ] R10 — a sink that raises on every event leaves the run's status, report
      and error unchanged, and `run_manager` works with no sink.
- [ ] R11 — the existing suite passes; the only test changes are the
      `run_analyst` stubs adapted to the new per-run capability argument.
- [ ] R12 — `PLAN.md` contains the before/after table produced by re-running
      `scripts/run_evaluation.py` and `scripts/run_manager_evaluation.py` on the
      same provider, with the commands that produced it.
- [ ] The read-only boundary is unweakened: no new SQL construction outside the
      existing tools, and `app/sql_safety.py` is untouched.
- [ ] `ruff format .` and `ruff check .` clean; `uv run pytest` green.

## Verification

```bash
# repository gate (from analytics-agent/)
uv run pytest
ruff format .
ruff check .

# existing behavior unchanged
uv run python -m app.manager --out /tmp/m8-before '<management request>'

# live surface (loopback)
uv run python -m app.web            # then open http://127.0.0.1:8001
uv run python -m app.web --check    # starts, serves /, exits cleanly (CI-friendly)

# measurement (hosted provider, same model before and after)
uv run python scripts/run_evaluation.py
uv run python scripts/run_manager_evaluation.py

# manual: submit one analyst question and one management request; confirm the
# pipeline renders live, evidence rows match the report's claims, and the
# groundedness verdict is shown
```

## Open assumptions

- **Framework scope** — Starlette is enough for one route plus static files;
  if the implementer finds it cramped, FastAPI is an acceptable substitute for
  the same design. Risk: low. Confirmed by: implementer.
- **Event granularity** — per-node analyst events (retrieve/generate/validate/
  execute/analyze) are included because they are cheap and make the wait
  legible. If they make the page noisy, collapse them client-side. Risk: low.
- **Queue over 429** — a visitor waiting for a slot is friendlier than a
  rejection. Risk: low. Owner: user (already answered).
- **Speedup magnitude** — the parallel win depends on the hosted provider
  tolerating ~4 concurrent requests; the measured number replaces the estimate,
  and every scenario is re-run rather than extrapolated. Risk: medium (a
  rate-limited provider could give back less than expected). Confirmed by: the
  M8 benchmark run.
- **Budget shape** — the two weeks are expected to split roughly: parallel
  fan-out + attribution (~3 days), progress plumbing (~3 days), web surface and
  page (~4 days), server concurrency safety + tests (~2 days), measurement and
  `PLAN.md` (~1 day), review slack (~1 day). Risk: medium; the page is the item
  most likely to overrun.
- **Local provider** — the parallel path is correct but not faster with a
  serial local model server; no claim will be made about local latency. Risk:
  none (documented as a non-goal).
