#!/usr/bin/env python3
"""Capture and learn from recurring OpenClaw session errors.

Clusters similar failures via normalized signatures, infers triage buckets, and
exposes ``patterns``, ``suggest``, and ``open`` commands for faster remediation.

Also scans ``.learnings/**/*.md`` for structured corrections (YAML front matter
and/or markdown section headings), supports similarity search over those
records, and can refresh ``.learnings/auto/error_summary.md`` with recurring
themes drawn from both the JSON log and markdown sources.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOG_PATH = ROOT / ".learnings" / "error_log.json"
SCHEMA_VERSION = 1
INSIGHTS_GLOB = "run_*.md"
AUTO_SUBDIR = "auto"
ERROR_SUMMARY_FILENAME = "error_summary.md"
ERRORS_SUBDIR = "errors"
SUMMARY_RELATIVE_PATH = f"{AUTO_SUBDIR}/{ERROR_SUMMARY_FILENAME}"
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


@dataclass(frozen=True, slots=True)
class MarkdownLearning:
    """Structured correction parsed from markdown under ``.learnings/``."""

    relative_path: str
    ordinal: int
    error_pattern: str
    root_cause: str
    fix: str
    tags: tuple[str, ...] = ()


# Maps normalized heading text to canonical field names.
_MD_SECTION_ALIASES: dict[str, str] = {
    "error pattern": "error_pattern",
    "error description": "error_pattern",
    "symptom": "error_pattern",
    "error": "error_pattern",
    "root cause": "root_cause",
    "what went wrong": "root_cause",
    "cause": "root_cause",
    "fix": "fix",
    "what fixed it": "fix",
    "resolution": "fix",
    "corrective action": "fix",
    "lesson learned": "fix",
    "tags": "tags",
}

_FM_KEY_ALIASES: dict[str, str] = {
    "error_pattern": "error_pattern",
    "error": "error_pattern",
    "pattern": "error_pattern",
    "symptom": "error_pattern",
    "root_cause": "root_cause",
    "cause": "root_cause",
    "what_went_wrong": "root_cause",
    "fix": "fix",
    "resolution": "fix",
    "lesson": "fix",
    "what_fixed_it": "fix",
    "tags": "tags",
}


def learnings_dir_from_log(log_path: Path) -> Path:
    """Return the ``.learnings`` directory that owns ``error_log.json``."""

    return log_path.parent


def _relative_posix(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def iter_learnings_markdown_paths(learnings_root: Path) -> Iterator[Path]:
    """Yield every ``*.md`` file under ``learnings_root`` except the auto summary output."""

    if not learnings_root.is_dir():
        return
    for path in sorted(learnings_root.rglob("*.md")):
        rel = _relative_posix(learnings_root, path)
        if rel == SUMMARY_RELATIVE_PATH:
            continue
        yield path


def _parse_tags_value(raw: str) -> tuple[str, ...]:
    text = raw.strip()
    if not text:
        return ()
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        if not inner:
            return ()
        parts = [p.strip().strip("'\"") for p in inner.split(",")]
        return tuple(p for p in parts if p)
    return tuple(t.strip() for t in re.split(r"[,;]+", text) if t.strip())


def _parse_flat_front_matter_block(block: str) -> dict[str, str]:
    """Parse simple ``key: value`` lines (no nested YAML)."""

    out: dict[str, str] = {}
    for raw_line in block.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        canon = _FM_KEY_ALIASES.get(key.strip().lower().replace(" ", "_"))
        if not canon:
            continue
        out[canon] = value.strip().strip("\"'")
    return out


def _extract_leading_front_matter(text: str) -> tuple[dict[str, str], str]:
    """Split optional YAML-like front matter from the remainder of the file."""

    stripped = text.lstrip("\ufeff")
    lines = stripped.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, stripped

    meta_lines: list[str] = []
    i = 1
    while i < len(lines):
        if lines[i].strip() == "---":
            body = "\n".join(lines[i + 1 :])
            return _parse_flat_front_matter_block("\n".join(meta_lines)), body
        meta_lines.append(lines[i])
        i += 1
    return {}, stripped


def _normalize_section_title(title: str) -> str:
    inner = title.strip().lower()
    inner = re.sub(r"[`_*]+", "", inner)
    return " ".join(inner.split())


def _parse_markdown_sections(body: str) -> dict[str, str]:
    """Parse ``## Heading`` sections into canonical keys."""

    pattern = re.compile(r"^##\s+(.+)\s*$", re.MULTILINE)
    matches = list(pattern.finditer(body))
    out: dict[str, str] = {}
    if not matches:
        return out

    for idx, match in enumerate(matches):
        raw_title = match.group(1)
        key = _MD_SECTION_ALIASES.get(_normalize_section_title(raw_title))
        if not key:
            continue
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(body)
        chunk = body[start:end].strip()
        if chunk:
            out[key] = chunk
    return out


