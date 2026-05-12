#!/usr/bin/env python3
"""
Cron-friendly self-reflection over recent session logs under `memory/` (and optional paths).

Reads recent `memory/**/*.md` (and optional globs), derives what went well, what went
wrong, and actionable insights, writes a concise daily note to
`.learnings/YYYY-MM-DD_reflection.md`, rolls insights into weekly summaries, and can
append a short digest to `MEMORY.md`.

Direct run (from repo root or any cwd if `memory/` lives next to `scripts/`):

    python3 scripts/auto_reflection.py
    python3 scripts/auto_reflection.py --update-memory

Example crontab (daily at 09:00 UTC):

    0 9 * * * cd /path/to/repo && /usr/bin/python3 scripts/auto_reflection.py

Environment (all optional unless posting):

- AUTO_REFLECTION_ROOT — workspace root (default: repo root inferred from this file, else cwd)
- AUTO_REFLECTION_SESSION_GLOBS — extra comma-separated glob patterns relative to root
- AUTO_REFLECTION_UPDATE_MEMORY — set to `1`/`true`/`yes` to append digest to MEMORY.md (same as --update-memory)
- MEMORY_MD_PATH — path to MEMORY.md (default: `<root>/MEMORY.md`)
- TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID — post summary via Telegram sendMessage
- REFLECTION_WEBHOOK_URL — POST JSON {\"text\": \"...\", \"meta\": {...}} to this URL
"""

from __future__ import annotations

import argparse
import hashlib
import json
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


LEARNINGS_DIR = ".learnings"
INSIGHTS_SUBDIR = "insights"
SUMMARIES_SUBDIR = "summaries"
STATE_NAME = ".state.json"

DEFAULT_SINCE_HOURS = 24 * 7
# When using incremental state, still read at least this many hours of `memory/` for the daily note.
MEMORY_LOOKBACK_FLOOR_HOURS = 24
# Primary sources: daily/session-style markdown under memory/ (add logs via env or --glob).
DEFAULT_SESSION_GLOBS = (
    "memory/**/*.md",
    "memory/**/*.txt",
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
    r"(?i)(\b(ok|success|successful|completed|passed|no\s+errors?|no\s+failures?)\b|"
    r"broken\s+links:\s*0|unlinked\s+mentions:\s*0|failed\s*[:=]\s*0|"
    r"\|\s*ok\s*\||vault\s+scan\s+completed|self-test\b)"
)
MAX_FILE_BYTES = 2 * 1024 * 1024
TELEGRAM_TEXT_LIMIT = 4000


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
    daily_reflection_rel: str = ""


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


def _strip_signal_suffix(line: str) -> str:
    """Strip trailing source hint for deduplication."""

    return re.sub(r"\s*_\s*\(see `[^`]+`\)_\s*$", "", line).strip()


def extract_positive_signals(path: Path, root: Path, raw: str) -> Iterator[str]:
    """Lines that look like clean runs, successes, or zero-failure summaries."""

    if path.suffix.lower() == ".json":
        return
    rel = path.relative_to(root).as_posix()
    for line in raw.splitlines():
        stripped = line.strip()
        if len(stripped) < 14:
            continue
        if FAILURE_HINTS.search(stripped):
            continue
        if SUCCESS_HINTS.search(stripped):
            text = normalize_insight_text(stripped)
            if text:
                yield f"{text} _(see `{rel}`)_"


def dedupe_signal_lines(lines: Iterable[str], max_items: int = 14) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        fp = insight_fingerprint(_strip_signal_suffix(line))
        if fp in seen:
            continue
        seen.add(fp)
        out.append(line)
        if len(out) >= max_items:
            break
    return out


