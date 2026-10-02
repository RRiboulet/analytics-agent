# Analytics Agent

An autonomous analytics system over a realistic relational dataset (Olist Brazilian e-commerce), built as a local demonstration:

- a **read-only MCP server** exposing database capabilities (table discovery, schema inspection, bounded `SELECT` queries, semantic metadata search) over PostgreSQL 16
- a **SQL safety layer** enforcing strictly read-only access — the agent can never gain unrestricted database access
- an **analytics agent** that answers natural-language analytical questions end-to-end: retrieve metadata → generate SQL → validate → execute through MCP → analyze → answer, with bounded retry recovery and optional Langfuse tracing
- **evaluation tooling** (deterministic reference-SQL benchmark) and observability for the full workflow

The long-term goal is an **Autonomous Analytics Manager**; see [`PLAN.md`](PLAN.md) for the vision, milestones, and current status. `PLAN.md` is the source of truth for project direction.

## Repository layout

| Path | Purpose |
|---|---|
| `analytics-agent/` | The project home: MCP server, analytics agent, DB init scripts, tests |
| `PLAN.md` | Project plan, milestones, architectural decisions |
| `AGENTS.md` | Instructions for coding agents working in this repo |
| `.pi/` | Pi extensions and the quality-check configuration |
| `.devcontainer/` | VSCode Dev Container definition |

## Documentation

- Project setup, running the server, and connecting a client: [`analytics-agent/README.md`](analytics-agent/README.md)
- Development workflow, conventions, and safety rules: [`AGENTS.md`](AGENTS.md)
- Roadmap and milestones: [`PLAN.md`](PLAN.md)

## Development environment

The project is developed inside a VSCode Dev Container providing Python 3.13, `uv`, Node.js, the `pi` coding agent, tmux, Docker-in-Docker, and PostgreSQL via Docker Compose. Open the repo in VSCode and select **Dev Containers: Reopen in Container...**.

## Pi agent extensions

The repo vendors project-local pi extensions in `.pi/extensions/`. The `subagent` extension lets the main agent delegate isolated tasks to child pi processes that run in detached `tmux` sessions:

- `subagent` starts a child and returns immediately, so the main agent is **not blocked** and can launch more subagents or keep working. Runs execute concurrently (bounded by `PI_SUBAGENT_MAX_CONCURRENT`, default `4`).
- `subagent_status` inspects runs and their output; `subagent_wait` blocks for selected runs; `subagent_cancel` stops one; `subagent_clean` reaps finished tmux sessions/run dirs.
- Each run reports tokens/turns/cost parsed from the child session. `/subagents` opens a live dashboard with pane/output preview, cancel, and attach commands.
- Children default to the **OpenRouter** provider (inheriting the parent model), trust the project only when the parent does, and persist state under `~/.pi/agent/tmux-subagents/<session-id>/`. Finished tmux sessions auto-reap (`PI_SUBAGENT_AUTO_REAP`, default `true`); old run dirs are GC'd after `PI_SUBAGENT_GC_DAYS` (default `7`).
- Attach to a running child interactively with the printed `pi --attach-subagent <id>` command.

Relevant environment variables: `PI_SUBAGENT_MAX_CONCURRENT`, `PI_SUBAGENT_NOTIFY` (default `true`), `PI_SUBAGENT_KILL_ON_SHUTDOWN` (default `false`), `PI_SUBAGENT_AUTO_REAP` (default `true`), `PI_SUBAGENT_REAP_DELAY_MS`, `PI_SUBAGENT_GC_DAYS` (default `7`), `PI_SUBAGENT_PROVIDER`, `PI_SUBAGENT_MODEL`.

The extension has a Node test suite in `.pi/extensions/subagent/test/` (no build step):

```bash
.pi/extensions/subagent/test/setup-deps.sh   # link the global pi packages into node_modules
node --test --test-force-exit .pi/extensions/subagent/test/*.test.ts
```

It is registered in `.pi/config/quality.yaml` as `extension_test` / `extension-coverage` (both optional); the Python gate remains the required one. See `.pi/extensions/subagent/test/README.md`.
