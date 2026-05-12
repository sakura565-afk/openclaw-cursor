#!/usr/bin/env python3
"""Track agent errors for learning: timestamp, type, message, context, suggested fix (JSON store)."""

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


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOG_PATH = ROOT / ".learnings" / "agent_errors.json"
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
    """Raised when the error log cannot be read or written."""


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


def category_color(label: str) -> str:
    """Choose a stable display color for an error type label."""

    normalized = normalize_text(label)
    if any(token in normalized for token in ("lesson", "resolved", "fix", "success")):
        return "green"
    if any(token in normalized for token in ("warn", "warning", "caution")):
        return "yellow"
    if any(token in normalized for token in ("error", "failure", "fatal", "exception", "crash", "bug")):
        return "red"
    digest = hashlib.sha1(normalized.encode("utf-8")).digest()[0]
    return FALLBACK_CATEGORY_COLORS[digest % len(FALLBACK_CATEGORY_COLORS)]


def _migrate_legacy_entry_fields(entry: dict[str, object]) -> None:
    """Normalize v1 field names (category/error/lesson) into v2 in place."""

    if "error_type" not in entry and isinstance(entry.get("category"), str):
        entry["error_type"] = entry.pop("category")
    if "message" not in entry and isinstance(entry.get("error"), str):
        entry["message"] = entry.pop("error")
    if "suggested_fix" not in entry and isinstance(entry.get("lesson"), str):
        entry["suggested_fix"] = entry.pop("lesson")


def canonical_payload(
    error_type: str,
    message: str,
    suggested_fix: str,
    context: dict[str, object],
    resolved: bool,
) -> dict[str, object]:
    """Return a normalized payload used for IDs and deduplication."""

    ctx_norm = json.dumps(context, sort_keys=True, separators=(",", ":"))
    return {
        "error_type": normalize_text(error_type),
        "message": normalize_text(message),
        "suggested_fix": normalize_text(suggested_fix),
        "context": ctx_norm,
        "resolved": bool(resolved),
    }


def build_entry(
    error_type: str,
    message: str,
    suggested_fix: str,
    *,
    context: dict[str, object] | None = None,
    resolved: bool = True,
    timestamp: str | None = None,
) -> dict[str, object]:
    """Create a log entry that matches the JSON schema."""

    ctx: dict[str, object] = dict(context) if context else {}
    payload = canonical_payload(error_type, message, suggested_fix, ctx, resolved)
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