def compress_table_ok_signals(lines: Sequence[str]) -> list[str]:
    """Collapse many markdown table rows with status ok into one line per source file."""

    by_src: dict[str, list[str]] = {}
    rest: list[str] = []
    for line in lines:
        m = re.search(r"_\(see `([^`]+)`\)_\s*$", line)
        if m and line.strip().startswith("|") and re.search(r"\|\s*ok\s*\|", line, re.I):
            by_src.setdefault(m.group(1), []).append(line)
        else:
            rest.append(line)
    out: list[str] = list(rest)
    for src, rows in sorted(by_src.items()):
        if len(rows) >= 2:
            out.append(f"Batch/table: **{len(rows)}** items completed with status ok _(see `{src}`)_")
        else:
            out.extend(rows)
    return out


def build_actionable_lines(
    all_insights: Sequence[Insight],
    went_wrong: Sequence[Insight],
) -> list[str]:
    out: list[str] = []
    for ins in all_insights:
        if ins.category == "lesson":
            out.append(f"Apply: {ins.text}")
    if went_wrong:
        by_cat = Counter(i.category for i in went_wrong)
        for cat, n in by_cat.most_common(3):
            if n >= 2:
                out.append(
                    f"Recurring issue category **{cat}** ({n} signals) — worth a focused pass."
                )
        timeouts = sum(1 for i in went_wrong if re.search(r"(?i)\btimeout\b", i.text))
        if timeouts >= 2:
            out.append("Multiple timeouts — tighten retries, deadlines, or split work into smaller steps.")
    if not out and went_wrong:
        top = sorted(
            went_wrong,
            key=lambda i: ({"error": 0, "warning": 1, "info": 2}.get(i.severity, 9), i.text),
        )[0]
        out.append(f"Triage next: {normalize_insight_text(top.text)}")
    seen: set[str] = set()
    deduped: list[str] = []
    for line in out:
        fp = insight_fingerprint(line)
        if fp in seen:
            continue
        seen.add(fp)
        deduped.append(line)
        if len(deduped) >= 10:
            break
    return deduped


def build_daily_reflection_markdown(
    run_at: datetime,
    files_scanned: int,
    top_sources: Sequence[str],
    went_well: Sequence[str],
    went_wrong: Sequence[Insight],
    all_insights: Sequence[Insight],
) -> str:
    date_iso = run_at.date().isoformat()
    lines = [
        f"# Daily reflection — {date_iso} (UTC)",
        "",
        f"- Files scanned (since cutoff): **{files_scanned}**",
        f"- Generated: `{run_at.replace(microsecond=0).isoformat()}`",
        "",
    ]
    if top_sources:
        lines.append("**Sources:**")
        for p in top_sources[:12]:
            lines.append(f"- `{p}`")
        lines.append("")
    lines.append("## What went well")
    if went_well:
        for w in went_well:
            lines.append(f"- {w}")
    else:
        lines.append(
            "- _No explicit success signals in scanned lines (empty window or logs are issue-only)._"
        )
    lines.append("")
    lines.append("## What went wrong")
    if went_wrong:
        rank = {"error": 0, "warning": 1, "info": 2}
        for ins in sorted(
            went_wrong,
            key=lambda i: (rank.get(i.severity, 9), i.text.lower()),
        )[:18]:
            src = ", ".join(f"`{s}`" for s in ins.source_paths[:2])
            lines.append(f"- **[{ins.severity.upper()}]** {ins.text} _(sources: {src})_")
    else:
        lines.append("- _No failure-style lines detected in the window._")
    lines.append("")
    lines.append("## Actionable insights")
    actionable = build_actionable_lines(all_insights, went_wrong)
    if actionable:
        for a in actionable:
            lines.append(f"- {a}")
    else:
        lines.append(
            "- _Keep capturing lessons and errors in `memory/` markdown logs for richer reflections._"
        )
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("_Auto-generated by `scripts/auto_reflection.py`; safe to edit._")
    return "\n".join(lines).rstrip() + "\n"


def daily_reflection_path(root: Path, run_at: datetime) -> Path:
    return root / LEARNINGS_DIR / f"{run_at.date().isoformat()}_reflection.md"


