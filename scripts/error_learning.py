#!/usr/bin/env python3
"""Self-improvement error learning for agent runs.

Records failures with context and fixes, auto-assigns canonical categories,
persists structured JSON, and exposes retrieval APIs plus a small CLI
(``log``, ``show``, and optional search/stats helpers).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Final, TypedDict


ROOT: Final = Path(__file__).resolve().parents[1]
DEFAULT_LOG_PATH: Final[Path] = ROOT / ".learnings" / "error_log.json"
SCHEMA_VERSION: Final = 2
INSIGHTS_GLOB: Final = "run_*.md"

ANSI: Final[dict[str, str]] = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "cyan": "\033[36m",
}
FALLBACK_CATEGORY_COLORS: Final[tuple[str, ...]] = ("red", "yellow", "green")

# Canonical categories used for filtering, stats, and agent guidance (see AGENTS.md).
CANONICAL_CATEGORIES: Final[tuple[str, ...]] = (
    "tool_failure",
    "context_limit",
    "api_error",
    "logic_error",
    "network_error",
    "parsing_error",
    "configuration_error",
    "authentication_error",
    "resource_limit",
    "unknown",
)
_CANONICAL_SET: Final[frozenset[str]] = frozenset(CANONICAL_CATEGORIES)


class ErrorLearningError(RuntimeError):
    """Raised when the error learning store cannot be read, written, or validated."""


class ErrorLearningEntry(TypedDict, total=False):
    """Structured learning record persisted to JSON."""

    id: str
    timestamp: str
    error_type: str
    category: str
    description: str
    context: str
    fix: str
    resolved: bool


def colorize(text: str, color: str) -> str:
    """Wrap *text* in ANSI color codes unless ``NO_COLOR`` is set."""

    if os.environ.get("NO_COLOR"):
        return text
    prefix = ANSI.get(color, "")
    if not prefix:
        return text
    return f"{prefix}{text}{ANSI['reset']}"


def normalize_text(text: str) -> str:
    """Collapse whitespace and lowercase for comparisons."""

    return " ".join(text.strip().lower().split())


def normalize_category_slug(name: str) -> str:
    """Normalize a user-supplied category label to a slug."""

    cleaned = name.strip().lower().replace("-", "_")
    return "_".join(part for part in cleaned.split() if part)


def category_color(category: str) -> str:
    """Pick a stable console color for *category*."""

    normalized = normalize_text(category)
    if any(token in normalized for token in ("resolved", "fix", "success")):
        return "green"
    if any(token in normalized for token in ("warn", "warning", "caution", "open")):
        return "yellow"
    if any(
        token in normalized
        for token in ("error", "failure", "fatal", "exception", "crash", "bug")
    ):
        return "red"
    digest = hashlib.sha1(normalized.encode("utf-8")).digest()[0]
    return FALLBACK_CATEGORY_COLORS[digest % len(FALLBACK_CATEGORY_COLORS)]


# First matching rule wins (order matters).
_INFERENCE_RULES: Final[tuple[tuple[str, tuple[str, ...]], ...]] = (
    (
        "tool_failure",
        (
            "tool call",
            "tool_use",
            "tool failure",
            "mcp",
            "function call",
            "plugin",
            "subprocess",
            "command failed",
            "exit code",
            "invocation",
        ),
    ),
    (
        "context_limit",
        (
            "context limit",
            "context length",
            "token limit",
            "max tokens",
            "too many tokens",
            "window",
            "input too long",
            "truncated prompt",
            "sequence length",
        ),
    ),
    (
        "authentication_error",
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
        "api_error",
        (
            "429",
            "rate limit",
            "too many requests",
            "502",
            "503",
            "504",
            "bad gateway",
            "service unavailable",
            "gateway timeout",
            "openai",
            "anthropic",
            "provider error",
            "api error",
            "http 5",
        ),
    ),
    (
        "network_error",
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
        "parsing_error",
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
        "configuration_error",
        (
            "config",
            "environment variable",
            "missing setting",
            "invalid option",
            "path not found",
            "file not found",
            "not configured",
        ),
    ),
    (
        "resource_limit",
        (
            "memory",
            "oom",
            "disk",
            "space",
            "quota",
            "out of memory",
        ),
    ),
    (
        "logic_error",
        (
            "wrong conclusion",
            "incorrect reasoning",
            "logical error",
            "mistaken",
            "flawed",
            "wrong approach",
            "hallucination",
            "contradiction",
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


def infer_canonical_category(
    error_type: str,
    description: str,
    fix: str,
    *,
    context: str = "",
) -> str:
    """Infer a canonical category from free-form agent text.

    If *error_type* is already a known canonical slug, it is returned.
    Otherwise the combined text is matched against ordered keyword rules.
    """

    slug = normalize_category_slug(error_type)
    if slug in _CANONICAL_SET:
        return slug
    blob = normalize_text(f"{error_type} {description} {fix} {context}")
    for category, tokens in _INFERENCE_RULES:
        if any(token in blob for token in tokens):
            return category
    return "unknown"


def entry_signature(entry: Mapping[str, Any]) -> str:
    """Normalized signature for clustering (based on failure description)."""

    return error_signature(str(entry.get("description", "")))


def count_signatures(entries: Iterable[Mapping[str, Any]]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for entry in entries:
        sig = entry_signature(entry)
        if sig:
            counts[sig] += 1
    return counts


def iter_insight_error_lines(
    learnings_root: Path, *, max_files: int = 40, max_lines_per_file: int = 80
) -> list[str]:
    """Collect likely failure lines from ``.learnings/insights`` markdown (optional signal)."""

    insights = learnings_root / "insights"
    if not insights.is_dir():
        return []

    paths = sorted(insights.glob(INSIGHTS_GLOB), key=lambda p: p.stat().st_mtime, reverse=True)[
        :max_files
    ]
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


def canonical_payload(
    error_type: str,
    description: str,
    fix: str,
    *,
    context: str,
    category: str,
    resolved: bool,
) -> dict[str, object]:
    """Payload used for stable IDs and deduplication."""

    return {
        "error_type": normalize_text(error_type),
        "description": normalize_text(description),
        "fix": normalize_text(fix),
        "context": normalize_text(context),
        "category": normalize_category_slug(category),
        "resolved": bool(resolved),
    }


def build_entry(
    error_type: str,
    description: str,
    fix: str,
    *,
    context: str = "",
    resolved: bool = True,
    timestamp: str | None = None,
    category: str | None = None,
) -> dict[str, object]:
    """Construct a new log entry dict matching :class:`ErrorLearningEntry`."""

    cat = category or infer_canonical_category(error_type, description, fix, context=context)
    if cat not in _CANONICAL_SET:
        cat = "unknown"
    payload = canonical_payload(
        error_type, description, fix, context=context, category=cat, resolved=resolved
    )
    digest = hashlib.sha1(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:12]
    created_at = timestamp or datetime.now(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )
    return {
        "id": digest,
        "timestamp": created_at,
        "error_type": error_type.strip(),
        "category": cat,
        "description": description.strip(),
        "context": context.strip(),
        "fix": fix.strip(),
        "resolved": bool(resolved),
    }


def _migrate_v1_entry(raw: Mapping[str, Any]) -> dict[str, object]:
    """Upgrade a version-1 row to the current schema."""

    category_hint = str(raw.get("category", ""))
    description = str(raw.get("error", ""))
    fix = str(raw.get("lesson", ""))
    resolved = bool(raw.get("resolved", False))
    ts = str(raw.get("timestamp", ""))
    entry_id = str(raw.get("id", ""))
    cat = infer_canonical_category(category_hint, description, fix, context="")
    row = build_entry(
        category_hint,
        description,
        fix,
        context="",
        resolved=resolved,
        timestamp=ts or None,
        category=cat,
    )
    if entry_id.strip():
        row["id"] = entry_id.strip()
    return row


def default_store() -> dict[str, object]:
    """Empty store document."""

    return {"schema_version": SCHEMA_VERSION, "entries": []}


def validate_entry(raw_entry: object) -> dict[str, object]:
    """Validate and normalize a single persisted entry."""

    if not isinstance(raw_entry, dict):
        raise ErrorLearningError("Each entry in the error log must be a JSON object.")

    entry = dict(raw_entry)

    # Legacy v1 field names
    if "description" not in entry and "error" in entry:
        entry = _migrate_v1_entry(entry)
        return validate_entry(entry)

    required_text = ("timestamp", "error_type", "category", "description", "fix")
    for field in required_text:
        value = entry.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ErrorLearningError(f"Entry field '{field}' must be a non-empty string.")

    ctx = entry.get("context", "")
    if ctx is None:
        entry["context"] = ""
    elif not isinstance(ctx, str):
        raise ErrorLearningError("Entry field 'context' must be a string.")
    else:
        entry["context"] = ctx.strip()

    resolved = entry.get("resolved", True)
    if not isinstance(resolved, bool):
        raise ErrorLearningError("Entry field 'resolved' must be a boolean.")

    cat = normalize_category_slug(str(entry["category"]))
    if cat not in _CANONICAL_SET:
        entry["category"] = infer_canonical_category(
            str(entry["error_type"]),
            str(entry["description"]),
            str(entry["fix"]),
            context=str(entry["context"]),
        )
    else:
        entry["category"] = cat

    if not isinstance(entry.get("id"), str) or not str(entry["id"]).strip():
        rebuilt = build_entry(
            str(entry["error_type"]),
            str(entry["description"]),
            str(entry["fix"]),
            context=str(entry["context"]),
            resolved=resolved,
            timestamp=str(entry["timestamp"]),
            category=str(entry["category"]),
        )
        entry["id"] = str(rebuilt["id"])

    entry["resolved"] = resolved
    return entry


def load_store(log_path: Path) -> dict[str, object]:
    """Load the JSON store from disk (tolerates legacy list-only files)."""

    if not log_path.exists():
        return default_store()

    try:
        raw = json.loads(log_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ErrorLearningError(f"Unable to parse JSON from {log_path}: {exc}") from exc

    if isinstance(raw, list):
        migrated = [_migrate_v1_entry(item) if isinstance(item, dict) else item for item in raw]
        entries = [validate_entry(item) for item in migrated]
        return {"schema_version": SCHEMA_VERSION, "entries": entries}

    if not isinstance(raw, dict):
        raise ErrorLearningError("Error log must contain a JSON object or list of entries.")

    version = int(raw.get("schema_version", 1))
    raw_entries = raw.get("entries", [])
    if not isinstance(raw_entries, list):
        raise ErrorLearningError("Error log field 'entries' must be a JSON array.")

    if version < 2:
        migrated = []
        for item in raw_entries:
            if isinstance(item, dict) and "description" not in item and "error" in item:
                migrated.append(_migrate_v1_entry(item))
            else:
                migrated.append(item)
        raw_entries = migrated

    return {
        "schema_version": SCHEMA_VERSION,
        "entries": [validate_entry(item) for item in raw_entries],
    }


def save_store(log_path: Path, store: dict[str, object]) -> None:
    """Atomically persist the store as indented UTF-8 JSON."""

    log_path.parent.mkdir(parents=True, exist_ok=True)
    store_out = dict(store)
    store_out["schema_version"] = SCHEMA_VERSION
    payload = json.dumps(store_out, indent=2, ensure_ascii=False) + "\n"
    fd, tmp_name = tempfile.mkstemp(
        dir=str(log_path.parent),
        prefix=log_path.name + ".",
        suffix=".tmp",
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        tmp_path.replace(log_path)
    except BaseException:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def entries_match(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    """Return True when two entries represent the same learning."""

    if str(left.get("id")) == str(right.get("id")) and str(left.get("id", "")).strip():
        return True
    return canonical_payload(
        str(left["error_type"]),
        str(left["description"]),
        str(left["fix"]),
        context=str(left.get("context", "")),
        category=str(left["category"]),
        resolved=bool(left["resolved"]),
    ) == canonical_payload(
        str(right["error_type"]),
        str(right["description"]),
        str(right["fix"]),
        context=str(right.get("context", "")),
        category=str(right["category"]),
        resolved=bool(right["resolved"]),
    )


def log_agent_error(
    log_path: Path,
    error_type: str,
    description: str,
    fix: str,
    *,
    context: str = "",
    resolved: bool = True,
) -> tuple[dict[str, object], bool]:
    """Append a learning unless an equivalent record already exists.

    Returns ``(entry, created)`` where *created* is False on deduplicated skips.
    """

    store = load_store(log_path)
    category = infer_canonical_category(error_type, description, fix, context=context)
    new_entry = build_entry(
        error_type,
        description,
        fix,
        context=context,
        resolved=resolved,
        category=category,
    )
    entries_obj = store["entries"]
    assert isinstance(entries_obj, list)
    entries: list[dict[str, object]] = entries_obj
    for item in entries:
        validated = validate_entry(item)
        if entries_match(validated, new_entry):
            return validated, False

    entries.append(new_entry)
    entries.sort(key=lambda item: (str(item["timestamp"]), str(item["id"])), reverse=True)
    save_store(log_path, store)
    return new_entry, True


def retrieve_past_errors(
    log_path: Path | None = None,
    *,
    category: str | None = None,
    limit: int = 50,
    query: str | None = None,
    unresolved_only: bool = False,
) -> list[dict[str, object]]:
    """Return past errors and fixes, newest first.

    *category* filters by canonical slug (for example ``api_error``). *query* performs
    a lightweight fuzzy match across type, description, context, and fix.
    """

    path = log_path or DEFAULT_LOG_PATH
    store = load_store(path)
    raw_entries = store["entries"]
    assert isinstance(raw_entries, list)
    entries = [validate_entry(item) for item in raw_entries]

    if unresolved_only:
        entries = [e for e in entries if not bool(e["resolved"])]

    if category is not None:
        want = normalize_category_slug(category)
        entries = [e for e in entries if str(e["category"]) == want]

    if query and query.strip():
        ranked: list[tuple[float, dict[str, object]]] = []
        for entry in entries:
            score = search_score(
                query,
                entry,
            )
            if score >= 0.45:
                ranked.append((score, entry))
        ranked.sort(key=lambda item: (-item[0], str(item[1]["timestamp"]), str(item[1]["id"])))
        entries = [e for _, e in ranked]
    else:
        entries.sort(key=lambda item: (str(item["timestamp"]), str(item["id"])), reverse=True)

    cap = max(1, limit)
    return entries[:cap]


def related_same_signature(
    entries: Iterable[Mapping[str, Any]],
    target: Mapping[str, Any],
) -> list[dict[str, object]]:
    """Other entries sharing the same normalized failure shape as *target*."""

    sig = entry_signature(target)
    if not sig:
        return []
    out: list[dict[str, object]] = []
    tid = str(target.get("id", ""))
    for entry in entries:
        if str(entry.get("id")) == tid:
            continue
        if entry_signature(entry) == sig:
            out.append(dict(entry))
    return out


def format_compact_neighbor(entry: Mapping[str, Any]) -> str:
    """Single-line related learning for CLI hints."""

    desc = str(entry["description"]).replace("\n", " ")
    if len(desc) > 100:
        desc = desc[:97] + "..."
    fix = str(entry["fix"])
    if len(fix) > 120:
        fix = fix[:117] + "..."
    st = "resolved" if bool(entry["resolved"]) else "open"
    return (
        f"  {colorize(f'[{st}]', 'green' if st == 'resolved' else 'yellow')} "
        f"{colorize(str(entry['id']), 'cyan')}: "
        f"{colorize(desc, 'red')} → {colorize(fix, 'green')}"
    )


def format_entry(
    entry: Mapping[str, Any],
    *,
    sig_counts: Counter[str] | None = None,
    action_first: bool = False,
) -> str:
    """Render one entry for terminal output."""

    error_type = str(entry["error_type"])
    resolved = bool(entry["resolved"])
    status_text = "resolved" if resolved else "open"
    status_color = "green" if resolved else "yellow"
    cat = str(entry["category"])
    sig = entry_signature(entry)
    recur = ""
    if sig_counts and sig and sig_counts[sig] > 1:
        recur = colorize(f" [pattern ×{sig_counts[sig]}]", "yellow")

    header = (
        f"{colorize(error_type, category_color(error_type))} / "
        f"{colorize(cat, 'cyan')} "
        f"{colorize(f'[{status_text}]', status_color)}{recur} "
        f"{colorize(str(entry['timestamp']), 'cyan')}"
    )
    meta = f"  {colorize('ID:', 'yellow')} {entry['id']}"
    desc_line = f"  {colorize('What went wrong:', 'red')} {entry['description']}"
    ctx = str(entry.get("context") or "").strip()
    ctx_line = f"  {colorize('Context:', 'yellow')} {ctx}" if ctx else ""
    fix_line = f"  {colorize('Fix applied:', 'green')} {entry['fix']}"

    blocks = [header, meta]
    if action_first:
        blocks.extend([fix_line, desc_line])
    else:
        blocks.extend([desc_line])
        if ctx_line:
            blocks.append(ctx_line)
        blocks.append(fix_line)
    return "\n".join(blocks)


def print_entries(
    entries: Sequence[Mapping[str, Any]],
    *,
    heading: str,
    sig_counts: Counter[str] | None = None,
    action_first: bool = False,
) -> None:
    """Print a block of entries."""

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
    """Print counts by user *error_type* and canonical *category*."""

    print(colorize("Error learning stats", "bold"))
    print(colorize("====================", "cyan"))
    if not entries:
        print(colorize("No entries found.", "yellow"))
        return

    total = len(entries)
    by_type = Counter(str(e["error_type"]) for e in entries)
    by_cat = Counter(str(e["category"]) for e in entries)

    print(colorize("By error_type", "bold"))
    for label, count in sorted(by_type.items(), key=lambda item: (-item[1], item[0].lower())):
        share = (count / total) * 100
        print(
            f"- {colorize(label, category_color(label))}: "
            f"{colorize(str(count), 'red')} ({share:.1f}%)"
        )

    print()
    print(colorize("By auto category", "bold"))
    for label, count in sorted(by_cat.items(), key=lambda item: (-item[1], item[0].lower())):
        share = (count / total) * 100
        print(
            f"- {colorize(label, 'cyan')}: {colorize(str(count), 'red')} ({share:.1f}%)"
        )


def search_score(query: str, entry: Mapping[str, Any]) -> float:
    """Score relevance of *entry* to *query*."""

    normalized_query = normalize_text(query)
    haystack = normalize_text(
        " ".join(
            (
                str(entry["error_type"]),
                str(entry["category"]),
                str(entry["description"]),
                str(entry.get("context", "")),
                str(entry["fix"]),
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


def suggest_score(query: str, entry: Mapping[str, Any], counts: Counter[str]) -> float:
    """Rank entries for remediation suggestions."""

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


def search_entries(
    entries: list[dict[str, object]], query: str, limit: int = 10
) -> list[dict[str, object]]:
    """Return best-matching entries for *query*."""

    ranked: list[tuple[float, dict[str, object]]] = []
    for entry in entries:
        score = search_score(query, entry)
        if score >= 0.45:
            ranked.append((score, entry))
    ranked.sort(key=lambda item: (-item[0], str(item[1]["timestamp"])))
    return [entry for _, entry in ranked[: max(limit, 1)]]


def suggest_entries(
    entries: list[dict[str, object]], query: str, limit: int = 8
) -> list[dict[str, object]]:
    """Suggest fixes for a new failure string."""

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
    """Clusters with at least *min_count* occurrences."""

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
    """Print recurring failure shapes with the best-known fix."""

    print(colorize("Recurring error patterns", "bold"))
    print(colorize("========================", "cyan"))
    rows = recurring_pattern_rows(entries, min_count=min_count, limit=limit)
    if not rows:
        print(colorize("No repeated signatures yet (need ≥2 similar errors).", "yellow"))
    else:
        for idx, (count, sig, rep) in enumerate(rows):
            if idx:
                print()
            cat = str(rep["category"])
            preview = sig if len(sig) <= 140 else sig[:137] + "..."
            print(
                f"{colorize(f'×{count}', 'red')} {colorize(cat, 'cyan')} "
                f"{colorize(str(rep['error_type']), category_color(str(rep['error_type'])))}"
            )
            print(f"  {colorize('Shape:', 'yellow')} {preview}")
            print(f"  {colorize('Fix:', 'green')} {rep['fix']}")
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
                tail = "…" if len(line) > 240 else ""
                print(f"  {colorize('·', 'yellow')} {line[:240]}{tail}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""

    parser = argparse.ArgumentParser(
        description="Self-improvement error learning for agent runs.",
    )
    parser.add_argument(
        "--log-path",
        type=Path,
        default=DEFAULT_LOG_PATH,
        help="Path to the JSON error learning log.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    log_parser = subparsers.add_parser(
        "log",
        help="Record what went wrong, optional context, and the fix applied.",
    )
    log_parser.add_argument("error_type", help="Agent-supplied type or hint (also used for inference).")
    log_parser.add_argument("description", help="What went wrong (failure summary).")
    log_parser.add_argument("fix", help="Fix or mitigation applied.")
    log_parser.add_argument(
        "--context",
        default="",
        help="Additional context (paths, tool names, session id, etc.).",
    )
    resolved_group = log_parser.add_mutually_exclusive_group()
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
        help="Mark as still open.",
    )

    show_parser = subparsers.add_parser("show", help="Display stored learnings.")
    show_parser.add_argument(
        "--category",
        default=None,
        help="Filter by canonical category (e.g. api_error, tool_failure).",
    )
    show_parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum entries to print (default: 10).",
    )
    show_parser.add_argument(
        "--unresolved",
        action="store_true",
        help="Only show entries still marked unresolved.",
    )

    subparsers.add_parser("stats", help="Show frequency by error_type and canonical category.")

    search_parser = subparsers.add_parser("search", help="Search learnings by keywords.")
    search_parser.add_argument("query", help="Search query.")
    search_parser.add_argument("--limit", type=int, default=10, help="Max results.")

    suggest_parser = subparsers.add_parser(
        "suggest",
        help="Recommend fixes for a failure description (boosts recurring shapes).",
    )
    suggest_parser.add_argument("error_text", help="Error message or short failure description.")
    suggest_parser.add_argument("--limit", type=int, default=8, help="Max suggestions.")

    patterns_parser = subparsers.add_parser(
        "patterns",
        help="List recurring normalized failure shapes with a representative fix.",
    )
    patterns_parser.add_argument("--min-count", type=int, default=2, help="Minimum occurrences.")
    patterns_parser.add_argument("--limit", type=int, default=25, help="Max pattern rows.")
    patterns_parser.add_argument(
        "--include-insights",
        action="store_true",
        help="Append failure-like lines from .learnings/insights when present.",
    )

    # Backward-compatible aliases (older scripts and tests).
    add_parser = subparsers.add_parser(
        "add",
        help="Deprecated: same as log.",
    )
    add_parser.add_argument("error_category", help=argparse.SUPPRESS)
    add_parser.add_argument("error_message", help=argparse.SUPPRESS)
    add_parser.add_argument("lesson_learned", help=argparse.SUPPRESS)
    add_parser.add_argument("--context", default="", help=argparse.SUPPRESS)
    add_resolved = add_parser.add_mutually_exclusive_group()
    add_resolved.add_argument("--resolved", dest="resolved", action="store_true", default=True)
    add_resolved.add_argument("--unresolved", dest="resolved", action="store_false")

    subparsers.add_parser("list", help="Deprecated: same as show without filters.")

    subparsers.add_parser("open", help="Deprecated: same as show --unresolved.")

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint."""

    args = parse_args(argv)
    try:
        store = load_store(args.log_path)
    except ErrorLearningError as exc:
        print(colorize(str(exc), "red"), file=sys.stderr)
        return 1

    entries_raw = store["entries"]
    assert isinstance(entries_raw, list)

    if args.command == "log":
        try:
            entry, created = log_agent_error(
                args.log_path,
                args.error_type,
                args.description,
                args.fix,
                context=args.context,
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

    if args.command == "add":
        try:
            entry, created = log_agent_error(
                args.log_path,
                args.error_category,
                args.error_message,
                args.lesson_learned,
                context=getattr(args, "context", "") or "",
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

    validated_entries = [validate_entry(item) for item in entries_raw]
    all_counts = count_signatures(validated_entries)

    if args.command == "list":
        print_entries(
            validated_entries,
            heading="Agent error learnings",
            sig_counts=all_counts,
        )
        return 0

    if args.command == "open":
        open_entries = [item for item in validated_entries if not bool(item["resolved"])]
        print_entries(open_entries, heading="Open (unresolved) learnings", sig_counts=all_counts)
        return 0

    if args.command == "show":
        subset = retrieve_past_errors(
            args.log_path,
            category=args.category,
            limit=max(args.limit, 1),
            unresolved_only=bool(args.unresolved),
        )
        title = "Agent error learnings"
        if args.category:
            title += f" (category={args.category})"
        if args.unresolved:
            title += " (unresolved only)"
        print_entries(subset, heading=title, sig_counts=all_counts)
        return 0

    if args.command == "stats":
        print_stats(validated_entries)
        return 0

    if args.command == "search":
        matches = search_entries(validated_entries, args.query, limit=max(args.limit, 1))
        print_entries(matches, heading=f"Search results: {args.query}", sig_counts=all_counts)
        return 0

    if args.command == "suggest":
        matches = suggest_entries(
            validated_entries,
            args.error_text,
            limit=max(args.limit, 1),
        )
        preview = args.error_text[:120] + ("…" if len(args.error_text) > 120 else "")
        print_entries(
            matches,
            heading=f"Suggested fixes for: {preview}",
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


# Backward-compatible name used by older callers and tests.
add_entry = log_agent_error

__all__ = [
    "CANONICAL_CATEGORIES",
    "DEFAULT_LOG_PATH",
    "ErrorLearningEntry",
    "ErrorLearningError",
    "add_entry",
    "infer_canonical_category",
    "log_agent_error",
    "main",
    "retrieve_past_errors",
]


if __name__ == "__main__":
    raise SystemExit(main())
