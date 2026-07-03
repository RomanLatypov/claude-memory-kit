#!/usr/bin/env python3
"""Thin stdio-MCP shim for memory-vault.

Proxies the 4 memory tools to the always-warm app container over HTTP
(no local embedding model: ~1s cold start, ~30 MB RAM per Claude window).

Env:
    MV_URL   base URL of the app (default http://localhost:8000)
    MV_TOKEN Bearer token (created via `memory-vault token create`)
"""

from __future__ import annotations

import json
import os

import httpx
from fastmcp import FastMCP

MV_URL = os.environ.get("MV_URL", "http://localhost:8000").rstrip("/")
MV_TOKEN = os.environ.get("MV_TOKEN", "")

mcp = FastMCP("memory-vault")


def _client() -> httpx.AsyncClient:
    headers = {"Authorization": f"Bearer {MV_TOKEN}"} if MV_TOKEN else {}
    return httpx.AsyncClient(base_url=MV_URL, headers=headers, timeout=30.0)


def _dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)


def _budget(results: list[dict], max_tokens: int) -> tuple[list[dict], bool]:
    """Trim result contents to fit a rough token budget (~4 chars/token)."""
    budget_chars = max_tokens * 4
    out, used, truncated = [], 0, False
    for r in results:
        content = r.get("content") or ""
        overhead = 120  # metadata fields per entry, rough
        remaining = budget_chars - used - overhead
        if remaining <= 0:
            truncated = True
            break
        if len(content) > remaining:
            r = {**r, "content": content[:remaining] + "... [truncated]"}
            truncated = True
        used += min(len(content), remaining) + overhead
        out.append(r)
    return out, truncated


@mcp.tool()
async def recall(
    query: str,
    spaces: list[str] | None = None,
    since: str | None = None,
    limit: int = 10,
    max_tokens: int = 2000,
) -> str:
    """
    Search your memories for information relevant to a query.

    Returns chunks ranked by relevance using hybrid search (vector + full-text + RRF).
    Uses query enrichment (keyword extraction + variation) for better recall.
    Results are budgeted to fit within max_tokens to avoid flooding context.

    Args:
        query: The search query — a question, topic, or keyword phrase.
        spaces: Filter to specific memory spaces (e.g. ["default", "projects"]).
                If omitted, searches all spaces.
        since: Only return memories after this date (ISO format, e.g. "2025-01-01").
        limit: Maximum number of results (default 10, max 50).
        max_tokens: Token budget for results (default 2000).
    """
    limit = min(max(limit, 1), 50)
    max_tokens = min(max(max_tokens, 200), 8000)
    payload: dict = {"query": query, "limit": limit}
    if spaces:
        payload["spaces"] = spaces
    if since:
        payload["since"] = since
    try:
        async with _client() as c:
            resp = await c.post("/api/search", json=payload)
        if resp.status_code != 200:
            return _dumps({"status": "error", "results": [],
                           "message": f"HTTP {resp.status_code}: {resp.text[:300]}"})
        data = resp.json()
        results = data.get("results", [])
        budgeted, was_truncated = _budget(results, max_tokens)
        response = {
            "status": "ok",
            "results": budgeted,
            "total_results": data.get("total_results", len(results)),
            "results_shown": len(budgeted),
            "query_time_ms": data.get("query_time_ms"),
            "query_variations": data.get("query_variations", []),
        }
        if was_truncated:
            response["note"] = (
                f"Some results were truncated to fit within {max_tokens} token budget. "
                "Use max_tokens to increase."
            )
        return _dumps(response)
    except httpx.HTTPError as e:
        return _dumps({"status": "offline", "results": [],
                       "message": f"memory-vault app unreachable at {MV_URL}: {e}. "
                                  "Start stack: docker compose up -d (from your claude-memory-kit directory)"})


@mcp.tool()
async def remember(
    text: str,
    space: str = "default",
    source: str = "mcp",
    speaker: str = "human",
) -> str:
    """
    Store a new memory in the system.

    The text is embedded and stored as a searchable chunk.
    Use this to save important information, decisions, or knowledge.

    Args:
        text: The text content to remember.
        space: Which memory space to store it in (default "default").
        source: Where this memory comes from (default "mcp").
        speaker: Who said/wrote this — "human" or "assistant" (default "human").
    """
    payload = {"text": text, "space": space, "source": source, "speaker": speaker}
    try:
        async with _client() as c:
            resp = await c.post("/api/ingest/text", json=payload)
        if resp.status_code != 200:
            return _dumps({"stored": False,
                           "message": f"HTTP {resp.status_code}: {resp.text[:300]}"})
        return _dumps(resp.json())
    except httpx.HTTPError as e:
        return _dumps({"stored": False, "message": f"app unreachable: {e}"})


@mcp.tool()
async def forget(chunk_id: str) -> str:
    """
    Delete a memory chunk by its ID.

    Args:
        chunk_id: The UUID of the chunk to delete (from recall results).
    """
    try:
        async with _client() as c:
            resp = await c.delete(f"/api/chunks/{chunk_id}")
        if resp.status_code != 200:
            return _dumps({"deleted": False,
                           "message": f"HTTP {resp.status_code}: {resp.text[:300]}"})
        return _dumps(resp.json())
    except httpx.HTTPError as e:
        return _dumps({"deleted": False, "message": f"app unreachable: {e}"})


@mcp.tool()
async def memory_status() -> str:
    """
    Get the current status of the memory system: health, spaces, chunk counts.
    """
    out: dict = {}
    try:
        async with _client() as c:
            h = await c.get("/api/health")
            out["health"] = h.json() if h.status_code == 200 else {"error": h.status_code}
            s = await c.get("/api/spaces")
            if s.status_code == 200:
                out["spaces"] = s.json()
        return _dumps(out)
    except httpx.HTTPError as e:
        return _dumps({"status": "offline", "message": f"app unreachable at {MV_URL}: {e}"})


if __name__ == "__main__":
    mcp.run()
