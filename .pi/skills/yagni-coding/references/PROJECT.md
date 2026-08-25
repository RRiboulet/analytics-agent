# YAGNI Project Integration: factory-mcp-demo

Read this before implementing. It turns the four general principles into concrete rules for this codebase, so a change that merely *works* is still held to the surgical/YAGNI bar.

The project code lives in `mcp-server-demo-main/` (repo docs `AGENTS.md`/`PLAN.md` sit at the repo root). Run `uv`/pytest/ruff from that directory.

## Safety invariants — never bypass

- All user/agent SQL must pass `app/sql_safety.py:validate_and_bound_query()` (single read-only statement, auto `LIMIT`). Do not add a raw SQL path for agent-controlled input.
- The app connects with the read-only role (`factory_readonly`), not admin creds.
- The Postgres pool is owned by the ASGI app lifespan in `create_asgi_app()`. FastMCP's own `lifespan` re-enters per request under `stateless_http=True`. Do not open/close the pool in the wrong layer.

## Response contract — keep uniform

Every tool returns `structuredContent` shaped `{valid: bool, message: str, entries: list}` and runs through `copy_structured_content_to_content()`. If you add or touch a tool, preserve this shape unless the user explicitly asks to change it.

## What counts as "scope" here

- **Don't** add config flags, abstractions, or per-tool options "for future use".
- **Don't** extend the schema, seed data, or README walkthrough to support a hypothetical feature.
- **Don't** add telemetry, auth, or write paths — the README declares these out of scope for V1.
- If the change would break the README's numbered walkthrough (tool names, seed counts, example SQL), call that out explicitly rather than silently shipping a doc contradiction.

## Conventions to match

- One file per tool in `app/tools/`, registered in `app/tools/registry.py`.
- Format with `ruff format` / `ruff check`, line length 100.
- Tests: pytest with asyncio auto mode; no live DB needed for unit tests.

## Verify commands for this repo

```bash
uv run pytest          # tests must pass before and after a change
uv run ruff check .    # lint must stay clean
```

## Merge points

This skill pairs with the sibling [code-review](../code-review/SKILL.md). When you finish a change, run that skill over your own diff — it enforces the skeptical-distance pass. "Done" is: plan stated, goal verified by a test or check, diff traceable to the request, safety invariants intact.