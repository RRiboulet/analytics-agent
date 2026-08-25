# Project Context: factory-mcp-demo

Load this reference when a review needs the surrounding architecture, not just the diff. Keeps reviews grounded in how this codebase is actually built.

Project code lives in `mcp-server-demo-main/` (repo docs at `/workspace`); review that directory, not `/workspace` itself.

## What this project is

A local FastMCP server exposing PostgreSQL-backed tools. It demos a **reusable MCP server pattern**: server factory owns lifecycle and transport, `app/tools/registry.py` is the explicit tool registry, and `app/data_sources/postgres.py` owns all database access.

## Where things live

| Path | Role |
|------|------|
| `app/server.py` | `create_mcp_server()` and `create_asgi_app()`; owns the process-lifetime Postgres pool |
| `app/__main__.py` | `uvicorn` entrypoint |
| `app/config.py` | pydantic-settings, reads `.env` |
| `app/sql_safety.py` | `validate_and_bound_query()` — single read-only statement + auto `LIMIT` |
| `app/tools/registry.py` | explicit tool registration (`register_tools`) |
| `app/tools/*_factory_*.py` | one file per tool; each tool returns Go `CallToolResult` with `structuredContent` |
| `app/data_sources/postgres.py` | `PostgresClient` — asyncpg pool, timeouts, caps |
| `app/middleware.py` | `copy_structured_content_to_content` — llama.cpp compat: copies `structuredContent` into `content` |
| `db/init/` | deterministic schema + seed + read-only role |
| `tests/` | pytest (asyncio auto mode) |

## Key review conventions to treat as a baseline

- **Response contract:** every tool returns `{valid: bool, message: str, entries: list}` as `structuredContent`. Keep this uniform.
- **Safety gate.** All user SQL passes through `validate_and_bound_query()` and the DB role is read-only. Never bypass this for agent-controlled input.
- **Lifecycle.** The Postgres pool is owned by the ASGI app lifespan in `create_asgi_app()`, *not* FastMCP's `lifespan` (which re-enters per request under `stateless_http=True`). Do not open/close the pool in the wrong layer.
- **Middleware default.** Tools build `CallToolResult` with `structuredContent` and run it through `copy_structured_content_to_content()` so llama.cpp-style clients see JSON in `content`.
- **Deterministic data.** Seed data must stay reproducible; the survival test count and README examples depend on it.

## Non-goals (V1)

Auth, cloud deployment, telemetry, agents, and write operations are intentionally out of scope. A change that silently adds writes or auth coupling should be flagged, not praised, unless the README's scope claims change deliberately with it.

## Test shape

- `tests/test_sql_safety.py` — pure validation, no DB.
- `tests/test_factory_tools.py` — fake client/MCP asserting the tool response contract.
- `tests/test_middleware.py` — structured-content serialization (incl. Decimal) without a DB.

Run: `uv run pytest` and `uv run ruff check .`

## Gotchas to watch in review

- Changing the `structuredContent` keys or tool names silently breaks the README walkthrough and client expectations.
- Adding a raw SQL path (e.g., a new tool) that skips `sql_safety` breaks the safety invariant.
- Moving pool connect/close between layers breaks request handling on a stateless server.