def _parse_context_arg(raw: str | None) -> dict[str, object]:
    if raw is None or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ErrorLearningError(f"Invalid JSON for --context: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ErrorLearningError("--context must be a JSON object.")
    return parsed


def validate_entry(raw_entry: object) -> dict[str, object]:
    """Validate a single persisted entry and normalize legacy rows."""

    if not isinstance(raw_entry, dict):
        raise ErrorLearningError("Each entry in the error log must be a JSON object.")

    entry: dict[str, object] = dict(raw_entry)
    _migrate_legacy_entry_fields(entry)

    required_text = ("timestamp", "error_type", "message", "suggested_fix")
    for field in required_text:
        value = entry.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ErrorLearningError(f"Entry field '{field}' must be a non-empty string.")

    ctx = entry.get("context", {})
    if ctx is None:
        entry["context"] = {}
    elif isinstance(ctx, dict):
        entry["context"] = dict(ctx)
    else:
        raise ErrorLearningError("Entry field 'context' must be a JSON object.")

    resolved = entry.get("resolved", False)
    if not isinstance(resolved, bool):
        raise ErrorLearningError("Entry field 'resolved' must be a boolean.")

    if not isinstance(entry.get("id"), str) or not str(entry["id"]).strip():
        entry["id"] = build_entry(
            str(entry["error_type"]),
            str(entry["message"]),
            str(entry["suggested_fix"]),
            context=entry["context"] if isinstance(entry["context"], dict) else {},
            resolved=resolved,
            timestamp=str(entry["timestamp"]),
        )["id"]
    entry["resolved"] = resolved
    return entry


def load_store(log_path: Path) -> dict[str, object]:
    """Load the persisted error log from disk."""

    if not log_path.exists():
        return default_store()

    text = log_path.read_text(encoding="utf-8").strip()
    if not text:
        return default_store()

    try:
        raw = json.loads(text)
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

    schema_v = int(raw.get("schema_version", SCHEMA_VERSION))
    return {
        "schema_version": max(schema_v, SCHEMA_VERSION),
        "entries": [validate_entry(item) for item in raw_entries],
    }


def save_store(log_path: Path, store: dict[str, object]) -> None:
    """Persist the error log to disk."""

    log_path.parent.mkdir(parents=True, exist_ok=True)
    out = dict(store)
    out["schema_version"] = SCHEMA_VERSION
    log_path.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")


def entries_match(left: dict[str, object], right: dict[str, object]) -> bool:
    """Return True when two entries represent the same learning."""

    if left.get("id") == right.get("id"):
        return True
    lctx = left["context"] if isinstance(left.get("context"), dict) else {}
    rctx = right["context"] if isinstance(right.get("context"), dict) else {}
    return canonical_payload(
        str(left["error_type"]),
        str(left["message"]),
        str(left["suggested_fix"]),
        lctx,
        bool(left["resolved"]),
    ) == canonical_payload(
        str(right["error_type"]),
        str(right["message"]),
        str(right["suggested_fix"]),
        rctx,
        bool(right["resolved"]),
    )


def add_entry(
    log_path: Path,
    error_type: str,
    message: str,
    suggested_fix: str,
    *,
    context: dict[str, object] | None = None,
    resolved: bool = True,
) -> tuple[dict[str, object], bool]:
    """Append an error entry unless an equivalent one already exists."""

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

    error_type = str(entry["error_type"])
    resolved = bool(entry["resolved"])
    status_text = "resolved" if resolved else "open"
    status_color = "green" if resolved else "yellow"
    ctx = entry.get("context", {})
    ctx_preview = json.dumps(ctx, ensure_ascii=False) if isinstance(ctx, dict) and ctx else "{}"
    lines = [
        (
            f"{colorize(error_type, category_color(error_type))} "
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

    print(colorize("Agent error stats", "bold"))
    print(colorize("=================", "cyan"))
    if not entries:
        print(colorize("No entries found.", "yellow"))
        return

    counts = Counter(str(entry["error_type"]) for entry in entries)
    total = len(entries)
    for err_type, count in sorted(counts.items(), key=lambda item: (-item[1], item[0].lower())):
        share = (count / total) * 100
        print(
            f"- {colorize(err_type, category_color(err_type))}: "
            f"{colorize(str(count), 'red')} "
            f"({share:.1f}%)"
        )


def search_score(query: str, entry: dict[str, object]) -> float:
    """Score how relevant an entry is to a search query."""

    normalized_query = normalize_text(query)
    ctx = entry.get("context", {})
    ctx_str = json.dumps(ctx, sort_keys=True) if isinstance(ctx, dict) else ""
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
    Demo: record sample agent errors to a temp JSON file, then list them.

    Usage as a library: ``error_learning.run_demo()``; CLI: ``python scripts/error_learning.py demo``.
    """
    path = log_path
    own_temp = False
    if path is None:
        fd, name = tempfile.mkstemp(prefix="agent_errors_demo_", suffix=".json")
        os.close(fd)
        path = Path(name)
        own_temp = True

    try:
        add_entry(
            path,
            "ToolTimeout",
            "Shell command exceeded block_until_ms and was moved to background.",
            "Increase block_until_ms for long-running commands or split the work.",
            context={"tool": "shell", "hint": "ollama pull"},
        )
        add_entry(
            path,
            "ParseError",
            "Model returned markdown instead of strict JSON.",
            "Ask for fenced JSON only and validate with json.loads before use.",
            context={"task": "structured_extract", "model": "local"},
            resolved=False,
        )
        store = load_store(path)
        entries = store["entries"]
        assert isinstance(entries, list)
        validated = [validate_entry(e) for e in entries]
        print(colorize("Demo: agent error log (JSON)", "bold"))
        print(colorize(f"File: {path}", "cyan"))
        print()
        print_entries(validated, heading="Recorded entries")
        print()
        print(colorize("Raw JSON excerpt (first entry):", "bold"))
        if validated:
            excerpt = {k: validated[0][k] for k in ("timestamp", "error_type", "message", "context", "suggested_fix")}
            print(json.dumps(excerpt, indent=2, ensure_ascii=False))
        return 0
    finally:
        if own_temp and path is not None and path.exists():
            path.unlink(missing_ok=True)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""

    parser = argparse.ArgumentParser(
        description="Track agent errors (timestamp, type, message, context, suggested fix) in JSON."
    )
    parser.add_argument(
        "--log-path",
        type=Path,
        default=DEFAULT_LOG_PATH,
        help="Path to the agent errors JSON log.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    add_parser = subparsers.add_parser("add", help="Add a new error entry.")
    add_parser.add_argument("error_type", help="Error classification (e.g. ToolTimeout, ParseError).")
    add_parser.add_argument("message", help="Error message or short description.")
    add_parser.add_argument("suggested_fix", help="Suggested remediation for future runs.")
    add_parser.add_argument(
        "--context",
        default=None,
        help='Optional JSON object string, e.g. \'{"tool":"shell","cwd":"/tmp"}\'',
    )
    resolved_group = add_parser.add_mutually_exclusive_group()
    resolved_group.add_argument(
        "--resolved",
        dest="resolved",
        action="store_true",
        default=True,
        help="Mark resolved (default).",
    )
    resolved_group.add_argument(
        "--unresolved",
        dest="resolved",
        action="store_false",
        help="Mark still open.",
    )

    subparsers.add_parser("list", help="List all entries.")
    subparsers.add_parser("stats", help="Show counts by error_type.")
    subparsers.add_parser("demo", help="Run an in-memory/temp-file demo (no confirmation).")

    search_parser = subparsers.add_parser("search", help="Search past errors.")
    search_parser.add_argument("query", help="Search query.")
    search_parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum number of matches to print.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the error learning CLI."""

    args = parse_args(argv)
    try:
        if args.command == "demo":
            return run_demo()

        store = load_store(args.log_path)
    except ErrorLearningError as exc:
        print(colorize(str(exc), "red"), file=sys.stderr)
        return 1

    entries = store["entries"]
    assert isinstance(entries, list)

    if args.command == "add":
        try:
            ctx = _parse_context_arg(getattr(args, "context", None))
            entry, created = add_entry(
                args.log_path,
                args.error_type,
                args.message,
                args.suggested_fix,
                context=ctx,
                resolved=args.resolved,
            )
        except ErrorLearningError as exc:
            print(colorize(str(exc), "red"), file=sys.stderr)
            return 1

        if created:
            print(colorize("Saved agent error entry.", "green"))
        else:
            print(colorize("Duplicate entry detected; keeping existing row.", "yellow"))
        print(format_entry(entry))
        return 0

    validated_entries = [validate_entry(entry) for entry in entries]

    if args.command == "list":
        print_entries(validated_entries, heading="Agent error log")
        return 0

    if args.command == "stats":
        print_stats(validated_entries)
        return 0

    if args.command == "search":
        matches = search_entries(validated_entries, args.query, limit=max(args.limit, 1))
        print_entries(matches, heading=f"Search: {args.query}")
        return 0

    print(colorize(f"Unsupported command: {args.command}", "red"), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
