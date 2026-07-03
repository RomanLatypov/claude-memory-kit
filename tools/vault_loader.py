"""
Bulk-load mempalace export JSONL into memory-vault chunks, full fidelity.

Runs INSIDE the app container (reuses src.services.embedding + src.models.db).
- embeds content with the same model (all-MiniLM-L6-v2, normalized, 384)
- resolves/creates a memory_space per wing
- inserts chunks with metadata JSONB + importance + created_at(from filed_at)
- skips knowledge-graph extraction (do separately if wanted)

Usage (inside container):
  python /tmp/vault_loader.py /tmp/drawers_signal.jsonl
"""
from __future__ import annotations

import asyncio
import json
import sys
import uuid
from datetime import datetime

from src.models.db import execute_query, fetch_one
from src.services.embedding import embed_batch

BATCH = 64


async def resolve_space(name: str) -> int:
    row = await fetch_one("SELECT id FROM memory_spaces WHERE name = %s", (name,))
    if row:
        return row["id"]
    await execute_query(
        "INSERT INTO memory_spaces (name, description) VALUES (%s, %s) "
        "ON CONFLICT (name) DO NOTHING",
        (name, f"Imported from mempalace wing '{name}'"),
    )
    row = await fetch_one("SELECT id FROM memory_spaces WHERE name = %s", (name,))
    return row["id"]


def parse_ts(meta: dict):
    for k in ("filed_at", "date", "content_date"):
        v = meta.get(k)
        if not v:
            continue
        try:
            return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        except ValueError:
            continue
    return None


async def main(path: str) -> None:
    records = [json.loads(line) for line in open(path, encoding="utf-8") if line.strip()]
    print(f"loaded {len(records)} records from {path}", flush=True)

    # cache space ids
    spaces: dict[str, int] = {}
    for r in records:
        w = r.get("space", "default")
        if w not in spaces:
            spaces[w] = await resolve_space(w)
    print(f"spaces: {spaces}", flush=True)

    inserted = 0
    for i in range(0, len(records), BATCH):
        batch = records[i : i + BATCH]
        texts = [r["content"] for r in batch]
        embeddings = embed_batch(texts)
        for r, emb in zip(batch, embeddings, strict=True):
            meta = r.get("metadata", {})
            await execute_query(
                """INSERT INTO chunks
                       (id, space_id, content, embedding, source, speaker,
                        metadata, importance, chunk_index, created_at)
                   VALUES (%s, %s, %s, %s::vector, %s, %s, %s::jsonb, %s, %s,
                           COALESCE(%s, now()))""",
                (
                    str(uuid.uuid4()),
                    spaces[r.get("space", "default")],
                    r["content"],
                    str(list(emb)),
                    r.get("source", "mempalace"),
                    None,
                    json.dumps(meta, default=str, ensure_ascii=False),
                    float(r.get("importance", 0.5)),
                    int(meta.get("chunk_index", 0) or 0),
                    parse_ts(meta),
                ),
            )
            inserted += 1
        print(f"  {inserted}/{len(records)}", flush=True)

    total = await fetch_one("SELECT count(*) AS n FROM chunks")
    print(f"DONE. inserted={inserted}  chunks_total={total['n']}", flush=True)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
