#!/usr/bin/env python3
"""
NOUZ MCP-style search over an Obsidian vault: SQLite index + numpy embeddings.

Embeddings: MiniMax API when ``MINIMAX_API_KEY`` (and ``MINIMAX_GROUP_ID``) are set,
otherwise Ollama at ``OLLAMA_EMBED_URL`` (default http://127.0.0.1:11434) with
``OLLAMA_EMBED_MODEL`` (default nomic-embed-text).

Index and database live under ``NOUZ_DATA_DIR`` or ``openclaw-cursor/data``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

import frontmatter
import numpy as np

from scripts.nouz_common import (
    default_data_dir,
    default_vault_path,
    extract_title,
    iter_markdown_files,
    note_uid,
)
from scripts.sqlite_helper import connect_sqlite

WIKI_LINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
DEFAULT_OLLAMA_EMBED_URL = os.environ.get("OLLAMA_EMBED_URL", "http://127.0.0.1:11434")
DEFAULT_OLLAMA_EMBED_MODEL = os.environ.get("OLLAMA_EMBED_MODEL", "nomic-embed-text")
MINIMAX_EMBED_URL = os.environ.get("MINIMAX_EMBED_URL", "https://api.minimax.chat/v1/embeddings")
MINIMAX_EMBED_MODEL = os.environ.get("MINIMAX_EMBED_MODEL", "embo-01")

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS notes (
    uid TEXT PRIMARY KEY,
    rel_path TEXT NOT NULL UNIQUE,
    title TEXT,
    level TEXT,
    role TEXT,
    status TEXT,
    domain TEXT,
    core_id TEXT,
    mtime REAL NOT NULL,
    content TEXT
);

CREATE TABLE IF NOT EXISTS wiki_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_uid TEXT NOT NULL,
    to_uid TEXT,
    raw_target TEXT NOT NULL,
    FOREIGN KEY (from_uid) REFERENCES notes(uid) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_wiki_from ON wiki_links(from_uid);
CREATE INDEX IF NOT EXISTS idx_wiki_to ON wiki_links(to_uid);
CREATE INDEX IF NOT EXISTS idx_notes_core ON notes(core_id);
CREATE INDEX IF NOT EXISTS idx_notes_filters ON notes(level, domain, status);
"""


def parse_wiki_target(raw_target: str) -> str:
    target = raw_target.split("|", 1)[0].strip()
    target = target.split("#", 1)[0].strip()
    return target


def extract_wiki_links(content: str) -> list[str]:
    links: list[str] = []
    for match in WIKI_LINK_RE.findall(content):
        parsed = parse_wiki_target(match)
        if parsed:
            links.append(parsed)
    return links


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    conn.commit()


def _http_json(url: str, payload: dict[str, Any], headers: dict[str, str] | None = None) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers or {}, method="POST")
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def embed_minimax(texts: list[str], embed_type: str) -> list[list[float]]:
    api_key = os.environ.get("MINIMAX_API_KEY", "")
    group_id = os.environ.get("MINIMAX_GROUP_ID", "")
    if not api_key or not group_id:
        raise RuntimeError("MiniMax requires MINIMAX_API_KEY and MINIMAX_GROUP_ID")
    params = f"?GroupId={urllib.parse.quote(str(group_id))}"
    url = f"{MINIMAX_EMBED_URL}{params}"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {"model": MINIMAX_EMBED_MODEL, "type": embed_type, "texts": texts}
    parsed = _http_json(url, payload, headers)
    base = parsed.get("base_resp") or {}
    if base.get("status_code") not in (0, None):
        raise RuntimeError(f"MiniMax error: {base}")
    vectors = parsed.get("vectors")
    if not vectors:
        raise RuntimeError("MiniMax response missing vectors")
    return vectors


def embed_ollama(text: str, base_url: str, model: str) -> list[float]:
    url = base_url.rstrip("/") + "/api/embeddings"
    payload = {"model": model, "prompt": text}
    parsed = _http_json(url, payload)
    emb = parsed.get("embedding")
    if not emb:
        raise RuntimeError("Ollama response missing embedding")
    return emb