def _coalesce_learning_fields(meta: dict[str, str], sections: dict[str, str]) -> dict[str, str]:
    merged: dict[str, str] = {}
    for key in ("error_pattern", "root_cause", "fix"):
        merged[key] = (sections.get(key) or meta.get(key) or "").strip()
    tags_raw = (sections.get("tags") or meta.get("tags") or "").strip()
    merged["tags"] = tags_raw
    return merged


def _learning_from_fields(
    rel_path: str,
    ordinal: int,
    fields: dict[str, str],
) -> MarkdownLearning | None:
    ep = fields.get("error_pattern", "").strip()
    rc = fields.get("root_cause", "").strip()
    fx = fields.get("fix", "").strip()
    if not (ep or rc or fx):
        return None
    tags = _parse_tags_value(fields.get("tags", ""))
    return MarkdownLearning(
        relative_path=rel_path,
        ordinal=ordinal,
        error_pattern=ep,
        root_cause=rc,
        fix=fx,
        tags=tags,
    )


def parse_markdown_file(path: Path, *, learnings_root: Path) -> list[MarkdownLearning]:
    """Parse every structured learning block found in a single markdown file."""

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    rel = _relative_posix(learnings_root, path)
    out: list[MarkdownLearning] = []
    ordinal = 0

    meta, body = _extract_leading_front_matter(text)
    sections = _parse_markdown_sections(body)
    merged = _coalesce_learning_fields(meta, sections)
    item = _learning_from_fields(rel, ordinal, merged)
    if item:
        out.append(item)
    return out


def scan_markdown_learnings(learnings_root: Path) -> list[MarkdownLearning]:
    """Scan ``.learnings/**/*.md`` and return all structured learnings."""

    found: list[MarkdownLearning] = []
    for md_path in iter_learnings_markdown_paths(learnings_root):
        found.extend(parse_markdown_file(md_path, learnings_root=learnings_root))
    return found


def markdown_learning_text_blob(item: MarkdownLearning) -> str:
    """Flatten searchable fields for keyword / fuzzy similarity."""

    tag_blob = " ".join(item.tags)
    return normalize_text(" ".join((item.error_pattern, item.root_cause, item.fix, tag_blob)))


def markdown_similarity_score(query: str, item: MarkdownLearning) -> float:
    """Score markdown learnings using token overlap and fuzzy ratios (no embeddings)."""

    normalized_query = normalize_text(query)
    haystack = markdown_learning_text_blob(item)
    if not normalized_query or not haystack:
        return 0.0

    substring_bonus = 1.5 if normalized_query in haystack else 0.0
    query_tokens = set(normalized_query.split())
    haystack_tokens = set(haystack.split())
    overlap = len(query_tokens & haystack_tokens) / max(len(query_tokens), 1)
    ratio = SequenceMatcher(None, normalized_query, haystack).ratio()

    shape_bonus = 0.0
    qsig = error_signature(query)
    for field in (item.error_pattern, item.root_cause, item.fix):
        if not field:
            continue
        fsig = error_signature(field)
        if qsig and fsig:
            if qsig == fsig:
                shape_bonus += 2.5
            else:
                shape_bonus += SequenceMatcher(None, qsig, fsig).ratio() * 0.9
    return substring_bonus + overlap + (ratio * 0.45) + shape_bonus


def find_similar_markdown_learnings(
    query: str,
    learnings_root: Path,
    *,
    limit: int = 12,
    min_score: float = 0.55,
) -> list[tuple[float, MarkdownLearning]]:
    """Return markdown learnings ranked by lexical / pattern similarity to ``query``."""

    ranked: list[tuple[float, MarkdownLearning]] = []
    for item in scan_markdown_learnings(learnings_root):
        score = markdown_similarity_score(query, item)
        if score >= min_score:
            ranked.append((score, item))
    ranked.sort(key=lambda row: (-row[0], row[1].relative_path.lower(), row[1].ordinal))
    return ranked[: max(limit, 1)]


