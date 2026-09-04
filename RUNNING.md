# Running the Analytics Agent

A quick hands-on guide to running the system. All commands are executed from
the project home:

```bash
cd /workspace/analytics-agent
```

## Prerequisites

* The environment is ready when:
  * `uv` is available (Python 3.13; the `.venv/` environment is managed by uv).
  * Postgres is up: `docker compose ps` shows `postgres` as `Up (healthy)`.
  * An LLM backend is configured in `.env` (see [Configuration](#configuration)).
* At a minimum, this needs: the Postgres container, the MCP server, and the
  LLM backend. The MCP server is **required** for the agent, manager, and
  evaluation — they reach the database only through it.

## 1. Start the database (first time or after a reset)

```bash
docker compose up -d postgres          # initializes Olist schema + data from db/init/
```

Wait until the container reports healthy (`docker compose ps`). To start from
a clean slate:

```bash
docker compose down -v && docker compose up -d postgres
```

Rebuild the semantic metadata index (pgvector) whenever the database is
recreated (the script is idempotent — safe to re-run):

```bash
uv run python -m scripts.seed_metadata
```

> Note: the first run downloads the embedding model
> (`BAAI/bge-small-en-v1.5`, fastembed/ONNX) and needs network access.

## 2. Start the MCP server (one terminal)

```bash
uv run python -m app
```

Health check:

```bash
curl http://localhost:8000/live     # → {"status":"alive"}
```

## 3. Run the analytics agent (M4)

Ask a single analytical question. The agent discovers schema/metadata, writes
and validates SQL, executes it through the read-only MCP boundary, and answers
grounded in the results:

```bash
uv run python -m app.agent "Which product categories generated the most revenue?"
uv run python -m app.agent --json "How does delivery time relate to review scores?"
```

`--json` prints the full run state (status, attempts, generated SQL, answers).

## 4. Run the autonomous analytics manager (M7 — latest)

Give it a high-level management request. It decomposes it into ≤4 sub-analyses,
runs each through the analyst agent, and synthesizes a report whose numbers are
all traceable to executed, read-only queries:

```bash
uv run python -m app.manager "Analyze recent sales performance and identify areas that deserve management attention"
```

Options:

```bash
uv run python -m app.manager --json "<request>"            # machine-readable output
uv run python -m app.manager --out /tmp/manager-run "<request>"   # writes report.md + evidence.json
```

## 5. Run the evaluation benchmark (M6)

Measure agent performance against the 30-case `data/evaluation/olist_v1.yaml`
benchmark (deterministic judging against reference SQL result sets):

```bash
uv run python -m scripts.run_evaluation --out /tmp/eval-slice --from 1 --to 10    # cases 1–10
uv run python -m scripts.run_evaluation --case revenue-011 --out /tmp/eval-one     # single case
uv run python -m scripts.run_evaluation --out /tmp/eval-full                       # all 30 cases
```

Writes `summary.json`, per-case `results.json`, and `report.md` into `--out`.

## 6. Test suite & checks

```bash
uv run pytest                          # ~245 tests (unit + live-DB integration)
ruff check .
ruff format --check .
```

## Configuration (`.env`)

Everything lives in `analytics-agent/.env` (`.env.example` documents all keys):

* `LLM_PROVIDER` — `openrouter` (hosted, fast) or `llamacpp` (local
  llama.cpp server on `LLM_BASE_URL`). Default is `llamacpp`.
* `OPENROUTER_API_KEY` / `OPENROUTER_MODEL` — used when
  `LLM_PROVIDER=openrouter`.
* `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` — optional tracing. When set,
  every agent/manager run is exported to Langfuse (cloud by default); tracing
  is fail-open and never blocks a run.
* `AGENT_MAX_ATTEMPTS` / `MANAGER_MAX_ATTEMPTS` — retry budgets.

## Notes

* The MCP server (step 2) must be running for steps 3–5.
* The agent/manager take the question as a *positional* argument — quote it.
* With `LLM_PROVIDER=llamacpp` (local CPU model, ~1–3 tok/s) a manager run can
  take several minutes; OpenRouter is much faster.
* The database is strictly read-only from the agent's point of view: every
  query passes through the SQL safety boundary.
* With `LLM_PROVIDER=openrouter` (and with Langfuse Cloud tracing), questions,
  generated SQL, and query results are sent to a third-party SaaS — the
  project accepts this tradeoff for development (see PLAN.md D005/D007).