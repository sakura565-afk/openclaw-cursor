#!/usr/bin/env python3
"""Self-improvement error learning: capture failures, categorize them, and suggest fixes.

Supports JSON (default) and SQLite storage, heuristic error-type inference, and
pattern-based suggestions from historical lessons. Suitable for CLI use and
library import from automation or agents.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import threading
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_LOG_PATH = ROOT / ".learnings" / "error_log.json"
DEFAULT_SQLITE_PATH = ROOT / ".learnings" / "error_learnings.db"
SCHEMA_VERSION = 2

_JSON_IO_LOCK = threading.Lock()

ANSI: dict[str, str] = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "cyan": "\033[36m",
}
FALLBACK_CATEGORY_COLORS = ("red", "yellow", "green")

# Ordered: first match wins (most specific rules first).
_ERROR_TYPE_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "database",
        (
            "sqlite",
            "database is locked",
            "operationalerror",
            "deadlock",
            "connection to server",
            "could not connect",
            "postgresql",
            "mysql",
            "mongodb",
        ),
    ),
    (
        "network",
        (
            "connection refused",
            "econnrefused",
            "econnreset",
            "connection reset",
            "network unreachable",
            "name or service not known",
            "getaddrinfo",
            "ssl handshake",
            "certificate verify failed",
            "timed out",
            "timeout",
            "read timed out",
            "connect timeout",
        ),
    ),
    (
        "authentication",
        (
            "401",
            "403",
            "unauthorized",
            "forbidden",
            "invalid token",
            "authentication failed",
            "jwt",
            "oauth",
            "api key",
            "credential",
            "permission denied (publickey",
        ),
    ),
    (
        "rate_limit",
        ("429", "rate limit", "too many requests", "throttl", "quota exceeded"),
    ),
    (
        "parse",
        (
            "json decode",
            "jsondecodeerror",
            "yaml",
            "parse error",
            "unexpected token",
            "invalid syntax",
            "unterminated string",
            "expected ',' or '}'",
        ),
    ),
    (
        "filesystem",
        (
            "file not found",
            "no such file",
            "errno 2",
            "not a directory",
            "is a directory",
        ),
    ),
    (
        "permission",
        ("permission denied", "errno 13", "access is denied", "operation not permitted"),
    ),
    (
        "memory",
        ("memoryerror", "out of memory", "cannot allocate", "killed process", "oom"),
    ),
    (
        "subprocess",
        ("exit status", "exit code", "non-zero exit", "command failed", "calledprocesserror"),
    ),
    (
        "dependency",
        (
            "modulenotfounderror",
            "importerror",
            "no module named",
            "cannot find package",
        ),
    ),
    (
        "type_contract",
        (
            "typeerror",
            "attributeerror",
            "unsupported operand",
            "validation error",
            "assertionerror",
        ),
    ),
)


class ErrorLearningError(RuntimeError):
    """Raised when persisted learnings cannot be read, written, or validated."""


def colorize(text: str, color: str) -> str:
    if os.environ.get("NO_COLOR"):
        return text
    prefix = ANSI.get(color, "")
    if not prefix:
        return text
    return f"{prefix}{text}{ANSI['reset']}"


def normalize_text(text: str) -> str:
    return " ".join(text.strip().lower().split())


def infer_error_type(message: str, stack: str | None = None) -> str:
    """Heuristic error family from message (and optional stack) text."""

    blob = normalize_text(message)
    if stack:
        blob = f"{blob} {normalize_text(stack)}"
    for label, needles in _ERROR_TYPE_RULES:
        if any(n in blob for n in needles):
            return label
    return "general"


def category_color(category: str) -> str:
    normalized = normalize_text(category)
    if any(token in normalized for token in ("lesson", "resolved", "fix", "success")):
        return "green"
    if any(token in normalized for token in ("warn", "warning", "caution")):
        return "yellow"
    if any(
        token in normalized
        for token in ("error", "failure", "fatal", "exception", "crash", "bug")
    ):
        return "red"
    digest = hashlib.sha1(normalized.encode("utf-8")).digest()[0]
    return FALLBACK_CATEGORY_COLORS[digest % len(FALLBACK_CATEGORY_COLORS)]


def canonical_payload(
    category: str, error: str, lesson: str, resolved: bool
) -> dict[str, object]:
    return {
        "category": normalize_text(category),
        "error": normalize_text(error),
        "lesson": normalize_text(lesson),
        "resolved": bool(resolved),
    }


def _payload_hash(payload: Mapping[str, object]) -> str:
    return hashlib.sha1(
        json.dumps(dict(payload), sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:12]


def build_entry(
    category: str,
    error: str,
    lesson: str,
    *,
    resolved: bool = True,
    timestamp: str | None = None,
    error_type: str | None = None,
) -> dict[str, object]:
    payload = canonical_payload(category, error, lesson, resolved)
    digest = _payload_hash(payload)
    created_at = (
        timestamp
        or datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    )
    et = error_type or infer_error_type(error)
    return {
        "id": digest,
        "timestamp": created_at,
        "category": category.strip(),
        "error_type": et,
        "error": error.strip(),
        "lesson": lesson.strip(),
        "resolved": bool(resolved),
    }


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

    et = entry.get("error_type")
    if et is None or (isinstance(et, str) and not et.strip()):
        entry["error_type"] = infer_error_type(str(entry["error"]))
    elif not isinstance(et, str):
        raise ErrorLearningError("Entry field 'error_type' must be a string when present.")
    else:
        entry["error_type"] = et.strip()

    if not isinstance(entry.get("id"), str) or not str(entry["id"]).strip():
        entry["id"] = build_entry(
            entry["category"],
            entry["error"],
            entry["lesson"],
            resolved=resolved,
            timestamp=str(entry["timestamp"]),
            error_type=str(entry["error_type"]),
        )["id"]
    entry["resolved"] = resolved
    return entry


def default_store() -> dict[str, object]:
    return {"schema_version": SCHEMA_VERSION, "entries": []}


def _is_sqlite_path(path: Path) -> bool:
    return path.suffix.lower() in {".db", ".sqlite", ".sqlite3"}


def _sqlite_connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def _sqlite_ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS learnings (
            id TEXT PRIMARY KEY,
            timestamp TEXT NOT NULL,
            category TEXT NOT NULL,
            error_type TEXT NOT NULL,
            error TEXT NOT NULL,
            lesson TEXT NOT NULL,
            resolved INTEGER NOT NULL,
            payload_hash TEXT NOT NULL UNIQUE
        );
        CREATE INDEX IF NOT EXISTS idx_learnings_error_type ON learnings(error_type);
        CREATE INDEX IF NOT EXISTS idx_learnings_timestamp ON learnings(timestamp DESC);
        CREATE INDEX IF NOT EXISTS idx_learnings_category ON learnings(category);
        """
    )