def build_embedder() -> tuple[str, Callable[..., np.ndarray]]:
    """Return (provider_name, fn(text, purpose)) -> numpy vector; purpose is 'document'|'query'."""
    if os.environ.get("MINIMAX_API_KEY") and os.environ.get("MINIMAX_GROUP_ID"):

        def minimax_embed(text: str, purpose: str) -> np.ndarray:
            etype = "query" if purpose == "query" else "db"
            vec = embed_minimax([text], etype)[0]
            arr = np.asarray(vec, dtype=np.float32)
            norm = np.linalg.norm(arr)
            if norm > 0:
                arr = arr / norm
            return arr

        return ("minimax", minimax_embed)

    base_url = DEFAULT_OLLAMA_EMBED_URL
    model = DEFAULT_OLLAMA_EMBED_MODEL

    def ollama_embed(text: str, purpose: str) -> np.ndarray:
        _ = purpose
        vec = embed_ollama(text, base_url, model)
        arr = np.asarray(vec, dtype=np.float32)
        norm = np.linalg.norm(arr)
        if norm > 0:
            arr = arr / norm
        return arr

    return ("ollama", ollama_embed)


def embedding_path(embeddings_dir: Path, uid: str) -> Path:
    return embeddings_dir / f"{uid}.npy"


def save_embedding(embeddings_dir: Path, uid: str, vector: np.ndarray) -> None:
    embeddings_dir.mkdir(parents=True, exist_ok=True)
    np.save(embedding_path(embeddings_dir, uid), vector.astype(np.float32))


def _row_with_path(row: sqlite3.Row) -> dict[str, Any]:
    """Normalize note rows for MCP-style callers (``path`` = vault-relative path)."""
    d = dict(row)
    d["path"] = d["rel_path"]
    return d


def load_embedding(embeddings_dir: Path, uid: str) -> np.ndarray | None:
    path = embedding_path(embeddings_dir, uid)
    if not path.is_file():
        return None
    return np.load(path).astype(np.float32)


def resolve_wiki_target(target: str, stem_to_uid: dict[str, str], path_to_uid: dict[str, str]) -> str | None:
    key = target.replace("\\", "/").strip()
    lower = key.lower()
    if lower in path_to_uid:
        return path_to_uid[lower]
    stem = Path(key).stem.lower()
    return stem_to_uid.get(stem)


