# Analytics Agent V2 — Project Plan

## 1. Vision
Upgrade the current MCP + PostgreSQL demo into a reproducible, portfolio-quality **autonomous analytics system**.

The system should eventually allow a user to ask high-level analytical questions about a realistic relational dataset.

The agent should:
1. Understand the analytical task.
2. Discover relevant database metadata.
3. Generate appropriate SQL.
4. Validate the SQL.
5. Execute the query through MCP.
6. Inspect the results.
7. Recover from errors or insufficient queries.
8. Iterate until the task is solved or the attempt limit is reached.
9. Produce an evidence-based analytical answer.
10. Expose the complete workflow through observability and evaluation tooling.

The long-term goal is an **Autonomous Analytics Manager**, but V2 should first establish a reliable evidence-driven analytics agent.

---

# 2. Current V1

The current project contains (under the `mcp-server-demo-main/` project home; `AGENTS.md`/`PLAN.md` live at the repo root `/workspace`):

```text
app/
├── config.py
├── data_sources/
│   └── postgres.py
├── middleware.py
├── server.py
├── sql_safety.py
└── tools/
    ├── describe_factory_table.py
    ├── list_factory_tables.py
    ├── query_factory_data.py
    └── registry.py

db/
└── init/
    ├── 01_schema.sql
    ├── 02_seed.sql
    └── 03_readonly_role.sql

tests/
├── test_factory_tools.py
├── test_middleware.py
└── test_sql_safety.py
```

The existing MCP server currently provides:

* `list_tables`
* `describe_table`
* `query`

The query capability already has a read-only SQL safety boundary.
PostgreSQL is currently run through Docker Compose.
The project uses Python 3.13 and `uv`.
The existing implementation and tests should be preserved and evolved rather than unnecessarily rewritten.

---

# 3. V2 Target Architecture

The target architecture is:

```text
                    Local LLM
                 llama.cpp / Gemma
                        |
                        v
                  LangGraph Agent
                        |
                MCP capabilities
                        |
          +-------------+-------------+
          |             |             |
          v             v             v
      Schema        Metadata        Query
     discovery      retrieval     execution
          |             |             |
          +-------------+-------------+
                        |
                        v
                   PostgreSQL
                 /            \
                /              \
        analytical data      pgvector
                              metadata
```

LangSmith provides observability across the agent workflow.
The LLM should not be expected to memorize the database schema.
Database knowledge should be discovered through metadata retrieval and MCP capabilities.

---

# 4. Dataset

Use the **Olist Brazilian E-Commerce dataset** as the primary V2 dataset.

The dataset is preferred because it provides a realistic relational structure involving concepts such as:
* customers
* orders
* order items
* products
* sellers
* payments
* reviews
* geolocation

The final database should feel like a realistic analytical database rather than a toy dataset.
The ingestion process must be reproducible.
A fresh environment should be able to initialize the database using version-controlled SQL/scripts and documented source data.

---

# 5. Database

PostgreSQL remains the source of truth for analytical data.

The database should contain:
* realistic relational tables
* primary keys
* foreign keys
* appropriate data types
* indexes where justified
* useful timestamps
* numerical measures
* meaningful relationships

Database initialization should remain reproducible through Docker Compose and version-controlled initialization/migration/seed scripts.
The exact final schema should be determined after inspecting and transforming the source dataset.
Do not introduce unnecessary database abstractions.

---

# 6. pgvector Metadata Layer

pgvector is used for **semantic retrieval of database metadata**, not for storing the analytical dataset.

Metadata should capture information such as:
* table descriptions
* column descriptions
* data types
* business meaning
* relationships
* foreign keys
* useful sample values where appropriate

Metadata documents should have embeddings stored in PostgreSQL using pgvector.
The agent should be able to retrieve relevant metadata from a natural-language question.

Example:

```text
Question:
"Which product categories generated the most revenue?"

Metadata retrieval:
    -> order_items
    -> products
    -> category information
    -> relevant monetary columns
```

The complete database schema should not be blindly inserted into every LLM prompt.

---

# 7. MCP Layer

MCP is the capability boundary between the LangGraph agent and PostgreSQL.

Existing capabilities:
```text
list_tables
describe_table
query
```

These should remain.

