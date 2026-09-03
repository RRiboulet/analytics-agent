FROM mcr.microsoft.com/devcontainers/python:1-3.13-bookworm

RUN rm -f /etc/apt/sources.list.d/yarn.list

RUN apt-get update && export DEBIAN_FRONTEND=noninteractive \
    && curl -fsSL https://deb.nodesource.com/setup_lts.x | bash - \
    && apt-get install -y nodejs

RUN curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh

RUN npm install -g @earendil-works/pi-coding-agent

# Docker-in-Docker + tooling needed by the Factory Data MCP demo
# (MCP server on :8000, PostgreSQL on :5432 via the project's docker-compose).
# API testing: `http :8000/health` or `http POST :8000/api/foo name=test`
RUN apt-get update && export DEBIAN_FRONTEND=noninteractive \
    && apt-get install -y \
        docker.io \
        dbus \
        init \
        procps \
        slirp4netns \
        curl \
        httpie \ 
    && rm -rf /var/lib/apt/lists/*

# Powerlevel10k (standalone; no Oh My Zsh required)
RUN git clone --depth=1 https://github.com/romkatv/powerlevel10k.git /home/vscode/powerlevel10k \
    && chown -R vscode:vscode /home/vscode/powerlevel10k

RUN mkdir -p /home/vscode/.pi/agent

WORKDIR /workspace

# Shell aliases
COPY .devcontainer/shell/aliases.zsh /home/vscode/.aliases.zsh

RUN cat <<'EOF' >> /home/vscode/.zshrc
source /home/vscode/.aliases.zsh
EOF

RUN chown vscode:vscode \
    /home/vscode/.aliases.zsh \
    /home/vscode/.zshrc

# Git aliases
RUN git config --system alias.st status \
    && git config --system alias.co checkout \
    && git config --system alias.br branch \
    && git config --system alias.last 'log -1 HEAD --stat' \
    && git config --system alias.lg 'log --oneline --decorate --graph -20'

# Pre-create the project venv and install dependencies for the MCP demo
# so the environment is ready without a manual `uv sync`.
COPY analytics-agent/pyproject.toml \
    analytics-agent/uv.lock \
    analytics-agent/README.md \
    /workspace/analytics-agent/

RUN cd /workspace/analytics-agent && uv venv && uv sync --all-groups
