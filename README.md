# claude-memory-kit

Persistent, self-hosted long-term memory for [Claude Code](https://claude.com/claude-code), built around [vault-engine](https://github.com/RomanLatypov/vault-engine) (Postgres + pgvector).

Claude wakes up every session already knowing who you are and what you were working on. Memories survive restarts, work across all your projects, and never leave your machine.

```
Claude Code ──stdio──> thin MCP shim (~1s start, ~40 MB)
                          │ HTTP + Bearer token
                          ▼
              vault-engine app container (always warm, embeds + hybrid search)
                          │
                          ▼
              Postgres + pgvector (docker volume)
```

## Why this kit and not just the engine?

vault-engine ships a Docker-based MCP server: every Claude window spawns a
container with its own embedding model — ~10s cold start and ~400 MB RAM each,
and orphaned containers pile up. This kit replaces that with a **thin stdio
shim** that proxies to the always-warm app container: **~1s start, ~40 MB per
window**, nothing to orphan.

On top of that it adds the operational pieces the upstream project leaves to you:

| Piece | What it does |
|---|---|
| `shim/shim.py` | 4 MCP tools — `recall`, `remember`, `forget`, `memory_status` — with token-budgeted results so recall never floods the context |
| `wakeup/vault_wakeup.sh` | SessionStart hook: prints your identity + the freshest facts from the vault at every session start; falls back to a frozen snapshot when Docker is down |
| `backup/backup.sh` | daily `pg_dump` → compressed, rotated (default keep 14), integrity-checked; refreshes the offline snapshot; launchd schedule on macOS |
| `tools/` | migrate an existing ChromaDB memory into the vault (idempotent, see [docs/MIGRATION.md](docs/MIGRATION.md)) |
| `install.sh` | one command: stack up → API token → shim venv → MCP registration → backup schedule |

## Quick start

Requirements: Docker (Desktop on macOS), Python 3.11+, Claude Code.

```bash
git clone https://github.com/RomanLatypov/claude-memory-kit.git
cd claude-memory-kit
./install.sh
```

Then:

1. Edit `wakeup/identity.txt` — one short paragraph about you. Claude sees it at every session start.
2. Add the SessionStart hook to `~/.claude/settings.json` (the installer prints the exact snippet).
3. Restart Claude Code and ask: *"what do you remember about me?"*

The installer also registers the shim with the **Claude Desktop app** if it finds
`claude_desktop_config.json` (backup saved next to it; restart Desktop after).
One vault, both clients, same memories.

To store something: just tell Claude *"remember that ..."* — it calls the `remember` tool. Retrieval happens automatically via `recall` whenever the context calls for it.

## What Claude sees at session start

Real output of the wake-up hook (from the installer's e2e test):

```
User: Jane Doe (jane@example.com). Role: founder of ExampleCorp (embedded systems).
Preferred language: English. Style: concise answers, no filler.
Memory: memory-vault (Postgres+pgvector, MCP tools recall/remember/forget/memory_status). Vault = source of truth.

## Facts from memory-vault (most recent)
  - Test fact: the kit installer works end to end.

(Full memory via MCP tools: recall / remember. Vault = source of truth.)
```

The whole pipeline — `install.sh` on a clean copy, token creation, shim MCP
handshake, `remember` → `recall` round-trip, wake-up output, offline fallback —
is exercised end-to-end before every release (flags `MK_NO_MCP=1
MK_NO_SCHEDULE=1` exist exactly for that).

## Configuration

Everything lives in `.env` (created by the installer, never committed):

| Var | Default | Meaning |
|---|---|---|
| `MV_PORT` | `8000` | host port of the memory-vault API |
| `MV_DB_PASSWORD` | random | Postgres password (containers only) |
| `MV_TOKEN` | created by installer | Bearer token for the shim |
| `MV_BACKUP_DIR` | `~/memory-vault-backups` | where dumps go — point it at a cloud-synced folder for offsite copies |
| `MV_WAKE_SPACES` | `'default'` | which memory spaces the wake-up prints, e.g. `'default','work'` |

## Day-2 operations

```bash
docker compose up -d          # start the stack (memory lives only while this runs)
bash backup/backup.sh         # manual backup any time
gunzip -c backup.sql.gz | docker compose exec -T db psql -U memory_vault -d memory_vault   # restore
```

The wake-up hook degrades gracefully: if Docker is down it prints the last
frozen snapshot instead, so Claude is never completely amnesiac.

## Migrating an existing memory

Coming from a ChromaDB-based memory system (mem0-style, memory-palace tools,
your own)? `tools/chroma_export.py` + `tools/vault_loader.py` move it over
idempotently — safe to re-run, only missing records load. Battle-tested on a
1,540-record migration. Details and gotchas: [docs/MIGRATION.md](docs/MIGRATION.md).

## Using the vault from ChatGPT (or another external AI)

The vault is Claude-first, but the same memories can be read **and written** from
a ChatGPT Custom GPT (or any external client) over an authenticated HTTPS tunnel.
No engine changes — it uses the built-in Bearer auth plus a Cloudflare/ngrok
tunnel, with a dedicated revocable token so a leak never affects Claude.

Setup (token → tunnel → Custom GPT Action) and a ready OpenAPI schema:
[tools/remote-bridge/README.md](tools/remote-bridge/README.md).

## Security notes

- The API requires a Bearer token; the DB password is random per install.
- Both ports bind to localhost by default. Do not expose them; the memories are plaintext.
- Backups are unencrypted SQL dumps — treat the backup dir accordingly.
- The [remote bridge](tools/remote-bridge/README.md) deliberately exposes the API
  over HTTPS. Use a dedicated token, keep it in a password manager, and add a
  Cloudflare WAF/rate-limit rule — the token is the only lock.

## Credits

- [vault-engine](https://github.com/RomanLatypov/vault-engine) — the storage/search engine this kit orchestrates; based on [memory-vault](https://github.com/MihaiBuilds/memory-vault) by MihaiBuilds (MIT).
- Kit by [Roman Latypov](https://github.com/RomanLatypov). MIT.
