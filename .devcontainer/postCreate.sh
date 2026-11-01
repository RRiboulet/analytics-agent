#!/usr/bin/env bash
set -euo pipefail

# --- zsh + Powerlevel10k ---
# The common-utils feature creates ~/.zshrc before postCreate runs.
touch ~/.zshrc
grep -qxF 'source /home/vscode/.aliases.zsh' ~/.zshrc || echo 'source /home/vscode/.aliases.zsh' >> ~/.zshrc
grep -qxF 'source /home/vscode/powerlevel10k/powerlevel10k.zsh-theme' ~/.zshrc || echo 'source /home/vscode/powerlevel10k/powerlevel10k.zsh-theme' >> ~/.zshrc
grep -qxF '[[ -f ~/.p10k.zsh ]] && source ~/.p10k.zsh' ~/.zshrc || echo '[[ -f ~/.p10k.zsh ]] && source ~/.p10k.zsh' >> ~/.zshrc

# Install the repo's checked-in p10k config.
cp /workspace/.devcontainer/shell/p10k.zsh /home/vscode/.p10k.zsh

# Install the repo's tmux config (used by pi's tmux-backed subagents).
cp /workspace/.devcontainer/shell/tmux.conf /home/vscode/.tmux.conf

# Copy the custom theme for pi and select it WITHOUT clobbering the rest of the
# user's settings: /home/vscode/.pi is a persistent volume and postCreate runs
# on every recreate, so truncating settings.json would discard personal
# packages, model prefs and tool choices each time.
mkdir -p /home/vscode/.pi/agent/themes
cp /workspace/.devcontainer/pi/themes/kokomi-theme.json /home/vscode/.pi/agent/themes/kokomi-theme.json

SETTINGS=/home/vscode/.pi/agent/settings.json
if [ -f "$SETTINGS" ] && command -v jq >/dev/null 2>&1; then
  jq --arg t kokomi-theme '.theme = $t' "$SETTINGS" > "$SETTINGS.tmp" \
    && mv "$SETTINGS.tmp" "$SETTINGS" \
    || echo '{"theme": "kokomi-theme"}' > "$SETTINGS"
else
  echo '{"theme": "kokomi-theme"}' > "$SETTINGS"
fi

# Copy the local model config for pi, merging providers so a personal provider
# already in the volume is kept rather than replaced. If the existing file is
# not mergeable (invalid JSON), leave it alone and say so rather than silently
# replacing it with the repo's.
MODELS=/home/vscode/.pi/agent/models.json
if [ -f "$MODELS" ] && command -v jq >/dev/null 2>&1; then
  if jq -s '.[0] as $cur | .[1] as $repo | $cur * $repo | .providers = (($cur.providers // {}) * ($repo.providers // {}))' \
    "$MODELS" /workspace/.devcontainer/pi/models.json > "$MODELS.tmp"; then
    mv "$MODELS.tmp" "$MODELS"
  else
    rm -f "$MODELS.tmp"
    echo "postCreate: $MODELS is not mergeable; leaving it unchanged" >&2
  fi
else
  cp /workspace/.devcontainer/pi/models.json "$MODELS"
fi

# --- permissions ---
sudo chown -R vscode:vscode /home/vscode/.pi
sudo chown -R vscode:vscode /workspace

# --- clipboard ---
# The host injects WAYLAND_DISPLAY and DISPLAY into the container, but neither
# socket exists here. pi's clipboard helper then finds no wl-copy/xclip and
# reports the clipboard as unavailable. Install the OSC 52 shim under all three
# names so whichever it tries works, and the outer terminal owns the clipboard.
sudo install -m 0755 /workspace/.devcontainer/shell/osc52-clipboard /usr/local/bin/osc52-clipboard
for helper in wl-copy xclip xsel; do
  sudo ln -sf /usr/local/bin/osc52-clipboard "/usr/local/bin/$helper"
done

# --- project setup ---
# `.env` holds local secrets (an OpenRouter key, Langfuse keys) and postCreate
# runs on every recreate, so only seed it on first run — never clobber local edits.
if [ ! -f analytics-agent/.env ]; then
  cp analytics-agent/.env.example analytics-agent/.env
fi

cd analytics-agent
uv sync 2>&1 | tail -5

# PostgreSQL is a service of the devcontainer's own compose stack (see
# .devcontainer/docker-compose.yml) and is started by `devcontainer up`; there is
# no Docker daemon in this container to start it with.
#
# Wait for its TCP listener. The entrypoint's temporary init server during the
# Olist load answers on the Unix socket only (`listen_addresses` is empty), so a
# successful TCP connect is the first moment the real server is up — the same
# reasoning the previous `pg_isready -h localhost` probe documented.
PG_READY=
for _ in $(seq 1 60); do
  if bash -c 'exec 3<>/dev/tcp/postgres/5432' 2>/dev/null; then
    PG_READY=1
    break
  fi
  sleep 2
done
if [ -z "$PG_READY" ]; then
  echo 'postCreate: postgres not reachable at postgres:5432 after 120s; on the host run `docker compose -f .devcontainer/docker-compose.yml logs postgres`' >&2
fi
echo
echo 'Analytics Agent ready: MCP on http://localhost:8000/mcp, Postgres on the compose network as postgres:5432'