Potential future capabilities include:
```text
search_metadata
get_relationships
get_table_statistics
get_sample_rows
get_column_statistics
```

These are candidates, not mandatory requirements.
Add capabilities when they are justified by the analytics workflow.
MCP tools should provide database capabilities and should not contain agent-specific reasoning or orchestration.
The existing read-only SQL boundary must remain enforced.

---

# 8. LangGraph Agent

The first agent version should use a relatively simple stateful workflow.

Target workflow:

```text
User Question
      |
      v
Understand Question
      |
      v
Retrieve Relevant Metadata
      |
      v
Generate SQL
      |
      v
Validate SQL
      |
      +-------- invalid/error --------+
      |                               |
      |                               v
      |                         Regenerate SQL
      |                               |
      +-------------------------------+
      |
      v
Execute SQL
      |
      v
Analyze Results
      |
      v
Final Answer
```

The graph should use explicit state.

State should contain information such as:
* user question
* retrieved metadata
* relevant tables
* generated SQL
* validation result
* SQL error
* query result
* analysis
* final answer
* iteration count
* current workflow status

The agent must be iterative.

It should be able to recover from:
* SQL syntax errors
* incorrect table/column selection
* query execution errors
* empty or clearly insufficient results

The graph must have a maximum attempt/iteration limit to prevent infinite loops.
The agent should never fabricate database results.
All numerical and analytical conclusions must be grounded in actual query results.

---

# 9. Agent Status

The workflow should expose an explicit status so that the current state of an agent run can be understood.

Potential states include:
```text
planning
retrieving_metadata
generating_sql
validating_sql
executing_sql
analyzing_results
retrying
completed
failed
```

The exact implementation should follow the needs of the LangGraph state model.
This status will also be useful for observability and future user interfaces.

---

# 10. LangSmith

LangSmith should be integrated from the beginning of the agent implementation.

Trace at minimum:
* complete agent runs
* LangGraph nodes
* LLM calls
* metadata retrieval
* MCP tool calls
* generated SQL
* SQL execution
* errors
* retries
* final answers

Observability is considered a core feature rather than an optional addition.

The purpose is to understand:
* why an agent succeeded
* why it failed
* what SQL it generated
* which metadata it retrieved
* how many iterations it required
* where latency occurs
* which tool calls were unnecessary

---

# 11. Evaluation

Create a reproducible evaluation dataset of analytical questions.
The evaluation set should eventually contain approximately 30–100 questions of increasing difficulty.

Examples:

```text
Which product categories generated the most revenue?

Which states have the highest average order value?

How does delivery time relate to review scores?

Which sellers have declining sales?

What are the busiest purchasing periods?

Identify unusual changes in revenue.

Compare sales performance between two time periods.

Analyze recent sales performance and identify areas
that deserve management attention.
```

Evaluation should consider:

* SQL correctness
* relevant table selection
* numerical correctness
* final answer correctness
* unnecessary tool calls
* retry behavior
* failure rate
* latency where practical

LangSmith should be used for tracing and, where appropriate, evaluation.

---

# 12. Security

The agent is an analytics system and should be read-only.
The existing SQL safety boundary is a critical security property.

The agent must not be able to:
```text
INSERT
UPDATE
DELETE
DROP
ALTER
TRUNCATE
```

or otherwise mutate the database through the analytics MCP interface.
Database credentials and direct PostgreSQL implementation details should remain outside the agent reasoning layer.

---

# 13. Development Milestones

## M0 — Architecture Discovery

Status: pending

Inspect the existing implementation.

Document:
* current architecture
* reusable components
* required refactoring
* database assumptions
* MCP assumptions
* risks
* proposed V2 implementation sequence

No large implementation changes should be made during M0.

---

## M1 — Realistic Database

Status: pending

Replace the toy database with the Olist-based relational dataset.

Goals:

* reproducible dataset acquisition
* transformed relational schema
* PostgreSQL initialization
* SQL schema scripts
* seed/load scripts
* indexes and constraints
* tests
* Docker Compose integration

Success criterion:

A fresh environment can initialize the realistic database reproducibly.

---

## M2 — MCP Analytics Capabilities

Status: complete (see the detailed section below).

Success criterion:
The database can be meaningfully explored through MCP without the agent needing
direct PostgreSQL access.

---

