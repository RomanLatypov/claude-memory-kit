#!/bin/bash
# claude-memory-kit installer.
# Brings up the memory-vault stack, creates an API token, builds the MCP shim
# venv, and wires everything into Claude Code (MCP server + wake-up hook).
#
# Safe to re-run: every step is idempotent.
set -euo pipefail

KIT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$KIT_DIR"

# macOS Docker Desktop sometimes ships without a working /usr/local symlink
export PATH="/Applications/Docker.app/Contents/Resources/bin:$PATH"

say()  { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------- 0. checks
command -v docker >/dev/null 2>&1 || fail "docker not found. Install Docker Desktop (macOS) or docker-ce (Linux) first."
docker info >/dev/null 2>&1      || fail "docker daemon not running. Start Docker and re-run."
command -v python3 >/dev/null 2>&1 || fail "python3 not found."

# ---------------------------------------------------------------- 1. .env
if [ ! -f .env ]; then
  say "creating .env with a random DB password"
  DB_PW="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
  sed -e "s|^MV_DB_PASSWORD=.*|MV_DB_PASSWORD=$DB_PW|" .env.example > .env
  chmod 600 .env
else
  say ".env exists — keeping it"
fi
# shellcheck disable=SC1091
set -a; source .env; set +a

# ---------------------------------------------------------------- 2. stack
say "starting the stack (db + app)"
docker compose up -d

say "waiting for the app to become healthy"
for i in $(seq 1 60); do
  if curl -fsS "${MV_URL:-http://localhost:${MV_PORT:-8000}}/api/health" >/dev/null 2>&1; then
    break
  fi
  [ "$i" = 60 ] && fail "app did not come up in 60s — check: docker compose logs app"
  sleep 1
done
say "app is up"

# ---------------------------------------------------------------- 3. token
if [ -z "${MV_TOKEN:-}" ]; then
  say "creating API token 'claude-shim' inside the app container"
  TOKEN_OUT="$(docker compose exec -T app python -m src.cli token create claude-shim 2>&1 || true)"
  MV_TOKEN="$(printf '%s' "$TOKEN_OUT" | grep -oE 'mv_[A-Za-z0-9_-]+' | head -1 || true)"
  if [ -z "$MV_TOKEN" ]; then
    echo "$TOKEN_OUT"
    fail "could not parse the token from the CLI output above. Create one manually and put it in .env as MV_TOKEN=..."
  fi
  # persist into .env
  if grep -q '^MV_TOKEN=' .env; then
    sed -i.bak "s|^MV_TOKEN=.*|MV_TOKEN=$MV_TOKEN|" .env && rm -f .env.bak
  else
    echo "MV_TOKEN=$MV_TOKEN" >> .env
  fi
  say "token stored in .env"
else
  say "MV_TOKEN already set — keeping it"
fi

# ---------------------------------------------------------------- 4. shim venv
if [ ! -x shim/.venv/bin/python ]; then
  say "building the shim venv"
  python3 -m venv shim/.venv
  shim/.venv/bin/pip install --quiet --upgrade pip
  shim/.venv/bin/pip install --quiet -r shim/requirements.txt
else
  say "shim venv exists — keeping it"
fi

# ---------------------------------------------------------------- 5. identity
if [ ! -f wakeup/identity.txt ]; then
  cp wakeup/identity.example.txt wakeup/identity.txt
  say "wakeup/identity.txt created from the example — EDIT IT: it is printed to Claude at every session start"
fi
chmod +x wakeup/vault_wakeup.sh backup/backup.sh

# ---------------------------------------------------------------- 6. MCP registration
MCP_JSON=$(cat <<EOF
{"type":"stdio","command":"$KIT_DIR/shim/.venv/bin/python","args":["$KIT_DIR/shim/shim.py"],"env":{"MV_URL":"${MV_URL:-http://localhost:${MV_PORT:-8000}}","MV_TOKEN":"$MV_TOKEN"}}
EOF
)
if command -v claude >/dev/null 2>&1; then
  say "registering the MCP server with Claude Code (user scope)"
  claude mcp remove memory-vault -s user >/dev/null 2>&1 || true
  claude mcp add-json memory-vault "$MCP_JSON" -s user \
    && say "MCP server 'memory-vault' registered" \
    || { echo "claude mcp add-json failed — add this to ~/.claude.json under mcpServers manually:"; echo "\"memory-vault\": $MCP_JSON"; }
else
  say "claude CLI not found — add this to ~/.claude.json under mcpServers manually:"
  echo "\"memory-vault\": $MCP_JSON"
fi

# ---------------------------------------------------------------- 7. daily backup (macOS launchd)
if [ "$(uname)" = "Darwin" ]; then
  PLIST=~/Library/LaunchAgents/com.memorykit.backup.plist
  sed "s|__KIT_DIR__|$KIT_DIR|g" backup/com.memorykit.backup.plist.template > "$PLIST"
  launchctl unload "$PLIST" 2>/dev/null || true
  launchctl load "$PLIST"
  say "daily backup scheduled at 03:30 (launchd com.memorykit.backup)"
else
  say "non-macOS: schedule backups yourself, e.g. crontab: 30 3 * * * bash $KIT_DIR/backup/backup.sh"
fi

# ---------------------------------------------------------------- 8. wake-up hook
cat <<EOF

DONE. Two manual steps remain:

1. EDIT wakeup/identity.txt — one short paragraph about you, printed to Claude
   at every session start.

2. Add the wake-up hook to ~/.claude/settings.json so Claude starts each
   session already knowing your recent memory:

   "hooks": {
     "SessionStart": [
       { "hooks": [ { "type": "command", "command": "bash $KIT_DIR/wakeup/vault_wakeup.sh" } ] }
     ]
   }

Then restart Claude Code and try:  "what do you remember about me?"
EOF
