#!/usr/bin/env python3
"""Self-improvement error learning: capture failures, categorize, and suggest fixes.

Supports JSON (simple, portable) and SQLite (production: indexing, stats, scale).
Use ``--log-path file.json`` or ``--log-path file.db``. Default is SQLite under
``.learnings/error_learnings.db``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import threading
from abc import ABC, abstractmethod
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Sequence

SCHEMA_VERSION = 1

ROOT = Path(__file__).resolve().parent
DEFAULT_LOG_PATH = ROOT / ".learnings" / "error_learnings.db"

ANSI = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "cyan": "\033[36m",
}
FALLBACK_CATEGORY_COLORS = ("red", "yellow", "green")

_JSON_LOCK = threading.Lock()
_SQLITE_LOCK = threading.Lock()


class ErrorLearningError(RuntimeError):
    """Raised when the error learning store cannot be read or written."""


def colorize(text: str, color: str) -> str:
    if os.environ.get("NO_COLOR"):
        return text
    prefix = ANSI.get(color, "")
    if not prefix:
        return text
    return f"{prefix}{text}{ANSI['reset']}"


def normalize_text(text: str) -> str:
    return " ".join(text.strip().lower().split())


def fingerprint_error(text: str) -> str:
    """Stable short fingerprint for clustering similar raw errors."""
    n = normalize_text(text)
    digest = hashlib.sha256(n.encode("utf-8")).hexdigest()[:16]
    return digest


def category_color(category: str) -> str:
    normalized = normalize_text(category)
    if any(token in normalized for token in ("lesson", "resolved", "fix", "success")):
        return "green"
    if any(token in normalized for token in ("warn", "warning", "caution")):
        return "yellow"
    if any(token in normalized for token in ("error", "failure", "fatal", "exception", "crash", "bug")):
        return "red"
    digest = hashlib.sha1(normalized.encode("utf-8")).digest()[0]
    return FALLBACK_CATEGORY_COLORS[digest % len(FALLBACK_CATEGORY_COLORS)]


# Ordered rules: first match wins (most specific patterns first).
_ERROR_TYPE_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("authentication", re.compile(r"\b(auth|unauthori[sz]ed|401|403|forbidden|invalid\s*token|jwt|api[_\s]?key|credential|password|login)\b", re.I)),
    ("network", re.compile(r"\b(connection refused|econnrefused|timeout|timed out|etimedout|network|dns|enotfound|ssl|tls|certificate|unreachable)\b", re.I)),
    ("timeout", re.compile(r"\b(deadline|timeout|timed out|etimedout|context deadline)\b", re.I)),
    ("parser", re.compile(r"\b(parse|parser|json|yaml|xml|syntax|invalid\s*character|unexpected token|truncat|malformed)\b", re.I)),
    ("permission", re.compile(r"\b(permission denied|eacces|eperm|forbidden|not allowed)\b", re.I)),
    ("resource", re.compile(r"\b(enomem|out of memory|disk full|enospc|too many open|emfile|quota)\b", re.I)),
    ("dependency", re.compile(r"\b(module not found|importerror|modulenotfound|no module named|package not found|cannot find module)\b", re.I)),
    ("database", re.compile(r"\b(sql|sqlite|postgres|mysql|database|operationalerror|integrityerror)\b", re.I)),
    ("configuration", re.compile(r"\b(config|configuration|env|environment variable|missing key|invalid setting)\b", re.I)),
    ("process", re.compile(r"\b(exit code|subprocess|killed|signal|segfault|core dump)\b", re.I)),
)


def infer_error_type(error_text: str) -> str:
    """Auto-categorize a raw error message into a coarse error type."""
    if not error_text or not error_text.strip():
        return "unknown"
    for name, pattern in _ERROR_TYPE_RULES:
        if pattern.search(error_text):
            return name
    return "unknown"


def canonical_payload(category: str, error: str, lesson: str, resolved: bool) -> dict[str, object]:
    return {
        "category": normalize_text(category),
        "error": normalize_text(error),
        "lesson": normalize_text(lesson),
        "resolved": bool(resolved),
    }


def build_entry(
    category: str,
    error: str,
    lesson: str,
    *,
    resolved: bool = True,
    timestamp: str | None = None,
) -> dict[str, object]:
    payload = canonical_payload(category, error, lesson, resolved)
    digest = hashlib.sha1(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:12]
    created_at = timestamp or datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    return {
        "id": digest,
        "timestamp": created_at,
        "category": category.strip(),
        "error": error.strip(),
        "lesson": lesson.strip(),
        "resolved": bool(resolved),
    }


def default_store() -> dict[str, object]:
    return {"schema_version": SCHEMA_VERSION, "entries": []}


def validate_entry(raw_entry: object) -> dict[str, object]:
    if not isinstance(raw_entry, dict):
        raise ErrorLearningError("Each entry in the error log must be a JSON object.")

    entry = dict(raw_entry)
    required_text_fields = ("timestamp", "category", "error", "lesson")
    for field in required_text_fields:
        value = entry.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ErrorLearningError(f"Entry field '{field}' must be a non-empty string.")

    resolved = entry.get("resolved", False)
    if not isinstance(resolved, bool):
        raise ErrorLearningError("Entry field 'resolved' must be a boolean.")

    if not isinstance(entry.get("id"), str) or not entry["id"].strip():
        entry["id"] = build_entry(
            entry["category"],
            entry["error"],
            entry["lesson"],
            resolved=resolved,
            timestamp=entry["timestamp"],
        )["id"]
    entry["resolved"] = resolved
    return entry


def entries_match(left: dict[str, object], right: dict[str, object]) -> bool:
    if left.get("id") == right.get("id"):
        return True
    return canonical_payload(
        str(left["category"]),
        str(left["error"]),
        str(left["lesson"]),
        bool(left["resolved"]),
    ) == canonical_payload(
        str(right["category"]),
        str(right["error"]),
        str(right["lesson"]),
        bool(right["resolved"]),
    )


def search_score(query: str, entry: dict[str, object]) -> float:
    normalized_query = normalize_text(query)
    haystack = normalize_text(
        " ".join((str(entry["category"]), str(entry["error"]), str(entry["lesson"])))
    )
    if not normalized_query:
        return 0.0

    substring_bonus = 1.5 if normalized_query in haystack else 0.0
    query_tokens = set(normalized_query.split())
    haystack_tokens = set(haystack.split())
    overlap = len(query_tokens & haystack_tokens) / max(len(query_tokens), 1)
    ratio = SequenceMatcher(None, normalized_query, haystack).ratio()
    return substring_bonus + overlap + (ratio * 0.5)


def search_entries(entries: list[dict[str, object]], query: str, limit: int = 10) -> list[dict[str, object]]:
    ranked: list[tuple[float, dict[str, object]]] = []
    for entry in entries:
        score = search_score(query, entry)
        if score >= 0.45:
            ranked.append((score, entry))
    ranked.sort(key=lambda item: (-item[0], str(item[1]["timestamp"])), reverse=False)
    return [entry for _, entry in ranked[:limit]]


def suggest_fixes(
    entries: Sequence[dict[str, object]],
    error_text: str,
    *,
    limit: int = 5,
    min_score: float = 0.35,
) -> list[tuple[float, dict[str, object]]]:
    """Rank historical lessons by textual similarity to ``error_text`` (JSON path)."""
    ranked: list[tuple[float, dict[str, object]]] = []
    for entry in entries:
        score = search_score(error_text, entry)
        et = infer_error_type(error_text)
        if et != "unknown" and et == infer_error_type(str(entry["error"])):
            score += 0.35
        if score >= min_score:
            ranked.append((score, entry))
    ranked.sort(key=lambda item: (-item[0], str(item[1]["timestamp"])), reverse=False)
    return ranked[:limit]


# --- Storage backends ---


def _is_json_path(path: Path) -> bool:
    return path.suffix.lower() == ".json"


class _StoreBase(ABC):
    @abstractmethod
    def load_entries(self) -> list[dict[str, object]]:
        ...

    @abstractmethod
    def add(
        self,
        category: str,
        error: str,
        lesson: str,
        *,
        resolved: bool = True,
        infer_category: bool = False,
    ) -> tuple[dict[str, object], bool]:
        ...

    @abstractmethod
    def suggest(self, error_text: str, limit: int) -> list[tuple[float, dict[str, object]]]:
        ...


class JsonFileStore(_StoreBase):
    def __init__(self, path: Path) -> None:
        self.path = path

    def load_store(self) -> dict[str, object]:
        if not self.path.exists():
            return default_store()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ErrorLearningError(f"Unable to parse JSON from {self.path}: {exc}") from exc

        if isinstance(raw, list):
            entries = [validate_entry(item) for item in raw]
            return {"schema_version": SCHEMA_VERSION, "entries": entries}

        if not isinstance(raw, dict):
            raise ErrorLearningError("Error log must contain a JSON object or list of entries.")

        raw_entries = raw.get("entries", [])
        if not isinstance(raw_entries, list):
            raise ErrorLearningError("Error log field 'entries' must be a JSON array.")

        return {
            "schema_version": int(raw.get("schema_version", SCHEMA_VERSION)),
            "entries": [validate_entry(item) for item in raw_entries],
        }

    def save_store(self, store: dict[str, object]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(store, indent=2) + "\n", encoding="utf-8")

    def load_entries(self) -> list[dict[str, object]]:
        store = self.load_store()
        entries = store["entries"]
        assert isinstance(entries, list)
        return [validate_entry(e) for e in entries]

    def add(
        self,
        category: str,
        error: str,
        lesson: str,
        *,
        resolved: bool = True,
        infer_category: bool = False,
    ) -> tuple[dict[str, object], bool]:
        with _JSON_LOCK:
            store = self.load_store()
            eff_category = infer_error_type(error) if infer_category else category
            new_entry = build_entry(eff_category, error, lesson, resolved=resolved)
            entries = store["entries"]
            assert isinstance(entries, list)
            for entry in entries:
                validated = validate_entry(entry)
                if entries_match(validated, new_entry):
                    return validated, False

            entries.append(new_entry)
            entries.sort(key=lambda item: str(item["timestamp"]), reverse=True)
            self.save_store(store)
            return new_entry, True

    def suggest(self, error_text: str, limit: int) -> list[tuple[float, dict[str, object]]]:
        entries = self.load_entries()
        return suggest_fixes(entries, error_text, limit=limit)


_SQLITE_DDL = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS entries (
    id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    category TEXT NOT NULL,
    error TEXT NOT NULL,
    lesson TEXT NOT NULL,
    resolved INTEGER NOT NULL,
    error_type TEXT NOT NULL,
    error_fp TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_entries_error_type ON entries(error_type);
CREATE INDEX IF NOT EXISTS idx_entries_fp ON entries(error_fp);
CREATE INDEX IF NOT EXISTS idx_entries_ts ON entries(timestamp DESC);
"""