## M2 — MCP Analytics Capabilities

Status: complete.

Implemented (all read-only, following the existing tool/module conventions):

* `get_relationships` — foreign-key graph (child table/column → parent table/column) via
  `pg_constraint`, so the agent can discover how to join tables.
* `get_sample_rows` — bounded, arbitrary sample rows from one table, to inspect real
  values (statuses, ids, formats) before writing a query.
* `get_table_statistics` — exact row count for every public table (single `UNION ALL`).
* `get_column_statistics` — total rows, distinct non-null count, null count, min/max and
  data type for one column.
* New `PostgresClient` methods in `app/data_sources/postgres.py`; table/column names are
  validated against `information_schema` then identifier-quoted (no injection path).
* All four registered in `app/tools/registry.py` (tool count 3 → 7).
* Unit tests in `tests/test_analytics_tools.py` (response contract, blank/unknown inputs,
  infrastructure-error masking, read-only annotations, empty-database edge case).
* Live-DB integration tests in `tests/test_olist_database.py` (FK set, bounded samples,
  exact counts, column stats against real Olist data).

Decisions made during M2:

* The `limit` parameter of `get_sample_rows` is capped at 100 (matches `MAX_ROWS`).
* `get_table_statistics` uses exact `COUNT(*)` rather than planner estimates: only 9 tables
  and a single `UNION ALL`, and exactness matters for an analytics agent; the 1M-row
  `geolocation` count is still fast (~100ms).
* `get_column_statistics` reports distinct *non-null* values and a separate null count.
* Table/column inputs are validated against `information_schema` then quoted, so unknown
  names return a clear "not found" result instead of a SQL error.
* Fixed a shared serializer gap discovered during M2 validation (`app/middleware.py`):
  `structuredContent` rows containing `date`/`datetime`/`time` (e.g. timestamps from
  `get_sample_rows`, or `min`/`max` of a timestamp column) could not be JSON-encoded and made
  every such call fail. The serializer now emits ISO-8601 strings. This also fixes a latent
  failure in the pre-existing `query` tool for any result containing a timestamp/date.

Verified:

* `uv run pytest` → 43 passed (42 after M2 + 1 empty-database edge case; 25 pre-M2).
* New tool modules and new client methods reach 100% line/branch coverage.
* `ruff check .` clean for all new/changed code (see note below).
* Live DB: `get_relationships` returns the 7 expected FKs; `order_status` column stats are
  sensible (8 distinct, 0 null); table counts match source CSVs.

Note: `ruff format --check .` and `ruff check .` report pre-existing deviations in code
not touched by M2 (`app/data_sources/postgres.py` `list_tables`/`fetch_many`/`describe_table`
lines and `app/sql_safety.py`). These predate M2 and are left unresolved per AGENTS.md
(no unrelated formatting churn).

# M3 — Metadata + pgvector

Status: complete (see the detailed section below).

Create the database metadata model and semantic retrieval layer.

Goals:
* metadata generation
* embeddings
* pgvector storage
* semantic search
* retrieval tests

Success criterion:
A natural-language analytical question can retrieve the relevant tables, columns and
relationships without sending the complete schema to the LLM.

## M3 — Metadata + pgvector

Status: complete.

Implemented (all read-only, following the existing module/tool conventions):

* PostgreSQL image switched to `pgvector/pgvector:pg16` (same Postgres 16, + the `vector`
  extension). `db/init/04_metadata.sql` creates the extension and the `metadata_documents`
  table (id, entity_type, entity_id, title, content, doc_metadata jsonb, `vector(384)`
  embedding) with an HNSW cosine index, and grants `olist_readonly` SELECT.
* `app/metadata_seed.json` — curated business meaning + retrieval keywords for every Olist
  table and the key monetary/time/quality columns.
* `app/metadata.py` — `build_metadata_documents()` combines auto-generated schema facts
  (columns, data types via `describe_table`, FK graph via `get_relationships`) with the
  curated seed into one self-contained document per table, column and relationship.
* `app/embedder.py` — `MetadataEmbedder` (fastembed `BAAI/bge-small-en-v1.5`, ONNX, fully
  local, deterministic model in a fixed `cache_dir`) with a process-wide lazy `get_embedder()`.