def json_entry_similarity_score(query: str, entry: dict[str, object]) -> float:
    """Wrap :func:`search_score` for type clarity when merging sources."""

    return search_score(query, entry)


def find_similar_learnings_unified(
    query: str,
    *,
    log_path: Path,
    learnings_root: Path | None = None,
    limit: int = 12,
) -> tuple[list[tuple[float, str, object]], list[tuple[float, MarkdownLearning]]]:
    """Return JSON log hits and markdown hits for the same natural-language query."""

    root = learnings_root or learnings_dir_from_log(log_path)
    json_hits: list[tuple[float, str, object]] = []
    store = load_store(log_path)
    raw_entries = store.get("entries", [])
    if isinstance(raw_entries, list):
        validated = [validate_entry(item) for item in raw_entries]
        for entry in validated:
            score = json_entry_similarity_score(query, entry)
            if score >= 0.45:
                json_hits.append((score, "json", entry))
        json_hits.sort(key=lambda row: (-row[0], str(row[2].get("timestamp", ""))))  # type: ignore[union-attr]

    md_hits = find_similar_markdown_learnings(query, root, limit=limit)
    return json_hits[:limit], md_hits[:limit]


def _signature_for_summary(item: MarkdownLearning) -> str:
    blob = item.error_pattern or item.root_cause or item.fix
    sig = error_signature(blob)
    return sig or normalize_text(blob)


def _summary_rows_from_markdown(items: list[MarkdownLearning]) -> list[tuple[int, str, MarkdownLearning]]:
    counts = Counter(_signature_for_summary(item) for item in items if _signature_for_summary(item))
    buckets: dict[str, list[MarkdownLearning]] = defaultdict(list)
    for item in items:
        sig = _signature_for_summary(item)
        if sig:
            buckets[sig].append(item)

    rows: list[tuple[int, str, MarkdownLearning]] = []
    for sig, count in counts.items():
        group = buckets.get(sig, [])
        if not group:
            continue
        rep = max(group, key=lambda it: len(it.error_pattern))
        rows.append((count, sig, rep))
    rows.sort(key=lambda row: (-row[0], -len(row[1])))
    return rows


def _summary_rows_from_json(entries: list[dict[str, object]]) -> list[tuple[int, str, dict[str, object]]]:
    return recurring_pattern_rows(entries, min_count=1, limit=500)


def write_error_summary_md(
    learnings_root: Path,
    log_path: Path,
    *,
    out_path: Path | None = None,
    top_markdown: int = 15,
    top_json: int = 15,
) -> Path:
    """Write ``.learnings/auto/error_summary.md`` with recurring themes."""

    destination = out_path or (learnings_root / AUTO_SUBDIR / ERROR_SUMMARY_FILENAME)
    destination.parent.mkdir(parents=True, exist_ok=True)

    md_items = scan_markdown_learnings(learnings_root)
    md_rows = _summary_rows_from_markdown(md_items)[:top_markdown]

    store = load_store(log_path)
    raw_entries = store.get("entries", [])
    json_entries = [validate_entry(item) for item in raw_entries] if isinstance(raw_entries, list) else []
    json_rows = _summary_rows_from_json(json_entries)[:top_json]

    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    lines: list[str] = [
        "# Error learning summary",
        "",
        f"_Generated {stamp} (UTC) by ``scripts/error_learning.py``._",
        "",
        "This file consolidates the most recurring failure shapes discovered in the JSON "
        "error log plus structured markdown learnings under ``.learnings/``. Regenerate with "
        "``python3 scripts/error_learning.py write-summary``.",
        "",
        "## Top recurring patterns (JSON log)",
        "",
    ]

    if not json_rows:
        lines.append("_No JSON log entries yet._")
        lines.append("")
    else:
        for idx, (count, sig, rep) in enumerate(json_rows, start=1):
            bucket = infer_bucket(str(rep["category"]), str(rep["error"]), str(rep["lesson"]))
            preview = sig if len(sig) <= 160 else sig[:157] + "..."
            lines.append(f"{idx}. **×{count}** `{bucket}` — category _{rep['category']}_")
            lines.append(f"   - **Shape:** {preview}")
            lines.append(f"   - **Action:** {rep['lesson']}")
            lines.append("")

    lines.extend(
        [
            "## Top recurring themes (markdown learnings)",
            "",
        ]
    )
    if not md_rows:
        lines.append("_No structured markdown entries detected (add sections or front matter)._")
        lines.append("")
    else:
        for idx, (count, sig, rep) in enumerate(md_rows, start=1):
            preview = sig if len(sig) <= 160 else sig[:157] + "..."
            tag_txt = ", ".join(rep.tags) if rep.tags else "_none_"
            lines.append(f"{idx}. **×{count}** — `{rep.relative_path}`")
            lines.append(f"   - **Signature:** {preview}")
            if rep.error_pattern:
                lines.append(f"   - **Error pattern:** {rep.error_pattern}")
            if rep.root_cause:
                lines.append(f"   - **Root cause:** {rep.root_cause}")
            if rep.fix:
                lines.append(f"   - **Fix:** {rep.fix}")
            lines.append(f"   - **Tags:** {tag_txt}")
            lines.append("")

    destination.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return destination