class SqliteStore(_StoreBase):
    def __init__(self, path: Path) -> None:
        self.path = path

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=30.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.executescript(_SQLITE_DDL)
        return conn

    def load_entries(self) -> list[dict[str, object]]:
        with _SQLITE_LOCK, self._connect() as conn:
            rows = conn.execute(
                "SELECT id, timestamp, category, error, lesson, resolved FROM entries ORDER BY timestamp DESC"
            ).fetchall()
        return [
            {
                "id": str(r["id"]),
                "timestamp": str(r["timestamp"]),
                "category": str(r["category"]),
                "error": str(r["error"]),
                "lesson": str(r["lesson"]),
                "resolved": bool(r["resolved"]),
            }
            for r in rows
        ]

    def add(
        self,
        category: str,
        error: str,
        lesson: str,
        *,
        resolved: bool = True,
        infer_category: bool = False,
    ) -> tuple[dict[str, object], bool]:
        eff_category = infer_error_type(error) if infer_category else category
        new_entry = build_entry(eff_category, error, lesson, resolved=resolved)
        et = infer_error_type(error)
        fp = fingerprint_error(error)

        with _SQLITE_LOCK, self._connect() as conn:
            row = conn.execute(
                "SELECT id, timestamp, category, error, lesson, resolved FROM entries WHERE id = ?",
                (new_entry["id"],),
            ).fetchone()
            if row is not None:
                return validate_entry(
                    {
                        "id": row["id"],
                        "timestamp": row["timestamp"],
                        "category": row["category"],
                        "error": row["error"],
                        "lesson": row["lesson"],
                        "resolved": bool(row["resolved"]),
                    }
                ), False

            for r in conn.execute("SELECT id, timestamp, category, error, lesson, resolved FROM entries").fetchall():
                ex: dict[str, object] = {
                    "id": r["id"],
                    "timestamp": r["timestamp"],
                    "category": r["category"],
                    "error": r["error"],
                    "lesson": r["lesson"],
                    "resolved": bool(r["resolved"]),
                }
                if entries_match(ex, new_entry):
                    return validate_entry(ex), False

            conn.execute(
                """INSERT INTO entries (id, timestamp, category, error, lesson, resolved, error_type, error_fp)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    new_entry["id"],
                    new_entry["timestamp"],
                    new_entry["category"],
                    new_entry["error"],
                    new_entry["lesson"],
                    1 if resolved else 0,
                    et,
                    fp,
                ),
            )
        return new_entry, True

    def suggest(self, error_text: str, limit: int) -> list[tuple[float, dict[str, object]]]:
        et = infer_error_type(error_text)
        with _SQLITE_LOCK, self._connect() as conn:
            if et == "unknown":
                rows = conn.execute(
                    "SELECT id, timestamp, category, error, lesson, resolved FROM entries ORDER BY timestamp DESC LIMIT 500"
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT id, timestamp, category, error, lesson, resolved FROM entries
                       WHERE error_type = ? OR error_type = 'unknown'
                       ORDER BY timestamp DESC LIMIT 500""",
                    (et,),
                ).fetchall()

        entries = [
            validate_entry(
                {
                    "id": r["id"],
                    "timestamp": r["timestamp"],
                    "category": r["category"],
                    "error": r["error"],
                    "lesson": r["lesson"],
                    "resolved": bool(r["resolved"]),
                }
            )
            for r in rows
        ]
        return suggest_fixes(entries, error_text, limit=limit, min_score=0.25)

    def export_json(self) -> dict[str, object]:
        entries = self.load_entries()
        return {"schema_version": SCHEMA_VERSION, "entries": entries}

    def stats(self) -> tuple[Counter[str], Counter[str]]:
        with _SQLITE_LOCK, self._connect() as conn:
            cat_rows = conn.execute(
                "SELECT category, COUNT(*) as c FROM entries GROUP BY category ORDER BY c DESC"
            ).fetchall()
            type_rows = conn.execute(
                "SELECT error_type, COUNT(*) as c FROM entries GROUP BY error_type ORDER BY c DESC"
            ).fetchall()
        return (
            Counter({str(r["category"]): int(r["c"]) for r in cat_rows}),
            Counter({str(r["error_type"]): int(r["c"]) for r in type_rows}),
        )

    def top_patterns(self, limit: int = 10) -> list[tuple[str, int, str]]:
        """Most common error fingerprints with best-known lesson (by recency)."""
        with _SQLITE_LOCK, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT e.error_fp, COUNT(*) as cnt,
                       (SELECT lesson FROM entries e2 WHERE e2.error_fp = e.error_fp
                        ORDER BY e2.timestamp DESC LIMIT 1) as top_lesson
                FROM entries e
                GROUP BY e.error_fp
                ORDER BY cnt DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [(str(r["error_fp"]), int(r["cnt"]), str(r["top_lesson"])) for r in rows]