def write_daily_reflection_file(root: Path, run_at: datetime, body: str) -> Path:
    path = daily_reflection_path(root, run_at)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body.rstrip() + "\n", encoding="utf-8")
    return path


def distill_memory_bullets(
    went_well: Sequence[str],
    went_wrong: Sequence[Insight],
    actionable: Sequence[str],
) -> list[str]:
    bullets: list[str] = []
    for a in actionable[:4]:
        bullets.append(a)
    for ins in went_wrong[:2]:
        bullets.append(f"Watch: {normalize_insight_text(ins.text)[:240]}")
    for w in went_well[:2]:
        bullets.append(f"Good: {_strip_signal_suffix(w)[:240]}")
    seen: set[str] = set()
    out: list[str] = []
    for b in bullets:
        fp = insight_fingerprint(b)
        if fp in seen:
            continue
        seen.add(fp)
        out.append(b)
        if len(out) >= 7:
            break
    return out


def update_memory_digest(memory_path: Path, date_iso: str, bullets: Sequence[str]) -> None:
    heading = f"## Reflection digest — {date_iso}"
    section_body = "\n".join(f"- {b}" for b in bullets) if bullets else "_No distilled bullets._"
    block = f"{heading}\n\n{section_body}\n"
    memory_path.parent.mkdir(parents=True, exist_ok=True)
    if not memory_path.exists():
        memory_path.write_text(f"# Memory\n\n{block}", encoding="utf-8")
        return
    text = memory_path.read_text(encoding="utf-8")
    pattern = re.compile(re.escape(heading) + r"\n\n.*?(?=\n## |\Z)", re.DOTALL)
    if pattern.search(text):
        new_text = pattern.sub(block.rstrip() + "\n", text, count=1)
    else:
        new_text = text.rstrip() + "\n\n" + block
    memory_path.write_text(new_text.rstrip() + "\n", encoding="utf-8")


def env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def infer_repo_root() -> Path:
    """Prefer the repo that contains `scripts/auto_reflection.py` and `memory/`."""

    here = Path(__file__).resolve()
    cand = here.parent.parent
    if (cand / "memory").is_dir():
        return cand
    return Path.cwd().resolve()


def resolve_workspace_root(explicit: Path | None) -> Path:
    if explicit is not None:
        return explicit.resolve()
    env = os.environ.get("AUTO_REFLECTION_ROOT", "").strip()
    if env:
        return Path(env).resolve()
    return infer_repo_root()


def _is_benign_failure_mention(line: str) -> bool:
    """True when 'failed' is only reporting a zero count (common in summaries), not an incident."""

    if not re.search(r"(?i)failed['\"]?\s*[:=]\s*0", line):
        return False
    if re.search(r"(?i)\b(traceback|exception|syntaxerror)\b", line):
        return False
    if "{" in line:
        return True
    if re.search(r"(?i)\bok\b", line) and re.search(r"(?i)failed['\"]?\s*[:=]\s*0", line):
        return True
    return False


def extract_insights_from_text(path: Path, root: Path, raw: str) -> Iterator[Insight]:
    rel = path.relative_to(root).as_posix()
    for line in raw.splitlines():
        stripped = line.strip()
        if len(stripped) < 12:
            continue
        if FAILURE_HINTS.search(stripped) or LESSON_HINTS.search(stripped):
            if FAILURE_HINTS.search(stripped) and _is_benign_failure_mention(stripped):
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
        elif isinstance(obj, str) and FAILURE_HINTS.search(obj):
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
    except OSError:
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