def append_interactive_markdown_entry(
    learnings_root: Path,
    *,
    error_pattern: str,
    root_cause: str,
    fix: str,
    tags: tuple[str, ...],
) -> Path:
    """Persist a new markdown learning under ``.learnings/errors/``."""

    def _yaml_scalar(value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            return '""'
        return json.dumps(cleaned, ensure_ascii=False)

    target_dir = learnings_root / ERRORS_SUBDIR
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    digest = hashlib.sha1(
        json.dumps(
            {
                "error_pattern": normalize_text(error_pattern),
                "root_cause": normalize_text(root_cause),
                "fix": normalize_text(fix),
                "tags": list(tags),
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:8]
    path = target_dir / f"entry_{stamp}_{digest}.md"
    tag_line = ", ".join(tags) if tags else ""
    body = "\n".join(
        (
            "---",
            f"error_pattern: {_yaml_scalar(error_pattern)}",
            f"root_cause: {_yaml_scalar(root_cause)}",
            f"fix: {_yaml_scalar(fix)}",
            f"tags: {tag_line}",
            "---",
            "",
            "## Error pattern",
            error_pattern.strip(),
            "",
            "## What went wrong",
            root_cause.strip(),
            "",
            "## What fixed it",
            fix.strip(),
            "",
            "## Tags",
            tag_line or "_none_",
            "",
        )
    )
    path.write_text(body + "\n", encoding="utf-8")
    return path


def prompt_interactive_entry(learnings_root: Path) -> int:
    """Prompt for fields and save a markdown learning entry."""

    print(colorize("Interactive error learning entry", "bold"))
    print(colorize("Press Ctrl+C to cancel.", "yellow"))
    try:
        error_pattern = input(colorize("Error description / pattern: ", "cyan")).strip()
        root_cause = input(colorize("What went wrong (root cause): ", "cyan")).strip()
        fix = input(colorize("What fixed it: ", "cyan")).strip()
        tags_raw = input(colorize("Tags (comma-separated, optional): ", "cyan")).strip()
    except EOFError:
        print(colorize("Cancelled (EOF).", "yellow"))
        return 1

    if not (error_pattern or root_cause or fix):
        print(colorize("Nothing to save — at least one field must be non-empty.", "red"))
        return 1

    tags = tuple(t.strip() for t in tags_raw.replace(";", ",").split(",") if t.strip())
    path = append_interactive_markdown_entry(
        learnings_root,
        error_pattern=error_pattern or "(unspecified)",
        root_cause=root_cause or "(unspecified)",
        fix=fix or "(unspecified)",
        tags=tags,
    )
    try:
        shown = str(path.relative_to(ROOT))
    except ValueError:
        shown = str(path)
    print(colorize(f"Wrote {shown}", "green"))
    return 0


def print_markdown_learning(item: MarkdownLearning, *, score: float | None = None) -> None:
    """Pretty-print a markdown-derived learning."""

    header_bits = [colorize(item.relative_path, "cyan"), f"#{item.ordinal}"]
    if score is not None:
        header_bits.append(colorize(f"score={score:.2f}", "yellow"))
    print(" ".join(header_bits))
    if item.error_pattern:
        print(f"  {colorize('Pattern:', 'yellow')} {item.error_pattern}")
    if item.root_cause:
        print(f"  {colorize('Cause:', 'yellow')} {item.root_cause}")
    if item.fix:
        print(f"  {colorize('Fix:', 'green')} {item.fix}")
    if item.tags:
        print(f"  {colorize('Tags:', 'yellow')} {', '.join(item.tags)}")


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
        "--learnings-dir",
        type=Path,
        default=None,
        help="Markdown learnings root (defaults to the directory containing the JSON log).",
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

    subparsers.add_parser("md-scan", help="List structured learnings parsed from .learnings/**/*.md.")

    md_search_parser = subparsers.add_parser(
        "md-search",
        help="Search markdown learnings by keyword / pattern similarity.",
    )
    md_search_parser.add_argument("query", help="Natural language or error snippet.")
    md_search_parser.add_argument(
        "--limit",
        type=int,
        default=12,
        help="Maximum number of matches to print.",
    )
    md_search_parser.add_argument(
        "--min-score",
        type=float,
        default=0.55,
        help="Minimum similarity score (0-∞, higher is stricter).",
    )

    similar_parser = subparsers.add_parser(
        "similar",
        help="Search both the JSON log and markdown learnings for a single query.",
    )
    similar_parser.add_argument("query", help="Natural language or error snippet.")
    similar_parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum number of hits per source.",
    )

    subparsers.add_parser(
        "add-interactive",
        help="Interactively capture a markdown learning entry under .learnings/errors/.",
    )

    summary_parser = subparsers.add_parser(
        "write-summary",
        help=f"Write {SUMMARY_RELATIVE_PATH} with recurring JSON + markdown themes.",
    )
    summary_parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Override output path (defaults to .learnings/auto/error_summary.md).",
    )
    summary_parser.add_argument(
        "--top-md",
        type=int,
        default=15,
        help="How many markdown clusters to include.",
    )
    summary_parser.add_argument(
        "--top-json",
        type=int,
        default=15,
        help="How many JSON signature clusters to include.",
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

    learnings_root = (args.learnings_dir or learnings_dir_from_log(args.log_path)).resolve()

    if args.command == "md-scan":
        items = scan_markdown_learnings(learnings_root)
        print(colorize(f"Markdown learnings under {learnings_root}", "bold"))
        print(colorize(f"Structured entries: {len(items)}", "cyan"))
        if not items:
            print(colorize("No entries matched the structured format.", "yellow"))
            return 0
        by_file: dict[str, list[MarkdownLearning]] = defaultdict(list)
        for item in items:
            by_file[item.relative_path].append(item)
        for rel in sorted(by_file):
            group = by_file[rel]
            print()
            print(colorize(rel, "green"), colorize(f"({len(group)})", "yellow"))
            for it in group:
                print_markdown_learning(it)
        return 0

    if args.command == "md-search":
        hits = find_similar_markdown_learnings(
            args.query,
            learnings_root,
            limit=max(args.limit, 1),
            min_score=float(args.min_score),
        )
        print(colorize(f"Markdown search: {args.query}", "bold"))
        if not hits:
            print(colorize("No markdown learnings met the score threshold.", "yellow"))
            return 0
        for idx, (score, item) in enumerate(hits):
            if idx:
                print()
            print_markdown_learning(item, score=score)
        return 0

    if args.command == "similar":
        json_hits, md_hits = find_similar_learnings_unified(
            args.query,
            log_path=args.log_path,
            learnings_root=learnings_root,
            limit=max(args.limit, 1),
        )
        print(colorize(f"Unified similarity search: {args.query}", "bold"))
        print(colorize("JSON log", "bold"))
        if not json_hits:
            print(colorize("  (no strong matches)", "yellow"))
        else:
            for score, _src, entry in json_hits:
                je = entry  # type: ignore[assignment]
                assert isinstance(je, dict)
                print(
                    f"  {colorize(f'score={score:.2f}', 'yellow')} "
                    f"{colorize(str(je.get('category')), category_color(str(je.get('category'))))}"
                )
                print(f"    {colorize('Error:', 'red')} {je.get('error')}")
                print(f"    {colorize('Action:', 'green')} {je.get('lesson')}")
        print()
        print(colorize("Markdown learnings", "bold"))
        if not md_hits:
            print(colorize("  (no strong matches)", "yellow"))
        else:
            for score, item in md_hits:
                print()
                print_markdown_learning(item, score=score)
        return 0

    if args.command == "add-interactive":
        return prompt_interactive_entry(learnings_root)

    if args.command == "write-summary":
        try:
            path = write_error_summary_md(
                learnings_root,
                args.log_path,
                out_path=args.output,
                top_markdown=max(1, args.top_md),
                top_json=max(1, args.top_json),
            )
        except ErrorLearningError as exc:
            print(colorize(str(exc), "red"), file=sys.stderr)
            return 1
        print(colorize(f"Wrote {path}", "green"))
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
            learnings_dir=learnings_root,
        )
        return 0

    print(colorize(f"Unsupported command: {args.command}", "red"), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