def get_store(path: Path) -> _StoreBase:
    if _is_json_path(path):
        return JsonFileStore(path)
    return SqliteStore(path)


def load_store(log_path: Path) -> dict[str, object]:
    """Load full document (JSON layout); for SQLite, synthesizes from DB."""
    store_backend = get_store(log_path)
    if isinstance(store_backend, JsonFileStore):
        return store_backend.load_store()
    assert isinstance(store_backend, SqliteStore)
    return store_backend.export_json()


def save_store(log_path: Path, store: dict[str, object]) -> None:
    if not _is_json_path(log_path):
        raise ErrorLearningError("save_store is only supported for JSON log paths.")
    JsonFileStore(log_path).save_store(store)


def add_entry(
    log_path: Path,
    category: str,
    error: str,
    lesson: str,
    *,
    resolved: bool = True,
    infer_category: bool = False,
) -> tuple[dict[str, object], bool]:
    return get_store(log_path).add(category, error, lesson, resolved=resolved, infer_category=infer_category)


def format_entry(entry: dict[str, object]) -> str:
    category = str(entry["category"])
    resolved = bool(entry["resolved"])
    status_text = "resolved" if resolved else "open"
    status_color = "green" if resolved else "yellow"
    et = infer_error_type(str(entry["error"]))
    type_hint = f"  {colorize('Inferred type:', 'cyan')} {et}\n" if et != "unknown" else ""
    lines = [
        (
            f"{colorize(category, category_color(category))} "
            f"{colorize(f'[{status_text}]', status_color)} "
            f"{colorize(str(entry['timestamp']), 'cyan')}"
        ),
        f"  {colorize('ID:', 'yellow')} {entry['id']}",
        f"  {colorize('Error:', 'red')} {entry['error']}",
        type_hint.rstrip("\n"),
        f"  {colorize('Lesson:', 'green')} {entry['lesson']}",
    ]
    return "\n".join(line for line in lines if line)


