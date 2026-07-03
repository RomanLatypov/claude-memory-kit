# Migrating an existing ChromaDB memory into the vault

`tools/chroma_export.py` reads a ChromaDB sqlite file and emits JSONL;
`tools/vault_loader.py` loads that JSONL into the vault **inside the app
container**, re-embedding with the vault's own model so vectors are always
consistent.

## Steps

```bash
# 1. Export from Chroma (read-only; --dry-run first to see what you have)
python3 tools/chroma_export.py --db /path/to/chroma.sqlite3 --dry-run
python3 tools/chroma_export.py --db /path/to/chroma.sqlite3 \
    --wings user,notes --out /tmp/export.jsonl

# 2. Compute the delta against what is already in the vault (idempotency)
docker compose exec -T db psql -U memory_vault -d memory_vault -tA -c \
  "SELECT metadata->>'mempalace_drawer_id' FROM chunks
   WHERE metadata->>'mempalace_drawer_id' IS NOT NULL;" | sort -u > /tmp/vault_ids.txt
# ...filter export.jsonl to records whose drawer_id is not in vault_ids.txt

# 3. Load (the loader is NOT baked into the app image — copy it in first)
docker compose cp tools/vault_loader.py app:/tmp/vault_loader.py
docker compose cp /tmp/delta.jsonl      app:/tmp/delta.jsonl
docker compose exec -T app python /tmp/vault_loader.py /tmp/delta.jsonl
```

Note: `chroma_export.py` reads the `mempalace_drawers` collection by default —
adjust `DRAWERS_COLLECTION` at the top of the script to your collection name.

## Gotchas learned the hard way

- **Verify counts with SQL after migrating.** Our first cutover silently
  dropped an entire 727-record wing; the status tooling reported success.
  `SELECT s.name, count(*) FROM chunks c JOIN memory_spaces s ON
  c.space_id=s.id GROUP BY 1;` is the ground truth.
- **Idempotency key.** Every exported record carries its Chroma embedding id in
  metadata; the delta step makes re-runs safe. Never bulk-load without one.
- **NULL metadata vs SQL comparisons.** A filter like
  `metadata->>'room' <> 'diary'` silently drops rows where `room` is NULL
  (NULL comparison). Wrap in `coalesce(metadata->>'room','')`. This bug hid
  every fresh memory from our session-start hook for a while.
- **Embeddings are not exported.** The loader re-embeds on ingest with the
  vault's model (all-MiniLM-L6-v2, 384-dim, normalized). Exporting vectors
  from Chroma and inserting them raw risks cosine-normalization mismatch.
- **Keep the source archive.** Tar the old Chroma directory before deleting
  anything. Ours saved the day two weeks after "successful" migration.
