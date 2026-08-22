FROM mcr.microsoft.com/devcontainers/python:1-3.13-bookworm

RUN rm -f /etc/apt/sources.list.d/yarn.list

RUN apt-get update && export DEBIAN_FRONTEND=noninteractive \
    && curl -fsSL https://deb.nodesource.com/setup_lts.x | bash - \
    && apt-get install -y nodejs

RUN curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh

RUN npm install -g @earendil-works/pi-coding-agent

# Docker-in-Docker + tooling needed by the Factory Data MCP demo
# (MCP server on :8000, PostgreSQL on :5432 via the project's docker-compose).
RUN apt-get update && export DEBIAN_FRONTEND=noninteractive \
    && apt-get install -y \
        docker.io \
        dbus \
        init \
        procps \
        slirp4netns \
        git \
        curl \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /home/vscode/.pi/agent

WORKDIR /workspace

# Pre-create the project venv and install dependencies for the MCP demo
# so the environment is ready without a manual `uv sync`.
COPY mcp-server-demo-main/pyproject.toml \
    mcp-server-demo-main/uv.lock \
    mcp-server-demo-main/README.md \
    /workspace/mcp-server-demo-main/

RUN cd /workspace/mcp-server-demo-main && uv venv && uv sync --all-groups