#!/usr/bin/env python3
"""Capture and learn from recurring OpenClaw session errors."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Iterator


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOG_PATH = ROOT / ".learnings" / "error_log.json"
SCHEMA_VERSION = 1
ANSI = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "cyan": "\033[36m",
}
FALLBACK_CATEGORY_COLORS = ("red", "yellow", "green")

# Set True when the CLI passes --no-color (also honors NO_COLOR in colorize).
_COLORS_DISABLED = False


class ErrorLearningError(RuntimeError):
    """Raised when the error learning log cannot be read or written."""


def colorize(text: str, color: str) -> str:
    """Wrap text in ANSI color codes unless the user disabled them."""

    if _COLORS_DISABLED or os.environ.get("NO_COLOR"):
        return text
    prefix = ANSI.get(color, "")
    if not prefix:
        return text
    return f"{prefix}{text}{ANSI['reset']}"


def normalize_text(text: str) -> str:
    """Normalize free-form text for comparisons and search."""

    return " ".join(text.strip().lower().split())


def category_color(category: str) -> str:
    """Choose a stable display color for a category name."""

    normalized = normalize_text(category)
    if any(token in normalized for token in ("lesson", "resolved", "fix", "success")):
        return "green"
    if any(token in normalized for token in ("warn", "warning", "caution")):
        return "yellow"
    if any(token in normalized for token in ("error", "failure", "fatal", "exception", "crash", "bug")):
        return "red"
    digest = hashlib.sha1(normalized.encode("utf-8")).digest()[0]
    return FALLBACK_CATEGORY_COLORS[digest % len(FALLBACK_CATEGORY_COLORS)]


def canonical_payload(category: str, error: str, lesson: str, resolved: bool) -> dict[str, object]:
    """Return a normalized payload used for IDs and deduplication."""

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
    """Create a log entry that matches the JSON schema."""

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
    """Return an empty log document."""

    return {"schema_version": SCHEMA_VERSION, "entries": []}


def validate_entry(raw_entry: object) -> dict[str, object]:
    """Validate a single persisted entry and normalize minor omissions."""

    if not isinstance(raw_entry, dict):
        raise ErrorLearningError("Each entry in the error log must be a JSON object.")

    entry = dict(raw_entry)
    required_text_fields = ("timestamp", "category", "error", "lesson")
    for field in required_text_fields:
        value = entry.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ErrorLearningError(f"Entry field '{field}' must be a non-empty string.")
        entry[field] = value.strip()

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
    else:
        entry["id"] = str(entry["id"]).strip()
    entry["resolved"] = resolved
    return entry


def parse_schema_version(raw: object) -> int:
    """Parse persisted schema_version with validation."""

    if raw is None:
        return SCHEMA_VERSION
    if isinstance(raw, bool):
        raise ErrorLearningError("Error log field 'schema_version' must be an integer.")
    if isinstance(raw, int):
        if raw < 1:
            raise ErrorLearningError("Error log field 'schema_version' must be a positive integer.")
        return raw
    if isinstance(raw, str):
        stripped = raw.strip()
        if not stripped:
            return SCHEMA_VERSION
        try:
            value = int(stripped)
        except ValueError as exc:
            raise ErrorLearningError("Error log field 'schema_version' must be an integer.") from exc
        if value < 1:
            raise ErrorLearningError("Error log field 'schema_version' must be a positive integer.")
        return value
    raise ErrorLearningError("Error log field 'schema_version' must be an integer.")


@contextmanager
def _exclusive_store_lock(log_path: Path) -> Iterator[None]:
    """Serialize read-modify-write cycles across processes when fcntl is available."""

    lock_path = log_path.with_name(log_path.name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import fcntl  # type: ignore[attr-defined]
    except ImportError:
        yield
        return

    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def load_store(log_path: Path) -> dict[str, object]:
    """Load the persisted error log from disk."""

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

    return {
        "schema_version": parse_schema_version(raw.get("schema_version")),
        "entries": [validate_entry(item) for item in raw_entries],
    }


def save_store(log_path: Path, store: dict[str, object]) -> None:
    """Persist the error log to disk using an atomic replace to avoid truncated JSON."""

    log_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(store, indent=2) + "\n"
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{log_path.name}.",
        suffix=".tmp",
        dir=str(log_path.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, log_path)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def entries_match(left: dict[str, object], right: dict[str, object]) -> bool:
    """Return True when two entries are the same learning."""

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


def add_entry(
    log_path: Path,
    category: str,
    error: str,
    lesson: str,
    *,
    resolved: bool = True,
) -> tuple[dict[str, object], bool]:
    """Add an error learning entry unless it already exists."""

    with _exclusive_store_lock(log_path):
        store = load_store(log_path)
        new_entry = build_entry(category, error, lesson, resolved=resolved)
        entries = store["entries"]
        assert isinstance(entries, list)
        for entry in entries:
            if entries_match(entry, new_entry):
                return entry, False

        entries.append(new_entry)
        entries.sort(key=lambda item: str(item["timestamp"]), reverse=True)
        save_store(log_path, store)
        return new_entry, True


def remove_entry(log_path: Path, entry_id: str) -> tuple[dict[str, object] | None, bool]:
    """Remove an entry by id. Returns ``(removed_entry, removed)``."""

    token = entry_id.strip()
    if not token:
        raise ErrorLearningError("Entry id must be a non-empty string.")

    with _exclusive_store_lock(log_path):
        store = load_store(log_path)
        entries = store["entries"]
        assert isinstance(entries, list)
        for index, entry in enumerate(entries):
            if str(entry["id"]) == token:
                removed = dict(entry)
                del entries[index]
                save_store(log_path, store)
                return removed, True
        return None, False


def set_entry_resolved(
    log_path: Path,
    entry_id: str,
    *,
    resolved: bool,
) -> tuple[dict[str, object] | None, bool]:
    """Update the resolved flag for an entry. Returns ``(entry, changed)``."""

    token = entry_id.strip()
    if not token:
        raise ErrorLearningError("Entry id must be a non-empty string.")

    with _exclusive_store_lock(log_path):
        store = load_store(log_path)
        entries = store["entries"]
        assert isinstance(entries, list)
        for index, entry in enumerate(entries):
            if str(entry["id"]) != token:
                continue
            if bool(entry["resolved"]) == resolved:
                return entry, False
            updated = build_entry(
                str(entry["category"]),
                str(entry["error"]),
                str(entry["lesson"]),
                resolved=resolved,
                timestamp=str(entry["timestamp"]),
            )
            updated["id"] = str(entry["id"])
            entries[index] = updated
            entries.sort(key=lambda item: str(item["timestamp"]), reverse=True)
            save_store(log_path, store)
            return updated, True
        return None, False


def format_entry(entry: dict[str, object]) -> str:
    """Render a single entry for console output."""

    category = str(entry["category"])
    resolved = bool(entry["resolved"])
    status_text = "resolved" if resolved else "open"
    status_color = "green" if resolved else "yellow"
    lines = [
        (
            f"{colorize(category, category_color(category))} "
            f"{colorize(f'[{status_text}]', status_color)} "
            f"{colorize(str(entry['timestamp']), 'cyan')}"
        ),
        f"  {colorize('ID:', 'yellow')} {entry['id']}",
        f"  {colorize('Error:', 'red')} {entry['error']}",
        f"  {colorize('Lesson:', 'green')} {entry['lesson']}",
    ]
    return "\n".join(lines)


def print_entries(entries: list[dict[str, object]], *, heading: str) -> None:
    """Print a collection of entries in a human-readable layout."""

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
    """Print category-level frequency stats."""

    print(colorize("Error Learning Stats", "bold"))
    print(colorize("====================", "cyan"))
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


def search_score(query: str, entry: dict[str, object]) -> float:
    """Score how relevant an entry is to a search query."""

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
    """Return the most relevant matching entries for the given query."""

    cap = max(limit, 1)
    if not normalize_text(query):
        newest_first = sorted(entries, key=lambda item: str(item["timestamp"]), reverse=True)
        return newest_first[:cap]

    ranked: list[tuple[float, dict[str, object]]] = []
    for entry in entries:
        score = search_score(query, entry)
        if score >= 0.45:
            ranked.append((score, entry))
    ranked.sort(key=lambda item: (-item[0], str(item[1]["timestamp"])), reverse=False)
    return [entry for _, entry in ranked[:cap]]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""

    parser = argparse.ArgumentParser(description="Capture and learn from OpenClaw errors.")
    parser.add_argument(
        "--log-path",
        type=Path,
        default=DEFAULT_LOG_PATH,
        help="Path to the error learning JSON log.",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable ANSI colors (in addition to honoring NO_COLOR).",
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

    list_parser = subparsers.add_parser("list", help="List all learned errors.")
    list_parser.add_argument(
        "--json",
        action="store_true",
        help="Print schema_version and entries as JSON (no colors).",
    )

    subparsers.add_parser("stats", help="Show error frequency by category.")

    search_parser = subparsers.add_parser("search", help="Search for relevant past errors.")
    search_parser.add_argument("query", help="Search query.")
    search_parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum number of matching entries to print.",
    )
    search_parser.add_argument(
        "--json",
        action="store_true",
        help="Print matching entries as JSON (no colors).",
    )

    remove_parser = subparsers.add_parser("remove", help="Remove an entry by id.")
    remove_parser.add_argument("entry_id", help="Entry id to delete.")

    set_resolved_parser = subparsers.add_parser(
        "set-resolved",
        help="Mark an entry as resolved or still open.",
    )
    set_resolved_parser.add_argument("entry_id", help="Entry id to update.")
    sr_group = set_resolved_parser.add_mutually_exclusive_group()
    sr_group.add_argument(
        "--resolved",
        dest="resolved",
        action="store_true",
        default=True,
        help="Mark the entry as resolved (default).",
    )
    sr_group.add_argument(
        "--unresolved",
        dest="resolved",
        action="store_false",
        help="Mark the entry as still open.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the error learning CLI."""

    global _COLORS_DISABLED

    args = parse_args(argv)
    _COLORS_DISABLED = bool(args.no_color)

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

    if args.command == "remove":
        try:
            removed, did_remove = remove_entry(args.log_path, args.entry_id)
        except ErrorLearningError as exc:
            print(colorize(str(exc), "red"), file=sys.stderr)
            return 1
        if not did_remove or removed is None:
            print(colorize(f"No entry found with id {args.entry_id!r}.", "red"), file=sys.stderr)
            return 1
        print(colorize("Removed error learning entry.", "green"))
        print(format_entry(removed))
        return 0

    if args.command == "set-resolved":
        try:
            entry, changed = set_entry_resolved(
                args.log_path,
                args.entry_id,
                resolved=args.resolved,
            )
        except ErrorLearningError as exc:
            print(colorize(str(exc), "red"), file=sys.stderr)
            return 1
        if entry is None:
            print(colorize(f"No entry found with id {args.entry_id!r}.", "red"), file=sys.stderr)
            return 1
        if changed:
            print(colorize("Updated entry status.", "green"))
        else:
            print(colorize("Entry status unchanged.", "yellow"))
        print(format_entry(entry))
        return 0

    validated_entries = [validate_entry(entry) for entry in entries]

    if args.command == "list":
        if args.json:
            print(
                json.dumps(
                    {"schema_version": store["schema_version"], "entries": validated_entries},
                    indent=2,
                )
            )
            return 0
        print_entries(validated_entries, heading="OpenClaw Error Learnings")
        return 0

    if args.command == "stats":
        print_stats(validated_entries)
        return 0

    if args.command == "search":
        matches = search_entries(validated_entries, args.query, limit=max(args.limit, 1))
        if args.json:
            print(
                json.dumps(
                    {
                        "query": args.query,
                        "limit": max(args.limit, 1),
                        "entries": matches,
                    },
                    indent=2,
                )
            )
            return 0
        print_entries(matches, heading=f"Search Results: {args.query}")
        return 0

    print(colorize(f"Unsupported command: {args.command}", "red"), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
