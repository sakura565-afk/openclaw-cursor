#!/usr/bin/env python3
"""Capture recurring errors, track recurrence, and mirror state to ``.learnings/ERRORS.md``."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LOG_PATH = ROOT / ".learnings" / "error_log.json"
SCHEMA_VERSION = 2
PATTERN_THRESHOLD = 3

ANSI = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "cyan": "\033[36m",
}
FALLBACK_CATEGORY_COLORS = ("red", "yellow", "green")

AUTO_BEGIN = "<!-- error-learning:auto-begin -->"
AUTO_END = "<!-- error-learning:auto-end -->"

__all__ = [
    "ErrorLearningError",
    "add_entry",
    "build_entry",
    "canonical_payload",
    "category_color",
    "colorize",
    "default_store",
    "entries_match",
    "format_entry",
    "load_store",
    "main",
    "normalize_text",
    "save_store",
    "search_entries",
    "search_score",
    "sync_errors_markdown",
    "validate_entry",
]


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


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def build_entry(
    category: str,
    error: str,
    lesson: str,
    *,
    resolved: bool = True,
    timestamp: str | None = None,
    recurrence_count: int = 1,
    last_seen: str | None = None,
) -> dict[str, object]:
    """Create a log entry that matches the JSON schema."""

    payload = canonical_payload(category, error, lesson, resolved)
    digest = hashlib.sha1(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:12]
    created_at = timestamp or _now_iso()
    seen = last_seen or created_at
    count = max(1, int(recurrence_count))
    return {
        "id": digest,
        "timestamp": created_at,
        "category": category.strip(),
        "error": error.strip(),
        "lesson": lesson.strip(),
        "resolved": bool(resolved),
        "recurrence_count": count,
        "last_seen": seen,
    }


def default_store() -> dict[str, object]:
    """Return an empty log document."""

    return {"schema_version": SCHEMA_VERSION, "entries": []}


def _safe_int(value: object, default: int) -> int:
    try:
        if value is None:
            return default
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


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

    resolved = entry.get("resolved", False)
    if not isinstance(resolved, bool):
        raise ErrorLearningError("Entry field 'resolved' must be a boolean.")

    recurrence = _safe_int(entry.get("recurrence_count"), 1)
    if recurrence < 1:
        recurrence = 1
    entry["recurrence_count"] = recurrence

    ts = str(entry["timestamp"]).strip()
    last_seen_raw = entry.get("last_seen")
    if isinstance(last_seen_raw, str) and last_seen_raw.strip():
        entry["last_seen"] = last_seen_raw.strip()
    else:
        entry["last_seen"] = ts

    if not isinstance(entry.get("id"), str) or not entry["id"].strip():
        entry["id"] = build_entry(
            entry["category"],
            entry["error"],
            entry["lesson"],
            resolved=resolved,
            timestamp=entry["timestamp"],
            recurrence_count=recurrence,
            last_seen=entry["last_seen"],
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
    normalized = [validate_entry(item) for item in raw_entries]
    return {"schema_version": max(version, SCHEMA_VERSION), "entries": normalized}


def save_store(log_path: Path, store: dict[str, object]) -> None:
    """Persist the error log to disk."""

    store = dict(store)
    store["schema_version"] = SCHEMA_VERSION
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(json.dumps(store, indent=2) + "\n", encoding="utf-8")


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
    md_path: Path | None = None,
) -> tuple[dict[str, object], bool]:
    """Add an error learning entry, or bump recurrence when the same case is logged again."""

    target_md = md_path if md_path is not None else log_path.parent / "ERRORS.md"
    store = load_store(log_path)
    new_entry = build_entry(category, error, lesson, resolved=resolved)
    entries = store["entries"]
    assert isinstance(entries, list)

    for index, entry in enumerate(entries):
        validated = validate_entry(entry)
        if entries_match(validated, new_entry):
            count = int(validated["recurrence_count"]) + 1
            now = _now_iso()
            merged = dict(validated)
            merged["recurrence_count"] = count
            merged["last_seen"] = now
            entries[index] = merged
            entries.sort(key=lambda item: str(item["last_seen"]), reverse=True)
            save_store(log_path, store)
            sync_errors_markdown(store, target_md)
            return validate_entry(merged), False

    entries.append(new_entry)
    entries.sort(key=lambda item: str(item["last_seen"]), reverse=True)
    save_store(log_path, store)
    sync_errors_markdown(store, target_md)
    return new_entry, True


def _escape_md_cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _format_patterns_block(entries: list[dict[str, object]]) -> str:
    patterns = [e for e in entries if int(e.get("recurrence_count", 1)) >= PATTERN_THRESHOLD]
    if not patterns:
        return (
            "## Recurring patterns (auto-detected)\n\n"
            f"_No error type has been logged **{PATTERN_THRESHOLD}+** times with the same "
            "context, fix, and resolution flag yet._\n"
        )

    lines = [
        "## Recurring patterns (auto-detected)",
        "",
        f"_These items reached **{PATTERN_THRESHOLD}+** matching log events (same error type, "
        "context, suggested fix, and resolved flag)._",
        "",
    ]
    for entry in sorted(patterns, key=lambda e: (-int(e["recurrence_count"]), str(e["category"]))):
        cat = str(entry["category"])
        lines.append(f"### `{_escape_md_cell(cat)}` — **{entry['recurrence_count']}×**")
        lines.append("")
        lines.append(f"- **Context:** {_escape_md_cell(str(entry['error']))}")
        lines.append(f"- **Suggested fix:** {_escape_md_cell(str(entry['lesson']))}")
        lines.append(f"- **Resolved:** {entry['resolved']}")
        lines.append(f"- **First seen:** `{entry['timestamp']}` · **Last seen:** `{entry['last_seen']}`")
        lines.append(f"- **Entry id:** `{entry['id']}`")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _format_catalog_block(entries: list[dict[str, object]]) -> str:
    lines = [
        "## Error catalog",
        "",
        "| Error type | Recurrence | Status | Last seen | Context (excerpt) |",
        "|------------|------------|--------|-------------|-------------------|",
    ]
    for entry in sorted(entries, key=lambda e: str(e["last_seen"]), reverse=True):
        excerpt = str(entry["error"])
        if len(excerpt) > 80:
            excerpt = excerpt[:77] + "..."
        status = "resolved" if entry["resolved"] else "open"
        lines.append(
            "| "
            + " | ".join(
                (
                    _escape_md_cell(str(entry["category"])),
                    str(int(entry["recurrence_count"])),
                    status,
                    str(entry["last_seen"]),
                    _escape_md_cell(excerpt),
                )
            )
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


def sync_errors_markdown(store: dict[str, object], md_path: Path) -> None:
    """Rewrite the auto-generated region of ``ERRORS.md`` from the JSON store."""

    entries_obj = store.get("entries", [])
    if not isinstance(entries_obj, list):
        return
    entries = [validate_entry(e) for e in entries_obj]

    inner = "\n".join(
        (
            "### Field reference",
            "",
            "- **Error type** — high-level category (CLI: first `add` / `log` argument).",
            "- **Timestamp** — first time this distinct case was recorded (`timestamp`).",
            "- **Context** — what happened (`error` in JSON).",
            "- **Suggested fix** — remediation (`lesson` in JSON).",
            "- **Recurrence count** — how many times the same case was logged (`recurrence_count`).",
            "",
            _format_patterns_block(entries),
            _format_catalog_block(entries),
        )
    ).strip()
    marked_block = f"{AUTO_BEGIN}\n{inner}\n{AUTO_END}"

    preamble = "\n".join(
        (
            "# Error learnings",
            "",
            "_Human notes can go above this block. The section between the HTML comments is "
            "regenerated by `scripts/self_improvement/error_learning.py`._",
            "",
        )
    )
    full_document = f"{preamble}{marked_block}\n"

    md_path.parent.mkdir(parents=True, exist_ok=True)
    if not md_path.exists():
        md_path.write_text(full_document, encoding="utf-8")
        return

    existing = md_path.read_text(encoding="utf-8")
    if AUTO_BEGIN in existing and AUTO_END in existing:
        pattern = re.compile(
            re.escape(AUTO_BEGIN) + r"[\s\S]*?" + re.escape(AUTO_END),
            re.MULTILINE,
        )
        new_text, count = pattern.subn(marked_block, existing, count=1)
        if count:
            md_path.write_text(new_text, encoding="utf-8")
            return

    merged = f"{existing.rstrip()}\n\n{full_document}"
    md_path.write_text(merged, encoding="utf-8")


def format_entry(entry: dict[str, object]) -> str:
    """Render a single entry for console output."""

    category = str(entry["category"])
    resolved = bool(entry["resolved"])
    status_text = "resolved" if resolved else "open"
    status_color = "green" if resolved else "yellow"
    recurrence = int(entry.get("recurrence_count", 1))
    lines = [
        (
            f"{colorize(category, category_color(category))} "
            f"{colorize(f'[{status_text}]', status_color)} "
            f"{colorize(str(entry['timestamp']), 'cyan')}"
        ),
        f"  {colorize('ID:', 'yellow')} {entry['id']}",
        f"  {colorize('Recurrence:', 'cyan')} {recurrence} "
        f"(last seen {colorize(str(entry.get('last_seen', entry['timestamp'])), 'cyan')})",
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
        " ".join(
            (
                str(entry["category"]),
                str(entry["error"]),
                str(entry["lesson"]),
                str(entry.get("recurrence_count", "")),
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


def print_patterns(entries: list[dict[str, object]]) -> None:
    """Print entries that crossed the recurrence threshold."""

    flagged = [validate_entry(e) for e in entries if int(e.get("recurrence_count", 1)) >= PATTERN_THRESHOLD]
    print(colorize(f"Recurring patterns ({PATTERN_THRESHOLD}+ occurrences)", "bold"))
    print(colorize("=" * 50, "cyan"))
    if not flagged:
        print(colorize(f"No entries at or above {PATTERN_THRESHOLD} occurrences.", "yellow"))
        return
    for index, entry in enumerate(flagged):
        if index:
            print()
        print(format_entry(entry))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""

    parser = argparse.ArgumentParser(
        description="Log and query error learnings (JSON + .learnings/ERRORS.md).",
    )
    parser.add_argument(
        "--log-path",
        type=Path,
        default=DEFAULT_LOG_PATH,
        help="Path to the error learning JSON log.",
    )
    parser.add_argument(
        "--md-path",
        type=Path,
        default=None,
        help="Path to ERRORS.md (default: sibling of log path).",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    def _attach_add_parser(name: str, help_text: str) -> argparse.ArgumentParser:
        p = subparsers.add_parser(name, help=help_text)
        p.add_argument("error_type", help="Error type / category for this failure.")
        p.add_argument("context", help="What happened (context / error summary).")
        p.add_argument("suggested_fix", help="Suggested fix or lesson learned.")
        resolved_group = p.add_mutually_exclusive_group()
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
        return p

    _attach_add_parser("add", "Add a new error learning entry (same as log).")
    _attach_add_parser("log", "Log an error (alias of add).")

    subparsers.add_parser("list", help="List all learned errors.")
    subparsers.add_parser("stats", help="Show error frequency by category.")
    subparsers.add_parser("patterns", help=f"List entries with recurrence ≥ {PATTERN_THRESHOLD}.")

    search_parser = subparsers.add_parser("search", help="Search for relevant past errors.")
    search_parser.add_argument("query", help="Search query.")
    search_parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum number of matching entries to print.",
    )
    query_parser = subparsers.add_parser("query", help="Alias of search.")
    query_parser.add_argument("query", help="Search query.")
    query_parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum number of matching entries to print.",
    )

    return parser.parse_args(argv)


def _md_path_for_args(args: argparse.Namespace) -> Path:
    if args.md_path is not None:
        return Path(args.md_path)
    return Path(args.log_path).parent / "ERRORS.md"


def main(argv: list[str] | None = None) -> int:
    """Run the error learning CLI."""

    args = parse_args(argv)
    md_path = _md_path_for_args(args)
    try:
        store = load_store(args.log_path)
    except ErrorLearningError as exc:
        print(colorize(str(exc), "red"), file=sys.stderr)
        return 1

    entries = store["entries"]
    assert isinstance(entries, list)

    if args.command in ("add", "log"):
        try:
            entry, created = add_entry(
                args.log_path,
                args.error_type,
                args.context,
                args.suggested_fix,
                resolved=args.resolved,
                md_path=md_path,
            )
        except ErrorLearningError as exc:
            print(colorize(str(exc), "red"), file=sys.stderr)
            return 1

        recurrence = int(entry["recurrence_count"])
        if created:
            print(colorize("Saved error learning entry.", "green"))
        else:
            msg = (
                f"Duplicate entry detected; recurrence count is now {recurrence}. "
                f"Synced {md_path.name}."
            )
            if recurrence >= PATTERN_THRESHOLD:
                msg += f" Pattern flag (≥{PATTERN_THRESHOLD}) is active for this case."
            print(colorize(msg, "yellow"))
        print(format_entry(entry))
        return 0

    validated_entries = [validate_entry(entry) for entry in entries]

    if args.command == "list":
        print_entries(validated_entries, heading="OpenClaw Error Learnings")
        return 0

    if args.command == "stats":
        print_stats(validated_entries)
        return 0

    if args.command == "patterns":
        print_patterns(validated_entries)
        return 0

    if args.command in ("search", "query"):
        q = args.query
        lim = max(args.limit, 1)
        matches = search_entries(validated_entries, q, limit=lim)
        print_entries(matches, heading=f"Search Results: {q}")
        return 0

    print(colorize(f"Unsupported command: {args.command}", "red"), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
