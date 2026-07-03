#!/bin/bash
# Session-start wake-up from memory-vault.
# Prints: static identity (L0) + freshest high-signal facts from the vault.
# Falls back to a frozen snapshot if Docker/the stack is down.
#
# Wire it as a Claude Code SessionStart hook (see README).
#
# Config (env or defaults):
#   MK_DIR         kit directory (default: this script's parent dir)
#   MV_WAKE_SPACES SQL list of spaces to pull facts from (default: 'default')
#   MV_WAKE_LIMIT  how many recent facts to print (default: 18)
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MK_DIR="${MK_DIR:-$(dirname "$SCRIPT_DIR")}"
WAKE_SPACES="${MV_WAKE_SPACES:-'default'}"
WAKE_LIMIT="${MV_WAKE_LIMIT:-18}"

# Docker Desktop on macOS often lacks a working /usr/local symlink
export PATH="/Applications/Docker.app/Contents/Resources/bin:$PATH"
COMPOSE=(docker compose -f "$MK_DIR/docker-compose.yml")

# 1) static identity (L0) — always available, survives everything
[ -f "$SCRIPT_DIR/identity.txt" ] && cat "$SCRIPT_DIR/identity.txt"

# 2) live facts from the vault, most recent first.
# NB: coalesce — rows written via the MCP shim have no 'room' metadata;
# a bare `<> 'diary'` comparison silently drops NULL rows.
FACTS=$("${COMPOSE[@]}" exec -T db psql -U memory_vault -d memory_vault -tA -F': ' -c "
  SELECT left(regexp_replace(content, E'[\\n\\r]+', ' ', 'g'), 260)
  FROM chunks c JOIN memory_spaces s ON s.id = c.space_id
  WHERE s.name IN ($WAKE_SPACES)
    AND coalesce(c.metadata->>'room', '') <> 'diary'
  ORDER BY c.created_at DESC NULLS LAST
  LIMIT $WAKE_LIMIT;" 2>/dev/null)

if [ -n "$FACTS" ]; then
  echo ""
  echo "## Facts from memory-vault (most recent)"
  echo "$FACTS" | sed 's/^/  - /'
  echo ""
  echo "(Full memory via MCP tools: recall / remember. Vault = source of truth.)"
else
  # Fallback: stack down -> frozen snapshot (refreshed by backup.sh)
  echo ""
  echo "## (vault offline — frozen snapshot)"
  [ -f "$SCRIPT_DIR/wakeup_snapshot.txt" ] && cat "$SCRIPT_DIR/wakeup_snapshot.txt"
fi
