# Project Instructions

## Development Environment

This project is developed inside a **VSCode DevContainer** purpose-built for the **pi coding agent**.

Do **not** perform development work on the local host. All development commands, tests, dependency management, database operations, and application execution must happen inside the DevContainer.

To open the environment:

1. Open the project folder in VSCode.
2. Select **Dev Containers: Reopen in Container...**.
3. The container image is defined in `.devcontainer/Dockerfile`.
4. Container orchestration is defined by `.devcontainer/devcontainer.json` and the project's Docker Compose configuration.

The container provides:

* Python 3.13
* `uv` for Python dependency management
* Node.js LTS
* the `pi` coding agent
* Docker-in-Docker
* Docker Compose
* git
* ruff
* pytest

On container startup, the project environment is prepared and the PostgreSQL service is started.

## Working Directory

Coordination and planning docs (`AGENTS.md`, `PLAN.md`) live at the repo root `/workspace`.

The project code lives in `analytics-agent/` — the **permanent project home**.

Run `uv`, `uvx`, pytest, ruff, and Docker Compose commands from `analytics-agent/`. Relative paths such as `app/`, `db/`, and `tests/` in this file and in `PLAN.md` are relative to that directory.

## Project Context

Before making architectural or substantial implementation changes:

1. Read `PLAN.md`.
2. Inspect the existing repository and relevant implementation.
3. Understand what already works.
4. Prefer extending existing functionality over rewriting it.
5. Identify the smallest coherent change that satisfies the current milestone.

`PLAN.md` is the source of truth for the current project direction, milestones, architectural decisions, and project status.

If implementation reveals that a decision in `PLAN.md` is incorrect, unsafe, unnecessarily complex, or incompatible with the existing system, do not silently work around it. Explain the issue and propose an update before making a consequential architectural change.

Do not implement future milestones unless explicitly requested.

## Development Workflow

The project uses the bundled agent-skill suite in `.pi/skills/`. Use skills according to the phase of the task:

* **`understand-and-plan`** — start of any substantial change: investigate, assess repository impact and risk, establish a baseline, and produce a safe plan.
* **`clean-implementation`** — implement the smallest complete change that fits the project's architecture and conventions.
* **`comprehensive-testing`** — design meaningful risk-based tests with coverage evidence for new/changed code.
* **`automated-quality-checks`** — run the repository's configured checks declared in `.pi/config/quality.yaml`.
* **`final-review-and-validation`** — compare the final diff with requirements, run the final gate, and report evidence and residual risks.

For a substantial change, the preferred workflow is:

```text
understand-and-plan
        ↓
clean-implementation
        ↓
comprehensive-testing
        ↓
automated-quality-checks
        ↓
final-review-and-validation
```

The project quality configuration lives at `.pi/config/quality.yaml`; the skills read their commands from that file (working directory is `analytics-agent/`). Coverage is measured for new/changed executable code via pytest-cov, targeting the configured line/branch thresholds.

The sequence is not strictly linear — later phases may return to planning, implementation, or testing when evidence reveals a gap. Do not invoke every skill mechanically for trivial changes. Use the skills when their scope applies.

## Development Philosophy

The project is being developed incrementally.

Do not attempt to implement the entire V2 architecture in one step.

For substantial tasks:

1. Inspect.
2. Explain your understanding.
3. Propose an implementation approach.
4. Implement the defined scope.
5. Run relevant tests.
6. Run formatting and linting.
7. Report what changed and any remaining issues.

When requirements are ambiguous, identify the ambiguity and propose options rather than making a large speculative change.

Ask the user before proceeding when ambiguity affects:

* architecture
* security
* data integrity
* public interfaces
* significant scope
* irreversible decisions

For ordinary implementation details with a reasonable conventional solution, make the simplest reasonable choice, state the assumption briefly, and proceed.

## Existing Architecture

The current project contains:

* a Python MCP server
* PostgreSQL running through Docker Compose
* MCP tools for database interaction
* a SQL safety layer enforcing read-only access
* tests covering the existing MCP functionality
* database initialization scripts under `db/init/`

The existing functionality is valuable and should be preserved unless there is a clear reason to change it.

## Database Safety

The database is accessed through the MCP server.

The existing SQL safety boundary is a critical security property.

The analytics agent must never gain unrestricted database access.

Only read-only SQL is allowed for the analytics workflow.

Do not weaken, bypass, or remove the existing SQL validation boundary.

Any changes to SQL execution or safety must include appropriate tests.

## MCP

MCP is the capability boundary between the agent and PostgreSQL.

Existing MCP capabilities include:

* `list_tables`
* `describe_table`
* `query`

Future MCP capabilities should be added only when they provide a clear benefit to the analytics workflow.

MCP tools should remain focused on database capabilities rather than embedding agent-specific business logic.

## Python Environment

Use `uv` for dependency management:

```bash
uv add <package>
uv sync
uv run <command>
```

Python version: 3.13.

The virtual environment is located at `.venv/`.

## Code Quality

Format:

```bash
ruff format .
```

Lint:

```bash
ruff check .
```

Run tests with:

```bash
uv run pytest
```

Run relevant tests during development and the full test suite before considering a milestone complete.

## Git

Use git for version control.

Prefer small, focused commits when commits are requested.

Do not modify or remove unrelated code merely to satisfy formatting or architectural preferences.

## Documentation

Keep `PLAN.md` up to date when a milestone, architectural decision, or significant implementation detail changes.

Do not turn `PLAN.md` into a detailed implementation log.

Keep architectural decisions concise and explain the reason behind important decisions.

## Communication

Keep responses concise and focused.

For implementation tasks, report:

* what was changed
* why it was changed
* tests/checks performed
* any remaining concerns

When beginning a new milestone, first verify the current repository state against `PLAN.md`.
