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

Status: pending

Preserve the existing MCP tools and add only the capabilities needed for the analytics workflow.

Goals:
* schema discovery
* metadata access
* analytical query execution
* useful database inspection capabilities
* continued read-only enforcement
* comprehensive tests

Success criterion:
The database can be meaningfully explored through MCP without the agent needing direct PostgreSQL access.

---

## M3 — Metadata + pgvector

Status: pending

Create the database metadata model and semantic retrieval layer.

Goals:
* metadata generation
* embeddings
* pgvector storage
* semantic search
* retrieval tests

Success criterion:
A natural-language analytical question can retrieve the relevant tables, columns and relationships without sending the complete schema to the LLM.

---

## M4 — First LangGraph Agent

Status: pending

Implement the initial analytics workflow:

```text
question
→ metadata retrieval
→ SQL generation
→ validation
→ execution
→ analysis
→ answer
```

Goals:
* explicit state
* deterministic transitions where appropriate
* MCP tool integration
* SQL error recovery
* maximum attempt limit
* evidence-grounded answers

Success criterion:
The agent can answer a representative set of analytical questions against the realistic database.

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

Decision: Use LangSmith.

Reason: Agent traces, tool calls, failures and evaluation are core to understanding and improving the system.

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

**M0 — Architecture Discovery**

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

Not changed (out of M1 scope):

* MCP tool names still `list_factory_tables` / `describe_factory_table` / `query_factory_data` (via
  `data` source label `sample-factory-postgres`). Renaming is an M2 interface decision and was left
  untouched to keep M1 surgical and to avoid breaking the registry/README contract.
* `app/` code, SQL safety layer, and existing MCP tool implementation unchanged.

# M2 — MCP Analytics Capabilities

Status: pending (next).

Goals:
* preserve existing MCP tools and add only capabilities justified by the analytics workflow
* schema discovery, metadata access, analytical query execution, useful database inspection capabilities
* continued read-only enforcement, comprehensive tests

Success criterion:
The Olist database can be meaningfully explored through MCP without the agent needing direct
PostgreSQL access.
