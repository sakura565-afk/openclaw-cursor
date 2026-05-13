#!/usr/bin/env python3
"""
Cron-friendly self-reflection over recent agent-style logs and session artifacts.

Scans configurable paths for logs and JSON, analyzes session history, detects
patterns in errors, corrections, and successful approaches, emits actionable
recommendations and a stats summary, writes structured outputs under
`.learnings/` (including date-stamped files under `.learnings/reflections/`),
builds a periodic weekly summary, and optionally posts the summary (Telegram or
generic webhook).

Example crontab (daily at 09:00 UTC):

    0 9 * * * cd /path/to/repo && /usr/bin/python3 -m scripts.auto_reflection

Environment (all optional unless posting):

- AUTO_REFLECTION_ROOT — workspace root (default: current working directory)
- AUTO_REFLECTION_SESSION_GLOBS — extra comma-separated glob patterns relative to root
- TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID — post summary via Telegram sendMessage
- REFLECTION_WEBHOOK_URL — POST JSON {\"text\": \"...\", \"meta\": {...}} to this URL
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sys
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence


logger = logging.getLogger(__name__)

LEARNINGS_DIR = ".learnings"
INSIGHTS_SUBDIR = "insights"
SUMMARIES_SUBDIR = "summaries"
REFLECTIONS_SUBDIR = "reflections"
STATE_NAME = ".state.json"

DEFAULT_SINCE_HOURS = 24 * 7
DEFAULT_SESSION_GLOBS = (
    "logs/**/*.log",
    "logs/**/*.json",
    "memory/**/*_log.md",
    "memory/**/*.md",
)

FAILURE_HINTS = re.compile(
    r"(?i)(\b(traceback|exception|error|failed|failure|timed?\s*out|exit\s*code\s*[1-9]\d*)\b|"
    r"\b(Fatal|Critical)\b|^Error:|\[\s*ERROR\s*\])"
)
LESSON_HINTS = re.compile(
    r"(?i)(\blesson learned\b|\btakeaway\b|\bremember to\b|\bnext time\b|"
    r"\bavoid\b|\bshould have\b|\broot cause\b)"
)
CORRECTION_HINTS = re.compile(
    r"(?i)(\bfixed\b|\bcorrected\b|\bworkaround\b|\bpatched\b|\bupdated (?:to|the)\b|\bnow uses\b)"
)
SUCCESS_HINTS = re.compile(
    r"(?i)(\bsuccess\b|\bworked\b|\ball tests passed\b|\bcompleted successfully\b|"
    r"\bverified\b|\bno errors\b|\bbuild passed\b)"
)
MAX_FILE_BYTES = 2 * 1024 * 1024
TELEGRAM_TEXT_LIMIT = 4000
ERROR_LOG_MAX_ENTRIES = 200


@dataclass
class Insight:
    """One deduplicated insight line derived from logs."""

    text: str
    source_paths: list[str] = field(default_factory=list)
    severity: str = "info"  # info | warning | error
    category: str = "general"


@dataclass
class ReflectionRun:
    """Serializable result of one reflection pass."""

    run_id: str
    started_at_utc: str
    finished_at_utc: str
    files_scanned: int
    session_files: list[str]
    insights: list[Insight]
    summary_markdown: str


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _state_path(root: Path) -> Path:
    return root / LEARNINGS_DIR / STATE_NAME


def load_state(root: Path) -> dict[str, Any]:
    path = _state_path(root)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not load reflection state from %s: %s", path, exc)
        return {}


def save_state(root: Path, data: dict[str, Any]) -> None:
    path = _state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def iter_session_files(root: Path, globs: Sequence[str], cutoff: datetime) -> list[Path]:
    seen: set[Path] = set()
    out: list[Path] = []
    for pattern in globs:
        for path in root.glob(pattern):
            if not path.is_file():
                continue
            try:
                st = path.stat()
            except OSError:
                continue
            if datetime.fromtimestamp(st.st_mtime, tz=timezone.utc) < cutoff:
                continue
            if st.st_size > MAX_FILE_BYTES:
                continue
            rp = path.resolve()
            if rp in seen:
                continue
            seen.add(rp)
            out.append(path)
    out.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    logger.info("Matched %d session files after cutoff %s", len(out), cutoff.isoformat())
    return out


def _severity_for_line(line: str) -> str:
    if re.search(r"(?i)\b(traceback|exception|fatal|critical)\b", line):
        return "error"
    if FAILURE_HINTS.search(line):
        return "warning"
    if SUCCESS_HINTS.search(line):
        return "info"
    return "info"


def _category_for_line(line: str) -> str:
    if LESSON_HINTS.search(line):
        return "lesson"
    if SUCCESS_HINTS.search(line):
        return "success"
    if CORRECTION_HINTS.search(line):
        return "correction"
    if re.search(r"(?i)\b(test|pytest|unittest)\b", line):
        return "testing"
    if re.search(r"(?i)\b(git|commit|merge|branch)\b", line):
        return "git"
    if re.search(r"(?i)\b(api|http|request|timeout)\b", line):
        return "integration"
    return "general"


def normalize_insight_text(line: str) -> str:
    line = line.strip()
    line = re.sub(r"\s+", " ", line)
    return line[:500]


def insight_fingerprint(text: str) -> str:
    return hashlib.sha256(text.lower().encode("utf-8")).hexdigest()[:16]


def _line_triggers_insight(stripped: str) -> bool:
    return bool(
        FAILURE_HINTS.search(stripped)
        or LESSON_HINTS.search(stripped)
        or CORRECTION_HINTS.search(stripped)
        or SUCCESS_HINTS.search(stripped)
    )


def extract_insights_from_text(path: Path, root: Path, raw: str) -> Iterator[Insight]:
    rel = path.relative_to(root).as_posix()
    for line in raw.splitlines():
        stripped = line.strip()
        if len(stripped) < 12:
            continue
        if not _line_triggers_insight(stripped):
            continue
        text = normalize_insight_text(stripped)
        if not text:
            continue
        yield Insight(
            text=text,
            source_paths=[rel],
            severity=_severity_for_line(stripped),
            category=_category_for_line(stripped),
        )


def extract_insights_from_json(path: Path, root: Path, raw: str) -> Iterator[Insight]:
    rel = path.relative_to(root).as_posix()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        yield from extract_insights_from_text(path, root, raw)
        return

    strings: list[str] = []

    def walk(obj: Any) -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                if isinstance(k, str) and re.search(r"(?i)\b(error|stderr|message|detail)\b", k):
                    if isinstance(v, str) and v.strip():
                        strings.append(v.strip())
                walk(v)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)
        elif isinstance(obj, str) and (
            FAILURE_HINTS.search(obj) or SUCCESS_HINTS.search(obj) or CORRECTION_HINTS.search(obj)
        ):
            strings.append(obj.strip())

    walk(data)
    for s in strings:
        for insight in extract_insights_from_text(path, root, s):
            if rel not in insight.source_paths:
                insight.source_paths.insert(0, rel)
            yield insight


def read_and_extract(path: Path, root: Path) -> list[Insight]:
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.debug("Skip unreadable file %s: %s", path, exc)
        return []
    if path.suffix.lower() == ".json":
        return list(extract_insights_from_json(path, root, raw))
    return list(extract_insights_from_text(path, root, raw))


def dedupe_insights(insights: Iterable[Insight]) -> list[Insight]:
    buckets: dict[str, Insight] = {}
    for ins in insights:
        fp = insight_fingerprint(ins.text)
        existing = buckets.get(fp)
        if existing is None:
            buckets[fp] = Insight(
                text=ins.text,
                source_paths=list(ins.source_paths),
                severity=ins.severity,
                category=ins.category,
            )
        else:
            for p in ins.source_paths:
                if p not in existing.source_paths:
                    existing.source_paths.append(p)
            sev_rank = {"error": 3, "warning": 2, "info": 1}
            if sev_rank.get(ins.severity, 0) > sev_rank.get(existing.severity, 0):
                existing.severity = ins.severity
    return list(buckets.values())


def load_error_log_entries(root: Path, *, max_entries: int = ERROR_LOG_MAX_ENTRIES) -> list[dict[str, Any]]:
    path = root / LEARNINGS_DIR / "error_log.json"
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not read %s: %s", path, exc)
        return []
    raw_entries = data.get("entries")
    if not isinstance(raw_entries, list):
        return []
    parsed: list[dict[str, Any]] = [e for e in raw_entries if isinstance(e, dict)]
    return parsed[-max_entries:]


def build_session_analysis_markdown(
    root: Path,
    session_paths: Sequence[Path],
    cutoff: datetime,
    run_at: datetime,
) -> str:
    lines = [
        "## Session analysis",
        "",
        f"- **Analysis window:** `{cutoff.isoformat()}` → `{run_at.isoformat()}` (UTC)",
        f"- **Files in window:** {len(session_paths)}",
        "",
    ]
    if not session_paths:
        lines.append("_No session artifacts matched the configured globs in this window._")
        return "\n".join(lines)

    ext_counts = Counter(p.suffix.lower() or "(no extension)" for p in session_paths)
    total_bytes = 0
    lines.append("### Recently touched artifacts")
    lines.append("")
    lines.append("| Relative path | Size (bytes) | Modified (UTC) |")
    lines.append("| --- | ---: | --- |")
    for p in session_paths[:40]:
        try:
            st = p.stat()
        except OSError:
            continue
        total_bytes += st.st_size
        mtime = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
        rel = p.relative_to(root).as_posix()
        lines.append(f"| `{rel}` | {st.st_size} | {mtime} |")
    if len(session_paths) > 40:
        lines.append(f"| _… {len(session_paths) - 40} more files_ | | |")
    lines.append("")
    lines.append("### File-type mix")
    for ext, n in ext_counts.most_common():
        lines.append(f"- `{ext}`: **{n}**")
    lines.append("")
    lines.append(f"- **Approx. total size (listed rows):** {total_bytes} bytes")
    return "\n".join(lines)


def build_pattern_detection_markdown(
    insights: Sequence[Insight],
    error_entries: Sequence[dict[str, Any]],
) -> str:
    lines = ["## Pattern detection", ""]
    if not insights and not error_entries:
        lines.append("_No structured signals yet; widen the time window or add session logs._")
        return "\n".join(lines)

    if insights:
        by_cat = Counter(i.category for i in insights)
        by_sev = Counter(i.severity for i in insights)
        lines.append("### From scanned session logs")
        lines.append("")
        lines.append("- **Insight categories:** " + ", ".join(f"`{k}`×{v}" for k, v in by_cat.most_common(12)))
        lines.append("- **Severity mix:** " + ", ".join(f"`{k}`×{v}" for k, v in by_sev.most_common()))
        hi = [i for i in insights if i.severity in ("error", "warning")]
        if hi:
            lines.append("")
            lines.append("**Recurring failure / risk lines (sample):**")
            for ins in sorted(hi, key=lambda x: (-len(x.source_paths), x.text.lower()))[:8]:
                lines.append(f"- [{ins.severity}] {ins.text} _(hits: {len(ins.source_paths)})_")
        succ = [i for i in insights if i.category in ("success", "correction", "lesson")]
        if succ:
            lines.append("")
            lines.append("**Successful approaches / corrections (sample):**")
            for ins in succ[:8]:
                lines.append(f"- [{ins.category}] {ins.text}")
        lines.append("")

    if error_entries:
        cats: Counter[str] = Counter()
        unresolved = 0
        for e in error_entries:
            c = e.get("category")
            if isinstance(c, str) and c.strip():
                cats[c.strip()] += 1
            if e.get("resolved") is False:
                unresolved += 1
        lines.append("### From `.learnings/error_log.json`")
        lines.append("")
        lines.append(f"- **Entries considered:** {len(error_entries)}")
        lines.append(f"- **Unresolved in sample:** {unresolved}")
        if cats:
            lines.append("- **Top recorded categories:** " + ", ".join(f"`{k}`×{v}" for k, v in cats.most_common(8)))
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def build_actionable_recommendations(
    insights: Sequence[Insight],
    error_entries: Sequence[dict[str, Any]],
) -> list[str]:
    recs: list[str] = []
    by_cat = Counter(i.category for i in insights)
    by_sev = Counter(i.severity for i in insights)

    if by_sev.get("error", 0) + by_sev.get("warning", 0) >= 3:
        recs.append(
            "Several high-signal errors or warnings appeared across logs; schedule a focused pass "
            "to triage root causes and add regression checks for the top two themes."
        )
    if by_cat.get("integration", 0) >= 2:
        recs.append(
            "Integration-related hints cluster together; confirm timeouts, retries, and API "
            "credentials, and capture a minimal reproduction for the worst offender."
        )
    if by_cat.get("testing", 0) >= 2:
        recs.append(
            "Testing-related noise is elevated; stabilize flaky tests or split slow suites so "
            "failures surface earlier with clearer diagnostics."
        )
    if by_cat.get("git", 0) >= 2:
        recs.append(
            "Git workflow friction shows up repeatedly; document the branch/merge checklist "
            "you actually follow and automate pre-push checks where possible."
        )

    unresolved_lessons: list[str] = []
    for e in error_entries:
        if e.get("resolved") is False:
            lesson = e.get("lesson")
            if isinstance(lesson, str) and lesson.strip():
                unresolved_lessons.append(lesson.strip()[:240])
    if unresolved_lessons:
        recs.append(
            f"Address {len(unresolved_lessons)} unresolved item(s) from the error learning log; "
            "oldest lesson: " + unresolved_lessons[0][:200]
        )

    succ_n = sum(1 for i in insights if i.category == "success")
    fix_n = sum(1 for i in insights if i.category == "correction")
    if succ_n and not recs:
        recs.append("Capture what made recent successes work (commands, settings) in team notes or runbooks.")
    elif fix_n >= 2:
        recs.append(
            "Multiple corrections logged; distill them into a short “pitfalls” section in the README "
            "or onboarding doc so the same mistakes are not repeated."
        )

    if not recs:
        recs.append(
            "Keep logging lessons and failures in `memory/` logs or `.learnings/error_log.json`; "
            "the next reflection pass will have richer patterns to act on."
        )
    return recs


def build_stats_summary(
    cutoff: datetime,
    run_at: datetime,
    session_files: Sequence[Path],
    insights: Sequence[Insight],
    error_entries: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    by_cat = Counter(i.category for i in insights)
    by_sev = Counter(i.severity for i in insights)
    unresolved = sum(1 for e in error_entries if e.get("resolved") is False)
    return {
        "window_start_utc": cutoff.isoformat(),
        "window_end_utc": run_at.isoformat(),
        "files_scanned": len(session_files),
        "insights_distinct": len(insights),
        "insights_by_category": dict(by_cat),
        "insights_by_severity": dict(by_sev),
        "error_log_entries_loaded": len(error_entries),
        "error_log_unresolved_in_sample": unresolved,
    }


def build_stats_summary_markdown(stats: dict[str, Any]) -> str:
    lines = [
        "## Stats summary",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| Window start (UTC) | `{stats['window_start_utc']}` |",
        f"| Window end (UTC) | `{stats['window_end_utc']}` |",
        f"| Session files scanned | **{stats['files_scanned']}** |",
        f"| Distinct insights | **{stats['insights_distinct']}** |",
        f"| Error log entries (sample) | **{stats['error_log_entries_loaded']}** |",
        f"| Unresolved in error-log sample | **{stats['error_log_unresolved_in_sample']}** |",
        "",
        "### Insight mix",
        "",
    ]
    by_cat = stats.get("insights_by_category") or {}
    by_sev = stats.get("insights_by_severity") or {}
    if isinstance(by_cat, dict) and by_cat:
        lines.append("- **By category:** " + ", ".join(f"`{k}`×{v}" for k, v in sorted(by_cat.items(), key=lambda kv: -kv[1])))
    else:
        lines.append("- **By category:** _none_")
    if isinstance(by_sev, dict) and by_sev:
        lines.append("- **By severity:** " + ", ".join(f"`{k}`×{v}" for k, v in sorted(by_sev.items(), key=lambda kv: -kv[1])))
    else:
        lines.append("- **By severity:** _none_")
    return "\n".join(lines).rstrip() + "\n"


def build_recommendations_markdown(recs: Sequence[str]) -> str:
    lines = ["## Actionable recommendations", ""]
    for r in recs:
        lines.append(f"- {r}")
    return "\n".join(lines).rstrip() + "\n"


def build_summary_markdown(
    run_at: datetime,
    files_scanned: int,
    insights: Sequence[Insight],
    top_sessions: Sequence[str],
) -> str:
    lines = [
        f"# Reflection summary ({run_at.date().isoformat()} UTC)",
        "",
        f"- Session files scanned: **{files_scanned}**",
        f"- Distinct insights: **{len(insights)}**",
        "",
    ]
    if top_sessions:
        lines.append("## Recently touched logs")
        for p in top_sessions[:15]:
            lines.append(f"- `{p}`")
        lines.append("")
    if not insights:
        lines.append("_No notable patterns in the scanned window._")
        return "\n".join(lines)

    lines.append("## Insights")
    by_cat: dict[str, list[Insight]] = {}
    for ins in insights:
        by_cat.setdefault(ins.category, []).append(ins)

    for cat in sorted(by_cat.keys()):
        lines.append(f"### {cat}")
        rank = {"error": 0, "warning": 1, "info": 2}
        for ins in sorted(
            by_cat[cat],
            key=lambda i: (rank.get(i.severity, 9), i.text.lower()),
        ):
            badge = ins.severity.upper()
            sources = ", ".join(f"`{s}`" for s in ins.source_paths[:3])
            if len(ins.source_paths) > 3:
                sources += ", …"
            lines.append(f"- **[{badge}]** {ins.text} _(sources: {sources})_")
        lines.append("")

    ctr = Counter(i.category for i in insights)
    lines.append("## Category counts")
    for cat, n in ctr.most_common():
        lines.append(f"- **{cat}**: {n}")
    return "\n".join(lines).rstrip() + "\n"


def build_full_reflection_markdown(
    run: ReflectionRun,
    session_block: str,
    patterns_block: str,
    recs_block: str,
    stats_block: str,
) -> str:
    parts = [
        f"# Auto-reflection — run `{run.run_id}`",
        "",
        f"- **Started:** {run.started_at_utc}",
        f"- **Finished:** {run.finished_at_utc}",
        f"- **Files scanned:** {run.files_scanned}",
        "",
        session_block.rstrip(),
        "",
        patterns_block.rstrip(),
        "",
        recs_block.rstrip(),
        "",
        stats_block.rstrip(),
        "",
        "---",
        "",
        run.summary_markdown.rstrip(),
        "",
    ]
    return "\n".join(parts).rstrip() + "\n"


def reflection_day_path(root: Path, run_at: datetime) -> Path:
    return root / LEARNINGS_DIR / REFLECTIONS_SUBDIR / f"{run_at.date().isoformat()}.md"


def append_daily_reflection(root: Path, run_at: datetime, run_id: str, body: str) -> Path:
    """Append this run under `.learnings/reflections/YYYY-MM-DD.md`."""

    path = reflection_day_path(root, run_at)
    path.parent.mkdir(parents=True, exist_ok=True)
    heading = f"\n## Run {run_id} — {run_at.strftime('%H:%M:%S')} UTC\n\n"
    chunk = heading + body + "\n"
    if path.exists():
        path.write_text(path.read_text(encoding="utf-8").rstrip() + "\n" + chunk, encoding="utf-8")
    else:
        path.write_text(f"# Daily reflections — {run_at.date().isoformat()} (UTC)\n" + chunk, encoding="utf-8")
    logger.info("Appended reflection markdown to %s", path)
    return path


def weekly_report_path(root: Path, dt: datetime) -> Path:
    iso = dt.isocalendar()
    week = f"{iso.year}-W{iso.week:02d}"
    return root / LEARNINGS_DIR / SUMMARIES_SUBDIR / f"weekly_{week}.md"


def update_weekly_summary(root: Path, run_at: datetime, body: str) -> Path:
    """Append this run into the ISO-week summary file (idempotent headings per day)."""

    path = weekly_report_path(root, run_at)
    path.parent.mkdir(parents=True, exist_ok=True)
    day_heading = f"\n## {run_at.date().isoformat()} (UTC)\n\n"
    chunk = day_heading + body + "\n"
    if path.exists():
        existing = path.read_text(encoding="utf-8")
        if run_at.date().isoformat() in existing:
            return path
        path.write_text(existing.rstrip() + "\n" + chunk, encoding="utf-8")
    else:
        path.write_text(f"# Weekly reflection — {run_at.isocalendar().year} W{run_at.isocalendar().week:02d}\n" + chunk)
    return path


def write_insight_artifacts(root: Path, run: ReflectionRun) -> tuple[Path, Path]:
    insights_dir = root / LEARNINGS_DIR / INSIGHTS_SUBDIR
    insights_dir.mkdir(parents=True, exist_ok=True)
    base = insights_dir / f"run_{run.run_id}"
    md_path = base.with_suffix(".md")
    json_path = base.with_suffix(".json")

    md_lines = [
        f"# Run {run.run_id}",
        f"- Started: {run.started_at_utc}",
        f"- Finished: {run.finished_at_utc}",
        f"- Files scanned: {run.files_scanned}",
        "",
        run.summary_markdown,
    ]
    md_path.write_text("\n".join(md_lines), encoding="utf-8")

    json_path.write_text(json.dumps(asdict(run), indent=2) + "\n", encoding="utf-8")
    logger.info("Wrote insight artifacts %s and %s", md_path, json_path)
    return md_path, json_path


def write_reflection_json(
    root: Path,
    run: ReflectionRun,
    stats: dict[str, Any],
    recommendations: Sequence[str],
) -> Path:
    out_dir = root / LEARNINGS_DIR / REFLECTIONS_SUBDIR
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"run_{run.run_id}.json"
    payload = {
        "run_id": run.run_id,
        "started_at_utc": run.started_at_utc,
        "finished_at_utc": run.finished_at_utc,
        "stats": stats,
        "recommendations": list(recommendations),
        "session_files": run.session_files,
        "insights": [asdict(i) for i in run.insights],
        "summary_markdown": run.summary_markdown,
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    logger.info("Wrote structured reflection JSON to %s", path)
    return path


def write_latest_pointers(root: Path, md_path: Path, weekly_path: Path, day_path: Path) -> None:
    """Small files for automation consumers."""

    ptr = root / LEARNINGS_DIR / "latest.json"
    data = {
        "insights_md": md_path.relative_to(root).as_posix(),
        "weekly_summary_md": weekly_path.relative_to(root).as_posix(),
        "reflections_day_md": day_path.relative_to(root).as_posix(),
        "generated_at_utc": utc_now().replace(microsecond=0).isoformat(),
    }
    ptr.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def post_webhook(url: str, payload: dict[str, Any]) -> tuple[bool, str]:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8", errors="replace")[:500]
            return True, raw or "ok"
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
        except OSError:
            detail = str(exc)
        return False, detail
    except urllib.error.URLError as exc:
        return False, str(exc.reason if hasattr(exc, "reason") else exc)


def post_telegram_summary(token: str, chat_id: str, text: str) -> tuple[bool, str]:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    chunks: list[str] = []
    remaining = text
    while remaining:
        chunks.append(remaining[:TELEGRAM_TEXT_LIMIT])
        remaining = remaining[TELEGRAM_TEXT_LIMIT:]

    last_msg = ""
    for i, chunk in enumerate(chunks):
        prefix = f"(part {i + 1}/{len(chunks)})\n" if len(chunks) > 1 else ""
        body = json.dumps(
            {"chat_id": chat_id, "text": prefix + chunk, "disable_web_page_preview": True}
        ).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                last_msg = resp.read().decode("utf-8", errors="replace")[:500]
        except urllib.error.HTTPError as exc:
            try:
                detail = exc.read().decode("utf-8", errors="replace")
            except OSError:
                detail = str(exc)
            return False, detail
        except urllib.error.URLError as exc:
            return False, str(exc.reason if hasattr(exc, "reason") else exc)
    return True, last_msg or "ok"


def collect_globs(extra: Sequence[str]) -> tuple[str, ...]:
    merged = list(DEFAULT_SESSION_GLOBS)
    env_extra = os.environ.get("AUTO_REFLECTION_SESSION_GLOBS", "")
    if env_extra.strip():
        merged.extend(p.strip() for p in env_extra.split(",") if p.strip())
    merged.extend(extra)
    return tuple(dict.fromkeys(merged))


def run_reflection(
    root: Path,
    *,
    since_hours: float,
    extra_globs: Sequence[str],
    overlap_minutes: int = 90,
    dry_run: bool = False,
) -> ReflectionRun:
    started = utc_now()
    state = load_state(root)
    last_run_s = state.get("last_run_utc")
    if last_run_s:
        try:
            last_dt = datetime.fromisoformat(last_run_s.replace("Z", "+00:00"))
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            cutoff = last_dt - timedelta(minutes=overlap_minutes)
        except ValueError:
            cutoff = started - timedelta(hours=since_hours)
    else:
        cutoff = started - timedelta(hours=since_hours)

    globs = collect_globs(extra_globs)
    logger.info("Reflection globs: %s", globs)
    session_files = iter_session_files(root, globs, cutoff)
    error_entries = load_error_log_entries(root)

    insights: list[Insight] = []
    for sf in session_files:
        insights.extend(read_and_extract(sf, root))

    insights = dedupe_insights(insights)
    insights.sort(key=lambda x: (x.category, x.severity, x.text))

    top_sessions = [p.relative_to(root).as_posix() for p in session_files[:20]]
    summary = build_summary_markdown(started, len(session_files), insights, top_sessions)

    finished = utc_now()
    run_id = started.strftime("%Y%m%d_%H%M%S")

    run = ReflectionRun(
        run_id=run_id,
        started_at_utc=started.replace(microsecond=0).isoformat(),
        finished_at_utc=finished.replace(microsecond=0).isoformat(),
        files_scanned=len(session_files),
        session_files=[p.relative_to(root).as_posix() for p in session_files],
        insights=insights,
        summary_markdown=summary,
    )

    session_block = build_session_analysis_markdown(root, session_files, cutoff, started)
    patterns_block = build_pattern_detection_markdown(insights, error_entries)
    recs = build_actionable_recommendations(insights, error_entries)
    recs_block = build_recommendations_markdown(recs)
    stats = build_stats_summary(cutoff, started, session_files, insights, error_entries)
    stats_block = build_stats_summary_markdown(stats)
    full_doc = build_full_reflection_markdown(run, session_block, patterns_block, recs_block, stats_block)

    if dry_run:
        logger.info("Dry-run: skipping writes for run %s", run_id)
        return run

    md_path, _ = write_insight_artifacts(root, run)
    day_path = append_daily_reflection(root, started, run_id, full_doc)
    write_reflection_json(root, run, stats, recs)
    weekly_path = update_weekly_summary(root, started, summary)
    write_latest_pointers(root, md_path, weekly_path, day_path)

    state["last_run_utc"] = finished.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    state["last_run_id"] = run_id
    state["last_insight_count"] = len(insights)
    save_state(root, state)

    try:
        from src.coordination.iskra_kara_shared_memory import notify_kara_from_iskra

        notify_kara_from_iskra(
            "reflection",
            {
                "summary_markdown": run.summary_markdown,
                "run_id": run.run_id,
                "files_scanned": run.files_scanned,
                "insight_count": len(run.insights),
                "stats": stats,
                "recommendations": recs,
            },
        )
    except Exception:
        logger.debug("notify_kara_from_iskra skipped (import or runtime)", exc_info=True)

    return run


def maybe_post_results(run: ReflectionRun, *, dry_run: bool) -> list[str]:
    log: list[str] = []
    text = run.summary_markdown
    webhook = os.environ.get("REFLECTION_WEBHOOK_URL", "").strip()
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

    payload = {
        "text": text,
        "meta": {
            "run_id": run.run_id,
            "started_at": run.started_at_utc,
            "files_scanned": run.files_scanned,
            "insight_count": len(run.insights),
        },
    }

    if dry_run:
        log.append("[dry-run] Skipping webhook and Telegram.")
        return log

    if webhook:
        ok, msg = post_webhook(webhook, payload)
        log.append(f"Webhook {'ok' if ok else 'FAILED'}: {msg[:400]}")

    if token and chat_id:
        ok, msg = post_telegram_summary(token, chat_id, text)
        log.append(f"Telegram {'ok' if ok else 'FAILED'}: {msg[:400]}")
    elif token or chat_id:
        log.append("Telegram skipped: need both TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.")

    if not webhook and not (token and chat_id):
        log.append("No REFLECTION_WEBHOOK_URL or full Telegram credentials; summary only on disk.")

    return log


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Analyze recent agent logs, refresh .learnings/, and optionally post a summary.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Workspace root (default: AUTO_REFLECTION_ROOT or cwd).",
    )
    parser.add_argument(
        "--since-hours",
        type=float,
        default=DEFAULT_SINCE_HOURS,
        help=f"Hours of history to include when no prior state exists (default: {DEFAULT_SINCE_HOURS}).",
    )
    parser.add_argument(
        "--glob",
        action="append",
        default=[],
        metavar="PATTERN",
        help="Additional glob relative to root (repeatable).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not write files or POST; prints intended actions.",
    )
    parser.add_argument(
        "--stdout-summary",
        action="store_true",
        help="Print the markdown summary to stdout.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable debug logging on stderr.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    root = args.root or Path(os.environ.get("AUTO_REFLECTION_ROOT", "") or ".").resolve()
    if args.dry_run:
        print(f"[dry-run] root={root}", file=sys.stderr)

    run = run_reflection(
        root,
        since_hours=args.since_hours,
        extra_globs=args.glob,
        dry_run=args.dry_run,
    )

    for line in maybe_post_results(run, dry_run=args.dry_run):
        print(line, file=sys.stderr)

    if args.stdout_summary:
        print(run.summary_markdown)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
