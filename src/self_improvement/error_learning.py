"""Capture errors, persist learnings, and suggest fixes for recurring patterns.

Each record is appended to ``.learnings/errors.log`` (JSON Lines) with context,
root cause, and fix applied. Similar failures are clustered via normalized
signatures so prior fixes can be suggested quickly.
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


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LOG_PATH = ROOT / ".learnings" / "errors.log"
SCHEMA_VERSION = 1

ANSI = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "cyan": "\033[36m",
}

_UUID_RE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.IGNORECASE
)
_HEX_RE = re.compile(r"\b0x[0-9a-f]{4,}\b", re.IGNORECASE)
_PATH_RE = re.compile(r"(?:/[\w.+-]+){2,}")
_LINE_COL_RE = re.compile(r"\b(line|column)\s+\d+\b", re.IGNORECASE)
_IP_RE = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
_NUM_TOKEN_RE = re.compile(r"\b\d+\b")


class ErrorLearningError(RuntimeError):
    """Raised when the error learning log cannot be read or written."""


def colorize(text: str, color: str) -> str:
    if os.environ.get("NO_COLOR"):
        return text
    prefix = ANSI.get(color, "")
    if not prefix:
        return text
    return f"{prefix}{text}{ANSI['reset']}"


def normalize_text(text: str) -> str:
    return " ".join(text.strip().lower().split())


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


def entry_signature(entry: dict[str, object]) -> str:
    return error_signature(str(entry.get("root_cause", "")))


def canonical_payload(
    context: str,
    root_cause: str,
    fix_applied: str,
    resolved: bool,
) -> dict[str, object]:
    return {
        "context": normalize_text(context),
        "root_cause": normalize_text(root_cause),
        "fix_applied": normalize_text(fix_applied),
        "resolved": bool(resolved),
    }


def build_entry(
    context: str,
    root_cause: str,
    fix_applied: str,
    *,
    resolved: bool = True,
    timestamp: str | None = None,
) -> dict[str, object]:
    payload = canonical_payload(context, root_cause, fix_applied, resolved)
    digest = hashlib.sha1(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:12]
    created_at = timestamp or datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "id": digest,
        "timestamp": created_at,
        "context": context.strip(),
        "root_cause": root_cause.strip(),
        "fix_applied": fix_applied.strip(),
        "resolved": bool(resolved),
    }


def validate_entry(raw_entry: object) -> dict[str, object]:
    if not isinstance(raw_entry, dict):
        raise ErrorLearningError("Each error log line must be a JSON object.")

    entry = dict(raw_entry)
    for field in ("timestamp", "context", "root_cause", "fix_applied"):
        value = entry.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ErrorLearningError(f"Entry field '{field}' must be a non-empty string.")

    resolved = entry.get("resolved", False)
    if not isinstance(resolved, bool):
        raise ErrorLearningError("Entry field 'resolved' must be a boolean.")

    if not isinstance(entry.get("id"), str) or not str(entry["id"]).strip():
        entry["id"] = build_entry(
            str(entry["context"]),
            str(entry["root_cause"]),
            str(entry["fix_applied"]),
            resolved=resolved,
            timestamp=str(entry["timestamp"]),
        )["id"]
    entry["resolved"] = resolved
    entry.setdefault("schema_version", SCHEMA_VERSION)
    return entry


def load_entries(log_path: Path) -> list[dict[str, object]]:
    if not log_path.exists():
        return []

    entries: list[dict[str, object]] = []
    for line_no, line in enumerate(log_path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            raw = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise ErrorLearningError(
                f"Unable to parse JSON on line {line_no} of {log_path}: {exc}"
            ) from exc
        entries.append(validate_entry(raw))
    return entries


def save_entries(log_path: Path, entries: list[dict[str, object]]) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(entry, ensure_ascii=False, separators=(",", ":")) for entry in entries]
    log_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def entries_match(left: dict[str, object], right: dict[str, object]) -> bool:
    if left.get("id") == right.get("id"):
        return True
    return canonical_payload(
        str(left["context"]),
        str(left["root_cause"]),
        str(left["fix_applied"]),
        bool(left["resolved"]),
    ) == canonical_payload(
        str(right["context"]),
        str(right["root_cause"]),
        str(right["fix_applied"]),
        bool(right["resolved"]),
    )


def capture_error(
    log_path: Path,
    context: str,
    root_cause: str,
    fix_applied: str,
    *,
    resolved: bool = True,
) -> tuple[dict[str, object], bool]:
    """Record an error learning; returns (entry, created)."""

    entries = load_entries(log_path)
    new_entry = build_entry(context, root_cause, fix_applied, resolved=resolved)
    for entry in entries:
        if entries_match(entry, new_entry):
            return entry, False

    entries.append(new_entry)
    entries.sort(key=lambda item: str(item["timestamp"]), reverse=True)
    save_entries(log_path, entries)
    return new_entry, True


def add_entry(
    log_path: Path,
    context: str,
    root_cause: str,
    fix_applied: str,
    *,
    resolved: bool = True,
) -> tuple[dict[str, object], bool]:
    """Alias for :func:`capture_error` (CLI compatibility)."""

    return capture_error(log_path, context, root_cause, fix_applied, resolved=resolved)


def count_signatures(entries: Iterable[dict[str, object]]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for entry in entries:
        sig = entry_signature(entry)
        if sig:
            counts[sig] += 1
    return counts


def related_same_signature(
    entries: Iterable[dict[str, object]],
    target: dict[str, object],
) -> list[dict[str, object]]:
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


def search_score(query: str, entry: dict[str, object]) -> float:
    normalized_query = normalize_text(query)
    haystack = normalize_text(
        " ".join(
            (
                str(entry["context"]),
                str(entry["root_cause"]),
                str(entry["fix_applied"]),
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


def suggest_score(query: str, entry: dict[str, object], counts: Counter[str]) -> float:
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
    ranked: list[tuple[float, dict[str, object]]] = []
    for entry in entries:
        score = search_score(query, entry)
        if score >= 0.45:
            ranked.append((score, entry))
    ranked.sort(key=lambda item: (-item[0], str(item[1]["timestamp"])))
    return [entry for _, entry in ranked[:limit]]


def suggest_entries(entries: list[dict[str, object]], query: str, limit: int = 8) -> list[dict[str, object]]:
    counts = count_signatures(entries)
    ranked: list[tuple[float, dict[str, object]]] = []
    for entry in entries:
        score = suggest_score(query, entry, counts)
        if score >= 0.55:
            ranked.append((score, entry))
    ranked.sort(key=lambda item: (-item[0], str(item[1]["timestamp"])))
    return [entry for _, entry in ranked[: max(limit, 1)]]


def recurring_pattern_rows(
    entries: list[dict[str, object]],
    *,
    min_count: int = 2,
    limit: int = 25,
) -> list[tuple[int, str, dict[str, object]]]:
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


def format_entry(entry: dict[str, object], *, sig_counts: Counter[str] | None = None) -> str:
    resolved = bool(entry["resolved"])
    status_text = "resolved" if resolved else "open"
    status_color = "green" if resolved else "yellow"
    sig = entry_signature(entry)
    recur = ""
    if sig_counts and sig and sig_counts[sig] > 1:
        recur = colorize(f" [pattern ×{sig_counts[sig]}]", "yellow")

    header = (
        f"{colorize(status_text, status_color)}{recur} "
        f"{colorize(str(entry['timestamp']), 'cyan')}"
    )
    lines = [
        header,
        f"  {colorize('ID:', 'yellow')} {entry['id']}",
        f"  {colorize('Context:', 'cyan')} {entry['context']}",
        f"  {colorize('Root cause:', 'red')} {entry['root_cause']}",
        f"  {colorize('Fix applied:', 'green')} {entry['fix_applied']}",
    ]
    return "\n".join(lines)


def format_compact_neighbor(entry: dict[str, object]) -> str:
    cause = str(entry["root_cause"]).replace("\n", " ")
    if len(cause) > 100:
        cause = cause[:97] + "..."
    fix = str(entry["fix_applied"])
    if len(fix) > 120:
        fix = fix[:117] + "..."
    st = "resolved" if bool(entry["resolved"]) else "open"
    return (
        f"  {colorize(f'[{st}]', 'green' if st == 'resolved' else 'yellow')} "
        f"{colorize(str(entry['id']), 'cyan')}: "
        f"{colorize(cause, 'red')} → {colorize(fix, 'green')}"
    )


def print_entries(
    entries: list[dict[str, object]],
    *,
    heading: str,
    sig_counts: Counter[str] | None = None,
) -> None:
    print(colorize(heading, "bold"))
    print(colorize("=" * len(heading), "cyan"))
    if not entries:
        print(colorize("No entries found.", "yellow"))
        return

    counts = sig_counts if sig_counts is not None else count_signatures(entries)
    for index, entry in enumerate(entries):
        if index:
            print()
        print(format_entry(entry, sig_counts=counts))


def print_stats(entries: list[dict[str, object]]) -> None:
    print(colorize("Error Learning Stats", "bold"))
    print(colorize("====================", "cyan"))
    if not entries:
        print(colorize("No entries found.", "yellow"))
        return

    resolved = sum(1 for e in entries if bool(e["resolved"]))
    open_count = len(entries) - resolved
    print(f"- {colorize('Total', 'cyan')}: {colorize(str(len(entries)), 'yellow')}")
    print(f"- {colorize('Resolved', 'green')}: {colorize(str(resolved), 'green')}")
    print(f"- {colorize('Open', 'yellow')}: {colorize(str(open_count), 'yellow')}")

    patterns = recurring_pattern_rows(entries, min_count=2, limit=10)
    if patterns:
        print()
        print(colorize("Top recurring root-cause shapes", "bold"))
        for count, sig, rep in patterns:
            preview = sig if len(sig) <= 100 else sig[:97] + "..."
            print(
                f"  {colorize(f'×{count}', 'red')} {preview} "
                f"→ {colorize(str(rep['fix_applied']), 'green')}"
            )


def print_recurring_patterns(
    entries: list[dict[str, object]],
    *,
    min_count: int = 2,
    limit: int = 25,
) -> None:
    print(colorize("Recurring error patterns", "bold"))
    print(colorize("======================", "cyan"))
    rows = recurring_pattern_rows(entries, min_count=min_count, limit=limit)
    if not rows:
        print(colorize("No repeated signatures yet (need ≥2 similar root causes).", "yellow"))
        return

    for idx, (count, sig, rep) in enumerate(rows):
        if idx:
            print()
        preview = sig if len(sig) <= 140 else sig[:137] + "..."
        print(f"{colorize(f'×{count}', 'red')} {colorize(str(rep['timestamp']), 'cyan')}")
        print(f"  {colorize('Shape:', 'yellow')} {preview}")
        print(f"  {colorize('Context:', 'cyan')} {rep['context']}")
        print(f"  {colorize('Fix:', 'green')} {rep['fix_applied']}")
        print(f"  {colorize('ID:', 'yellow')} {rep['id']}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Capture errors with context, root cause, and fix; suggest prior solutions."
    )
    parser.add_argument(
        "--log-path",
        type=Path,
        default=DEFAULT_LOG_PATH,
        help="Path to the JSONL error learning log (default: .learnings/errors.log).",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    add_parser = subparsers.add_parser("add", help="Record a new error learning.")
    add_parser.add_argument("context", help="Where/when the error occurred (task, file, command).")
    add_parser.add_argument("root_cause", help="Underlying failure or error message.")
    add_parser.add_argument("fix_applied", help="Mitigation or fix that addressed the issue.")
    resolved_group = add_parser.add_mutually_exclusive_group()
    resolved_group.add_argument(
        "--resolved",
        dest="resolved",
        action="store_true",
        default=True,
        help="Mark as resolved (default).",
    )
    resolved_group.add_argument(
        "--unresolved",
        dest="resolved",
        action="store_false",
        help="Mark as still open.",
    )

    subparsers.add_parser("list", help="List all recorded errors.")
    subparsers.add_parser("open", help="List unresolved errors only.")
    subparsers.add_parser("stats", help="Show summary statistics.")

    search_parser = subparsers.add_parser("search", help="Search past errors by text.")
    search_parser.add_argument("query", help="Search query.")
    search_parser.add_argument("--limit", type=int, default=10)

    suggest_parser = subparsers.add_parser(
        "suggest",
        help="Suggest prior fixes for a similar error pattern.",
    )
    suggest_parser.add_argument("error_text", help="New error text or root cause snippet.")
    suggest_parser.add_argument("--limit", type=int, default=8)

    patterns_parser = subparsers.add_parser("patterns", help="Show recurring root-cause patterns.")
    patterns_parser.add_argument("--min-count", type=int, default=2)
    patterns_parser.add_argument("--limit", type=int, default=25)

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        entries = load_entries(args.log_path)
    except ErrorLearningError as exc:
        print(colorize(str(exc), "red"), file=sys.stderr)
        return 1

    if args.command == "add":
        try:
            entry, created = capture_error(
                args.log_path,
                args.context,
                args.root_cause,
                args.fix_applied,
                resolved=args.resolved,
            )
        except ErrorLearningError as exc:
            print(colorize(str(exc), "red"), file=sys.stderr)
            return 1

        if created:
            print(colorize("Saved error learning entry.", "green"))
        else:
            print(colorize("Duplicate entry detected; existing learning kept.", "yellow"))

        refreshed = load_entries(args.log_path)
        counts_all = count_signatures(refreshed)
        print(format_entry(entry, sig_counts=counts_all))
        neighbors = related_same_signature(refreshed, entry)
        if neighbors:
            print()
            print(
                colorize(
                    f"Earlier fixes for the same error shape ({len(neighbors)}); try these first:",
                    "yellow",
                )
            )
            for prev in neighbors[:8]:
                print(format_compact_neighbor(prev))
        return 0

    validated = [validate_entry(e) for e in entries]
    all_counts = count_signatures(validated)

    if args.command == "list":
        print_entries(validated, heading="Error Learnings", sig_counts=all_counts)
        return 0

    if args.command == "open":
        open_entries = [e for e in validated if not bool(e["resolved"])]
        print_entries(open_entries, heading="Open (Unresolved) Errors", sig_counts=all_counts)
        return 0

    if args.command == "stats":
        print_stats(validated)
        return 0

    if args.command == "search":
        matches = search_entries(validated, args.query, limit=max(args.limit, 1))
        print_entries(matches, heading=f"Search: {args.query}", sig_counts=all_counts)
        return 0

    if args.command == "suggest":
        matches = suggest_entries(validated, args.error_text, limit=max(args.limit, 1))
        print_entries(
            matches,
            heading=f"Suggested fixes for: {args.error_text[:120]}{'…' if len(args.error_text) > 120 else ''}",
            sig_counts=all_counts,
        )
        return 0

    if args.command == "patterns":
        print_recurring_patterns(
            validated,
            min_count=max(1, args.min_count),
            limit=max(args.limit, 1),
        )
        return 0

    print(colorize(f"Unsupported command: {args.command}", "red"), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
