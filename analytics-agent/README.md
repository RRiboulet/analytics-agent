# Analytics MCP Server & Agent

A local FastMCP server for querying realistic e-commerce data (the Olist Brazilian E-Commerce dataset) in PostgreSQL, designed to be driven from a local LLM served by llama.cpp. It follows the reusable MCP server pattern: the server factory owns lifecycle and transport, `app/tools/registry.py` is the explicit tool registry, and `app/data_sources/postgres.py` owns database access.

## What is included

- Streamable HTTP MCP server at `http://localhost:8000/mcp`
- PostgreSQL 16 with the Olist e-commerce dataset loaded from `data/olist/`
- Read-only database role and SQL safety validation
- Tools for table discovery, table descriptions, bounded `SELECT` queries, and analytical
  schema inspection (`get_relationships`, `get_sample_rows`, `get_table_statistics`,
  `get_column_statistics`)
- Semantic metadata search (`search_metadata`) over a pgvector index, so the agent can
  retrieve relevant tables/columns/relationships from a natural-language question
- Liveness endpoint at `http://localhost:8000/live`
- Database-backed readiness endpoint at `http://localhost:8000/ready`
- Tool results return structured data in both `structuredContent` and `content` (for llama.cpp clients)
- A standalone **analytics agent** (`app/agent/`, M4) that answers a natural-language question
  end-to-end: retrieve metadata -> generate read-only SQL -> validate -> execute through MCP
  -> analyze -> answer, with bounded retry recovery and optional Langfuse tracing

This is a local demonstration. Authentication, Azure deployment, and write operations are intentionally outside this first version.

## Prerequisites

Install Docker, Docker Compose, Python 3.13+, and `uv`.

## Run locally

1. Create the local environment file:

	```bash
	cp .env.example .env
	```

2. Start PostgreSQL:

	```bash
	docker compose up -d postgres
	docker compose ps
	```

3. Install Python dependencies:

	```bash
	uv sync
	```

4. (Optional) Populate the semantic metadata index. The database is initialized
   with the pgvector extension and the empty `metadata_documents` table. Build
   and load the metadata documents once:

   ```bash
   uv run python -m scripts.seed_metadata
   ```

   This connects with the admin role, builds the metadata documents from the
   schema plus curated descriptions, embeds them locally via `fastembed`
   (ONNX, no external API), and atomically replaces the index. Re-run it
   anytime to refresh. The analytics server itself always runs as the read-only
   role.

5. Start the MCP server in a second terminal:

	```bash
	uv run mcp-analytics-server
	```

The server connects as `olist_readonly`. The database admin credentials are used only by PostgreSQL initialization and are not used by the MCP application.

## Connect a llama.cpp client

Point an MCP client backed by llama.cpp at the streamable HTTP endpoint `http://localhost:8000/mcp`. Tool results already carry structured data as a JSON string in the `content` field, so a llama.cpp client can parse it directly.

Useful questions to ask against the Olist data:

- Which product categories generate the most revenue?
- Which states have the highest average order value?
- How does delivery time relate to review scores?
- Which product categories have the best reviews?

The query tool is intended for read-only analysis. A client should use the discovery tools first when it needs to understand the schema.

## Run the analytics agent (M4)

The agent is a standalone consumer of the MCP server. It connects to the configured LLM and to the running MCP server, then runs the LangGraph workflow.

### Choose the LLM provider (llama.cpp or OpenRouter)

`LLM_PROVIDER` selects the backend the agent runs on. Both configurations can stay in `.env` simultaneously; the provider picks the active one.

**`llamacpp` (default)** — local OpenAI-compatible server:

```env
LLM_PROVIDER=llamacpp
LLM_BASE_URL=http://host.docker.internal:8080/v1
LLM_MODEL=gemma-4   # must match the --alias of the running llama-server
```

**`openrouter`** — hosted OpenRouter API (OpenAI-compatible, bearer-authenticated):

```env
LLM_PROVIDER=openrouter
OPENROUTER_API_KEY=<key from https://openrouter.ai/keys>
OPENROUTER_MODEL=openai/gpt-4o-mini   # any model id from https://openrouter.ai/models
# OPENROUTER_BASE_URL=https://openrouter.ai/api/v1  # override only if needed
```

With `LLM_PROVIDER=openrouter`, requests go to `{OPENROUTER_BASE_URL}/chat/completions` with `Authorization: Bearer <OPENROUTER_API_KEY>`. A missing key fails fast at startup with a clean `Configuration error` instead of a traceback. The shared per-call settings (`LLM_TIMEOUT_SECONDS`, `LLM_MAX_TOKENS`, `LLM_ANSWER_MAX_TOKENS`) apply to both providers — a hosted model is much faster than a local CPU-served one, so a shorter timeout is safe. Retries, error handling, read-only safety, and Langfuse tracing behave identically on both providers.

**Privacy note:** with `openrouter`, analytical questions, generated SQL and query results are sent to OpenRouter's SaaS (third-party processing). The `llamacpp` path keeps everything local. Switching between them is a one-line `.env` change.

With the MCP server running (step 5 above), ask a question:

	```bash
	uv run python -m app.agent --json "Which product categories generate the most revenue?"
	```

A plain (non-JSON) summary is printed without `--json`. The agent:

