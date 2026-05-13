#!/usr/bin/env python3
"""Capture and learn from recurring session errors.

Scans recent session logs (see default globs), classifies error patterns as API,
tool, or logic failures, proposes fixes, updates ``.learnings/error_log.md``, and
optionally merges distilled patterns into the JSON error log used by ``add`` /
``list`` / ``search``.

Run as:

    python -m scripts.self_improvement.error_learning analyze
    python -m scripts.self_improvement.error_learning add <category> <error> <lesson>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable, Iterator, Literal

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LOG_PATH = ROOT / ".learnings" / "error_log.json"
DEFAULT_MD_PATH = ROOT / ".learnings" / "error_log.md"
SCHEMA_VERSION = 1

LOGGER = logging.getLogger(__name__)

ERROR_TYPE = Literal["api", "tool", "logic"]

MD_AUTO_START = "<!-- error_learning:AUTO_REPORT_START -->"
MD_AUTO_END = "<!-- error_learning:AUTO_REPORT_END -->"

DEFAULT_SESSION_GLOBS = (
    "logs/**/*.log",
    "logs/**/*.json",
    "memory/**/*_log.md",
    "memory/**/*.md",
)
MAX_FILE_BYTES = 2 * 1024 * 1024

FAILURE_HINTS = re.compile(
    r"(?i)(\b(traceback|exception|error|failed|failure|timed?\s*out|exit\s*code\s*[1-9]\d*)\b|"
    r"\b(fatal|critical)\b|^Error:|\[\s*ERROR\s*\]|ECONNREFUSED|ETIMEDOUT|ENOTFOUND)"
)

API_HINTS = re.compile(
    r"(?i)(\b(?:https?://|api\.|graphql|rest\s+api|webhook)\b|"
    r"\b(?:401|403|404|408|409|422|429|500|502|503|504)\b|"
    r"\b(?:rate\s*limit|unauthorized|forbidden|bad\s+gateway|service\s+unavailable)\b|"
    r"\b(?:connection\s+refused|ssl\s+certificate|certificate\s+verify|dns)\b|"
    r"\b(?:requests\.|urllib|httpx|aiohttp|fetch\(|axios)\b|"
    r"\b(?:api[_\s-]?key|bearer\s+token|oauth)\b)"
)

TOOL_HINTS = re.compile(
    r"(?i)(\b(?:tool[_\s-]?call|tool\s+output|mcp[_\s:]|subprocess)\b|"
    r"\b(?:command\s+not\s+found|exit\s+status|exit\s+code)\b|"
    r"\b(?:permission\s+denied|enoent|not\s+a\s+directory)\b|"
    r"(?:^|\n)\s*(?:bash|zsh|sh):\s|"
    r"\b(?:terminal\s+returned|process\s+exited)\b)"
)

LOGIC_HINTS = re.compile(
    r"(?i)(\b(?:TypeError|ValueError|KeyError|AttributeError|IndexError|"
    r"AssertionError|NotImplementedError|RecursionError|ZeroDivisionError)\b|"
    r"\b(?:assertion\s+failed|nullpointer|undefined\s+is\s+not|"
    r"cannot\s+read\s+properties)\b|"
    r"\b(?:off[\s-]by[\s-]one|race\s+condition|deadlock|logic\s+error)\b)"
)

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


@dataclass
class ErrorHit:
    """One extracted error-related line or string from a session artifact."""

    text: str
    rel_path: str
    error_type: ERROR_TYPE
    recommendation: str


@dataclass
class AnalysisReport:
    """Structured outcome of a log scan."""

    started_at_utc: str
    finished_at_utc: str
    root: Path
    files_scanned: int
    hits: list[ErrorHit] = field(default_factory=list)
    skipped_files: list[str] = field(default_factory=list)

    def type_counts(self) -> Counter[ERROR_TYPE]:
        c: Counter[ERROR_TYPE] = Counter()
        for h in self.hits:
            c[h.error_type] += 1
        return c


def configure_logging(*, verbose: bool, quiet: bool) -> None:
    if quiet:
        level = logging.WARNING
    elif verbose:
        level = logging.DEBUG
    else:
        level = logging.INFO
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=level,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    root = logging.getLogger()
    root.setLevel(level)


def colorize(text: str, color: str) -> str:
    if os.environ.get("NO_COLOR"):
        return text
    prefix = ANSI.get(color, "")
    if not prefix:
        return text
    return f"{prefix}{text}{ANSI['reset']}"


def normalize_text(text: str) -> str:
    return " ".join(text.strip().lower().split())


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


def load_store(log_path: Path) -> dict[str, object]:
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
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(json.dumps(store, indent=2) + "\n", encoding="utf-8")


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


def add_entry(
    log_path: Path,
    category: str,
    error: str,
    lesson: str,
    *,
    resolved: bool = True,
) -> tuple[dict[str, object], bool]:
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


def format_entry(entry: dict[str, object]) -> str:
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


def classify_error_type(text: str) -> ERROR_TYPE:
    """Assign ``api``, ``tool``, or ``logic`` based on message heuristics."""

    if API_HINTS.search(text):
        return "api"
    if TOOL_HINTS.search(text):
        return "tool"
    if LOGIC_HINTS.search(text):
        return "logic"
    if FAILURE_HINTS.search(text):
        return "logic"
    return "logic"


def recommend_fix(error_type: ERROR_TYPE, snippet: str) -> str:
    """Return a short, actionable recommendation for operators and agents."""

    lowered = snippet.lower()
    if error_type == "api":
        if re.search(r"401|403|unauthorized|forbidden", lowered):
            return (
                "Verify credentials, token expiry, and scopes; rotate API keys if leaked in logs; "
                "confirm the correct environment (staging vs production)."
            )
        if re.search(r"429|rate\s*limit", lowered):
            return "Add exponential backoff with jitter, reduce concurrency, and respect Retry-After headers."
        if re.search(r"5\d\d|bad\s+gateway|service\s+unavailable", lowered):
            return "Treat as upstream instability: retry with backoff, check status pages, and add circuit breaking."
        if re.search(r"timeout|etimedout|408", lowered):
            return "Increase client timeouts where safe, split large requests, and verify network path and DNS."
        return "Log request id and payload shape (redacted), confirm endpoint contract, and add structured HTTP error handling."

    if error_type == "tool":
        if "permission" in lowered or "eacces" in lowered:
            return "Check file permissions, sandbox policies, and whether the workspace path is writable."
        if "not found" in lowered or "enoent" in lowered:
            return "Verify paths exist relative to cwd, pin absolute paths where needed, and guard optional tools."
        if "exit" in lowered and "code" in lowered:
            return "Capture stdout/stderr from the subprocess, assert prerequisites, and fail fast with a clear message."
        return "Validate tool inputs against a schema, add dry-run mode, and surface tool errors to the user verbatim."

    if re.search(r"typeerror|valueerror|keyerror|attributeerror", lowered):
        return "Add type checks or guards at the boundary, reproduce with a minimal fixture, and extend unit tests."
    if "assertion" in lowered:
        return "Replace brittle assertions with explicit validation errors and document invariants."
    return "Trace the failure to inputs vs assumptions, add logging at decision points, and cover with a regression test."


def _truncate_line(text: str, limit: int = 400) -> str:
    line = re.sub(r"\s+", " ", text.strip())
    if len(line) <= limit:
        return line
    return line[: limit - 3] + "..."


def iter_recent_files(
    root: Path,
    globs: Sequence[str],
    cutoff: datetime,
    *,
    max_files: int,
) -> tuple[list[Path], list[str]]:
    """Return recent files matching globs, newest first, capped by ``max_files``."""

    seen: set[Path] = set()
    out: list[Path] = []
    skipped: list[str] = []
    for pattern in globs:
        try:
            candidates = list(root.glob(pattern))
        except OSError as exc:
            LOGGER.warning("Glob %r failed under %s: %s", pattern, root, exc)
            skipped.append(f"{pattern}: {exc}")
            continue
        for path in candidates:
            if not path.is_file():
                continue
            try:
                st = path.stat()
            except OSError as exc:
                LOGGER.debug("stat failed for %s: %s", path, exc)
                continue
            if datetime.fromtimestamp(st.st_mtime, tz=timezone.utc) < cutoff:
                continue
            if st.st_size > MAX_FILE_BYTES:
                skipped.append(f"{path.relative_to(root).as_posix()}: exceeds size cap")
                continue
            rp = path.resolve()
            if rp in seen:
                continue
            seen.add(rp)
            out.append(path)
            if len(out) >= max_files:
                out.sort(key=lambda p: p.stat().st_mtime, reverse=True)
                return out[:max_files], skipped

    out.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return out[:max_files], skipped


def _walk_json_errors(obj: Any, acc: list[str]) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(k, str) and re.search(r"(?i)\b(error|stderr|detail|message|exception)\b", k):
                if isinstance(v, str) and v.strip():
                    acc.append(v.strip())
            _walk_json_errors(v, acc)
    elif isinstance(obj, list):
        for item in obj:
            _walk_json_errors(item, acc)
    elif isinstance(obj, str) and FAILURE_HINTS.search(obj):
        acc.append(obj.strip())


def extract_hits_from_text(rel: str, raw: str) -> Iterator[ErrorHit]:
    for line in raw.splitlines():
        stripped = line.strip()
        if len(stripped) < 10:
            continue
        if not FAILURE_HINTS.search(stripped):
            continue
        snippet = _truncate_line(stripped)
        et = classify_error_type(snippet)
        yield ErrorHit(
            text=snippet,
            rel_path=rel,
            error_type=et,
            recommendation=recommend_fix(et, snippet),
        )


def extract_hits_from_json(rel: str, raw: str) -> Iterator[ErrorHit]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        yield from extract_hits_from_text(rel, raw)
        return

    blobs: list[str] = []
    _walk_json_errors(data, blobs)
    for blob in blobs:
        for line in blob.splitlines():
            if FAILURE_HINTS.search(line):
                snippet = _truncate_line(line)
                et = classify_error_type(snippet)
                yield ErrorHit(
                    text=snippet,
                    rel_path=rel,
                    error_type=et,
                    recommendation=recommend_fix(et, snippet),
                )
        if "\n" not in blob and FAILURE_HINTS.search(blob):
            snippet = _truncate_line(blob)
            et = classify_error_type(snippet)
            yield ErrorHit(
                text=snippet,
                rel_path=rel,
                error_type=et,
                recommendation=recommend_fix(et, snippet),
            )


def read_and_extract_hits(path: Path, root: Path) -> list[ErrorHit]:
    rel = path.relative_to(root).as_posix()
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        LOGGER.warning("Could not read %s: %s", rel, exc)
        return []

    if path.suffix.lower() == ".json":
        return list(extract_hits_from_json(rel, raw))
    return list(extract_hits_from_text(rel, raw))


def fingerprint_hit(hit: ErrorHit) -> str:
    return hashlib.sha256(f"{hit.error_type}:{normalize_text(hit.text)}".encode("utf-8")).hexdigest()[:16]


def dedupe_hits(hits: Iterable[ErrorHit]) -> list[ErrorHit]:
    buckets: dict[str, ErrorHit] = {}
    for h in hits:
        fp = fingerprint_hit(h)
        if fp not in buckets:
            buckets[fp] = h
        else:
            existing = buckets[fp]
            paths = {p.strip() for p in existing.rel_path.split(",") if p.strip()}
            paths.add(h.rel_path.strip())
            existing.rel_path = ", ".join(sorted(paths))
    return list(buckets.values())


def run_analysis(
    root: Path,
    *,
    since_hours: int,
    globs: Sequence[str],
    max_files: int,
) -> AnalysisReport:
    started = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max(1, since_hours))
    files, skipped = iter_recent_files(root, globs, cutoff, max_files=max(1, max_files))

    all_hits: list[ErrorHit] = []
    for path in files:
        try:
            all_hits.extend(read_and_extract_hits(path, root))
        except Exception:
            LOGGER.exception("Unexpected failure processing %s", path)

    finished = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    return AnalysisReport(
        started_at_utc=started,
        finished_at_utc=finished,
        root=root,
        files_scanned=len(files),
        hits=dedupe_hits(all_hits),
        skipped_files=skipped,
    )


def build_markdown_block(report: AnalysisReport) -> str:
    lines = [
        f"## Automated scan ({report.finished_at_utc})",
        "",
        f"- Workspace: `{report.root}`",
        f"- Files scanned: **{report.files_scanned}**",
        f"- Distinct error patterns: **{len(report.hits)}**",
        "",
    ]
    if report.skipped_files:
        lines.append("### Skipped / warnings")
        for s in report.skipped_files[:20]:
            lines.append(f"- {s}")
        if len(report.skipped_files) > 20:
            lines.append(f"- _…and {len(report.skipped_files) - 20} more_")
        lines.append("")

    counts = report.type_counts()
    lines.append("### Counts by type")
    for key in ("api", "tool", "logic"):
        lines.append(f"- **{key}**: {counts.get(key, 0)}")
    lines.append("")

    by_type: dict[ERROR_TYPE, list[ErrorHit]] = {"api": [], "tool": [], "logic": []}
    for h in report.hits:
        by_type[h.error_type].append(h)

    for et in ("api", "tool", "logic"):
        group = by_type[et]
        if not group:
            continue
        lines.append(f"### {et.upper()} patterns")
        for hit in sorted(group, key=lambda x: x.text.lower())[:25]:
            lines.append(f"- **Source:** `{hit.rel_path}`  ")
            lines.append(f"  - **Signal:** {hit.text}")
            lines.append(f"  - **Fix:** {hit.recommendation}")
            lines.append("")
        if len(group) > 25:
            lines.append(f"_…{len(group) - 25} additional {et} patterns omitted_")
            lines.append("")

    lines.append("### Consolidated recommendations")
    rec_counts: Counter[str] = Counter()
    for h in report.hits:
        rec_counts[h.recommendation] += 1
    for rec, n in rec_counts.most_common(12):
        lines.append(f"- ({n}×) {rec}")
    lines.append("")
    return "\n".join(lines)


def update_error_log_markdown(md_path: Path, report_block: str) -> None:
    """Merge ``report_block`` into ``error_log.md`` between HTML comment markers."""

    md_path.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# Error learning log\n\n"
        "Human-written notes can go above the auto-generated block. "
        "The section between the markers is replaced on each **analyze** run.\n\n"
    )

    if not md_path.exists():
        body = header + MD_AUTO_START + "\n" + report_block.rstrip() + "\n" + MD_AUTO_END + "\n"
        md_path.write_text(body, encoding="utf-8")
        return

    try:
        existing = md_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ErrorLearningError(f"Cannot read {md_path}: {exc}") from exc

    if MD_AUTO_START in existing and MD_AUTO_END in existing:
        before, rest = existing.split(MD_AUTO_START, 1)
        _, after = rest.split(MD_AUTO_END, 1)
        new_body = before.rstrip() + "\n\n" + MD_AUTO_START + "\n" + report_block.rstrip() + "\n" + MD_AUTO_END + "\n" + after.lstrip()
    else:
        new_body = header + existing.strip() + "\n\n" + MD_AUTO_START + "\n" + report_block.rstrip() + "\n" + MD_AUTO_END + "\n"

    try:
        md_path.write_text(new_body, encoding="utf-8")
    except OSError as exc:
        raise ErrorLearningError(f"Cannot write {md_path}: {exc}") from exc


def merge_top_patterns_to_json(
    log_path: Path,
    report: AnalysisReport,
    *,
    limit: int,
    unresolved: bool,
) -> int:
    """Persist up to ``limit`` deduplicated patterns into the JSON store."""

    type_order = {"api": 0, "tool": 1, "logic": 2}
    ordered = sorted(
        report.hits,
        key=lambda h: (type_order.get(h.error_type, 9), h.text.lower()),
    )
    added = 0
    for hit in ordered[: max(1, limit)]:
        category = f"session_pattern:{hit.error_type}"
        try:
            _, created = add_entry(
                log_path,
                category,
                hit.text,
                hit.recommendation,
                resolved=not unresolved,
            )
        except ErrorLearningError:
            LOGGER.exception("Failed to append JSON entry for pattern")
            continue
        if created:
            added += 1
    return added


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Capture and learn from session errors (JSON log + markdown report)."
    )
    parser.add_argument(
        "--log-path",
        type=Path,
        default=DEFAULT_LOG_PATH,
        help="Path to the error learning JSON log.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging.")
    parser.add_argument("-q", "--quiet", action="store_true", help="Warnings only.")

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
    subparsers.add_parser("stats", help="Show error frequency by category.")

    search_parser = subparsers.add_parser("search", help="Search for relevant past errors.")
    search_parser.add_argument("query", help="Search query.")
    search_parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum number of matching entries to print.",
    )

    analyze = subparsers.add_parser(
        "analyze",
        help="Scan recent session logs, classify errors, update error_log.md (and optionally JSON).",
    )
    analyze.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="Workspace root for glob patterns (default: current directory).",
    )
    analyze.add_argument(
        "--md-path",
        type=Path,
        default=DEFAULT_MD_PATH,
        help="Markdown report path (default: .learnings/error_log.md under repo root).",
    )
    analyze.add_argument(
        "--since-hours",
        type=int,
        default=168,
        help="Only consider files modified within this many hours (default: 168).",
    )
    analyze.add_argument(
        "--max-files",
        type=int,
        default=200,
        help="Maximum number of session files to scan (default: 200).",
    )
    analyze.add_argument(
        "--glob",
        dest="globs",
        action="append",
        default=None,
        help="Extra glob relative to --root (repeatable). Merged with built-in defaults.",
    )
    analyze.add_argument(
        "--dry-run",
        action="store_true",
        help="Print findings only; do not write markdown or JSON.",
    )
    analyze.add_argument(
        "--merge-json",
        action="store_true",
        help="Also append distilled patterns to the JSON log (deduplicated).",
    )
    analyze.add_argument(
        "--merge-limit",
        type=int,
        default=30,
        help="Max patterns to merge into JSON when --merge-json is set (default: 30).",
    )
    analyze.add_argument(
        "--merge-unresolved",
        action="store_true",
        help="Mark merged JSON entries as unresolved (open).",
    )

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    configure_logging(verbose=args.verbose, quiet=args.quiet)

    try:
        store = load_store(args.log_path)
    except ErrorLearningError as exc:
        LOGGER.error("%s", exc)
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
            LOGGER.error("%s", exc)
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
        print_entries(validated_entries, heading="OpenClaw Error Learnings")
        return 0

    if args.command == "stats":
        print_stats(validated_entries)
        return 0

    if args.command == "search":
        matches = search_entries(validated_entries, args.query, limit=max(args.limit, 1))
        print_entries(matches, heading=f"Search Results: {args.query}")
        return 0

    if args.command == "analyze":
        root = args.root.resolve()
        globs = list(DEFAULT_SESSION_GLOBS)
        if args.globs:
            globs.extend(args.globs)

        LOGGER.info("Starting analysis under %s (globs=%s)", root, globs)
        try:
            report = run_analysis(
                root,
                since_hours=args.since_hours,
                globs=globs,
                max_files=args.max_files,
            )
        except OSError as exc:
            LOGGER.error("Analysis aborted: %s", exc)
            print(colorize(str(exc), "red"), file=sys.stderr)
            return 1

        block = build_markdown_block(report)
        md_target = args.md_path
        if not md_target.is_absolute():
            md_target = (ROOT / md_target).resolve()

        LOGGER.info(
            "Scan complete: %s files, %s distinct hits",
            report.files_scanned,
            len(report.hits),
        )

        if args.dry_run:
            print(colorize("Dry run — markdown / JSON not written.", "yellow"))
            print(block)
            return 0

        try:
            update_error_log_markdown(md_target, block)
        except ErrorLearningError as exc:
            LOGGER.error("%s", exc)
            print(colorize(str(exc), "red"), file=sys.stderr)
            return 1

        print(colorize(f"Updated markdown report: {md_target}", "green"))

        if args.merge_json:
            n = merge_top_patterns_to_json(
                args.log_path,
                report,
                limit=args.merge_limit,
                unresolved=args.merge_unresolved,
            )
            print(colorize(f"Merged {n} new pattern(s) into {args.log_path}", "green"))

        counts = report.type_counts()
        print(
            colorize(
                f"Summary: api={counts['api']} tool={counts['tool']} logic={counts['logic']}",
                "cyan",
            )
        )
        return 0

    LOGGER.error("Unsupported command: %s", args.command)
    print(colorize(f"Unsupported command: {args.command}", "red"), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