* New `PostgresClient` methods: `ensure_metadata_schema`, `replace_metadata_documents`
  (idempotent, atomic), `search_metadata` (cosine top-k). Table/column identifiers remain
  safe; the vector is passed as a validated literal.
* New read-only MCP tool `search_metadata(question, top_k)` (registered, tool count 7 → 8).
* `scripts/seed_metadata.py` — reproducible admin-role seeding pipeline that (re)builds and
  replaces the metadata index; the running server stays on the read-only role.
* `metadata_documents` is treated as infrastructure: it is excluded from `list_tables`,
  `describe_table`, `get_sample_rows`, `get_column_statistics` and `get_table_statistics` so
  it never surfaces as a queryable analytics source (analytics schema stays at 9 tables).
* Unit tests in `tests/test_metadata_tools.py` (embedder, document builder incl. legacy
  string seed and unknown-entity defaults, search_metadata response contract, blank/invalid
  inputs, infra-error masking, read-only annotation, empty-replace short-circuit).
* Live-DB integration tests in `tests/test_metadata_database.py` (vector extension present,
  seeding idempotent, read-only role can SELECT, semantic-search relevance + ordering,
  metadata table hidden from analytics tools).

Verified:

* `uv run pytest` → 60 passed (43 pre-M3 + 17 new).
* M3 modules (embedder, metadata builder, search_metadata, config, client methods) at 100%
  line/branch coverage; remaining live-DB-only client infra lines are the pre-existing M2
  lines that require an unreachable-db state.
* Ruff clean for all new/changed code (remaining errors are the same pre-existing
  deviations in `sql_safety.py` and two `postgres.py` lines, per AGENTS.md).
* Live DB: revenue query retrieves `order_items`/`order_items.price`/product-category join
  in the top hits; the metadata table does not appear in `list_tables`.

---

## M4 — First LangGraph Agent

Status: complete

Implemented a standalone analytics agent (`app/agent/`) that consumes the MCP
server (DB access only through the read-only tools, per D006) and a local
OpenAI-compatible LLM.

* `app/agent/state.py` — explicit `AgentState` TypedDict + `AgentStatus` (the nine
  PLAN §9 states).
* `app/agent/graph.py` — LangGraph state machine: understand -> retrieve metadata
  (`search_metadata`) -> generate SQL -> validate (read-only) -> execute (`query`)
  -> analyze -> answer, with a retry router and a bounded `AGENT_MAX_ATTEMPTS`
  limit so invalid/erroring queries recover but never loop forever.
* `app/agent/llm.py` — stateless OpenAI-compatible client (config-driven
  `LLM_BASE_URL`/`LLM_MODEL`, default `gemma-4-E4B`) plus a deterministic
  `FakeLLM` for tests.
* `app/agent/capabilities.py` — wraps the MCP server via `langchain-mcp-adapters`;
  surfaces query errors as the recovery signal and raises (never fabricates) on a
  total transport failure.
* `app/agent/tracing.py` — fail-open Langfuse tracer: enabled only when
  `LANGFUSE_PUBLIC_KEY` is present; otherwise un-instrumented.
* `app/agent/entrypoint.py` — `run()` API + CLI `python -m app.agent [--json] '<question>'`.
* Agent-as-MCP-tool is deliberately deferred to M7.

Decisions made during M4:

* **Process topology (D-M4-1)**: standalone agent process; clear boundary between
  the MCP server and the agent.
* **LLM provider (D-M4-2)**: OpenAI-compatible endpoint configured by env; default
  `http://host.docker.internal:11434/v1` (host-reachable interface), model
  `gemma-4-E4B`.
* **MCP transport (D-M4-3)**: reuse `langchain-mcp-adapters`; no custom transport.
* **Observability (D-M4-4)**: Langfuse (self-hosted, free) as the tracer; D005
  updated accordingly; fail-open.
* **Public surface (D-M4-5)**: CLI + `run()` for M4; agent-as-tool defer to M7.

Verified:

* `uv run pytest` -> 84+ passed (unit: graph, state, capabilities, LLM client,
  tracing, entrypoint; live-DB integration drives the read-only MCP boundary).
* New agent modules at ~100% line coverage (graph/state/llm/capabilities/tracing
  flat 100%); remaining 2 module-entry guard lines are exercised by a subprocess
  test but only run at process entry.