class NouzSearch:
    """SQLite + numpy embedding index over a vault."""

    def __init__(
        self,
        vault_path: Path | None = None,
        data_dir: Path | None = None,
    ) -> None:
        self.vault_path = Path(vault_path) if vault_path is not None else default_vault_path()
        raw_data = os.environ.get("NOUZ_DATA_DIR")
        self.data_dir = Path(raw_data) if raw_data else (data_dir if data_dir is not None else default_data_dir())
        self.db_path = self.data_dir / "nouz.db"
        self.embeddings_dir = self.data_dir / "embeddings"

    def connect(self) -> sqlite3.Connection:
        conn = connect_sqlite(self.db_path)
        init_schema(conn)
        return conn

    def sync_index(self, embed: bool = True) -> dict[str, Any]:
        """Scan vault, refresh SQLite notes/wiki_links, optionally rebuild embeddings."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        conn = self.connect()
        stats: dict[str, Any] = {"notes": 0, "links": 0, "embedded": 0, "embed_errors": [], "provider": None}

        stem_to_uid: dict[str, str] = {}
        path_to_uid: dict[str, str] = {}

        seen_uids: set[str] = set()
        rows: list[tuple[Any, ...]] = []

        if not self.vault_path.is_dir():
            conn.close()
            stats["error"] = "vault_missing"
            return stats

        for md in iter_markdown_files(self.vault_path):
            rel = md.relative_to(self.vault_path).as_posix()
            uid = note_uid(rel)
            seen_uids.add(uid)
            post = frontmatter.load(md)
            meta = post.metadata
            title = extract_title(post.content, Path(rel).stem)
            body = post.content or ""
            content = body.strip()
            stem_to_uid[Path(rel).stem.lower()] = uid
            path_to_uid[rel.lower()] = uid
            path_to_uid[Path(rel).as_posix().lower()] = uid

            rows.append(
                (
                    uid,
                    rel,
                    title,
                    meta.get("level"),
                    meta.get("role"),
                    meta.get("status"),
                    meta.get("domain"),
                    meta.get("core_id"),
                    md.stat().st_mtime,
                    content[:8000],
                )
            )

        conn.execute("DELETE FROM wiki_links")
        conn.execute("DELETE FROM notes")

        conn.executemany(
            """
            INSERT INTO notes (uid, rel_path, title, level, role, status, domain, core_id, mtime, content)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        stats["notes"] = len(rows)

        for md in iter_markdown_files(self.vault_path):
            rel = md.relative_to(self.vault_path).as_posix()
            uid = note_uid(rel)
            post = frontmatter.load(md)
            for raw in extract_wiki_links(post.content):
                to_uid = resolve_wiki_target(raw, stem_to_uid, path_to_uid)
                conn.execute(
                    "INSERT INTO wiki_links (from_uid, to_uid, raw_target) VALUES (?, ?, ?)",
                    (uid, to_uid, raw),
                )
                stats["links"] += 1

        conn.commit()

        if embed and rows:
            try:
                provider, embed_fn = build_embedder()
                stats["provider"] = provider
            except Exception as exc:
                stats["embed_errors"].append(f"embedder: {exc}")
                conn.close()
                return stats

            for row in rows:
                uid, rel, title, _, _, _, _, _, _, content = row
                text = f"{title}\n\n{content}"[:6000]
                try:
                    vec = embed_fn(text, "document")
                    save_embedding(self.embeddings_dir, uid, vec)
                    stats["embedded"] += 1
                except (urllib.error.URLError, urllib.error.HTTPError, RuntimeError, TimeoutError) as exc:
                    stats["embed_errors"].append(f"{rel}: {exc}")

        conn.close()
        return stats

    def find_notes(
        self,
        level: str | None = None,
        domain: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if level:
            clauses.append("level = ?")
            params.append(level)
        if domain:
            clauses.append("domain = ?")
            params.append(domain)
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        conn = self.connect()
        cur = conn.execute(
            f"SELECT uid, rel_path, title, level, status FROM notes {where} ORDER BY rel_path",
            params,
        )
        out = []
        for r in cur.fetchall():
            base = _row_with_path(r)
            out.append(
                {
                    "uid": base["uid"],
                    "path": base["path"],
                    "title": base["title"] or Path(base["rel_path"]).stem,
                    "level": base["level"],
                    "status": base["status"],
                }
            )
        conn.close()
        return out

    def semantic_search(self, query: str, top_k: int = 8) -> list[dict[str, Any]]:
        _, embed_fn = build_embedder()
        qvec = embed_fn(query.strip(), "query")
        conn = self.connect()
        cur = conn.execute("SELECT uid, rel_path, title FROM notes")
        scored: list[tuple[float, sqlite3.Row]] = []
        for row in cur.fetchall():
            emb = load_embedding(self.embeddings_dir, row["uid"])
            if emb is None:
                continue
            sim = float(np.dot(qvec, emb))
            scored.append((sim, row))
        conn.close()
        scored.sort(key=lambda x: x[0], reverse=True)
        out: list[dict[str, Any]] = []
        for sim, row in scored[: max(1, top_k)]:
            out.append(
                {
                    "uid": row["uid"],
                    "path": row["rel_path"],
                    "title": row["title"] or Path(row["rel_path"]).stem,
                    "similarity": sim,
                }
            )
        return out

    def get_context_bundle(self, note_id: str, depth: int = 1) -> dict[str, Any]:
        conn = self.connect()
        cur = conn.execute("SELECT * FROM notes WHERE uid = ?", (note_id,))
        row = cur.fetchone()
        if row is None:
            conn.close()
            return {"note": None, "parents": [], "children": [], "bridges": []}

        note = _row_with_path(row)
        core_id = note.get("core_id")

        parents = self._expand_neighbors(conn, note_id, depth, incoming=True)
        children = self._expand_neighbors(conn, note_id, depth, incoming=False)

        bridges: list[dict[str, Any]] = []
        if core_id:
            bcur = conn.execute(
                "SELECT uid, rel_path, title, level, status FROM notes WHERE core_id = ? AND uid != ? ORDER BY rel_path",
                (core_id, note_id),
            )
            bridges = [_row_with_path(r) for r in bcur.fetchall()]

        conn.close()
        return {"note": note, "parents": parents, "children": children, "bridges": bridges}

    def _expand_neighbors(
        self,
        conn: sqlite3.Connection,
        start_uid: str,
        depth: int,
        *,
        incoming: bool,
    ) -> list[dict[str, Any]]:
        if depth < 1:
            return []
        collected: dict[str, dict[str, Any]] = {}
        frontier: set[str] = {start_uid}
        for _ in range(depth):
            nxt: set[str] = set()
            for uid in frontier:
                if incoming:
                    cur = conn.execute(
                        "SELECT DISTINCT from_uid FROM wiki_links WHERE to_uid = ? AND from_uid IS NOT NULL",
                        (uid,),
                    )
                    for r in cur.fetchall():
                        other = r["from_uid"]
                        if other:
                            nxt.add(other)
                else:
                    cur = conn.execute(
                        "SELECT to_uid FROM wiki_links WHERE from_uid = ? AND to_uid IS NOT NULL",
                        (uid,),
                    )
                    for r in cur.fetchall():
                        other = r["to_uid"]
                        if other:
                            nxt.add(other)
            for uid in nxt:
                if uid == start_uid or uid in collected:
                    continue
                nrow = conn.execute(
                    "SELECT uid, rel_path, title, level, status FROM notes WHERE uid = ?",
                    (uid,),
                )
                one = nrow.fetchone()
                if one:
                    collected[uid] = _row_with_path(one)
            frontier = nxt
            if not frontier:
                break
        return list(collected.values())

    def get_core_profile(self, core_id: str) -> dict[str, Any]:
        conn = self.connect()
        cur = conn.execute(
            "SELECT uid, rel_path, title, content FROM notes WHERE core_id = ? ORDER BY rel_path LIMIT 10",
            (core_id,),
        )
        rows = cur.fetchall()
        count_cur = conn.execute(
            "SELECT COUNT(*) AS c FROM notes WHERE core_id = ?",
            (core_id,),
        )
        note_count = int(count_cur.fetchone()["c"])
        conn.close()
        title = core_id
        description = ""
        if rows:
            title = rows[0]["title"] or Path(rows[0]["rel_path"]).stem
            snippet = (rows[0]["content"] or "").strip().splitlines()
            description = "\n".join(snippet[:5])[:1500]
        return {
            "core_id": core_id,
            "title": title,
            "description": description,
            "note_count": note_count,
        }

    def notes_near_core(self, core_id: str, limit: int = 20) -> list[dict[str, Any]]:
        conn = self.connect()
        cur = conn.execute(
            """
            SELECT uid, rel_path, title, level, domain, status
            FROM notes WHERE core_id = ?
            ORDER BY rel_path
            LIMIT ?
            """,
            (core_id, max(1, limit)),
        )
        rows = [_row_with_path(r) for r in cur.fetchall()]
        conn.close()
        return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="NOUZ vault index and MCP-style queries.")
    parser.add_argument("--vault", type=Path, default=None)
    parser.add_argument("--data-dir", type=Path, default=None)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_sync = sub.add_parser("sync", help="Rebuild SQLite index from vault.")
    p_sync.add_argument("--no-embed", action="store_true", help="Skip embedding generation.")

    p_find = sub.add_parser("find", help="Filter notes by YAML fields.")
    p_find.add_argument("--level", default=None)
    p_find.add_argument("--domain", default=None)
    p_find.add_argument("--status", default=None)

    p_sem = sub.add_parser("semantic", help="Semantic search (requires embeddings).")
    p_sem.add_argument("query")
    p_sem.add_argument("--top-k", type=int, default=8)

    p_bundle = sub.add_parser("bundle", help="Context bundle for a note uid.")
    p_bundle.add_argument("uid")
    p_bundle.add_argument("--depth", type=int, default=1)

    p_core = sub.add_parser("core-profile", help="Aggregate stats for a core_id.")
    p_core.add_argument("core_id")

    p_near = sub.add_parser("near-core", help="Notes sharing core_id.")
    p_near.add_argument("core_id")
    p_near.add_argument("--limit", type=int, default=20)

    args = parser.parse_args(argv)
    ns = NouzSearch(vault_path=args.vault, data_dir=args.data_dir)

    if args.cmd == "sync":
        stats = ns.sync_index(embed=not args.no_embed)
        print(json.dumps(stats, indent=2))
        return 0 if stats.get("error") != "vault_missing" else 1

    if args.cmd == "find":
        rows = ns.find_notes(level=args.level, domain=args.domain, status=args.status)
        print(json.dumps(rows, indent=2))
        return 0

    if args.cmd == "semantic":
        rows = ns.semantic_search(args.query, top_k=args.top_k)
        print(json.dumps(rows, indent=2))
        return 0

    if args.cmd == "bundle":
        bundle = ns.get_context_bundle(args.uid, depth=args.depth)
        note = bundle["note"]
        out = {
            "note": {k: note[k] for k in note} if note else None,
            "parents": bundle["parents"],
            "children": bundle["children"],
            "bridges": bundle["bridges"],
        }
        print(json.dumps(out, indent=2, default=str))
        return 0

    if args.cmd == "core-profile":
        print(json.dumps(ns.get_core_profile(args.core_id), indent=2, default=str))
        return 0

    if args.cmd == "near-core":
        print(json.dumps(ns.notes_near_core(args.core_id, limit=args.limit), indent=2, default=str))
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
