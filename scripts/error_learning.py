#!/usr/bin/env python3
"""Capture and learn from recurring OpenClaw session errors.

Clusters similar failures via normalized signatures, infers triage buckets, and
exposes ``patterns``, ``suggest``, and ``open`` commands for faster remediation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOG_PATH = ROOT / ".learnings" / "error_log.json"
SCHEMA_VERSION = 1
INSIGHTS_GLOB = "run_*.md"
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


# Order matters: first matching bucket wins.
_BUCKET_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "network",
        (
            "timeout",
            "timed out",
            "connection refused",
            "econnrefused",
            "enotfound",
            "dns",
            "ssl",
            "tls",
            "certificate",
            "socket",
            "unreachable",
            "network",
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
            "api key",
            "credential",
            "oauth",
            "jwt",
            "permission denied",
        ),
    ),
    (
        "parsing",
        (
            "json",
            "yaml",
            "parse",
            "parser",
            "syntax error",
            "unexpected token",
            "invalid format",
            "malformed",
            "unterminated",
        ),
    ),
    (
        "tooling",
        (
            "tool call",
            "tool_use",
            "mcp",
            "function call",
            "plugin",
            "subprocess",
            "command failed",
            "exit code",
        ),
    ),
    (
        "resource_limits",
        (
            "memory",
            "oom",
            "disk",
            "space",
            "quota",
            "rate limit",
            "429",
            "too many requests",
        ),
    ),
    (
        "configuration",
        (
            "config",
            "environment variable",
            "missing setting",
            "invalid option",
            "path not found",
            "file not found",
        ),
    ),
)

_UUID_RE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.IGNORECASE
)
_HEX_RE = re.compile(r"\b0x[0-9a-f]{4,}\b", re.IGNORECASE)
_PATH_RE = re.compile(r"(?:/[\w.+-]+){2,}")
_LINE_COL_RE = re.compile(r"\b(line|column)\s+\d+\b", re.IGNORECASE)
_IP_RE = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
_NUM_TOKEN_RE = re.compile(r"\b\d+\b")

# Lines in reflection insight markdown that look like hard failures.
_INSIGHT_FAILURE_RE = re.compile(
    r"(?i)(\berror\b|\bexception\b|\btraceback\b|\bfailed\b|\bfailure\b|\btimed?\s*out\b)"
)


def error_signature(text: str) -> str:
    """Normalize volatile details so recurring failures cluster together."""

    raw = text.strip()
    if not raw:
        return ""
    collapsed = " ".join(raw.lower().split())
    collapsed = _UUID_RE.sub("<uuid>", collapsed)
    collapsed = _HEX_RE.sub("<hex>", collapsed)
    collapsed = _PATH_RE.sub("<path>", collapsed)
    collapsed = _IP_RE.sub("<ip>", collapsed)
    collapsed = _LINE_COL_RE.sub(r"\1 <n>", collapsed)
    collapsed = _NUM_TOKEN_RE.sub("<n>", collapsed)
    return collapsed


def infer_bucket(category: str, error: str, lesson: str) -> str:
    """Map free-form text to a coarse bucket for triage and stats."""

    blob = normalize_text(f"{category} {error} {lesson}")
    for bucket, tokens in _BUCKET_RULES:
        if any(token in blob for token in tokens):
            return bucket
    cat = normalize_text(category).replace(" ", "_")
    if cat in {"runtime_error", "warning", "parser_error", "tool_error", "config_error"}:
        return cat
    return "general"


def entry_signature(entry: dict[str, object]) -> str:
    return error_signature(str(entry["error"]))


def count_signatures(entries: Iterable[dict[str, object]]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for entry in entries:
        sig = entry_signature(entry)
        if sig:
            counts[sig] += 1
    return counts


def iter_insight_error_lines(learnings_root: Path, *, max_files: int = 40, max_lines_per_file: int = 80) -> list[str]:
    """Pull likely error lines from auto_reflection insight markdown (if present)."""

    insights = learnings_root / "insights"
    if not insights.is_dir():
        return []

    paths = sorted(insights.glob(INSIGHTS_GLOB), key=lambda p: p.stat().st_mtime, reverse=True)[:max_files]
    snippets: list[str] = []
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        kept = 0
        for line in text.splitlines():
            stripped = line.strip()
            if len(stripped) < 12 or not _INSIGHT_FAILURE_RE.search(stripped):
                continue
            snippets.append(stripped)
            kept += 1
            if kept >= max_lines_per_file:
                break
    return snippets


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
        "schema_version": int(raw.get("schema_version", SCHEMA_VERSION)),
        "entries": [validate_entry(item) for item in raw_entries],
    }


def save_store(log_path: Path, store: dict[str, object]) -> None:
    """Persist the error log to disk."""

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
) -> tuple[dict[str, object], bool]:
    """Add an error learning entry unless it already exists."""

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
    return new_entry, True


def related_same_signature(
    entries: Iterable[dict[str, object]],
    target: dict[str, object],
) -> list[dict[str, object]]:
    """Return other entries whose normalized error signature matches the target."""

    sig = entry_signature(target)
    if not sig:
        return []
    out: list[dict[str, object]] = []
    tid = str(target.get("id", ""))
    for entry in entries:
        if str(entry.get("id")) == tid:
            continue
        if entry_signature(entry) == sig:
            out.append(entry)
    return out


def format_compact_neighbor(entry: dict[str, object]) -> str:
    """One-line summary for quick scanning when a pattern repeats."""

    err = str(entry["error"]).replace("\n", " ")
    if len(err) > 100:
        err = err[:97] + "..."
    lesson = str(entry["lesson"])
    if len(lesson) > 120:
        lesson = lesson[:117] + "..."
    st = "resolved" if bool(entry["resolved"]) else "open"
    return (
        f"  {colorize(f'[{st}]', 'green' if st == 'resolved' else 'yellow')} "
        f"{colorize(str(entry['id']), 'cyan')}: "
        f"{colorize(err, 'red')} → {colorize(lesson, 'green')}"
    )


def format_entry(
    entry: dict[str, object],
    *,
    sig_counts: Counter[str] | None = None,
    action_first: bool = False,
) -> str:
    """Render a single entry for console output."""

    category = str(entry["category"])
    resolved = bool(entry["resolved"])
    status_text = "resolved" if resolved else "open"
    status_color = "green" if resolved else "yellow"
    bucket = infer_bucket(category, str(entry["error"]), str(entry["lesson"]))
    sig = entry_signature(entry)
    recur = ""
    if sig_counts and sig and sig_counts[sig] > 1:
        recur = colorize(f" [pattern ×{sig_counts[sig]}]", "yellow")

    header = (
        f"{colorize(category, category_color(category))} "
        f"{colorize(f'[{status_text}]', status_color)} "
        f"{colorize(f'⟨{bucket}⟩', 'cyan')}{recur} "
        f"{colorize(str(entry['timestamp']), 'cyan')}"
    )
    meta = f"  {colorize('ID:', 'yellow')} {entry['id']}"
    err_line = f"  {colorize('Error:', 'red')} {entry['error']}"
    action = f"  {colorize('Action:', 'green')} {entry['lesson']}"

    if action_first:
        return "\n".join((header, action, err_line, meta))
    return "\n".join((header, meta, err_line, action))


def print_entries(
    entries: list[dict[str, object]],
    *,
    heading: str,
    sig_counts: Counter[str] | None = None,
    action_first: bool = False,
) -> None:
    """Print a collection of entries in a human-readable layout."""

    print(colorize(heading, "bold"))
    print(colorize("=" * len(heading), "cyan"))
    if not entries:
        print(colorize("No entries found.", "yellow"))
        return

    counts = sig_counts if sig_counts is not None else count_signatures(entries)
    for index, entry in enumerate(entries):
        if index:
            print()
        print(format_entry(entry, sig_counts=counts, action_first=action_first))


def print_stats(entries: list[dict[str, object]]) -> None:
    """Print category-level frequency stats and inferred buckets."""

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

    buckets = Counter(
        infer_bucket(str(e["category"]), str(e["error"]), str(e["lesson"])) for e in entries
    )
    print()
    print(colorize("By triage bucket (inferred)", "bold"))
    print(colorize("---------------------------", "cyan"))
    for bucket, count in sorted(buckets.items(), key=lambda item: (-item[1], item[0].lower())):
        share = (count / total) * 100
        print(
            f"- {colorize(bucket, 'cyan')}: "
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


def suggest_score(query: str, entry: dict[str, object], counts: Counter[str]) -> float:
    """Rank entries for remediation: text match plus recurring-signature signal."""

    base = search_score(query, entry)
    qsig = error_signature(query)
    esig = entry_signature(entry)
    boost = 0.0
    if qsig and esig:
        if qsig == esig:
            boost += 3.0 + min(counts[esig], 12) * 0.2
        else:
            boost += SequenceMatcher(None, qsig, esig).ratio() * 1.25
    if bool(entry["resolved"]):
        boost += 0.2
    return base + boost


def search_entries(entries: list[dict[str, object]], query: str, limit: int = 10) -> list[dict[str, object]]:
    """Return the most relevant matching entries for the given query."""

    ranked: list[tuple[float, dict[str, object]]] = []
    for entry in entries:
        score = search_score(query, entry)
        if score >= 0.45:
            ranked.append((score, entry))
    ranked.sort(key=lambda item: (-item[0], str(item[1]["timestamp"])), reverse=False)
    return [entry for _, entry in ranked[:limit]]


def suggest_entries(entries: list[dict[str, object]], query: str, limit: int = 8) -> list[dict[str, object]]:
    """Surface the fastest wins: strong text match or the same recurring failure shape."""

    counts = count_signatures(entries)
    ranked: list[tuple[float, dict[str, object]]] = []
    for entry in entries:
        score = suggest_score(query, entry, counts)
        if score >= 0.55:
            ranked.append((score, entry))
    ranked.sort(key=lambda item: (-item[0], str(item[1]["timestamp"])), reverse=False)
    return [entry for _, entry in ranked[: max(limit, 1)]]


def recurring_pattern_rows(
    entries: list[dict[str, object]],
    *,
    min_count: int = 2,
    limit: int = 25,
) -> list[tuple[int, str, dict[str, object]]]:
    """Clusters with at least ``min_count`` occurrences, newest representative first."""

    counts = count_signatures(entries)
    by_sig: dict[str, list[dict[str, object]]] = defaultdict(list)
    for entry in entries:
        sig = entry_signature(entry)
        if sig:
            by_sig[sig].append(entry)

    rows: list[tuple[int, str, dict[str, object]]] = []
    for sig, count in counts.items():
        if count < min_count or sig not in by_sig:
            continue
        group = by_sig[sig]
        rep = max(group, key=lambda item: str(item["timestamp"]))
        rows.append((count, sig, rep))

    rows.sort(key=lambda item: (-item[0], -len(item[1]), str(item[2]["timestamp"])))
    return rows[:limit]


def print_recurring_patterns(
    entries: list[dict[str, object]],
    *,
    min_count: int = 2,
    limit: int = 25,
    include_insights: bool = False,
    learnings_dir: Path | None = None,
) -> None:
    """Print recurring normalized signatures with a concrete action line."""

    print(colorize("Recurring error patterns", "bold"))
    print(colorize("======================", "cyan"))
    rows = recurring_pattern_rows(entries, min_count=min_count, limit=limit)
    if not rows:
        print(colorize("No repeated signatures yet (need ≥2 similar errors).", "yellow"))
    else:
        for idx, (count, sig, rep) in enumerate(rows):
            if idx:
                print()
            bucket = infer_bucket(str(rep["category"]), str(rep["error"]), str(rep["lesson"]))
            preview = sig if len(sig) <= 140 else sig[:137] + "..."
            print(
                f"{colorize(f'×{count}', 'red')} {colorize(f'⟨{bucket}⟩', 'cyan')} "
                f"{colorize(str(rep['category']), category_color(str(rep['category'])))}"
            )
            print(f"  {colorize('Shape:', 'yellow')} {preview}")
            print(f"  {colorize('Action:', 'green')} {rep['lesson']}")
            print(f"  {colorize('Latest ID:', 'yellow')} {rep['id']}  {colorize(str(rep['timestamp']), 'cyan')}")

    if include_insights and learnings_dir is not None:
        snippets = iter_insight_error_lines(learnings_dir)
        print()
        print(colorize("Recent lines from .learnings/insights (unlogged signals)", "bold"))
        print(colorize("-------------------------------------------------------", "cyan"))
        if not snippets:
            print(colorize("No insight files matched or no failure-like lines.", "yellow"))
        else:
            for line in snippets[:30]:
                print(f"  {colorize('·', 'yellow')} {line[:240]}{'…' if len(line) > 240 else ''}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""

    parser = argparse.ArgumentParser(description="Capture and learn from OpenClaw errors.")
    parser.add_argument(
        "--log-path",
        type=Path,
        default=DEFAULT_LOG_PATH,
        help="Path to the error learning JSON log.",
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
    subparsers.add_parser("open", help="List unresolved (open) learnings only.")
    subparsers.add_parser("stats", help="Show error frequency by category and inferred triage bucket.")

    search_parser = subparsers.add_parser("search", help="Search for relevant past errors.")
    search_parser.add_argument("query", help="Search query.")
    search_parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum number of matching entries to print.",
    )

    suggest_parser = subparsers.add_parser(
        "suggest",
        help="Recommend fixes for a new failure (boosts recurring signatures).",
    )
    suggest_parser.add_argument("error_text", help="Paste error message or short failure description.")
    suggest_parser.add_argument(
        "--limit",
        type=int,
        default=8,
        help="Maximum number of suggestions to print.",
    )

    patterns_parser = subparsers.add_parser(
        "patterns",
        help="Show recurring normalized error shapes with the best-known action.",
    )
    patterns_parser.add_argument(
        "--min-count",
        type=int,
        default=2,
        help="Minimum occurrences to include a pattern.",
    )
    patterns_parser.add_argument(
        "--limit",
        type=int,
        default=25,
        help="Maximum number of pattern rows to print.",
    )
    patterns_parser.add_argument(
        "--include-insights",
        action="store_true",
        help="Append failure-like lines from .learnings/insights if they exist.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the error learning CLI."""

    args = parse_args(argv)
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

        refreshed = [validate_entry(item) for item in load_store(args.log_path)["entries"]]
        counts_all = count_signatures(refreshed)
        print(format_entry(entry, sig_counts=counts_all))
        neighbors = related_same_signature(refreshed, entry)
        if neighbors:
            print()
            print(
                colorize(
                    f"Earlier learnings with the same error shape ({len(neighbors)}); try these first:",
                    "yellow",
                )
            )
            for prev in neighbors[:8]:
                print(format_compact_neighbor(prev))
        return 0

    validated_entries = [validate_entry(entry) for entry in entries]
    all_counts = count_signatures(validated_entries)

    if args.command == "list":
        print_entries(validated_entries, heading="OpenClaw Error Learnings", sig_counts=all_counts)
        return 0

    if args.command == "open":
        open_entries = [item for item in validated_entries if not bool(item["resolved"])]
        print_entries(open_entries, heading="Open (Unresolved) Learnings", sig_counts=all_counts)
        return 0

    if args.command == "stats":
        print_stats(validated_entries)
        return 0

    if args.command == "search":
        matches = search_entries(validated_entries, args.query, limit=max(args.limit, 1))
        print_entries(matches, heading=f"Search Results: {args.query}", sig_counts=all_counts)
        return 0

    if args.command == "suggest":
        matches = suggest_entries(
            validated_entries,
            args.error_text,
            limit=max(args.limit, 1),
        )
        print_entries(
            matches,
            heading=f"Suggested fixes for: {args.error_text[:120]}{'…' if len(args.error_text) > 120 else ''}",
            sig_counts=all_counts,
            action_first=True,
        )
        return 0

    if args.command == "patterns":
        print_recurring_patterns(
            validated_entries,
            min_count=max(1, args.min_count),
            limit=max(args.limit, 1),
            include_insights=bool(args.include_insights),
            learnings_dir=args.log_path.parent,
        )
        return 0

    print(colorize(f"Unsupported command: {args.command}", "red"), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