@contextmanager
def _sqlite_tx(path: Path) -> Iterator[sqlite3.Connection]:
    conn = _sqlite_connect(path)
    try:
        _sqlite_ensure_schema(conn)
        conn.execute("BEGIN IMMEDIATE;")
        yield conn
        conn.execute("COMMIT;")
    except Exception:
        conn.execute("ROLLBACK;")
        raise
    finally:
        conn.close()


def _row_to_entry(row: sqlite3.Row) -> dict[str, object]:
    return {
        "id": str(row["id"]),
        "timestamp": str(row["timestamp"]),
        "category": str(row["category"]),
        "error_type": str(row["error_type"]),
        "error": str(row["error"]),
        "lesson": str(row["lesson"]),
        "resolved": bool(row["resolved"]),
    }


def load_store(log_path: Path) -> dict[str, object]:
    if _is_sqlite_path(log_path):
        if not log_path.exists():
            return default_store()
        conn = _sqlite_connect(log_path)
        try:
            _sqlite_ensure_schema(conn)
            rows = conn.execute(
                "SELECT id, timestamp, category, error_type, error, lesson, resolved "
                "FROM learnings ORDER BY timestamp DESC"
            ).fetchall()
        finally:
            conn.close()
        entries = [validate_entry(_row_to_entry(r)) for r in rows]
        return {"schema_version": SCHEMA_VERSION, "entries": entries}

    if not log_path.exists():
        return default_store()

    try:
        raw = json.loads(log_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ErrorLearningError(f"Unable to parse JSON from {log_path}: {exc}") from exc

    if isinstance(raw, list):
        entries = [validate_entry(item) for item in raw]
        return {"schema_version": SCHEMA_VERSION, "entries": entries}

    if not isinstance(raw, dict):
        raise ErrorLearningError("Error log must contain a JSON object or list of entries.")

    raw_entries = raw.get("entries", [])
    if not isinstance(raw_entries, list):
        raise ErrorLearningError("Error log field 'entries' must be a JSON array.")

    version = int(raw.get("schema_version", 1))
    return {
        "schema_version": max(version, SCHEMA_VERSION),
        "entries": [validate_entry(item) for item in raw_entries],
    }


def save_store(log_path: Path, store: dict[str, object]) -> None:
    if _is_sqlite_path(log_path):
        raise ErrorLearningError(
            "save_store() applies to JSON files; use add_entry() for SQLite updates."
        )
    entries = store.get("entries", [])
    if not isinstance(entries, list):
        raise ErrorLearningError("Store 'entries' must be a list.")
    payload = {
        "schema_version": int(store.get("schema_version", SCHEMA_VERSION)),
        "entries": entries,
    }
    log_path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2) + "\n"
    with _JSON_IO_LOCK:
        log_path.write_text(text, encoding="utf-8")


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


