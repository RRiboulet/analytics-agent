# Project Instructions

## Development Environment (DevContainer)

This project is developed inside a **VSCode DevContainer** that is purpose-built for the **pi coding agent**. Do **not** work in the local host editor for development.

To open the environment:

1. Open the project folder in VSCode.
2. Select the **Dev Containers: Reopen in Container...** command.
3. The container image is defined in `.devcontainer/Dockerfile` and orchestrated by `.devcontainer/devcontainer.json` (+ `docker-compose.yml`).

The container provides, out of the box:

- **Python 3.13** + [`uv`](https://docs.astral.sh/uv/) for dependency management.
- **Node.js (LTS)** via NodeSource.
- The **pi coding agent** (`@earendil-works/pi-coding-agent`) installed globally inside the container.
- **Docker-in-Docker** (`docker.io`) plus `docker-compose` for the MCP demo services.
- `git`, `curl`, and the ruff/pytest tooling already wired up.

On container startup (`postCreateCommand`) the project venv is synced, `.env` is created from `.env.example`, and the Postgres service is started via `docker compose -f docker-compose.yml up -d postgres`.

## Python Environment

- Use `uv` for dependency management: `uv add <package>`, `uv sync`, `uv run <script>`.
- Virtual environment is at `.venv/`.
- Python version: 3.13.
- The venv is pre-created and populated during container creation, so a manual `uv sync` is normally not required.

## Code Style

- Format with `ruff`: `ruff format .`
- Lint with `ruff`: `ruff check .`
- Auto-format on save is enabled in the container (default formatter is `charliermarsh.ruff`, and Python files also trigger `source.organizeImports`).

## General

- Keep responses concise and focused.
- Use git for version control.
- Run tests after making changes.
- All of the above commands run **inside the DevContainer** (i.e. via the pi agent's terminal), not on the host.
