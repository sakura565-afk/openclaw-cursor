#!/usr/bin/env python3
"""
Cron-friendly self-reflection over recent agent-style logs and session artifacts.

Scans configurable paths for logs and JSON, extracts recurring failure patterns and
actionable notes, writes structured outputs under `.learnings/`, builds a periodic
summary, and optionally posts the summary (Telegram or generic webhook).

Example crontab (daily at 09:00 UTC, include OpenClaw logs when present):

    0 9 * * * cd /path/to/repo && /usr/bin/python3 auto_reflection.py --period daily

Weekly (Mondays 07:00 UTC):

    0 7 * * 1 cd /path/to/repo && /usr/bin/python3 auto_reflection.py --period weekly

Environment (all optional unless posting):

- AUTO_REFLECTION_ROOT — workspace root (default: current working directory)
- AUTO_REFLECTION_SESSION_GLOBS — extra comma-separated glob patterns relative to root
- AUTO_REFLECTION_INCLUDE_OPENCLAW — set to 0/false/no to skip ~/.openclaw log scan
- OPENCLAW_HOME — override OpenClaw install dir (default: ~/.openclaw)
- AUTO_REFLECTION_OPENCLAW_MAX_FILES — cap OpenClaw log files per run (default: 200)
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
MAX_FILE_BYTES = 2 * 1024 * 1024
TELEGRAM_TEXT_LIMIT = 4000
CURATED_SUMMARY_NAME = "CURATED_SUMMARY.md"
OPENCLAW_LOG_GLOBS = (
    "logs/**/*.log",
    "logs/**/*.json",
    "logs/**/*.txt",
    "workspace/logs/**/*.log",
    "workspace/logs/**/*.json",
    "workspace/logs/**/*.txt",
)
UUID_LIKE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    re.IGNORECASE,
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


def _env_bool(name: str, default: bool = True) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw not in {"0", "false", "no", "off"}


def resolve_openclaw_home() -> Path | None:
    override = os.environ.get("OPENCLAW_HOME", "").strip()
    home = Path(override).expanduser().resolve() if override else (Path.home() / ".openclaw").resolve()
    return home if home.is_dir() else None


def iter_session_files(root: Path, globs: Sequence[str], cutoff: datetime) -> list[tuple[Path, str]]:
    """Return (path, display_label) pairs for files under ``root`` matching globs."""

    seen: set[Path] = set()
    out: list[tuple[Path, str]] = []
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
            try:
                label = path.resolve().relative_to(root.resolve()).as_posix()
            except ValueError:
                label = path.as_posix()
            out.append((path, label))
    out.sort(key=lambda pair: pair[0].stat().st_mtime, reverse=True)
    return out


def iter_openclaw_session_files(oc_home: Path, cutoff: datetime) -> list[tuple[Path, str]]:
    """Session-style logs under OpenClaw home (``logs/``, ``workspace/logs/``)."""

    seen: set[Path] = set()
    out: list[tuple[Path, str]] = []
    oc_resolved = oc_home.resolve()
    for pattern in OPENCLAW_LOG_GLOBS:
        for path in oc_home.glob(pattern):
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
            try:
                suffix = path.resolve().relative_to(oc_resolved).as_posix()
            except ValueError:
                suffix = path.name
            out.append((path, f"openclaw/{suffix}"))
    out.sort(key=lambda pair: pair[0].stat().st_mtime, reverse=True)
    max_oc = int(os.environ.get("AUTO_REFLECTION_OPENCLAW_MAX_FILES", "200"))
    return out[:max_oc]


def merge_session_sources(
    root: Path,
    globs: Sequence[str],
    cutoff: datetime,
    *,
    include_openclaw: bool,
) -> list[tuple[Path, str]]:
    """Repo-relative logs plus optional OpenClaw session logs, de-duplicated by inode path."""

    merged: list[tuple[Path, str]] = []
    merged.extend(iter_session_files(root, globs, cutoff))
    if include_openclaw:
        oc = resolve_openclaw_home()
        if oc is not None:
            merged.extend(iter_openclaw_session_files(oc, cutoff))
    seen: set[Path] = set()
    deduped: list[tuple[Path, str]] = []
    for path, label in merged:
        try:
            rp = path.resolve()
        except OSError:
            continue
        if rp in seen:
            continue
        seen.add(rp)
        deduped.append((path, label))
    deduped.sort(key=lambda pair: pair[0].stat().st_mtime, reverse=True)
    max_total = int(os.environ.get("AUTO_REFLECTION_MAX_FILES", "500"))
    return deduped[:max_total]


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


def extract_insights_from_text(path: Path, source_display: str, raw: str) -> Iterator[Insight]:
    for line in raw.splitlines():
        stripped = line.strip()
        if len(stripped) < 12:
            continue
        if FAILURE_HINTS.search(stripped) or LESSON_HINTS.search(stripped):
            text = normalize_insight_text(stripped)
            if not text:
                continue
            yield Insight(
                text=text,
                source_paths=[source_display],
                severity=_severity_for_line(stripped),
                category=_category_for_line(stripped),
            )


def extract_insights_from_json(path: Path, source_display: str, raw: str) -> Iterator[Insight]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        yield from extract_insights_from_text(path, source_display, raw)
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
        for insight in extract_insights_from_text(path, source_display, s):
            if source_display not in insight.source_paths:
                insight.source_paths.insert(0, source_display)
            yield insight


def read_and_extract(path: Path, source_display: str) -> list[Insight]:
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    if path.suffix.lower() == ".json":
        return list(extract_insights_from_json(path, source_display, raw))
    return list(extract_insights_from_text(path, source_display, raw))


def failure_pattern_key(text: str) -> str:
    """Normalize free-form lines so similar failures roll up together."""

    t = text.lower().strip()
    t = UUID_LIKE.sub("<uuid>", t)
    t = re.sub(r"https?://[^\s]+", "<url>", t)
    t = re.sub(r"\b0x[0-9a-f]+\b", "<hex>", t)
    t = re.sub(r"\b[\d.]+\b", "<n>", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:240] if t else "<empty>"


def rollup_failure_patterns(insights: Sequence[Insight]) -> list[tuple[str, int, str, str]]:
    """Return rows: pattern_key, count, example text, worst severity — sorted by impact."""

    buckets: dict[str, dict[str, Any]] = {}
    rank = {"error": 3, "warning": 2, "info": 1}
    for ins in insights:
        pk = failure_pattern_key(ins.text)
        b = buckets.setdefault(pk, {"count": 0, "example": ins.text, "sev": ins.severity})
        b["count"] += 1
        if rank.get(ins.severity, 0) > rank.get(str(b["sev"]), 0):
            b["sev"] = ins.severity
            b["example"] = ins.text
    rows = [(pk, int(b["count"]), str(b["example"]), str(b["sev"])) for pk, b in buckets.items()]
    rows.sort(
        key=lambda r: (
            -r[1],
            {"error": 0, "warning": 1, "info": 2}.get(r[3], 9),
        )
    )
    return rows


def historical_pattern_counter(
    insights_dir: Path,
    *,
    max_runs: int = 28,
    max_age_hours: float = 14 * 24,
) -> Counter[str]:
    """Load prior ``run_*.json`` artifacts to weight recurring themes."""

    ctr: Counter[str] = Counter()
    if not insights_dir.is_dir():
        return ctr
    cutoff = utc_now() - timedelta(hours=max_age_hours)
    files = sorted(insights_dir.glob("run_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for p in files[:max_runs]:
        try:
            mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
        except OSError:
            continue
        if mtime < cutoff:
            continue
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        raw_insights = doc.get("insights")
        if not isinstance(raw_insights, list):
            continue
        for item in raw_insights:
            if isinstance(item, dict):
                t = item.get("text")
                if isinstance(t, str) and t.strip():
                    ctr[failure_pattern_key(t)] += 1
    return ctr


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

    rollup = rollup_failure_patterns(insights)
    if rollup:
        lines.append("## Recurring failure patterns (normalized)")
        for _pk, count, example, sev in rollup[:14]:
            preview = example if len(example) <= 160 else example[:157] + "…"
            lines.append(f"- **[{sev.upper()}] ×{count}** — {preview}")
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


def daily_report_path(root: Path, dt: datetime) -> Path:
    return root / LEARNINGS_DIR / SUMMARIES_SUBDIR / f"daily_{dt.date().isoformat()}.md"


def update_daily_summary(root: Path, run_at: datetime, body: str, run_id: str) -> Path:
    """Append one run into the calendar-day summary (idempotent via HTML comment marker)."""

    path = daily_report_path(root, run_at)
    path.parent.mkdir(parents=True, exist_ok=True)
    marker = f"<!-- auto_reflection:{run_id} -->"
    chunk = f"\n{marker}\n### {run_at.strftime('%H:%M:%S')} UTC\n\n{body}\n"
    if path.exists():
        existing = path.read_text(encoding="utf-8")
        if marker in existing:
            return path
        path.write_text(existing.rstrip() + "\n" + chunk, encoding="utf-8")
    else:
        path.write_text(f"# Daily reflection — {run_at.date().isoformat()} (UTC)\n" + chunk)
    return path


def write_curated_summary(
    root: Path,
    run_at: datetime,
    run_id: str,
    insights: Sequence[Insight],
    historical: Counter[str],
) -> Path:
    """Rolling human-facing digest for stand-ups and prompt tuning."""

    path = root / LEARNINGS_DIR / CURATED_SUMMARY_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    current = Counter()
    examples: dict[str, str] = {}
    severity_by_key: dict[str, str] = {}
    rank = {"error": 3, "warning": 2, "info": 1}
    for ins in insights:
        pk = failure_pattern_key(ins.text)
        current[pk] += 1
        if pk not in examples:
            examples[pk] = ins.text
        if rank.get(ins.severity, 0) > rank.get(severity_by_key.get(pk, "info"), 0):
            severity_by_key[pk] = ins.severity
    merged: Counter[str] = Counter(historical)
    merged.update(current)
    top = merged.most_common(18)
    lines = [
        "# Curated reflection summary",
        "",
        f"- Generated: **{run_at.replace(microsecond=0).isoformat().replace('+00:00', 'Z')}**",
        f"- Latest run id: `{run_id}`",
        f"- Distinct pattern keys (history window + this pass): **{len(merged)}**",
        "",
        "## Top recurring themes",
        "",
    ]
    for i, (pk, n) in enumerate(top, start=1):
        ex = examples.get(pk, pk[:140])
        sev = severity_by_key.get(pk, "info").upper()
        short = ex if len(ex) <= 220 else ex[:217] + "…"
        lines.append(f"{i}. **[{sev}] ×{n}** — {short}")
    lines.extend(["", "## Suggested actions", ""])
    action_n = 0
    for pk, n in top:
        if action_n >= 8:
            break
        sev = severity_by_key.get(pk, "info")
        if sev == "info" and n < 2:
            continue
        action_n += 1
        sample = examples.get(pk, pk)
        tail = "…" if len(sample) > 170 else ""
        lines.append(
            f"- [ ] **{sev.upper()}** ({n}×): add a guardrail, test, or runbook step for: "
            f"_{sample[:170]}{tail}_"
        )
    if action_n == 0:
        lines.append("- _No high-priority actions inferred; keep monitoring._")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
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
    *,
    curated_path: Path,
    daily_path: Path,
) -> None:
    """Small files for automation consumers."""

    ptr = root / LEARNINGS_DIR / "latest.json"
    data = {
        "insights_md": md_path.relative_to(root).as_posix(),
        "weekly_summary_md": weekly_path.relative_to(root).as_posix(),
        "daily_summary_md": daily_path.relative_to(root).as_posix(),
        "curated_summary_md": curated_path.relative_to(root).as_posix(),
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
    include_openclaw: bool | None = None,
) -> ReflectionRun:
    started = utc_now()
    if include_openclaw is None:
        include_openclaw = _env_bool("AUTO_REFLECTION_INCLUDE_OPENCLAW", default=True)

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
    session_pairs = merge_session_sources(root, globs, cutoff, include_openclaw=include_openclaw)

    insights: list[Insight] = []
    for path, label in session_pairs:
        insights.extend(read_and_extract(path, label))

    insights = dedupe_insights(insights)
    insights.sort(key=lambda x: (x.category, x.severity, x.text))

    top_sessions = [label for _, label in session_pairs[:20]]
    summary = build_summary_markdown(started, len(session_pairs), insights, top_sessions)

    finished = utc_now()
    run_id = started.strftime("%Y%m%d_%H%M%S")

    run = ReflectionRun(
        run_id=run_id,
        started_at_utc=started.replace(microsecond=0).isoformat(),
        finished_at_utc=finished.replace(microsecond=0).isoformat(),
        files_scanned=len(session_pairs),
        session_files=[label for _, label in session_pairs],
        insights=insights,
        summary_markdown=summary,
    )

    if dry_run:
        return run

    insights_dir = root / LEARNINGS_DIR / INSIGHTS_SUBDIR
    historical = historical_pattern_counter(insights_dir)

    md_path, _ = write_insight_artifacts(root, run)
    weekly_path = update_weekly_summary(root, started, summary)
    daily_path = update_daily_summary(root, started, summary, run_id)
    curated_path = write_curated_summary(root, started, run_id, insights, historical)
    write_latest_pointers(root, md_path, weekly_path, curated_path=curated_path, daily_path=daily_path)

    state["last_run_utc"] = finished.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    state["last_run_id"] = run_id
    state["last_insight_count"] = len(insights)
    state["last_include_openclaw"] = include_openclaw
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
        default=None,
        help="Hours of history when no prior state exists (default: 168, or set by --period).",
    )
    parser.add_argument(
        "--period",
        choices=("daily", "weekly"),
        default=None,
        help="Initial scan window when no state file exists: daily=24h, weekly=168h.",
    )
    parser.add_argument(
        "--include-openclaw",
        action="store_true",
        default=None,
        help="Scan ~/.openclaw logs (default: on unless AUTO_REFLECTION_INCLUDE_OPENCLAW=0).",
    )
    parser.add_argument(
        "--no-include-openclaw",
        action="store_true",
        help="Do not scan OpenClaw home logs.",
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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    root = args.root or Path(os.environ.get("AUTO_REFLECTION_ROOT", "") or ".").resolve()
    if args.dry_run:
        print(f"[dry-run] root={root}", file=sys.stderr)

    if args.since_hours is not None:
        since_hours = float(args.since_hours)
    elif args.period == "daily":
        since_hours = 24.0
    elif args.period == "weekly":
        since_hours = 168.0
    else:
        since_hours = DEFAULT_SINCE_HOURS

    include_oc: bool | None = None
    if args.no_include_openclaw:
        include_oc = False
    elif args.include_openclaw:
        include_oc = True

    run = run_reflection(
        root,
        since_hours=since_hours,
        extra_globs=args.glob,
        dry_run=args.dry_run,
        include_openclaw=include_oc,
    )

    for line in maybe_post_results(run, dry_run=args.dry_run):
        print(line, file=sys.stderr)

    if args.stdout_summary:
        print(run.summary_markdown)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())