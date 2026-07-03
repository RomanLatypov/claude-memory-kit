#!/usr/bin/env python3
"""
Export mempalace drawers from chroma.sqlite3 -> JSONL for memory-vault ingest.

Reads only the `mempalace_drawers` collection (skips closets).
One JSON object per line:
  {
    "drawer_id": "<chroma embedding_id>",
    "content":   "<the fact text>",
    "space":     "<wing>",            # -> memory_vault memory_space
    "importance": 0.5,
    "source":    "<source_file or 'mempalace'>",
    "metadata":  { room, type, topic, entities[], date, content_date,
                   filed_at, source_file, chunk_index, ... }
  }

Embeddings are NOT exported — memory-vault re-embeds with the same model
(all-MiniLM-L6-v2 / 384) on ingest, avoiding cosine-normalization mismatch.

Usage:
  python3 chroma_export.py --db ~/.mempalace/palace/chroma.sqlite3 \
                           --out drawers.jsonl
  python3 chroma_export.py --dry-run          # print 5 samples + counts, write nothing
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections import Counter

DRAWERS_COLLECTION = "mempalace_drawers"
DOC_KEY = "chroma:document"

# metadata keys that map to top-level fields rather than the metadata blob
SKIP_IN_BLOB = {DOC_KEY}
# importance bump: L0/identity-ish drawers rank higher; default 0.5
IMPORTANCE_DEFAULT = 0.5


def find_drawers_metadata_segment(cur) -> str:
    cur.execute("SELECT id FROM collections WHERE name = ?", (DRAWERS_COLLECTION,))
    row = cur.fetchone()
    if not row:
        sys.exit(f"collection '{DRAWERS_COLLECTION}' not found")
    coll_id = row[0]
    cur.execute(
        "SELECT id FROM segments WHERE collection = ? AND scope = 'METADATA'",
        (coll_id,),
    )
    row = cur.fetchone()
    if not row:
        sys.exit("METADATA segment for drawers not found")
    return row[0]


def load_drawer_ids(cur, segment_id: str) -> list[tuple[int, str]]:
    cur.execute(
        "SELECT id, embedding_id FROM embeddings WHERE segment_id = ? ORDER BY id",
        (segment_id,),
    )
    return cur.fetchall()


def load_metadata(cur, emb_id: int) -> dict:
    cur.execute(
        "SELECT key, string_value, int_value, float_value, bool_value "
        "FROM embedding_metadata WHERE id = ?",
        (emb_id,),
    )
    meta: dict = {}
    for key, sv, iv, fv, bv in cur.fetchall():
        val = sv if sv is not None else (iv if iv is not None else (fv if fv is not None else bv))
        meta[key] = val
    return meta


def to_record(emb_id: int, drawer_id: str, meta: dict) -> dict | None:
    content = meta.get(DOC_KEY)
    if not content or not str(content).strip():
        return None  # skip empty/service rows

    wing = meta.get("wing") or "default"
    blob = {k: v for k, v in meta.items() if k not in SKIP_IN_BLOB and v is not None}

    # entities stored as ';'-joined string -> list
    ents = meta.get("entities")
    if isinstance(ents, str) and ents:
        blob["entities"] = [e for e in ents.split(";") if e]

    blob.setdefault("origin", "mempalace")
    blob["mempalace_drawer_id"] = drawer_id

    return {
        "drawer_id": drawer_id,
        "content": str(content),
        "space": wing,
        "importance": IMPORTANCE_DEFAULT,
        "source": meta.get("source_file") or "mempalace",
        "metadata": blob,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.path.expanduser("~/.mempalace/palace/chroma.sqlite3"))
    ap.add_argument("--out", default="drawers.jsonl")
    ap.add_argument("--wings", default="",
                    help="comma list of wings to keep (e.g. 'user,wing_user'); empty = all")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    keep_wings = {w.strip() for w in args.wings.split(",") if w.strip()}

    con = sqlite3.connect(f"file:{os.path.expanduser(args.db)}?mode=ro", uri=True)
    cur = con.cursor()

    seg = find_drawers_metadata_segment(cur)
    ids = load_drawer_ids(cur, seg)

    by_wing: Counter = Counter()
    by_room: Counter = Counter()
    written = skipped = filtered = 0
    samples = []

    out = None if args.dry_run else open(args.out, "w", encoding="utf-8")
    # second cursor for per-row metadata fetch
    mcur = con.cursor()
    for emb_id, drawer_id in ids:
        meta = load_metadata(mcur, emb_id)
        rec = to_record(emb_id, drawer_id, meta)
        if rec is None:
            skipped += 1
            continue
        if keep_wings and rec["space"] not in keep_wings:
            filtered += 1
            continue
        by_wing[rec["space"]] += 1
        by_room[f'{rec["space"]}/{rec["metadata"].get("room", "?")}'] += 1
        written += 1
        if out:
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
        elif len(samples) < 5:
            samples.append(rec)
    if out:
        out.close()

    print(f"segment: {seg}")
    print(f"rows in segment: {len(ids)}")
    print(f"written: {written}   skipped(empty): {skipped}   filtered(other wings): {filtered}")
    print("by wing:")
    for w, n in by_wing.most_common():
        print(f"  {w:14} {n}")
    print("by room (top 12):")
    for r, n in by_room.most_common(12):
        print(f"  {r:28} {n}")
    if args.dry_run:
        print("\n--- 5 sample records ---")
        for s in samples:
            c = s["content"][:90].replace("\n", " ")
            print(f"  [{s['space']}/{s['metadata'].get('room','?')}] {c}")
    else:
        print(f"\nwrote -> {args.out}")
    con.close()


if __name__ == "__main__":
    main()