def _sqlite_add_entry(
    log_path: Path,
    category: str,
    error: str,
    lesson: str,
    *,
    resolved: bool = True,
) -> tuple[dict[str, object], bool]:
    new_entry = build_entry(category, error, lesson, resolved=resolved)
    payload = canonical_payload(category, error, lesson, resolved)
    phash = _payload_hash(payload)
    with _sqlite_tx(log_path) as conn:
        cur = conn.execute("SELECT 1 FROM learnings WHERE payload_hash = ?", (phash,))
        if cur.fetchone():
            row = conn.execute(
                "SELECT id, timestamp, category, error_type, error, lesson, resolved "
                "FROM learnings WHERE payload_hash = ?",
                (phash,),
            ).fetchone()
            assert row is not None
            return validate_entry(_row_to_entry(row)), False
        conn.execute(
            "INSERT INTO learnings (id, timestamp, category, error_type, error, lesson, resolved, payload_hash) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                new_entry["id"],
                new_entry["timestamp"],
                new_entry["category"],
                new_entry["error_type"],
                new_entry["error"],
                new_entry["lesson"],
                1 if resolved else 0,
                phash,
            ),
        )
    return validate_entry(new_entry), True


def add_entry(
    log_path: Path,
    category: str,
    error: str,
    lesson: str,
    *,
    resolved: bool = True,
) -> tuple[dict[str, object], bool]:
    if _is_sqlite_path(log_path):
        return _sqlite_add_entry(log_path, category, error, lesson, resolved=resolved)

    store = load_store(log_path)
    new_entry = build_entry(category, error, lesson, resolved=resolved)
    entries = store["entries"]
    assert isinstance(entries, list)
    for entry in entries:
        validated = validate_entry(entry)
        if entries_match(validated, new_entry):
            return validated, False

    entries.append(new_entry)
    entries.sort(key=lambda item: str(item["timestamp"]), reverse=True)
    save_store(log_path, store)
    return validate_entry(new_entry), True