def print_entries(entries: list[dict[str, object]], *, heading: str) -> None:
    print(colorize(heading, "bold"))
    print(colorize("=" * len(heading), "cyan"))
    if not entries:
        print(colorize("No entries found.", "yellow"))
        return

    for index, entry in enumerate(entries):
        if index:
            print()
        print(format_entry(entry))


def print_stats(entries: list[dict[str, object]], store: _StoreBase | None = None) -> None:
    print(colorize("Error Learning Stats", "bold"))
    print(colorize("====================", "cyan"))
    if isinstance(store, SqliteStore):
        cat_counts, type_counts = store.stats()
        if not cat_counts:
            print(colorize("No entries found.", "yellow"))
            return
        total = sum(cat_counts.values())
        print(colorize("By category (user label):", "bold"))
        for category, count in sorted(cat_counts.items(), key=lambda item: (-item[1], item[0].lower())):
            share = (count / total) * 100
            print(
                f"- {colorize(category, category_color(category))}: "
                f"{colorize(str(count), 'red')} ({share:.1f}%)"
            )
        print()
        print(colorize("By inferred error type:", "bold"))
        for etype, count in sorted(type_counts.items(), key=lambda item: (-item[1], item[0].lower())):
            share = (count / total) * 100
            print(f"- {etype}: {count} ({share:.1f}%)")
        return

    if not entries:
        print(colorize("No entries found.", "yellow"))
        return

    counts = Counter(str(entry["category"]) for entry in entries)
    total = len(entries)
    for category, count in sorted(counts.items(), key=lambda item: (-item[1], item[0].lower())):
        share = (count / total) * 100
        print(
            f"- {colorize(category, category_color(category))}: "
            f"{colorize(str(count), 'red')} "
            f"({share:.1f}%)"
        )


