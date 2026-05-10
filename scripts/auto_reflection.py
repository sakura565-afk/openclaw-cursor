#!/usr/bin/env python3
"""
Cron-friendly self-reflection over recent agent-style logs and session artifacts.

Scans the workspace and ``~/.openclaw`` (``OPENCLAW_HOME``) for logs and memory
files, extracts patterns, writes ``.learnings/auto_reflection.md`` (date-tagged,
deduplicated across runs), plus run artifacts under ``.learnings/``.

**One-shot (cron / systemd oneshot):**

    cd /path/to/repo && python3 scripts/auto_reflection.py

**Periodic loop (companion to cron, or ad-hoc daemon):**

    python3 scripts/auto_reflection.py --daemon --interval-seconds 3600

Example crontab (daily at 09:00 UTC):

    0 9 * * * cd /path/to/repo && /usr/bin/python3 scripts/auto_reflection.py

User systemd: see ``src/self_improvement/openclaw-reflection.service`` and
``openclaw-reflection.timer``.

Environment (all optional unless posting):

- AUTO_REFLECTION_ROOT — workspace root (default: current working directory)
- OPENCLAW_HOME — OpenClaw data directory (default: ``~/.openclaw``); set empty to skip
- AUTO_REFLECTION_SESSION_GLOBS — extra comma-separated glob patterns relative to root
- TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID — post summary via Telegram sendMessage
- REFLECTION_WEBHOOK_URL — POST JSON {\"text\": \"...\", \"meta\": {...}} to this URL
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence


LEARNINGS_DIR = ".learnings"
INSIGHTS_SUBDIR = "insights"
SUMMARIES_SUBDIR = "summaries"
STATE_NAME = ".state.json"
AUTO_REFLECTION_MD = "auto_reflection.md"
SEEN_HASHES_KEY = "seen_insight_hashes"
MAX_SEEN_HASHES = 5000

DEFAULT_OPENCLAW_GLOBS = (
    "logs/**/*.log",
    "logs/**/*.json",
    "logs/**/*.txt",
    "workspace/logs/**/*.log",
    "workspace/logs/**/*.json",
    "workspace/memory/**/*.md",
    "workspace/**/*.md",
)


def openclaw_home_from_env() -> Path | None:
    if "OPENCLAW_HOME" not in os.environ:
        return (Path.home() / ".openclaw").resolve()
    raw = os.environ["OPENCLAW_HOME"].strip()
    if not raw:
        return None
    return Path(raw).expanduser().resolve()


DEFAULT_OPENCLAW_GLOBS = (
    "logs/**/*.log",
    "logs/**/*.json",
    "logs/**/*.txt",
    "workspace/logs/**/*.log",
    "workspace/logs/**/*.json",
    "workspace/memory/**/*.md",
)


FAILURE_HINTS = re.compile(
    r"(?i)(\b(traceback|exception|error|failed|failure|timed?\s*out|exit\s*code\s*[1-9]\d*)\b|"
    r"\b(Fatal|Critical)\b|^Error:|\[\s*ERROR\s*\])"
)
LESSON_HINTS = re.compile(
    r"(?i)(\blesson learned\b|\btakeaway\b|\bremember to\b|\bnext time\b|"
    r"\bavoid\b|\bshould have\b|\broot cause\b)"
)
SUCCESS_HINTS = re.compile(
    r"(?i)(\b(success|succeeded|completed successfully|all clear|all good|no errors|tests?\s+passed|"
    r"build succeeded|deployed successfully|merged successfully|resolved successfully|"
    r"looks good|working as expected)\b)"
)
ACTION_HINTS = re.compile(
    r"(?i)(\b(should|must|need to|recommend|consider|ensure|add a|add an|"
    r"implement|refactor|document|monitor|verify|double-check)\b)"
)
MAX_FILE_BYTES = 2 * 1024 * 1024
TELEGRAM_TEXT_LIMIT = 4000

DEFAULT_SINCE_HOURS = 24 * 7
DEFAULT_SESSION_GLOBS = (
    "logs/**/*.log",
    "logs/**/*.json",
    "memory/**/*_log.md",
    "memory/**/*.md",
)


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
    except (json.JSONDecodeError, OSError):
        return {}


def save_state(root: Path, data: dict[str, Any]) -> None:
    path = _state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def iter_session_files_multi(
    scan_specs: Sequence[tuple[Path, Sequence[str]]],
    cutoff: datetime,
) -> list[Path]:
    seen: set[Path] = set()
    out: list[Path] = []
    for base, patterns in scan_specs:
        if not base.exists():
            continue
        for pattern in patterns:
            for path in base.glob(pattern):
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
    return out


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
    return out


def _severity_for_line(line: str) -> str:
    if re.search(r"(?i)\b(traceback|exception|fatal|critical)\b", line):
        return "error"
    if FAILURE_HINTS.search(line):
        return "warning"
    return "info"


def _category_for_line(line: str) -> str:
    if LESSON_HINTS.search(line):
        return "lesson"
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


def insight_source_label(path: Path, workspace_root: Path, openclaw_home: Path | None) -> str:
    rp = path.resolve()
    if openclaw_home:
        try:
            inner = rp.relative_to(openclaw_home.resolve()).as_posix()
            return f".openclaw/{inner}"
        except ValueError:
            pass
    try:
        return rp.relative_to(workspace_root.resolve()).as_posix()
    except ValueError:
        pass
    return rp.as_posix()


def extract_insights_from_text(path: Path, rel: str, raw: str) -> Iterator[Insight]:
    for line in raw.splitlines():
        stripped = line.strip()
        if len(stripped) < 12:
            continue
        if (
            FAILURE_HINTS.search(stripped)
            or LESSON_HINTS.search(stripped)
            or SUCCESS_HINTS.search(stripped)
            or ACTION_HINTS.search(stripped)
        ):
            text = normalize_insight_text(stripped)
            if not text:
                continue
            yield Insight(
                text=text,
                source_paths=[rel],
                severity=_severity_for_line(stripped),
                category=_category_for_line(stripped),
            )


def extract_insights_from_json(path: Path, rel: str, raw: str) -> Iterator[Insight]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        yield from extract_insights_from_text(path, rel, raw)
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
        elif isinstance(obj, str) and FAILURE_HINTS.search(obj):
            strings.append(obj.strip())

    walk(data)
    for s in strings:
        for insight in extract_insights_from_text(path, rel, s):
            if rel not in insight.source_paths:
                insight.source_paths.insert(0, rel)
            yield insight


def read_and_extract(path: Path, workspace_root: Path, openclaw_home: Path | None) -> list[Insight]:
    rel = insight_source_label(path, workspace_root, openclaw_home)
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    if path.suffix.lower() == ".json":
        return list(extract_insights_from_json(path, rel, raw))
    return list(extract_insights_from_text(path, rel, raw))


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


def reflection_buckets(insights: Sequence[Insight]) -> tuple[list[Insight], list[Insight], list[Insight]]:
    """Split insights into what went well, what went wrong, and actionable (priority: wrong > action > well)."""

    well: list[Insight] = []
    wrong: list[Insight] = []
    action: list[Insight] = []
    for ins in insights:
        t = ins.text
        if FAILURE_HINTS.search(t) or ins.severity == "error":
            wrong.append(ins)
        elif ins.severity == "warning" and FAILURE_HINTS.search(t):
            wrong.append(ins)
        elif LESSON_HINTS.search(t) or ins.category == "lesson" or ACTION_HINTS.search(t):
            action.append(ins)
        elif SUCCESS_HINTS.search(t):
            well.append(ins)
        elif ins.severity == "warning":
            wrong.append(ins)
    return well, wrong, action


def format_insight_line(ins: Insight, max_sources: int = 2) -> str:
    src = ", ".join(f"`{s}`" for s in ins.source_paths[:max_sources])
    if len(ins.source_paths) > max_sources:
        src += ", …"
    return f"- {ins.text} _(sources: {src})_"


def filter_new_for_dedupe(items: Sequence[Insight], seen_hashes: set[str]) -> tuple[list[Insight], list[str]]:
    out: list[Insight] = []
    new_fps: list[str] = []
    for ins in items:
        fp = insight_fingerprint(ins.text)
        if fp in seen_hashes:
            continue
        out.append(ins)
        new_fps.append(fp)
        seen_hashes.add(fp)
    return out, new_fps


def build_auto_reflection_entry(
    run_at: datetime,
    new_well: Sequence[Insight],
    new_wrong: Sequence[Insight],
    new_action: Sequence[Insight],
    orig_well: Sequence[Insight],
    orig_wrong: Sequence[Insight],
    orig_action: Sequence[Insight],
) -> str:
    iso = run_at.replace(microsecond=0).isoformat().replace("+00:00", "Z")

    def section(title: str, new_items: Sequence[Insight], orig_items: Sequence[Insight]) -> list[str]:
        lines = [f"### {title}"]
        if new_items:
            lines.extend(format_insight_line(i) for i in new_items)
        elif orig_items:
            lines.append("- _(Repeated insights omitted; already recorded in a prior entry.)_")
        else:
            lines.append("- _(Nothing matching this category in the scanned window.)_")
        lines.append("")
        return lines

    parts = [f"## {iso}", ""]
    parts.extend(section("What went well", new_well, orig_well))
    parts.extend(section("What went wrong", new_wrong, orig_wrong))
    parts.extend(section("Actionable insights", new_action, orig_action))
    parts.extend(["---", ""])
    return "\n".join(parts)


def append_auto_reflection_md(
    root: Path,
    run_at: datetime,
    well: Sequence[Insight],
    wrong: Sequence[Insight],
    action: Sequence[Insight],
    state: dict[str, Any],
) -> list[str]:
    """Append dated entry to ``.learnings/auto_reflection.md``. Returns new fingerprints to merge into state."""

    raw_list = state.get(SEEN_HASHES_KEY, [])
    if not isinstance(raw_list, list):
        raw_list = []
    seen_mut: set[str] = {str(x) for x in raw_list if isinstance(x, str)}

    new_well, hw = filter_new_for_dedupe(well, seen_mut)
    new_wrong, hwr = filter_new_for_dedupe(wrong, seen_mut)
    new_action, ha = filter_new_for_dedupe(action, seen_mut)
    new_hashes = hw + hwr + ha

    entry = build_auto_reflection_entry(
        run_at, new_well, new_wrong, new_action, well, wrong, action
    )
    path = root / LEARNINGS_DIR / AUTO_REFLECTION_MD
    path.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# OpenClaw auto-reflection\n\n"
        "_Generated from workspace logs and from ``~/.openclaw`` when present. "
        "Cross-run deduplication uses fingerprints stored in ``.learnings/.state.json`` "
        f"(`{SEEN_HASHES_KEY}`)._\n\n"
    )
    if path.exists():
        path.write_text(path.read_text(encoding="utf-8").rstrip() + "\n\n" + entry, encoding="utf-8")
    else:
        path.write_text(header + entry, encoding="utf-8")
    return new_hashes


def merge_seen_hashes(state: dict[str, Any], new_hashes: Sequence[str]) -> None:
    merged: list[str] = []
    cur = state.get(SEEN_HASHES_KEY, [])
    if isinstance(cur, list):
        merged.extend(str(x) for x in cur if isinstance(x, str))
    for h in new_hashes:
        if h not in merged:
            merged.append(h)
    state[SEEN_HASHES_KEY] = merged[-MAX_SEEN_HASHES:]


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

    well, wrong, action = reflection_buckets(insights)
    lines.append("## Self-reflection")
    lines.append("")
    lines.append("### What went well")
    if well:
        for ins in sorted(well, key=lambda i: i.text.lower())[:25]:
            lines.append(format_insight_line(ins, max_sources=3))
    else:
        lines.append("- _(No clear success signals in extracted lines.)_")
    lines.append("")
    lines.append("### What went wrong")
    if wrong:
        for ins in sorted(wrong, key=lambda i: (i.severity, i.text.lower()))[:25]:
            lines.append(format_insight_line(ins, max_sources=3))
    else:
        lines.append("- _(No failure-style signals in extracted lines.)_")
    lines.append("")
    lines.append("### Actionable insights")
    if action:
        for ins in sorted(action, key=lambda i: i.text.lower())[:25]:
            lines.append(format_insight_line(ins, max_sources=3))
    else:
        lines.append("- _(No explicit lessons or action phrasing detected.)_")
    lines.append("")

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
    return md_path, json_path


def write_latest_pointers(root: Path, md_path: Path, weekly_path: Path) -> None:
    """Small files for automation consumers."""

    ptr = root / LEARNINGS_DIR / "latest.json"
    ar = root / LEARNINGS_DIR / AUTO_REFLECTION_MD
    data = {
        "insights_md": md_path.relative_to(root).as_posix(),
        "weekly_summary_md": weekly_path.relative_to(root).as_posix(),
        "generated_at_utc": utc_now().replace(microsecond=0).isoformat(),
    }
    if ar.exists():
        data["auto_reflection_md"] = ar.relative_to(root).as_posix()
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


def collect_openclaw_globs() -> tuple[str, ...]:
    merged = list(DEFAULT_OPENCLAW_GLOBS)
    env_extra = os.environ.get("AUTO_REFLECTION_OPENCLAW_GLOBS", "")
    if env_extra.strip():
        merged.extend(p.strip() for p in env_extra.split(",") if p.strip())
    return tuple(dict.fromkeys(merged))


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
    skip_openclaw: bool = False,
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

    openclaw_home = None if skip_openclaw else openclaw_home_from_env()
    scan_specs: list[tuple[Path, tuple[str, ...]]] = [(root, collect_globs(extra_globs))]
    if openclaw_home is not None and openclaw_home.exists():
        scan_specs.append((openclaw_home, collect_openclaw_globs()))

    session_files = iter_session_files_multi(scan_specs, cutoff)

    insights: list[Insight] = []
    for sf in session_files:
        insights.extend(read_and_extract(sf, root, openclaw_home))

    insights = dedupe_insights(insights)
    insights.sort(key=lambda x: (x.category, x.severity, x.text))

    top_sessions = [insight_source_label(p, root, openclaw_home) for p in session_files[:20]]
    summary = build_summary_markdown(started, len(session_files), insights, top_sessions)

    finished = utc_now()
    run_id = started.strftime("%Y%m%d_%H%M%S")

    run = ReflectionRun(
        run_id=run_id,
        started_at_utc=started.replace(microsecond=0).isoformat(),
        finished_at_utc=finished.replace(microsecond=0).isoformat(),
        files_scanned=len(session_files),
        session_files=[insight_source_label(p, root, openclaw_home) for p in session_files],
        insights=insights,
        summary_markdown=summary,
    )

    if dry_run:
        return run

    md_path, _ = write_insight_artifacts(root, run)
    weekly_path = update_weekly_summary(root, started, summary)

    well, wrong, action = reflection_buckets(insights)
    new_hashes = append_auto_reflection_md(root, started, well, wrong, action, state)
    merge_seen_hashes(state, new_hashes)

    write_latest_pointers(root, md_path, weekly_path)

    state["last_run_utc"] = finished.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    state["last_run_id"] = run_id
    state["last_insight_count"] = len(insights)
    save_state(root, state)

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
        "--skip-openclaw",
        action="store_true",
        help="Do not scan OPENCLAW_HOME (~/.openclaw by default).",
    )
    parser.add_argument(
        "--daemon",
        action="store_true",
        help="Run repeatedly until SIGINT/SIGTERM (cron-style companion loop).",
    )
    parser.add_argument(
        "--interval-seconds",
        type=int,
        default=3600,
        metavar="N",
        help="Sleep between daemon iterations (default: 3600).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    root = args.root or Path(os.environ.get("AUTO_REFLECTION_ROOT", "") or ".").resolve()
    if args.dry_run:
        print(f"[dry-run] root={root}", file=sys.stderr)

    stop = False

    def handle_stop(*_args: Any) -> None:
        nonlocal stop
        stop = True

    if args.daemon:
        signal.signal(signal.SIGTERM, handle_stop)
        signal.signal(signal.SIGINT, handle_stop)

    def one_pass() -> ReflectionRun:
        return run_reflection(
            root,
            since_hours=args.since_hours,
            extra_globs=args.glob,
            dry_run=args.dry_run,
            skip_openclaw=args.skip_openclaw,
        )

    if args.daemon:
        print(
            f"[daemon] interval={args.interval_seconds}s skip_openclaw={args.skip_openclaw} root={root}",
            file=sys.stderr,
        )
        while not stop:
            run = one_pass()
            for line in maybe_post_results(run, dry_run=args.dry_run):
                print(line, file=sys.stderr)
            if args.stdout_summary:
                print(run.summary_markdown)
            if stop:
                break
            elapsed = 0
            interval = max(1, int(args.interval_seconds))
            while elapsed < interval and not stop:
                chunk = min(5, interval - elapsed)
                time.sleep(chunk)
                elapsed += chunk
        print("[daemon] Stopped.", file=sys.stderr)
        return 0

    run = one_pass()

    for line in maybe_post_results(run, dry_run=args.dry_run):
        print(line, file=sys.stderr)

    if args.stdout_summary:
        print(run.summary_markdown)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())