def format_entry(entry: dict[str, object]) -> str:
    category = str(entry["category"])
    et = str(entry.get("error_type", infer_error_type(str(entry["error"]))))
    resolved = bool(entry["resolved"])
    status_text = "resolved" if resolved else "open"
    status_color = "green" if resolved else "yellow"
    lines = [
        (
            f"{colorize(category, category_color(category))} "
            f"{colorize(f'[{status_text}]', status_color)} "
            f"{colorize(str(entry['timestamp']), 'cyan')}"
        ),
        f"  {colorize('Type:', 'cyan')} {colorize(et, 'yellow')}",
        f"  {colorize('ID:', 'yellow')} {entry['id']}",
        f"  {colorize('Error:', 'red')} {entry['error']}",
        f"  {colorize('Lesson:', 'green')} {entry['lesson']}",
    ]
    return "\n".join(lines)


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


def print_stats(entries: list[dict[str, object]]) -> None:
    print(colorize("Error Learning Stats", "bold"))
    print(colorize("====================", "cyan"))
    if not entries:
        print(colorize("No entries found.", "yellow"))
        return

    validated = [validate_entry(e) for e in entries]
    by_cat = Counter(str(e["category"]) for e in validated)
    by_type = Counter(str(e["error_type"]) for e in validated)
    total = len(validated)
    print(colorize("By category", "bold"))
    for category, count in sorted(by_cat.items(), key=lambda item: (-item[1], item[0].lower())):
        share = (count / total) * 100
        print(
            f"- {colorize(category, category_color(category))}: "
            f"{colorize(str(count), 'red')} ({share:.1f}%)"
        )
    print()
    print(colorize("By inferred error type", "bold"))
    for et, count in sorted(by_type.items(), key=lambda item: (-item[1], item[0].lower())):
        share = (count / total) * 100
        print(f"- {colorize(et, 'yellow')}: {colorize(str(count), 'red')} ({share:.1f}%)")


def search_score(query: str, entry: dict[str, object]) -> float:
    normalized_query = normalize_text(query)
    et = str(entry.get("error_type", ""))
    haystack = normalize_text(
        " ".join((str(entry["category"]), et, str(entry["error"]), str(entry["lesson"])))
    )
    if not normalized_query:
        return 0.0

    substring_bonus = 1.5 if normalized_query in haystack else 0.0
    query_tokens = set(normalized_query.split())
    haystack_tokens = set(haystack.split())
    overlap = len(query_tokens & haystack_tokens) / max(len(query_tokens), 1)
    ratio = SequenceMatcher(None, normalized_query, haystack).ratio()
    type_bonus = 0.25 if normalized_query in et else 0.0
    return substring_bonus + overlap + (ratio * 0.5) + type_bonus


def search_entries(
    entries: list[dict[str, object]], query: str, limit: int = 10
) -> list[dict[str, object]]:
    ranked: list[tuple[float, dict[str, object]]] = []
    for entry in entries:
        score = search_score(query, entry)
        if score >= 0.45:
            ranked.append((score, entry))
    ranked.sort(key=lambda item: (-item[0], str(item[1]["timestamp"])), reverse=False)
    return [validate_entry(entry) for _, entry in ranked[:limit]]


@dataclass(frozen=True, slots=True)
class FixSuggestion:
    """Structured suggestion derived from past learnings."""

    lesson: str
    score: float
    error_type: str
    category: str
    source_error: str
    entry_id: str
    resolved: bool


