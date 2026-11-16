FROM mcr.microsoft.com/devcontainers/python:1-3.13-bookworm

RUN rm -f /etc/apt/sources.list.d/yarn.list

RUN apt-get update && export DEBIAN_FRONTEND=noninteractive \
    && curl -fsSL https://deb.nodesource.com/setup_lts.x | bash - \
    && apt-get install -y nodejs

RUN curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh

# Pin the coding agent. The baked my-agent-setup package (below) is built and
# tested against the 1.5.x line; a floating install could break its extensions
# on a rebuild. Bump this and .devcontainer/pi-agent-setup.version together.
ARG PI_AGENT_VERSION=1.5.2
RUN npm cache clean --force \
    && npm install -g --no-audit --no-fund "@earendil-works/pi-coding-agent@${PI_AGENT_VERSION}" \
    && pi --version

# The shared pi package (extensions + skills), baked at a pinned tag. The tag is
# recorded once in .devcontainer/pi-agent-setup.version; the build fails if this
# ARG and that file disagree, so a half-done bump cannot produce an image. The
# clone lives outside $AGENT_DIR on purpose: a named volume mounted at ~/.pi
# copies the image's content only when the volume is empty, so a package baked
# there would freeze at the first container's copy.
COPY .devcontainer/pi-agent-setup.version /tmp/pi-agent-setup.version
ARG PI_AGENT_SETUP_TAG=v1.14.0
RUN test "${PI_AGENT_SETUP_TAG}" = "$(tr -d '\r\n' < /tmp/pi-agent-setup.version)" \
    && mkdir -p /opt/pi-packages \
    && git clone --depth 1 --branch "${PI_AGENT_SETUP_TAG}" \
         https://github.com/RRiboulet/my-agent-setup.git \
         "/opt/pi-packages/my-agent-setup-${PI_AGENT_SETUP_TAG}" \
    && ln -sfn "my-agent-setup-${PI_AGENT_SETUP_TAG}" /opt/pi-packages/my-agent-setup \
    && test -f /opt/pi-packages/my-agent-setup/package.json \
    && printf '%s\n' "${PI_AGENT_SETUP_TAG}" > "/opt/pi-packages/my-agent-setup-${PI_AGENT_SETUP_TAG}/.pi-agent-setup-tag" \
    && rm -f /tmp/pi-agent-setup.version
ENV PI_AGENT_SETUP_TAG=${PI_AGENT_SETUP_TAG}
LABEL org.rriboulet.pi-package="${PI_AGENT_SETUP_TAG}"

# Tooling for the project: the MCP server on :8000 runs here, and PostgreSQL is a
# service of .devcontainer/docker-compose.yml, so nothing in this image talks to
# a Docker daemon (the docker-in-docker feature is gone along with the dbus,
# init and slirp4netns packages it needed).
# API testing: `http :8000/health` or `http POST :8000/api/foo name=test`
RUN apt-get update && export DEBIAN_FRONTEND=noninteractive \
    && apt-get install -y \
        procps \
        curl \
        httpie \
        jq \
        tmux \
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

# NOTE: do not pre-build the project venv here. Compose bind-mounts the repo
# over /workspace (`..:/workspace` in docker-compose.yml), which shadows
# anything an image layer writes under /workspace. A build-time `uv venv &&
# uv sync` would therefore be discarded and silently repeated at runtime by
# postCreate. The venv is created by postCreate.sh, in the mounted workspace.