* Full gate clean: `ruff format --check .` and `ruff check .` pass.

Success criterion met: the agent answers a representative set of analytical
questions against the realistic database (validated live with a deterministic
LLM; SQL/answer quality depends on the served model).

---

## M5 — LangSmith Observability

Status: pending

Instrument the complete agent workflow.

Success criterion:
An individual agent run can be inspected end-to-end, including metadata retrieval, LLM calls, generated SQL, MCP calls, retries and final answer.

---

## M6 — Evaluation

Status: pending

Create the initial analytical benchmark and evaluation process.

Success criterion:
Agent performance can be measured rather than judged only through manual experimentation.

---

## M7 — Autonomous Analytics Manager

Status: future

Extend the evidence-driven analytics agent toward autonomous analytical tasks.
Potential capabilities:

* decompose high-level analytical requests
* perform multiple analyses
* compare time periods
* investigate anomalies
* identify interesting findings
* perform follow-up queries
* synthesize management-level reports

This milestone should not be implemented until M1–M6 provide a reliable foundation.

---

# 14. Architectural Principles

The following principles should guide implementation:

1. Prefer simple architecture over premature abstraction.
2. Preserve working V1 functionality.
3. Keep PostgreSQL independent from the agent.
4. Keep MCP as the database capability boundary.
5. Keep pgvector focused on metadata retrieval.
6. Keep LangGraph responsible for agent state and orchestration.
7. Keep LangSmith responsible for observability and evaluation support.
8. Keep SQL execution read-only.
9. Ground analytical conclusions in database evidence.
10. Make the entire project reproducible.
11. Add complexity only when it solves a demonstrated problem.
12. Prefer independently testable components.
13. Do not implement future milestones prematurely.

---

# 15. Decisions

### D001 — Dataset

Decision: Use the Olist Brazilian E-Commerce dataset.

Reason: It provides a realistic multi-table relational domain suitable for analytical queries and agent-driven schema discovery.

### D002 — Database

Decision: PostgreSQL remains the primary analytical database.

Reason: It is already part of V1 and provides the relational and SQL capabilities required by the project.

### D003 — Vector Store

Decision: Use pgvector inside PostgreSQL.

Reason: Metadata can remain close to the database while avoiding an unnecessary second infrastructure dependency.

### D004 — Agent Orchestration

Decision: Use LangGraph.

Reason: The analytics workflow requires explicit state, controlled iteration, retries and observable transitions.

### D005 — Observability

Decision: Langfuse (self-hosted, free) as the V2 tracer, in the original
LangSmith direction.

Reason: Agent traces, tool calls, failures and evaluation are core to
understanding and improving the system. LangSmith's free developer tier requires
an account/API key; Langfuse is self-hostable at no cost and integrates with
LangGraph via a langchain callback. The M4 tracer is fail-open so runs are never
gated on telemetry.

### D007 — Agent LLM

Decision: local OpenAI-compatible LLM (llama.cpp/Ollama), configured by env.

Reason: the agent's SQL generation/analysis steps use a discrete-model-call
pattern (no open tool-calling loop within the model) for determinism; the served
model is ``gemma-4-E4B`` over ``LLM_BASE_URL``.

### D008 — Agent Tool Surface (M4)

Decision: no agent-as-MCP tool in M4; expose the agent via CLI + ``run()`` only.

Reason: keep the MCP server tool-only; converting the agent into an MCP tool is a
more autonomous capability best placed in M7.

### D006 — Agent Database Access

Decision: Database access occurs through MCP.

Reason: MCP provides a clear capability and security boundary between the agent and PostgreSQL.

---

# 16. Open Questions

These should be resolved during implementation rather than prematurely.

* Which embedding model should be used?
* How should metadata documents be chunked?
* Which metadata should be generated automatically versus manually curated?
* Which MCP capabilities are actually required beyond the existing three?
* How should SQL validation be divided between the MCP safety layer and agent workflow?
* How should query results be represented in LangGraph state?
* Which LangSmith evaluation mechanisms provide the most useful signal?
* How should the local Gemma model be prompted for reliable SQL generation?

---

# 17. Current Status

Current milestone:

**M4 — First LangGraph agent**

Completed:

