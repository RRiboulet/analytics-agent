FROM mcr.microsoft.com/devcontainers/python:1-3.13-bookworm

RUN rm -f /etc/apt/sources.list.d/yarn.list

RUN apt-get update && export DEBIAN_FRONTEND=noninteractive \
    && curl -fsSL https://deb.nodesource.com/setup_lts.x | bash - \
    && apt-get install -y nodejs

RUN curl -LsSf https://astral.sh/uv/install.sh | sh 2>&1

RUN npm install -g @earendil-works/pi-coding-agent

RUN mkdir -p /home/vscode/.pi/agent

WORKDIR /workspace
RUN uv init --no-workspace 2>/dev/null || true && uv venv 2>/dev/null || true