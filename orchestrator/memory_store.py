# ~/Projects/VelaNova/orchestrator/memory_store.py
# Phase D memory: local SQLite + FTS5 retrieval (embeddings-ready design).

from __future__ import annotations
import os
import sqlite3
import time
import json
import uuid
from typing import Optional, Dict, Any, List

DB_PATH = os.path.expanduser("~/Projects/VelaNova/memory/db/memory.sqlite")
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # keep WAL/synchronous set for reliability
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
    except sqlite3.DatabaseError:
        pass
    return conn

def ensure_doc(
    source: str = "voice_loop",
    meta: Optional[Dict[str, Any]] = None,
    doc_id: Optional[str] = None,
) -> str:
    """
    Ensure a logical 'conversation' doc exists. Returns doc_id (new or existing).
    """
    if not doc_id:
        doc_id = uuid.uuid4().hex
    ts = int(time.time() * 1000)
    with _conn() as c:
        c.execute(
            "INSERT OR IGNORE INTO docs(doc_id, source, meta, created_ts) VALUES(?,?,?,?)",
            (doc_id, source, json.dumps(meta or {}), ts),
        )
    return doc_id

def add_chunk(
    doc_id: str,
    role: str,
    text: str,
    ts: Optional[int] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> int:
    """
    Append one turn (user/assistant/system) into memory. Returns row id.
    """
    if not text:
        return -1
    ts = ts or int(time.time() * 1000)
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO chunks(doc_id, ts, role, text, meta) VALUES(?,?,?,?,?)",
            (doc_id, ts, role, text, json.dumps(meta or {})),
        )
        return int(cur.lastrowid)

def _has_fts(c: sqlite3.Connection) -> bool:
    row = c.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='chunks_fts'"
    ).fetchone()
    return bool(row)

def _search_fts(c: sqlite3.Connection, query: str, k: int) -> List[sqlite3.Row]:
    """
    Prefer BM25 ranking; fall back to default order if bm25() is unavailable.
    """
    sql_bm25 = """
        SELECT ch.id, ch.doc_id, ch.ts, ch.role, ch.text
        FROM chunks_fts
        JOIN chunks ch ON ch.id = chunks_fts.rowid
        WHERE chunks_fts MATCH ?
        ORDER BY bm25(chunks_fts)
        LIMIT ?
    """
    try:
        return c.execute(sql_bm25, (query, k)).fetchall()
    except sqlite3.OperationalError:
        # Fallback without bm25()
        sql_fallback = """
            SELECT ch.id, ch.doc_id, ch.ts, ch.role, ch.text
            FROM chunks_fts
            JOIN chunks ch ON ch.id = chunks_fts.rowid
            WHERE chunks_fts MATCH ?
            LIMIT ?
        """
        return c.execute(sql_fallback, (query, k)).fetchall()

def search(query: str, k: int = 6) -> List[sqlite3.Row]:
    """
    Retrieve relevant snippets using FTS5 if available; fallback to LIKE.
    """
    with _conn() as c:
        if _has_fts(c):
            rows = _search_fts(c, query, k)
        else:
            rows = c.execute(
                """
                SELECT id, doc_id, ts, role, text
                FROM chunks
                WHERE text LIKE ?
                ORDER BY ts DESC
                LIMIT ?
                """,
                (f"%{query}%", k),
            ).fetchall()
    return rows

def recent(k: int = 6) -> List[sqlite3.Row]:
    with _conn() as c:
        return c.execute(
            "SELECT id, doc_id, ts, role, text FROM chunks ORDER BY ts DESC LIMIT ?",
            (k,),
        ).fetchall()

def build_context(query_text: str, k: int = 6, max_chars: int = 1600) -> str:
    """
    Build a compact context block for the LLM prelude. If search is empty,
    fall back to recency. Hard-cap total characters to keep prompts lean.
    """
    rows = search(query_text, k)
    if not rows:
        rows = recent(k)

    parts: List[str] = []
    total = 0
    for r in rows:
        t = (r["text"] or "").strip()
        if not t:
            continue
        remaining = max_chars - total
        if remaining <= 0:
            break
        if len(t) > remaining:
            t = t[:remaining]
        parts.append(t)
        total += len(t)
    return "\n---\n".join(parts)

# Optional embedding hook for future use with a local embed model via Ollama.
# def embed(text: str) -> bytes:
#     ...
#     return vector_bytes

if __name__ == "__main__":
    # Smoke test: create a doc, add a line, and print retrieval.
    did = ensure_doc(source="smoke", meta={"note": "manual run"})
    rid = add_chunk(
        did,
        "user",
        "VelaNova should remember details across restarts.",
        meta={"turn": "t1"},
    )
    print("doc_id:", did)
    print("row_id:", rid)
    print("search_ids:", [r["id"] for r in search("remember")])
    print("recent_ids:", [r["id"] for r in recent(3)])
    print("context:", build_context("rest"))