def suggest_fixes(
    error_text: str,
    entries: Sequence[dict[str, object]],
    *,
    limit: int = 8,
    min_score: float = 0.35,
) -> list[FixSuggestion]:
    """Rank historical lessons by textual similarity to the given error text."""

    ranked: list[tuple[float, dict[str, object]]] = []
    for entry in entries:
        ve = validate_entry(entry)
        score = search_score(error_text, ve)
        if score >= min_score:
            ranked.append((score, ve))
    ranked.sort(key=lambda item: (-item[0], str(item[1]["timestamp"])), reverse=False)
    out: list[FixSuggestion] = []
    for score, e in ranked[:limit]:
        out.append(
            FixSuggestion(
                lesson=str(e["lesson"]),
                score=score,
                error_type=str(e["error_type"]),
                category=str(e["category"]),
                source_error=str(e["error"]),
                entry_id=str(e["id"]),
                resolved=bool(e["resolved"]),
            )
        )
    return out


def export_sqlite_to_json(sqlite_path: Path, json_path: Path) -> int:
    """Dump all SQLite rows to a JSON document; returns number of entries written."""

    store = load_store(sqlite_path)
    save_store(json_path, store)
    entries = store.get("entries", [])
    if not isinstance(entries, list):
        return 0
    return len(entries)


def import_json_to_sqlite(json_path: Path, sqlite_path: Path) -> tuple[int, int]:
    """Merge JSON entries into SQLite. Returns (imported, skipped_duplicates)."""

    store = load_store(json_path)
    entries = store.get("entries", [])
    if not isinstance(entries, list):
        raise ErrorLearningError("JSON store has invalid 'entries'.")
    imported = 0
    skipped = 0
    for raw in entries:
        e = validate_entry(raw)
        _, created = add_entry(
            sqlite_path,
            str(e["category"]),
            str(e["error"]),
            str(e["lesson"]),
            resolved=bool(e["resolved"]),
        )
        if created:
            imported += 1
        else:
            skipped += 1
    return imported, skipped


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Capture recurring errors, categorize them, and suggest fixes from history."
    )
    parser.add_argument(
        "--log-path",
        type=Path,
        default=DEFAULT_LOG_PATH,
        help="JSON log path or SQLite database path (.db / .sqlite).",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    add_parser = subparsers.add_parser("add", help="Add a new error learning entry.")
    add_parser.add_argument("error_category", help="High-level category for the error.")
    add_parser.add_argument("error_message", help="Error message or failure summary.")
    add_parser.add_argument("lesson_learned", help="Lesson learned from the failure.")
    resolved_group = add_parser.add_mutually_exclusive_group()
    resolved_group.add_argument(
        "--resolved",
        dest="resolved",
        action="store_true",
        default=True,
        help="Mark the entry as resolved (default).",
    )
    resolved_group.add_argument(
        "--unresolved",
        dest="resolved",
        action="store_false",
        help="Mark the entry as still open.",
    )

    subparsers.add_parser("list", help="List all learned errors.")
    subparsers.add_parser("stats", help="Show error frequency by category and inferred type.")

    search_parser = subparsers.add_parser("search", help="Search for relevant past errors.")
    search_parser.add_argument("query", help="Search query.")
    search_parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum number of matching entries to print.",
    )

    suggest_parser = subparsers.add_parser(
        "suggest", help="Suggest fixes for an error message from historical patterns."
    )
    suggest_parser.add_argument("error_text", help="Current error text or summary.")
    suggest_parser.add_argument(
        "--limit",
        type=int,
        default=8,
        help="Maximum number of suggestions to print.",
    )

    export_parser = subparsers.add_parser(
        "export-json", help="Export SQLite learnings to a JSON file."
    )
    export_parser.add_argument(
        "json_out",
        type=Path,
        help="Destination JSON path.",
    )

    import_parser = subparsers.add_parser(
        "import-sqlite", help="Import learnings from JSON into a SQLite database."
    )
    import_parser.add_argument(
        "json_in",
        type=Path,
        help="Source JSON path.",
    )
    import_parser.add_argument(
        "--sqlite-out",
        type=Path,
        default=DEFAULT_SQLITE_PATH,
        help="SQLite database path to write (default: .learnings/error_learnings.db).",
    )

    return parser.parse_args(argv)


