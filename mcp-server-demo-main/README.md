# Factory Data MCP Demo

A local FastMCP server for querying realistic e-commerce data (the Olist Brazilian E-Commerce dataset) in PostgreSQL, designed to be driven from a local LLM served by llama.cpp. It follows the reusable MCP server pattern: the server factory owns lifecycle and transport, `app/tools/registry.py` is the explicit tool registry, and `app/data_sources/postgres.py` owns database access.

## What is included

- Streamable HTTP MCP server at `http://localhost:8000/mcp`
- PostgreSQL 16 with the Olist e-commerce dataset loaded from `data/olist/`
- Read-only database role and SQL safety validation
- Tools for table discovery, table descriptions, and bounded `SELECT` queries
- Liveness endpoint at `http://localhost:8000/live`
- Database-backed readiness endpoint at `http://localhost:8000/ready`
- Tool results return structured data in both `structuredContent` and `content` (for llama.cpp clients)

This is a local demonstration. Authentication, Azure deployment, telemetry, agents, and write operations are intentionally outside this first version.

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

4. Start the MCP server in a second terminal:

	```bash
	uv run factory-mcp-server
	```

The server connects as `factory_readonly`. The database admin credentials are used only by PostgreSQL initialization and are not used by the MCP application.

## Connect a llama.cpp client

Point an MCP client backed by llama.cpp at the streamable HTTP endpoint `http://localhost:8000/mcp`. Tool results already carry structured data as a JSON string in the `content` field, so a llama.cpp client can parse it directly.

Useful questions to ask against the Olist data:

- Which product categories generate the most revenue?
- Which states have the highest average order value?
- How does delivery time relate to review scores?
- Which product categories have the best reviews?

The query tool is intended for read-only analysis. A client should use the discovery tools first when it needs to understand the schema.

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

5. In the Inspector **Tools** tab, click **List Tools** and confirm all three tools are present: `list_tables`, `describe_table`, `query`.

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