# Verification Project Integration: Analytics Agent V2

Specific verification commands and known integration boundaries for this project.

## Working directory

Coord/docs (`AGENTS.md`, `PLAN.md`) live at the repo root. The code lives in `mcp-server-demo-main/` (permanent project home). Run verification and test commands from there.

## Verify commands

```bash
cd mcp-server-demo-main
uv run pytest              # unit + integration suite
uv run ruff check .        # lint
uv run ruff format .       # format
```

Tests use pytest with asyncio auto mode; unit tests do not require a live database.

## Database / Docker

```bash
cd mcp-server-demo-main
docker compose up -d postgres
docker compose ps
```

After a fresh init, verify the read-only role (`db/init/03_readonly_role.sql`) exists and that `SELECT` works but write statements are denied.

## Known integration boundaries to verify

```text
Agent → MCP
MCP → PostgreSQL
Metadata retrieval → pgvector
LangGraph state → next node
Application → external service (LangSmith / llama.cpp / Docker)
```

A component passing its unit tests does not prove its integration boundary works.

## Milestone-appropriate verification

| Milestone | Verify |
|-----------|--------|
| M1 realistic DB | A fresh environment initializes the Olist schema + seed reproducibly; expected tables, constraints, data present |
| M2 MCP analytics | DB is explorable through MCP without direct Postgres access; read-only boundary holds |
| M3 metadata | A natural-language question retrieves relevant metadata (tables/columns/relationships) rather than the full schema |
| M4 agent | Representative task completes with expected state transitions and an answer grounded in query results |
| M5 LangSmith | An individual run is inspectable end-to-end (metadata, LLM calls, SQL, MCP calls, retries, answer) |
| M6 evaluation | Agent performance measurable against the benchmark, not only manual judgment |

## Reporting

Distinguish `Verified` from `Not verified` explicitly. Never claim a behavior was verified when it was only inferred; if verification can't be run, state why and the remaining risk.

## Layout

- `SKILL.md` — the verification skill.
- `references/PROJECT.md` — verification commands and known integration boundaries (this file).