* [x] MCP server
* [x] PostgreSQL Docker environment
* [x] `list_tables`
* [x] `describe_table`
* [x] `query`
* [x] SELECT-only SQL safety boundary
* [x] MCP tests
* [x] Docker-based database initialization
* [x] Inspect existing repository
* [x] Confirm V1 architecture
* [x] Design Olist database migration

Next:

* [x] **M1 — Implement reproducible realistic (Olist) database**
* [x] **M2 — MCP analytics capabilities** — `get_relationships`, `get_sample_rows`, `get_table_statistics`, `get_column_statistics`
* [x] **M3 — Metadata + pgvector** — metadata docs, embeddings (fastembed), pgvector storage, `search_metadata`, seeding
* [x] **M4 — First LangGraph agent** — standalone `app/agent/`, read-only MCP boundary, retry recovery, bounded attempts, Langfuse fail-open tracing.

# 18. M1 Status & Decisions

**Status: complete.**

Implemented:

* Olist source CSVs stored at `data/olist/` (9 files).
* `db/init/01_schema.sql` — realistic relational schema: `customers`, `sellers`, `products`,
  `product_category_translation`, `orders`, `order_items`, `order_payments`, `order_reviews`,
  `geolocation`.
* Proper PKs (incl. composite keys for `order_items`, `order_payments`, `order_reviews`), FKs,
  data types, and CHECK constraints.
* Justified indexes on FK-lookup and common analytics access paths.
* `db/init/02_load.sql` — deterministic server-side `COPY` from the mounted CSVs.
* `docker-compose.yml` mounts `./data/olist:/olist-data:ro` so init scripts can load the data.
* `tests/test_olist_database.py` — static schema/CSV tests (no live DB) plus DB integration tests
  that skip when Postgres is unreachable.

Decisions made during M1:

* **File role separation** (`data/` vs `db/init/` vs `app/`) as requested: `data/olist` holds source
  CSVs; `db/init` holds schema + load SQL; load uses server-side `COPY`.
* **geolocation has no natural key** (many coordinates share a zip prefix) — kept as a dimensional
  lookup table indexed on zip prefix only.
* **Missing translation entries handled explicitly**: two product categories present in
  `olist_products_dataset.csv` are absent from the translation file; they are added as a documented
  `INSERT` in `02_load.sql` rather than modifying the source data or dropping the FK.
* **Timestamp/zip types**: zip codes stored as `TEXT` (preserve leading zeros); money as `NUMERIC(10,2)`;
  timestamps as `TIMESTAMP` (dataset has no timezone carrier).

Verified:

* Fresh `docker compose down -v && up -d postgres` initializes all 9 tables with correct row counts
  matching every source CSV (incl. handling of embedded newlines in `order_reviews`).
* All foreign keys have zero orphan rows.
* Read-only role denies writes; `SELECT` works.
* Analytical join query across `order_items`/`products`/`product_category_translation` returns
  sensible revenue-by-category results.
* MCP server starts, `/live` and `/ready` return healthy, list/describe/query work against Olist
  data, and the read-only SQL safety layer still rejects writes.
* `uv run pytest` → 25 passed (16 original + 9 new); `ruff check tests/` clean.

Subsequent hardening (review findings):

* DB integration tests now retry the connection before skipping (Postgres temp-
  server restart window on fresh init), assert `InsufficientPrivilegeError` for the
  read-only write-denial test, and `02_load.sql` runs `ANALYZE` so fresh databases
  have planner statistics immediately.

Tool rename (user-requested, M2-aligned):

* MCP tools renamed to generic discovery names: `list_factory_tables` → `list_tables`,
  `describe_factory_table` → `describe_table`, `query_factory_data` → `query`; the
  data-source label is now `olist-postgres` and the MCP server name is `mcp-analytics-demo`.
  Module files renamed to match. The database and roles were also renamed to match
  the dataset: db `factory` → `olist`, roles `factory_admin`/`factory_readonly` →
  `olist_admin`/`olist_readonly`, volume `factory-postgres` → `olist-postgres`.
  `DATABASE_URL` and `.env`/`.env.example` updated accordingly.
* `query` surfaces real SQL execution errors (missing column/table) instead of
  masking them as an unavailable database, while genuine infrastructure failures
  still return the generic message. An agent can now self-correct on SQL errors.
