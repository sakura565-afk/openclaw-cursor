#!/usr/bin/env python3
"""Self-reflection cron system.

Scans markdown session transcripts under ``memory/``, identifies patterns in
what worked and what failed, then writes concise actionable insights to
``.learnings/LEARNINGS.md``.

The module is designed to run either standalone from the command line or from a
cron job. Reruns for the same reference date are idempotent: an existing
section with the same date header is replaced in place rather than appended.

Typical usage::

    python scripts/auto_reflection.py --memory-dir memory \\
        --learnings-file .learnings/LEARNINGS.md --days 7

Crontab example (daily at 03:15 UTC)::

    15 3 * * * cd /srv/project && python scripts/auto_reflection.py \\
        --days 1 >> logs/auto_reflection.log 2>&1
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Sequence


LOGGER = logging.getLogger("auto_reflection")

DEFAULT_MEMORY_DIR = Path("memory")
DEFAULT_LEARNINGS_FILE = Path(".learnings/LEARNINGS.md")
DEFAULT_LOCK_FILE = Path(".learnings/.cron.lock")
DEFAULT_LOOKBACK_DAYS = 7
DEFAULT_LOCK_TIMEOUT_SECONDS = 600  # 10 minutes
SECTION_MARKER = "<!-- auto-reflection:section "
SECTION_END_MARKER = "<!-- /auto-reflection:section -->"

# Patterns that capture inline timestamps inside transcripts.
ISO_TIMESTAMP_RE = re.compile(
    r"\b(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}(?::\d{2})?)"
    r"(?:\.\d+)?"
    r"(?:\s*(Z|UTC|[+-]\d{2}:?\d{2}))?",
    re.IGNORECASE,
)
ISO_DATE_ONLY_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")

# Category heuristics: ordered list, first match wins. The check is performed
# against a normalized blob of (filename + heading + body excerpt).
CATEGORY_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("image", ("image", "comfy", "face_swap", "faceswap", "thumbnail", "upscale", "watermark")),
    ("video", ("video", "render", "ffmpeg", "frame", "clip")),
    ("model", ("ollama", "model", "inference", "benchmark", "manifest")),
    ("memory", ("memory", "obsidian", "vault", "dashboard", "sync")),
    ("automation", ("cron", "scheduler", "queue", "pipeline", "batch")),
    ("telegram", ("telegram", "notification", "notify")),
    ("test", ("self-test", "selftest", "unittest", "pytest", "test run")),
)

SUCCESS_KEYWORDS: tuple[str, ...] = (
    "ok",
    "success",
    "succeeded",
    "completed",
    "passed",
    "done",
    "healthy",
    "verified",
    "all tests pass",
    "no issues",
    "no errors",
    "no broken",
    "0 broken",
    "0 failed",
    "0 errors",
)

FAILURE_KEYWORDS: tuple[str, ...] = (
    "fail",
    "failed",
    "failure",
    "error",
    "exception",
    "traceback",
    "timeout",
    "timed out",
    "broken",
    "missing",
    "skipped",
    "warning",
    "retry",
    "retried",
    "unhealthy",
    "stale",
    "could not",
    "unable to",
)

# Counters of the form `processed: 3`, `failed = 0`, `'failed': 0` and
# markdown-table cells with explicit status values.
METRIC_RE = re.compile(
    r"\b(processed|swapped|skipped|failed|errors?|warnings?|"
    r"broken[\s_]+links?|files?|passed|completed)\b"
    r"['\"]?\s*[:=]\s*(\d+)",
    re.IGNORECASE,
)
TABLE_STATUS_RE = re.compile(r"\|\s*(ok|fail|failed|error|skipped|warning)\s*\|", re.IGNORECASE)


@dataclass
class TranscriptEntry:
    """One logical block extracted from a transcript file."""

    source: Path
    timestamp: datetime | None
    title: str
    body: str
    category: str
    tags: list[str] = field(default_factory=list)
    success_signals: list[str] = field(default_factory=list)
    failure_signals: list[str] = field(default_factory=list)
    metrics: dict[str, int] = field(default_factory=dict)

    @property
    def is_success(self) -> bool:
        return bool(self.success_signals) and not self.failure_signals

    @property
    def is_failure(self) -> bool:
        return bool(self.failure_signals)

    def to_dict(self) -> dict[str, object]:
        return {
            "source": str(self.source),
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "title": self.title,
            "category": self.category,
            "tags": list(self.tags),
            "success_signals": list(self.success_signals),
            "failure_signals": list(self.failure_signals),
            "metrics": dict(self.metrics),
            "body_excerpt": self.body[:240],
        }


@dataclass
class ReflectionReport:
    """Aggregated reflection output ready for rendering."""

    generated_at: datetime
    reference_date: date
    window_start: date
    window_end: date
    memory_dir: Path
    entries: list[TranscriptEntry]
    category_stats: dict[str, dict[str, int]]
    top_failures: list[tuple[str, int]]
    top_successes: list[tuple[str, int]]
    insights: list[str]
    summary: dict[str, int]

    def to_dict(self) -> dict[str, object]:
        return {
            "generated_at": self.generated_at.isoformat(),
            "reference_date": self.reference_date.isoformat(),
            "window_start": self.window_start.isoformat(),
            "window_end": self.window_end.isoformat(),
            "memory_dir": str(self.memory_dir),
            "summary": dict(self.summary),
            "category_stats": {
                category: dict(stats) for category, stats in self.category_stats.items()
            },
            "top_failures": [list(item) for item in self.top_failures],
            "top_successes": [list(item) for item in self.top_successes],
            "insights": list(self.insights),
            "entries": [entry.to_dict() for entry in self.entries],
        }


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _parse_timestamp(raw: str) -> datetime | None:
    """Best-effort parser for ISO-ish timestamps embedded in transcripts."""

    raw = raw.strip()
    if not raw:
        return None
    candidate = raw.replace("Z", "+00:00")
    candidate = re.sub(r"\s*(UTC|GMT)$", "+00:00", candidate, flags=re.IGNORECASE)
    candidate = re.sub(r"(\+\d{2})(\d{2})$", r"\1:\2", candidate)
    if "T" not in candidate and " " in candidate:
        candidate = candidate.replace(" ", "T", 1)
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        try:
            parsed = datetime.strptime(raw[:10], "%Y-%m-%d")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _extract_first_timestamp(text: str) -> datetime | None:
    match = ISO_TIMESTAMP_RE.search(text)
    if match:
        date_part, time_part, tz_part = match.groups()
        suffix = (tz_part or "").strip()
        return _parse_timestamp(f"{date_part}T{time_part}{suffix}")
    date_match = ISO_DATE_ONLY_RE.search(text)
    if date_match:
        return _parse_timestamp(date_match.group(1))
    return None


def _classify_category(text: str, source: Path) -> tuple[str, list[str]]:
    blob = " ".join((source.name, text)).lower()
    matched_tags: list[str] = []
    primary_category = "general"
    for category, keywords in CATEGORY_RULES:
        for keyword in keywords:
            if keyword in blob:
                matched_tags.append(category)
                if primary_category == "general":
                    primary_category = category
                break
    return primary_category, sorted(set(matched_tags))


def _extract_signals(body: str) -> tuple[list[str], list[str]]:
    lower = body.lower()

    metric_pairs: list[tuple[str, int]] = []
    metric_max: dict[str, int] = {}
    for metric_match in METRIC_RE.finditer(body):
        name = metric_match.group(1).lower().replace(" ", "_")
        value = int(metric_match.group(2))
        metric_pairs.append((name, value))
        if value > metric_max.get(name, -1):
            metric_max[name] = value

    success_signals: list[str] = []
    failure_signals: list[str] = []

    for keyword in SUCCESS_KEYWORDS:
        if keyword in lower:
            success_signals.append(keyword)

    for keyword in FAILURE_KEYWORDS:
        if keyword not in lower:
            continue
        suppress = False
        for metric_name, max_value in metric_max.items():
            if max_value == 0 and (keyword in metric_name or metric_name.startswith(keyword[:5])):
                suppress = True
                break
        if suppress:
            continue
        failure_signals.append(keyword)

    for table_match in TABLE_STATUS_RE.finditer(body):
        token = table_match.group(1).lower()
        if token == "ok":
            success_signals.append("table:ok")
        else:
            failure_signals.append(f"table:{token}")

    for name, value in metric_pairs:
        if value > 0 and any(token in name for token in ("fail", "error", "broken", "skipped", "warning")):
            failure_signals.append(f"metric:{name}={value}")

    failure_metric_names = {"failed", "errors", "error", "broken_links", "skipped", "warnings", "warning"}
    if metric_max and not failure_signals:
        zero_failures = any(
            name in failure_metric_names and value == 0
            for name, value in metric_max.items()
        )
        nonzero_failures = any(
            name in failure_metric_names and value > 0
            for name, value in metric_max.items()
        )
        if zero_failures and not nonzero_failures and "metric:zero_failures" not in success_signals:
            success_signals.append("metric:zero_failures")

    success_signals = list(dict.fromkeys(success_signals))
    failure_signals = list(dict.fromkeys(failure_signals))
    return success_signals, failure_signals


def _extract_metrics(body: str) -> dict[str, int]:
    metrics: dict[str, int] = {}
    for match in METRIC_RE.finditer(body):
        name = match.group(1).lower().replace(" ", "_")
        value = int(match.group(2))
        metrics[name] = metrics.get(name, 0) + value
    return metrics


def _split_into_blocks(text: str) -> list[tuple[str, str]]:
    """Split markdown text into ``(title, body)`` blocks per heading."""

    blocks: list[tuple[str, str]] = []
    current_title = ""
    current_lines: list[str] = []

    for line in text.splitlines():
        heading_match = HEADING_RE.match(line)
        if heading_match:
            if current_lines or current_title:
                blocks.append((current_title, "\n".join(current_lines).strip()))
            current_title = heading_match.group(2).strip()
            current_lines = []
        else:
            current_lines.append(line)
    if current_lines or current_title:
        blocks.append((current_title, "\n".join(current_lines).strip()))

    return [(title, body) for title, body in blocks if title or body]


def parse_transcript(path: Path) -> list[TranscriptEntry]:
    """Parse a single transcript file into structured entries."""

    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        LOGGER.warning("Failed to read %s: %s", path, exc)
        return []

    entries: list[TranscriptEntry] = []
    for title, body in _split_into_blocks(text):
        combined = f"{title}\n{body}".strip()
        if not combined:
            continue
        timestamp = _extract_first_timestamp(combined)
        category, tags = _classify_category(combined, path)
        success_signals, failure_signals = _extract_signals(combined)
        metrics = _extract_metrics(combined)
        entries.append(
            TranscriptEntry(
                source=path,
                timestamp=timestamp,
                title=title or path.stem,
                body=body,
                category=category,
                tags=tags,
                success_signals=success_signals,
                failure_signals=failure_signals,
                metrics=metrics,
            )
        )
    return entries


def collect_transcripts(memory_dir: Path) -> list[Path]:
    """Return all markdown transcript files under ``memory_dir`` sorted by name."""

    if not memory_dir.exists():
        return []
    return sorted(path for path in memory_dir.rglob("*.md") if path.is_file())


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------


def _within_window(
    entry: TranscriptEntry,
    window_start: date,
    window_end: date,
    today: date,
) -> bool:
    # Undated entries cannot be reliably placed on the timeline, so they are
    # excluded from windowed analysis. They still appear in the JSON dump via
    # the global entry list when callers request it.
    if entry.timestamp is None:
        return False
    entry_date = entry.timestamp.date()
    return window_start <= entry_date <= window_end


def filter_entries(
    entries: Sequence[TranscriptEntry],
    window_start: date,
    window_end: date,
    today: date | None = None,
) -> list[TranscriptEntry]:
    today = today if today is not None else datetime.now(timezone.utc).date()
    return [
        entry
        for entry in entries
        if _within_window(entry, window_start, window_end, today)
    ]


def aggregate_category_stats(
    entries: Iterable[TranscriptEntry],
) -> dict[str, dict[str, int]]:
    stats: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "success": 0, "failure": 0, "neutral": 0})
    for entry in entries:
        bucket = stats[entry.category]
        bucket["total"] += 1
        if entry.is_failure:
            bucket["failure"] += 1
        elif entry.is_success:
            bucket["success"] += 1
        else:
            bucket["neutral"] += 1
    return dict(stats)


def _top_signals(
    entries: Iterable[TranscriptEntry], attribute: str, limit: int = 5
) -> list[tuple[str, int]]:
    counter: Counter[str] = Counter()
    for entry in entries:
        for signal in getattr(entry, attribute):
            if signal.startswith("metric:") or signal.startswith("table:"):
                continue
            counter[signal] += 1
    return counter.most_common(limit)


def derive_insights(
    entries: Sequence[TranscriptEntry],
    category_stats: dict[str, dict[str, int]],
    window_start: date,
    window_end: date,
) -> list[str]:
    """Produce concise, actionable insight bullets."""

    insights: list[str] = []
    total_entries = len(entries)

    if total_entries == 0:
        insights.append(
            "No transcript activity recorded between "
            f"{window_start.isoformat()} and {window_end.isoformat()}; "
            "verify cron jobs and memory writers are still running."
        )
        return insights

    failure_total = sum(stats["failure"] for stats in category_stats.values())
    success_total = sum(stats["success"] for stats in category_stats.values())

    if failure_total == 0 and success_total > 0:
        insights.append(
            f"All {success_total} dated runs in the window completed cleanly; "
            "lock in the current configuration as a reference baseline."
        )

    if total_entries > 0:
        failure_rate = failure_total / total_entries
        if failure_rate >= 0.5:
            insights.append(
                f"Failure rate is {failure_rate:.0%} ({failure_total}/{total_entries}); "
                "treat this as a regression and pause non-critical batches until "
                "root-cause is identified."
            )

    for category, stats in sorted(category_stats.items()):
        cat_total = stats["total"]
        cat_failure = stats["failure"]
        cat_success = stats["success"]
        if cat_total == 0:
            continue
        cat_failure_rate = cat_failure / cat_total
        if cat_failure >= 1 and cat_failure_rate >= 0.34:
            insights.append(
                f"`{category}`: {cat_failure}/{cat_total} runs failed "
                f"({cat_failure_rate:.0%}); add a guardrail or disable the "
                "category until the failure mode is addressed."
            )
        elif cat_success >= 3 and cat_failure == 0:
            insights.append(
                f"`{category}`: {cat_success} consecutive clean runs; "
                "promote the current settings into the default template."
            )

    failure_signals = _top_signals(entries, "failure_signals", limit=3)
    if failure_signals:
        signal_summary = ", ".join(f"`{name}`×{count}" for name, count in failure_signals)
        insights.append(
            f"Most frequent failure indicators: {signal_summary}. "
            "Convert the top one into a watchdog alert."
        )

    success_signals = _top_signals(entries, "success_signals", limit=3)
    if success_signals and not failure_signals:
        signal_summary = ", ".join(f"`{name}`×{count}" for name, count in success_signals)
        insights.append(
            f"Recurring success markers: {signal_summary}. "
            "Codify the surrounding steps as the default playbook."
        )

    metric_totals: Counter[str] = Counter()
    for entry in entries:
        for metric_name, value in entry.metrics.items():
            metric_totals[metric_name] += value

    if metric_totals.get("processed"):
        processed = metric_totals["processed"]
        failures = metric_totals.get("failed", 0)
        if failures == 0 and processed >= 10:
            insights.append(
                f"Processed {processed} items with zero failures; current "
                "throughput target appears stable, consider raising batch size."
            )
        elif failures and processed:
            insights.append(
                f"{failures}/{processed} items failed during the window; "
                "inspect the failing inputs and add a regression fixture."
            )

    if not insights:
        insights.append(
            f"{total_entries} entries recorded with no significant patterns; "
            "continue monitoring for the next cycle."
        )

    seen: set[str] = set()
    deduped: list[str] = []
    for insight in insights:
        if insight in seen:
            continue
        seen.add(insight)
        deduped.append(insight)
    return deduped


def build_report(
    entries: Sequence[TranscriptEntry],
    memory_dir: Path,
    reference_date: date,
    lookback_days: int,
    generated_at: datetime | None = None,
) -> ReflectionReport:
    """Build a full :class:`ReflectionReport` from already-parsed entries."""

    if lookback_days < 1:
        raise ValueError("lookback_days must be >= 1")

    window_end = reference_date
    window_start = reference_date - timedelta(days=lookback_days - 1)
    in_window = filter_entries(entries, window_start, window_end, today=reference_date)

    category_stats = aggregate_category_stats(in_window)
    top_failures = _top_signals(in_window, "failure_signals", limit=5)
    top_successes = _top_signals(in_window, "success_signals", limit=5)
    insights = derive_insights(in_window, category_stats, window_start, window_end)

    summary = {
        "transcript_files": len({entry.source for entry in in_window}),
        "entries_in_window": len(in_window),
        "entries_total": len(entries),
        "successes": sum(1 for entry in in_window if entry.is_success),
        "failures": sum(1 for entry in in_window if entry.is_failure),
        "neutral": sum(1 for entry in in_window if not entry.is_success and not entry.is_failure),
    }

    return ReflectionReport(
        generated_at=generated_at or datetime.now(timezone.utc),
        reference_date=reference_date,
        window_start=window_start,
        window_end=window_end,
        memory_dir=memory_dir,
        entries=list(in_window),
        category_stats=category_stats,
        top_failures=top_failures,
        top_successes=top_successes,
        insights=insights,
        summary=summary,
    )


# ---------------------------------------------------------------------------
# Rendering & persistence
# ---------------------------------------------------------------------------


def render_section(report: ReflectionReport) -> str:
    """Render a single dated section for the LEARNINGS file."""

    section_id = report.reference_date.isoformat()
    lines: list[str] = []
    lines.append(f"{SECTION_MARKER}{section_id} -->")
    lines.append(f"## {section_id} — Auto-Reflection")
    lines.append("")
    lines.append(
        f"_Window: {report.window_start.isoformat()} → {report.window_end.isoformat()}_  "
        f"_Generated: {report.generated_at.isoformat(timespec='seconds')}_  "
        f"_Source: `{report.memory_dir}`_"
    )
    lines.append("")
    summary = report.summary
    lines.append(
        "**Summary:** "
        f"{summary['entries_in_window']} entries from "
        f"{summary['transcript_files']} files — "
        f"{summary['successes']} ok / "
        f"{summary['failures']} failed / "
        f"{summary['neutral']} neutral."
    )
    lines.append("")

    lines.append("### Category breakdown")
    lines.append("")
    if report.category_stats:
        lines.append("| Category | Total | OK | Failed | Neutral |")
        lines.append("| --- | ---: | ---: | ---: | ---: |")
        for category in sorted(report.category_stats):
            stats = report.category_stats[category]
            lines.append(
                f"| `{category}` | {stats['total']} | {stats['success']} | "
                f"{stats['failure']} | {stats['neutral']} |"
            )
    else:
        lines.append("_No category activity in this window._")
    lines.append("")

    lines.append("### What worked")
    lines.append("")
    if report.top_successes:
        for name, count in report.top_successes:
            lines.append(f"- `{name}` × {count}")
    else:
        lines.append("- _No clear success signals captured._")
    lines.append("")

    lines.append("### What failed")
    lines.append("")
    if report.top_failures:
        for name, count in report.top_failures:
            lines.append(f"- `{name}` × {count}")
    else:
        lines.append("- _No failure signals captured._")
    lines.append("")

    lines.append("### Actionable insights")
    lines.append("")
    for insight in report.insights:
        lines.append(f"- {insight}")
    lines.append("")
    lines.append(SECTION_END_MARKER)
    return "\n".join(lines).rstrip() + "\n"


def _ensure_header(text: str) -> str:
    if text.lstrip().startswith("#"):
        return text
    header = (
        "# LEARNINGS\n\n"
        "Auto-generated reflections from `scripts/auto_reflection.py`. "
        "Newer entries are appended below; sections are keyed by their "
        "reference date and rerunning for the same date overwrites that "
        "section in place.\n\n"
    )
    return header + text


def upsert_section(existing: str, section: str, section_id: str) -> str:
    """Insert ``section`` into ``existing`` LEARNINGS body.

    If a previous block for the same ``section_id`` exists (delimited by the
    ``auto-reflection:section`` HTML comments) it is replaced; otherwise the
    new section is appended at the end of the document.
    """

    body = _ensure_header(existing or "")
    marker_open = f"{SECTION_MARKER}{section_id} -->"
    start_index = body.find(marker_open)
    if start_index != -1:
        end_index = body.find(SECTION_END_MARKER, start_index)
        if end_index == -1:
            end_index = len(body)
        else:
            end_index += len(SECTION_END_MARKER)
        replaced = body[:start_index] + section.rstrip() + "\n" + body[end_index:].lstrip("\n")
        if not replaced.endswith("\n"):
            replaced += "\n"
        return replaced

    if not body.endswith("\n"):
        body += "\n"
    if not body.endswith("\n\n"):
        body += "\n"
    return body + section


def write_learnings(report: ReflectionReport, learnings_file: Path) -> Path:
    """Idempotently write ``report`` into the LEARNINGS file."""

    learnings_file.parent.mkdir(parents=True, exist_ok=True)
    section = render_section(report)
    existing = ""
    if learnings_file.exists():
        existing = learnings_file.read_text(encoding="utf-8")
    updated = upsert_section(existing, section, report.reference_date.isoformat())
    learnings_file.write_text(updated, encoding="utf-8")
    return learnings_file


# ---------------------------------------------------------------------------
# Locking (cron-safety)
# ---------------------------------------------------------------------------


class LockHeldError(RuntimeError):
    """Raised when the reflection lock is held by another process."""


class ReflectionLock:
    """Lightweight inter-process lock based on an exclusive lock file.

    The lock automatically expires after ``timeout_seconds`` so a crashed
    process cannot stall future cron invocations forever.
    """

    def __init__(self, lock_path: Path, timeout_seconds: int = DEFAULT_LOCK_TIMEOUT_SECONDS) -> None:
        self.lock_path = lock_path
        self.timeout_seconds = timeout_seconds
        self._acquired = False

    def __enter__(self) -> "ReflectionLock":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.release()

    def acquire(self) -> None:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        if self.lock_path.exists():
            try:
                age = time.time() - self.lock_path.stat().st_mtime
            except OSError:
                age = 0
            if age <= self.timeout_seconds:
                raise LockHeldError(
                    f"Reflection lock {self.lock_path} held for {age:.0f}s "
                    f"(timeout {self.timeout_seconds}s)"
                )
            LOGGER.warning(
                "Stale reflection lock at %s (age %.0fs) — reclaiming",
                self.lock_path,
                age,
            )
            self.lock_path.unlink(missing_ok=True)
        try:
            fd = os.open(str(self.lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError as exc:
            raise LockHeldError(f"Reflection lock {self.lock_path} just acquired by another process") from exc
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(f"pid={os.getpid()} ts={datetime.now(timezone.utc).isoformat()}\n")
        self._acquired = True

    def release(self) -> None:
        if not self._acquired:
            return
        self.lock_path.unlink(missing_ok=True)
        self._acquired = False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def run_reflection(
    memory_dir: Path,
    learnings_file: Path,
    reference_date: date,
    lookback_days: int,
    json_path: Path | None = None,
    lock_path: Path | None = None,
    lock_timeout_seconds: int = DEFAULT_LOCK_TIMEOUT_SECONDS,
) -> ReflectionReport:
    """High-level entry point used by the CLI and other scripts."""

    lock_target = lock_path if lock_path is not None else learnings_file.parent / ".cron.lock"
    with ReflectionLock(lock_target, timeout_seconds=lock_timeout_seconds):
        transcripts = collect_transcripts(memory_dir)
        all_entries: list[TranscriptEntry] = []
        for transcript in transcripts:
            all_entries.extend(parse_transcript(transcript))

        report = build_report(
            entries=all_entries,
            memory_dir=memory_dir,
            reference_date=reference_date,
            lookback_days=lookback_days,
        )
        write_learnings(report, learnings_file)
        if json_path is not None:
            json_path.parent.mkdir(parents=True, exist_ok=True)
            json_path.write_text(
                json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
    return report


def _parse_iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Invalid ISO date: {value!r}") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Self-reflection cron: analyse transcripts and update LEARNINGS.md.",
    )
    parser.add_argument(
        "--memory-dir",
        type=Path,
        default=DEFAULT_MEMORY_DIR,
        help="Directory containing markdown transcripts (default: memory).",
    )
    parser.add_argument(
        "--learnings-file",
        type=Path,
        default=DEFAULT_LEARNINGS_FILE,
        help="Output LEARNINGS.md file (default: .learnings/LEARNINGS.md).",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=DEFAULT_LOOKBACK_DAYS,
        help="Lookback window in days, inclusive of the reference date (default: 7).",
    )
    parser.add_argument(
        "--reference-date",
        type=_parse_iso_date,
        default=None,
        help="Reference date (YYYY-MM-DD); defaults to today (UTC).",
    )
    parser.add_argument(
        "--json-out",
        type=Path,
        default=None,
        help="Optional path to also dump the report as JSON.",
    )
    parser.add_argument(
        "--lock-file",
        type=Path,
        default=None,
        help="Override the lock file location (default: <learnings dir>/.cron.lock).",
    )
    parser.add_argument(
        "--lock-timeout",
        type=int,
        default=DEFAULT_LOCK_TIMEOUT_SECONDS,
        help="Seconds before a stale lock is reclaimed (default: 600).",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress the human-readable console summary.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging.",
    )
    return parser


def _print_console_summary(report: ReflectionReport, learnings_file: Path) -> None:
    summary = report.summary
    print(
        f"auto_reflection: window {report.window_start} → {report.window_end} | "
        f"{summary['entries_in_window']} entries "
        f"({summary['successes']} ok, {summary['failures']} failed, {summary['neutral']} neutral) | "
        f"wrote {learnings_file}"
    )
    if report.insights:
        print("Insights:")
        for insight in report.insights:
            print(f"  - {insight}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    reference_date = args.reference_date or datetime.now(timezone.utc).date()
    if args.days < 1:
        print("--days must be >= 1", file=sys.stderr)
        return 2

    try:
        report = run_reflection(
            memory_dir=args.memory_dir,
            learnings_file=args.learnings_file,
            reference_date=reference_date,
            lookback_days=args.days,
            json_path=args.json_out,
            lock_path=args.lock_file,
            lock_timeout_seconds=args.lock_timeout,
        )
    except LockHeldError as exc:
        print(f"auto_reflection: {exc}", file=sys.stderr)
        return 75  # EX_TEMPFAIL
    except FileNotFoundError as exc:
        print(f"auto_reflection: {exc}", file=sys.stderr)
        return 1

    if not args.quiet:
        _print_console_summary(report, args.learnings_file)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
