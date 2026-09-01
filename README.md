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
| `.pi/` | Bundled agent skills and the quality-check configuration |
| `.devcontainer/` | VSCode Dev Container definition |

## Documentation

- Project setup, running the server, and connecting a client: [`analytics-agent/README.md`](analytics-agent/README.md)
- Development workflow, conventions, and safety rules: [`AGENTS.md`](AGENTS.md)
- Roadmap and milestones: [`PLAN.md`](PLAN.md)

## Development environment

The project is developed inside a VSCode Dev Container providing Python 3.13, `uv`, Node.js, the `pi` coding agent, Docker-in-Docker, and PostgreSQL via Docker Compose. Open the repo in VSCode and select **Dev Containers: Reopen in Container...**.
