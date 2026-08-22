FROM mcr.microsoft.com/devcontainers/python:1-3.13-bookworm

RUN rm -f /etc/apt/sources.list.d/yarn.list

RUN apt-get update && export DEBIAN_FRONTEND=noninteractive \
    && curl -fsSL https://deb.nodesource.com/setup_lts.x | bash - \
    && apt-get install -y nodejs

# Install the Python package manager uv to a system path so it is on PATH.
RUN curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh

RUN npm install -g @earendil-works/pi-coding-agent

RUN mkdir -p /home/vscode/.pi/agent

WORKDIR /workspace

# Create a virtualenv for the project. Dependencies (if any) are installed
# later via `uv sync` inside the container (see devcontainer.json).
RUN uv venv