def write_latest_pointers(
    root: Path,
    md_path: Path,
    weekly_path: Path,
    daily_path: Path | None = None,
) -> None:
    """Small files for automation consumers."""

    ptr = root / LEARNINGS_DIR / "latest.json"
    data: dict[str, Any] = {
        "insights_md": md_path.relative_to(root).as_posix(),
        "weekly_summary_md": weekly_path.relative_to(root).as_posix(),
        "generated_at_utc": utc_now().replace(microsecond=0).isoformat(),
    }
    if daily_path is not None:
        data["daily_reflection_md"] = daily_path.relative_to(root).as_posix()
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
    update_memory: bool = False,
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

    floor = started - timedelta(hours=MEMORY_LOOKBACK_FLOOR_HOURS)
    cutoff = min(cutoff, floor)

    globs = collect_globs(extra_globs)
    session_files = iter_session_files(root, globs, cutoff)

    insights: list[Insight] = []
    positive_lines: list[str] = []
    for sf in session_files:
        insights.extend(read_and_extract(sf, root))
        if sf.suffix.lower() != ".json":
            try:
                raw_pos = sf.read_text(encoding="utf-8", errors="replace")
            except OSError:
                raw_pos = ""
            positive_lines.extend(extract_positive_signals(sf, root, raw_pos))

    insights = dedupe_insights(insights)
    insights.sort(key=lambda x: (x.category, x.severity, x.text))
    positive_lines = dedupe_signal_lines(positive_lines, 20)
    positive_lines = compress_table_ok_signals(positive_lines)

    went_wrong = [i for i in insights if i.severity in ("warning", "error")]

    top_sessions = [p.relative_to(root).as_posix() for p in session_files[:20]]
    summary = build_summary_markdown(started, len(session_files), insights, top_sessions)
    daily_md = build_daily_reflection_markdown(
        started,
        len(session_files),
        top_sessions,
        positive_lines,
        went_wrong,
        insights,
    )

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

    if dry_run:
        return run

    daily_path = write_daily_reflection_file(root, started, daily_md)
    run.daily_reflection_rel = daily_path.relative_to(root).as_posix()

    md_path, _ = write_insight_artifacts(root, run)
    weekly_path = update_weekly_summary(root, started, summary)
    write_latest_pointers(root, md_path, weekly_path, daily_path)

    if update_memory:
        raw_mem = os.environ.get("MEMORY_MD_PATH", "").strip()
        mem = Path(raw_mem).expanduser() if raw_mem else (root / "MEMORY.md")
        actionable = build_actionable_lines(insights, went_wrong)
        digest = distill_memory_bullets(positive_lines, went_wrong, actionable)
        update_memory_digest(mem, started.date().isoformat(), digest)

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
            },
        )
    except Exception:
        pass

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
        description=(
            "Analyze recent `memory/` session logs, write `.learnings/YYYY-MM-DD_reflection.md`, "
            "refresh `.learnings/`, and optionally post a summary."
        ),
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Workspace root (default: AUTO_REFLECTION_ROOT, else repo root with memory/, else cwd).",
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
        "--update-memory",
        action="store_true",
        help="Append/replace today's ## Reflection digest section in MEMORY.md (also: AUTO_REFLECTION_UPDATE_MEMORY=1).",
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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    root = resolve_workspace_root(args.root)
    update_memory = bool(args.update_memory or env_truthy("AUTO_REFLECTION_UPDATE_MEMORY"))

    if args.dry_run:
        print(f"[dry-run] root={root}", file=sys.stderr)

    run = run_reflection(
        root,
        since_hours=args.since_hours,
        extra_globs=args.glob,
        dry_run=args.dry_run,
        update_memory=update_memory,
    )

    if not args.dry_run and run.daily_reflection_rel:
        print(f"Daily reflection: {run.daily_reflection_rel}", file=sys.stderr)
    if update_memory and not args.dry_run:
        raw_mem = os.environ.get("MEMORY_MD_PATH", "").strip()
        mem_disp = raw_mem if raw_mem else str((root / "MEMORY.md").as_posix())
        print(f"MEMORY digest updated: {mem_disp}", file=sys.stderr)

    for line in maybe_post_results(run, dry_run=args.dry_run):
        print(line, file=sys.stderr)

    if args.stdout_summary:
        print(run.summary_markdown)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())