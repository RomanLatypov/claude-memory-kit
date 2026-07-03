#!/bin/bash
# Daily pg_dump of memory-vault -> a backup directory (compressed, rotated),
# then refresh the offline wake-up snapshot.
# Dumps only — the live datadir stays in the local docker volume.
#
# Config (env or defaults):
#   MK_DIR          kit directory (default: this script's parent dir)
#   MV_BACKUP_DIR   where dumps go (default: ~/memory-vault-backups; point it
#                   at a cloud-synced folder like Dropbox for offsite copies)
#   MV_BACKUP_KEEP  how many dumps to keep (default: 14)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MK_DIR="${MK_DIR:-$(dirname "$SCRIPT_DIR")}"
BACKUP_DIR="${MV_BACKUP_DIR:-$HOME/memory-vault-backups}"
KEEP="${MV_BACKUP_KEEP:-14}"
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="$BACKUP_DIR/memory_vault_${STAMP}.sql.gz"

export PATH="/Applications/Docker.app/Contents/Resources/bin:$PATH"
COMPOSE=(docker compose -f "$MK_DIR/docker-compose.yml")

mkdir -p "$BACKUP_DIR"

# pg_dump from inside the db container, gzip on the host
"${COMPOSE[@]}" exec -T db pg_dump -U memory_vault -d memory_vault \
  | gzip > "$OUT"

# integrity: non-empty + gzip valid
if ! gzip -t "$OUT" 2>/dev/null || [ ! -s "$OUT" ]; then
  echo "$(date) BACKUP FAILED: $OUT bad/empty" >&2
  rm -f "$OUT"
  exit 1
fi

# rotate: keep newest $KEEP
ls -1t "$BACKUP_DIR"/memory_vault_*.sql.gz 2>/dev/null | tail -n +$((KEEP+1)) | xargs -I{} rm -f {}

echo "$(date) backup OK -> $OUT ($(du -h "$OUT" | cut -f1))"

# refresh the offline wakeup snapshot (facts section only, no identity —
# the fallback path in vault_wakeup.sh prints identity.txt itself).
# Guarded: never clobber a good snapshot with empty output.
WAKEUP_DIR="$MK_DIR/wakeup"
SNAP="$WAKEUP_DIR/wakeup_snapshot.txt"
if bash "$WAKEUP_DIR/vault_wakeup.sh" 2>/dev/null \
     | sed -n '/^## Facts/,$p' \
     | sed "1s/.*/## Facts from memory-vault (frozen snapshot $(date +%Y-%m-%d) — vault offline)/" \
     > "$SNAP.new" && [ -s "$SNAP.new" ] && grep -q '^  - ' "$SNAP.new"; then
  mv "$SNAP.new" "$SNAP"
  echo "$(date) snapshot refreshed"
else
  rm -f "$SNAP.new"
  echo "$(date) snapshot refresh skipped (vault offline?)" >&2
fi