def print_suggestions(pairs: list[tuple[float, dict[str, object]]], *, query: str) -> None:
    print(colorize(f"Suggested fixes (historical) for: {query}", "bold"))
    print(colorize("=" * 60, "cyan"))
    if not pairs:
        print(colorize("No strong historical matches.", "yellow"))
        return
    for rank, (score, entry) in enumerate(pairs, start=1):
        if rank > 1:
            print()
        print(colorize(f"#{rank} score={score:.2f}", "yellow"))
        print(format_entry(entry))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Track errors and solutions; auto-type and suggest fixes (JSON or SQLite)."
    )
    parser.add_argument(
        "--log-path",
        type=Path,
        default=DEFAULT_LOG_PATH,
        help="JSON file (.json) or SQLite database (.db). Default: .learnings/error_learnings.db",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    add_parser = subparsers.add_parser("add", help="Record an error and its lesson.")
    add_parser.add_argument("error_category", help="Category label (or overridden when --infer-category).")
    add_parser.add_argument("error_message", help="Error message or failure summary.")
    add_parser.add_argument("lesson_learned", help="What fixed it or what to do next.")
    resolved_group = add_parser.add_mutually_exclusive_group()
    resolved_group.add_argument("--resolved", dest="resolved", action="store_true", default=True)
    resolved_group.add_argument("--unresolved", dest="resolved", action="store_false")
    add_parser.add_argument(
        "--infer-category",
        action="store_true",
        help="Replace category with inferred error type from the error text.",
    )

    subparsers.add_parser("list", help="List entries (most recent first).")
    subparsers.add_parser("stats", help="Frequency by category (and by inferred type for SQLite).")

    search_parser = subparsers.add_parser("search", help="Keyword search over stored entries.")
    search_parser.add_argument("query", help="Search query.")
    search_parser.add_argument("--limit", type=int, default=10, help="Max results.")

    sug_parser = subparsers.add_parser(
        "suggest",
        help="Suggest fixes from history based on an error message (similarity + type).",
    )
    sug_parser.add_argument("error_message", help="New error text to match.")
    sug_parser.add_argument("--limit", type=int, default=5, help="Max suggestions.")

    export_parser = subparsers.add_parser(
        "export-json",
        help="Export SQLite store to a JSON document (ignored for JSON stores).",
    )
    export_parser.add_argument("output_path", type=Path, help="Destination .json path.")

    patterns_parser = subparsers.add_parser(
        "patterns",
        help="(SQLite) Show most recurring error fingerprints and latest lesson.",
    )
    patterns_parser.add_argument("--limit", type=int, default=10)

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    path: Path = args.log_path
    try:
        backend = get_store(path)
    except ErrorLearningError as exc:
        print(colorize(str(exc), "red"), file=sys.stderr)
        return 1

    if args.command == "add":
        try:
            entry, created = backend.add(
                args.error_category,
                args.error_message,
                args.lesson_learned,
                resolved=args.resolved,
                infer_category=args.infer_category,
            )
        except ErrorLearningError as exc:
            print(colorize(str(exc), "red"), file=sys.stderr)
            return 1

        if created:
            print(colorize("Saved error learning entry.", "green"))
        else:
            print(colorize("Duplicate entry detected; existing learning kept.", "yellow"))
        print(format_entry(entry))
        return 0

    if args.command == "export-json":
        if isinstance(backend, JsonFileStore):
            out = backend.load_store()
            args.output_path.parent.mkdir(parents=True, exist_ok=True)
            args.output_path.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
            print(colorize(f"Wrote {args.output_path}", "green"))
            return 0
        assert isinstance(backend, SqliteStore)
        out = backend.export_json()
        args.output_path.parent.mkdir(parents=True, exist_ok=True)
        args.output_path.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
        print(colorize(f"Exported {len(out['entries'])} entries to {args.output_path}", "green"))
        return 0

    if args.command == "patterns":
        if not isinstance(backend, SqliteStore):
            print(colorize("patterns is only available for SQLite log paths.", "yellow"))
            return 1
        rows = backend.top_patterns(limit=max(args.limit, 1))
        print(colorize("Top recurring error patterns", "bold"))
        for fp, cnt, lesson in rows:
            print(f"- {colorize(fp, 'yellow')} x{cnt}")
            print(f"  {colorize('Lesson:', 'green')} {lesson}")
        return 0

    try:
        entries = backend.load_entries()
    except ErrorLearningError as exc:
        print(colorize(str(exc), "red"), file=sys.stderr)
        return 1

    if args.command == "list":
        print_entries(entries, heading="Error Learnings")
        return 0

    if args.command == "stats":
        print_stats(entries, store=backend)
        return 0

    if args.command == "search":
        matches = search_entries(entries, args.query, limit=max(args.limit, 1))
        print_entries(matches, heading=f"Search Results: {args.query}")
        return 0

    if args.command == "suggest":
        pairs = backend.suggest(args.error_message, limit=max(args.limit, 1))
        print_suggestions(pairs, query=args.error_message)
        return 0

    print(colorize(f"Unsupported command: {args.command}", "red"), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
