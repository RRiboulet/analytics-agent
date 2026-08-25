# Architecture Planning Project Integration: Analytics Agent V2

Read this before proposing architecture. It maps the planning skill onto this repo's boundaries and the V2 direction.

## Working directory

Coordinating docs (`AGENTS.md`, `PLAN.md`) live at the repo root `/workspace`. The project code lives in `mcp-server-demo-main/`, which is the permanent project home (not a temporary download). Run planning inspections from that directory.

## Read first (in order)

1. `AGENTS.md` — environment, safety, and workflow rules.
2. `PLAN.md` — the milestone sequence, decisions, and current status (source of truth).
3. Inspect the existing implementation in `app/`, `db/`, `tests/`.

## Current state (V1)

| Layer | Location | Notes |
|-------|----------|-------|
| MCP server | `app/server.py` | `create_mcp_server()` / `create_asgi_app()`; owns the Postgres pool via ASGI lifespan |
| Tool registry | `app/tools/registry.py` | explicit registration; one file per tool |
| SQL safety | `app/sql_safety.py` | `validate_and_bound_query()` — single read-only statement + auto cap |
| DB access | `app/data_sources/postgres.py` | asyncpg pool, timeouts, caps |
| DB init | `db/init/` | `01_schema.sql`, `02_seed.sql`, `03_readonly_role.sql` |
| Credentials | — | app connects as `factory_readonly`; admin creds used only for init |
| Tests | `tests/` | pytest (asyncio auto mode); unit tests need no live DB |

Existing MCP capabilities: `list_tables`, `describe_table`, `query`.

## Planned boundaries (V2) — do not cross prematurely

- **MCP** is the capability boundary between PostgreSQL and the agent. Tools stay database-capability-focused; no agent orchestration or reasoning inside tools.
- **PostgreSQL** remains the source of truth for analytical data (read-only).
- **pgvector** (inside PostgreSQL) is for semantic metadata retrieval only, not the analytical dataset.
- **LangGraph** owns agent state and orchestration; **LangSmith** owns observability / evaluation.
- **The existing read-only SQL boundary must remain enforced** and never be weakened or bypassed.

## Milestones

Current: **M0 (architecture discovery, pending)**. Then M1 Olist realistic DB → M2 MCP analytics capabilities → M3 metadata + pgvector → M4 first LangGraph agent → M5 LangSmith → M6 evaluation → M7 Autonomous Analytics Manager (future).

Do not design for a later milestone while implementing an earlier one.

## Planning bar

- Prefer the simplest design and existing infrastructure/dependencies; avoid new infra (e.g., a separate vector DB) without a demonstrated need.
- Preserve working V1 functionality; prefer extension over rewrite.
- Call out decisions that are difficult or expensive to reverse.
- Match verification expectations to the `verification` skill.
- After implementation, apply the `code-review` skill to the diff.

## Layout

- `SKILL.md` — the planning skill.
- `references/PROJECT.md` — project-specific architecture and integration points (this file).