1. retrieves relevant metadata (`search_metadata`) for the question;
2. generates a candidate read-only query;
3. validates it is a single SELECT (the read-only safety boundary);
4. executes it through the MCP `query` tool;
5. recovers from invalid/erroring queries up to `AGENT_MAX_ATTEMPTS` (default 3);
6. produces an evidence-grounded answer.

Model failures (timeouts, transport/HTTP errors, malformed responses) are
handled the same way: the step is retried up to `AGENT_MAX_ATTEMPTS` and then
the run fails cleanly with a `failed` status and the error message in the JSON
output — a slow local model can no longer crash the CLI with an `httpx`
traceback. `LLM_TIMEOUT_SECONDS` (default 300) is the per-call budget; local
CPU-served models are slow (~1-3 tokens/s), so a single generation takes
minutes. The final answer step has its own, tighter token cap
(`LLM_ANSWER_MAX_TOKENS`, default 512) so a `--reasoning on` model cannot burn
the whole SQL-generation budget on chain-of-thought before the answer.

Because the database is reached only through the read-only MCP tools, the agent cannot
mutate the data. Tracing is fail-open: set `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY`/
`LANGFUSE_HOST` in the environment (or in `.env`) to enable Langfuse traces; otherwise
runs are un-instrumented. **Note:** the current setup traces to **Langfuse Cloud**
(`https://cloud.langfuse.com`, free tier) — trace payloads (questions, retrieved
metadata, generated SQL, query results) are sent to Langfuse's SaaS. To keep trace
data local instead, point `LANGFUSE_HOST` at a self-hosted Langfuse instance; no code
change is required. When enabled, each run produces one trace (tagged
`analytics-agent`, with the question as metadata) covering every graph node with its
state transitions, both LLM calls (`generate_sql` / `generate_answer`), each MCP tool
call with arguments and result, retries/errors, and the final answer.


## Test the MCP server step by step

These steps assume PostgreSQL and the MCP server are already running (see [Run locally](#run-locally)).

1. Check the server is alive:

	```bash
	curl -s http://localhost:8000/live
	```

	Expect `{"status": "alive"}`.

2. Check the server can reach the database:

	```bash
	curl -s http://localhost:8000/ready
	```

	Expect `{"status": "ready"}`. A `503` with `{"status": "not_ready"}` means PostgreSQL is not reachable — check `docker compose ps`.

3. Install Node.js if `npx` is not already available:

	```bash
	sudo apt update && sudo apt install -y nodejs npm
	```

4. Start MCP Inspector against the running server:

	```bash
	npx @modelcontextprotocol/inspector http://localhost:8000/mcp
	```

	Open the URL Inspector prints (it includes an auth token). Confirm the transport is "Streamable HTTP" with the URL `http://localhost:8000/mcp`, then click **Connect**.

5. In the Inspector **Tools** tab, click **List Tools** and confirm all eight tools are present:
   `list_tables`, `describe_table`, `query`, `get_relationships`, `get_sample_rows`,
   `get_table_statistics`, `get_column_statistics`, `search_metadata`.

6. Call `list_tables` with no arguments. Expect `customers`, `sellers`, `products`, `orders`, `order_items`, `order_payments`, `order_reviews`, `geolocation`, and `product_category_translation` in the result.

7. Call `describe_table` with `table_name` set to `orders`. Expect a list of columns including `order_id`, `customer_id`, `order_status`, and `order_purchase_timestamp`.

8. Call `query` with:

	```sql
	SELECT o.order_status, COUNT(*) AS order_count
	FROM orders AS o
	GROUP BY o.order_status
	ORDER BY order_count DESC
	```

	Expect a row per order status and `valid: true` in the structured result. The server appends a default `LIMIT 100` when a query does not specify one.

8b. Call `get_relationships` with no arguments. Expect child/parent table and column pairs,
	including `order_items.order_id -> orders.order_id` and
	`order_reviews.order_id -> orders.order_id`.

8c. Call `get_sample_rows` with `table_name` set to `orders` and `limit` set to `3`. Expect
	three rows of realistic order columns.

8d. Call `get_table_statistics` with no arguments. Expect the nine public tables with exact
	row counts (e.g. `orders` 99441, `geolocation` 1000163).

8e. Call `get_column_statistics` with `table_name` set to `orders` and `column_name` set to
	`order_status`. Expect `total_rows` 99441, `distinct_count` 8, `null_count` 0, and
	`data_type` `text`.

8f. Call `search_metadata` with `question` set to `Which product categories generate the
	most revenue?`. Expect the relevant tables, columns and relationships (e.g.
	`order_items`, `order_items.price`, `product_category_translation`) returned by cosine
	similarity. This requires the metadata index to have been seeded (step 4 in Run locally).

9. Confirm the safety checks by calling `query` with each of the following and expecting `valid: false` with no rows returned:

	- `` (blank query)
	- `DELETE FROM products`
	- `SELECT 1; SELECT 2`
	- `DROP TABLE products`

10. Repeat steps 6–8 from your llama.cpp MCP client to confirm the server behaves identically through a client other than Inspector.

## Development checks

```bash
uv run pytest
uv run ruff check .
```

The unit tests cover SQL validation and the MCP tool response contract without requiring a live database. For a clean database, remove the named volume and initialize it again:

```bash
docker compose down -v
docker compose up -d postgres
```

Never place real database credentials or production data in `.env`, seed files, query examples, or logs.