def _print_suggestions(suggestions: Iterable[FixSuggestion], *, heading: str) -> None:
    print(colorize(heading, "bold"))
    print(colorize("=" * len(heading), "cyan"))
    items = list(suggestions)
    if not items:
        print(colorize("No confident historical matches.", "yellow"))
        return
    for idx, s in enumerate(items):
        if idx:
            print()
        status = "resolved" if s.resolved else "open"
        print(
            f"{colorize(f'[{idx + 1}]', 'cyan')} "
            f"{colorize(f'score={s.score:.2f}', 'yellow')} "
            f"{colorize(f'type={s.error_type}', 'green')} "
            f"{colorize(f'[{status}]', 'green' if s.resolved else 'yellow')}"
        )
        print(f"  {colorize('Category:', 'yellow')} {s.category}")
        print(f"  {colorize('Prior error:', 'red')} {s.source_error}")
        print(f"  {colorize('Suggested fix:', 'green')} {s.lesson}")
        print(f"  {colorize('Entry ID:', 'cyan')} {s.entry_id}")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if args.command == "import-sqlite":
        try:
            imported, skipped = import_json_to_sqlite(args.json_in, args.sqlite_out)
        except ErrorLearningError as exc:
            print(colorize(str(exc), "red"), file=sys.stderr)
            return 1
        print(
            colorize(
                f"Imported {imported} new entries into {args.sqlite_out} "
                f"({skipped} duplicates skipped).",
                "green",
            )
        )
        return 0

    if args.command == "export-json":
        if not _is_sqlite_path(args.log_path):
            print(colorize("--log-path must be a SQLite .db for export-json.", "red"), file=sys.stderr)
            return 1
        try:
            count = export_sqlite_to_json(args.log_path, args.json_out)
        except ErrorLearningError as exc:
            print(colorize(str(exc), "red"), file=sys.stderr)
            return 1
        print(colorize(f"Exported {count} entries to {args.json_out}", "green"))
        return 0

    try:
        store = load_store(args.log_path)
    except ErrorLearningError as exc:
        print(colorize(str(exc), "red"), file=sys.stderr)
        return 1

    entries = store["entries"]
    assert isinstance(entries, list)

    if args.command == "add":
        try:
            entry, created = add_entry(
                args.log_path,
                args.error_category,
                args.error_message,
                args.lesson_learned,
                resolved=args.resolved,
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

    validated_entries = [validate_entry(entry) for entry in entries]

    if args.command == "list":
        print_entries(validated_entries, heading="Error Learnings")
        return 0

    if args.command == "stats":
        print_stats(validated_entries)
        return 0

    if args.command == "search":
        matches = search_entries(validated_entries, args.query, limit=max(args.limit, 1))
        print_entries(matches, heading=f"Search Results: {args.query}")
        return 0

    if args.command == "suggest":
        suggestions = suggest_fixes(
            args.error_text,
            validated_entries,
            limit=max(args.limit, 1),
        )
        _print_suggestions(suggestions, heading="Suggested fixes (from history)")
        return 0

    print(colorize(f"Unsupported command: {args.command}", "red"), file=sys.stderr)
    return 1


__all__ = [
    "DEFAULT_LOG_PATH",
    "DEFAULT_SQLITE_PATH",
    "SCHEMA_VERSION",
    "ErrorLearningError",
    "FixSuggestion",
    "add_entry",
    "build_entry",
    "canonical_payload",
    "category_color",
    "colorize",
    "entries_match",
    "export_sqlite_to_json",
    "format_entry",
    "import_json_to_sqlite",
    "infer_error_type",
    "load_store",
    "main",
    "normalize_text",
    "parse_args",
    "print_entries",
    "print_stats",
    "save_store",
    "search_entries",
    "search_score",
    "suggest_fixes",
    "validate_entry",
]


if __name__ == "__main__":
    raise SystemExit(main())
