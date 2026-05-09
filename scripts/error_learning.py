#!/usr/bin/env python3
"""Track agent errors for learning: timestamp, type, message, context, suggested fix (JSON log)."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOG_PATH = ROOT / ".learnings" / "error_log.json"
SCHEMA_VERSION = 2
ANSI = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "cyan": "\033[36m",
}
FALLBACK_CATEGORY_COLORS = ("red", "yellow", "green")


class ErrorLearningError(RuntimeError):
    """Raised when the error learning log cannot be read or written."""


def colorize(text: str, color: str) -> str:
    """Wrap text in ANSI color codes unless the user disabled them."""

    if os.environ.get("NO_COLOR"):
        return text
    prefix = ANSI.get(color, "")
    if not prefix:
        return text
    return f"{prefix}{text}{ANSI['reset']}"


def normalize_text(text: str) -> str:
    """Normalize free-form text for comparisons and search."""

    return " ".join(text.strip().lower().split())


def category_color(error_type: str) -> str:
    """Choose a stable display color for an error type name."""

    normalized = normalize_text(error_type)
    if any(token in normalized for token in ("lesson", "resolved", "fix", "success")):
        return "green"
    if any(token in normalized for token in ("warn", "warning", "caution")):
        return "yellow"
    if any(token in normalized for token in ("error", "failure", "fatal", "exception", "crash", "bug")):
        return "red"
    digest = hashlib.sha1(normalized.encode("utf-8")).digest()[0]
    return FALLBACK_CATEGORY_COLORS[digest % len(FALLBACK_CATEGORY_COLORS)]


def _normalize_context(raw: Any) -> dict[str, Any]:
    """Ensure context is a JSON-serializable dict."""

    if raw is None:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    raise ErrorLearningError("context must be a JSON object or omitted.")


def canonical_payload(
    error_type: str,
    message: str,
    suggested_fix: str,
    resolved: bool,
    context: dict[str, Any],
) -> dict[str, object]:
    """Return a normalized payload used for IDs and deduplication."""

    ctx_norm = json.dumps(context, sort_keys=True, separators=(",", ":"))
    return {
        "error_type": normalize_text(error_type),
        "message": normalize_text(message),
        "suggested_fix": normalize_text(suggested_fix),
        "resolved": bool(resolved),
        "context": ctx_norm,
    }


def build_entry(
    error_type: str,
    message: str,
    suggested_fix: str,
    *,
    context: dict[str, Any] | None = None,
    resolved: bool = True,
    timestamp: str | None = None,
) -> dict[str, object]:
    """Create a log entry that matches the JSON schema."""

    ctx = _normalize_context(context)
    payload = canonical_payload(error_type, message, suggested_fix, resolved, ctx)
    digest = hashlib.sha1(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:12]
    created_at = timestamp or datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    return {
        "id": digest,
        "timestamp": created_at,
        "error_type": error_type.strip(),
        "message": message.strip(),
        "context": ctx,
        "suggested_fix": suggested_fix.strip(),
        "resolved": bool(resolved),
    }


def default_store() -> dict[str, object]:
    """Return an empty log document."""

    return {"schema_version": SCHEMA_VERSION, "entries": []}


def _migrate_legacy_entry(raw_entry: dict[str, object]) -> dict[str, object]:
    """Convert v1 fields (category, error, lesson) to v2."""

    if "error_type" in raw_entry and "message" in raw_entry:
        return raw_entry
    if "category" in raw_entry and "error" in raw_entry and "lesson" in raw_entry:
        return {
            "id": raw_entry.get("id"),
            "timestamp": raw_entry["timestamp"],
            "error_type": raw_entry["category"],
            "message": raw_entry["error"],
            "context": {},
            "suggested_fix": raw_entry["lesson"],
            "resolved": raw_entry.get("resolved", False),
        }
    return raw_entry


def validate_entry(raw_entry: object) -> dict[str, object]:
    """Validate a single persisted entry and normalize minor omissions."""

    if not isinstance(raw_entry, dict):
        raise ErrorLearningError("Each entry in the error log must be a JSON object.")

    entry = dict(raw_entry)
    entry = _migrate_legacy_entry(entry)

    for field in ("timestamp", "error_type", "message", "suggested_fix"):
        value = entry.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ErrorLearningError(f"Entry field '{field}' must be a non-empty string.")

    ctx = entry.get("context", {})
    entry["context"] = _normalize_context(ctx)

    resolved = entry.get("resolved", False)
    if not isinstance(resolved, bool):
        raise ErrorLearningError("Entry field 'resolved' must be a boolean.")

    if not isinstance(entry.get("id"), str) or not str(entry["id"]).strip():
        entry["id"] = build_entry(
            str(entry["error_type"]),
            str(entry["message"]),
            str(entry["suggested_fix"]),
            context=entry["context"],
            resolved=resolved,
            timestamp=str(entry["timestamp"]),
        )["id"]
    entry["resolved"] = resolved
    return entry


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

    version = int(raw.get("schema_version", 1))
    store = {
        "schema_version": SCHEMA_VERSION if version >= SCHEMA_VERSION else SCHEMA_VERSION,
        "entries": [validate_entry(item) for item in raw_entries],
    }
    return store


def save_store(log_path: Path, store: dict[str, object]) -> None:
    """Persist the error log to disk."""

    log_path.parent.mkdir(parents=True, exist_ok=True)
    out = {"schema_version": SCHEMA_VERSION, "entries": store["entries"]}
    log_path.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")


def entries_match(left: dict[str, object], right: dict[str, object]) -> bool:
    """Return True when two entries are the same learning."""

    if left.get("id") == right.get("id"):
        return True
    return canonical_payload(
        str(left["error_type"]),
        str(left["message"]),
        str(left["suggested_fix"]),
        bool(left["resolved"]),
        _normalize_context(left.get("context")),
    ) == canonical_payload(
        str(right["error_type"]),
        str(right["message"]),
        str(right["suggested_fix"]),
        bool(right["resolved"]),
        _normalize_context(right.get("context")),
    )


def add_entry(
    log_path: Path,
    error_type: str,
    message: str,
    suggested_fix: str,
    *,
    context: dict[str, Any] | None = None,
    resolved: bool = True,
) -> tuple[dict[str, object], bool]:
    """Add an error learning entry unless it already exists."""

    store = load_store(log_path)
    new_entry = build_entry(error_type, message, suggested_fix, context=context, resolved=resolved)
    entries = store["entries"]
    assert isinstance(entries, list)
    for entry in entries:
        validated = validate_entry(entry)
        if entries_match(validated, new_entry):
            return validated, False

    entries.append(new_entry)
    entries.sort(key=lambda item: str(item["timestamp"]), reverse=True)
    save_store(log_path, store)
    return new_entry, True


def format_entry(entry: dict[str, object]) -> str:
    """Render a single entry for console output."""

    et = str(entry["error_type"])
    resolved = bool(entry["resolved"])
    status_text = "resolved" if resolved else "open"
    status_color = "green" if resolved else "yellow"
    ctx = entry.get("context") or {}
    ctx_preview = json.dumps(ctx, ensure_ascii=False) if ctx else "{}"
    if len(ctx_preview) > 120:
        ctx_preview = ctx_preview[:117] + "..."
    lines = [
        (
            f"{colorize(et, category_color(et))} "
            f"{colorize(f'[{status_text}]', status_color)} "
            f"{colorize(str(entry['timestamp']), 'cyan')}"
        ),
        f"  {colorize('ID:', 'yellow')} {entry['id']}",
        f"  {colorize('Message:', 'red')} {entry['message']}",
        f"  {colorize('Context:', 'cyan')} {ctx_preview}",
        f"  {colorize('Suggested fix:', 'green')} {entry['suggested_fix']}",
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
    """Print error-type frequency stats."""

    print(colorize("Error Learning Stats", "bold"))
    print(colorize("====================", "cyan"))
    if not entries:
        print(colorize("No entries found.", "yellow"))
        return

    counts = Counter(str(entry["error_type"]) for entry in entries)
    total = len(entries)
    for error_type, count in sorted(counts.items(), key=lambda item: (-item[1], item[0].lower())):
        share = (count / total) * 100
        print(
            f"- {colorize(error_type, category_color(error_type))}: "
            f"{colorize(str(count), 'red')} "
            f"({share:.1f}%)"
        )


def search_score(query: str, entry: dict[str, object]) -> float:
    """Score how relevant an entry is to a search query."""

    normalized_query = normalize_text(query)
    ctx_str = normalize_text(json.dumps(entry.get("context") or {}, ensure_ascii=False))
    haystack = normalize_text(
        " ".join(
            (
                str(entry["error_type"]),
                str(entry["message"]),
                str(entry["suggested_fix"]),
                ctx_str,
            )
        )
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

    ranked: list[tuple[float, dict[str, object]]] = []
    for entry in entries:
        score = search_score(query, entry)
        if score >= 0.45:
            ranked.append((score, entry))
    ranked.sort(key=lambda item: (-item[0], str(item[1]["timestamp"])), reverse=False)
    return [entry for _, entry in ranked[:limit]]


def run_demo(*, log_path: Path | None = None) -> int:
    """
    Demo: record sample agent errors, list them, print stats (uses a temp file if log_path omitted).

    Intended for `python scripts/error_learning.py demo` or programmatic calls.
    """
    own_temp = log_path is None
    if own_temp:
        tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        tmp.close()
        log_path = Path(tmp.name)
        log_path.unlink(missing_ok=True)

    try:
        add_entry(
            log_path,
            "tool_timeout",
            "Shell command exceeded block_until_ms and was moved to background",
            suggested_fix="Raise block_until_ms or split the job into smaller steps.",
            context={"tool": "run_terminal_cmd", "session": "demo"},
            resolved=False,
        )
        add_entry(
            log_path,
            "parse_error",
            "Model returned markdown instead of strict JSON",
            suggested_fix="Ask for fenced JSON only and validate with json.loads before use.",
            context={"agent": "openclaw", "output_format": "json"},
        )
        print(colorize("--- Demo: two errors recorded ---", "bold"))
        store = load_store(log_path)
        entries = [validate_entry(e) for e in store["entries"]]  # type: ignore[arg-type]
        print_entries(entries, heading="Agent error log (demo)")
        print()
        print_stats(entries)
        return 0
    finally:
        if own_temp and log_path is not None:
            try:
                log_path.unlink(missing_ok=True)  # type: ignore[arg-type]
            except OSError:
                pass


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""

    parser = argparse.ArgumentParser(
        description="Track agent errors (timestamp, type, message, context, suggested fix) in JSON."
    )
    parser.add_argument(
        "--log-path",
        type=Path,
        default=DEFAULT_LOG_PATH,
        help="Path to the error learning JSON log.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    add_parser = subparsers.add_parser("add", help="Add a new agent error entry.")
    add_parser.add_argument("error_type", help="Error classification (e.g. tool_timeout, parse_error).")
    add_parser.add_argument("message", help="What went wrong.")
    add_parser.add_argument("suggested_fix", help="Concrete remediation or guardrail.")
    add_parser.add_argument(
        "--context-json",
        default="{}",
        help='JSON object with extra context (default "{}").',
    )
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

    subparsers.add_parser("list", help="List all entries.")
    subparsers.add_parser("stats", help="Show frequency by error type.")
    subparsers.add_parser("demo", help="Run a built-in demo (temp log unless --log-path is set).")

    search_parser = subparsers.add_parser("search", help="Search entries by relevance.")
    search_parser.add_argument("query", help="Search query.")
    search_parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum number of matching entries to print.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the error learning CLI."""

    args = parse_args(argv)
    try:
        if args.command == "demo":
            return run_demo(log_path=args.log_path if args.log_path != DEFAULT_LOG_PATH else None)

        store = load_store(args.log_path)
    except ErrorLearningError as exc:
        print(colorize(str(exc), "red"), file=sys.stderr)
        return 1

    entries = store["entries"]
    assert isinstance(entries, list)

    if args.command == "add":
        try:
            ctx_raw = json.loads(args.context_json)
        except json.JSONDecodeError as exc:
            print(colorize(f"Invalid --context-json: {exc}", "red"), file=sys.stderr)
            return 1
        if not isinstance(ctx_raw, dict):
            print(colorize("--context-json must be a JSON object.", "red"), file=sys.stderr)
            return 1
        try:
            entry, created = add_entry(
                args.log_path,
                args.error_type,
                args.message,
                args.suggested_fix,
                context=ctx_raw,
                resolved=args.resolved,
            )
        except ErrorLearningError as exc:
            print(colorize(str(exc), "red"), file=sys.stderr)
            return 1

        if created:
            print(colorize("Saved agent error entry.", "green"))
        else:
            print(colorize("Duplicate entry detected; existing record kept.", "yellow"))
        print(format_entry(entry))
        return 0

    validated_entries = [validate_entry(entry) for entry in entries]

    if args.command == "list":
        print_entries(validated_entries, heading="Agent error learnings")
        return 0

    if args.command == "stats":
        print_stats(validated_entries)
        return 0

    if args.command == "search":
        matches = search_entries(validated_entries, args.query, limit=max(args.limit, 1))
        print_entries(matches, heading=f"Search results: {args.query}")
        return 0

    print(colorize(f"Unsupported command: {args.command}", "red"), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
