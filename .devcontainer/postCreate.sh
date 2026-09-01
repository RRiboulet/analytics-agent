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

# Copy the custom theme for pi
mkdir -p /home/vscode/.pi/agent/themes
cp /workspace/.devcontainer/pi/themes/kokomi-theme.json /home/vscode/.pi/agent/themes/kokomi-theme.json
echo '{"theme": "kokomi-theme"}' > /home/vscode/.pi/agent/settings.json

# --- permissions ---
sudo chown -R vscode:vscode /home/vscode/.pi
sudo chown -R vscode:vscode /workspace

# --- project setup ---
cp analytics-agent/.env.example analytics-agent/.env
cd analytics-agent
uv sync 2>&1 | tail -5
docker compose -f docker-compose.yml up -d postgres
sleep 8
curl -s http://localhost:5432/ || true
echo
echo 'Factory Data MCP demo ready: MCP on http://localhost:8000/mcp, Postgres on http://localhost:5432/'