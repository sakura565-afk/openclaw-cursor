#!/usr/bin/env python3
"""
Cron-friendly self-reflection over recent agent-style logs and session artifacts.

Scans configurable paths for logs and JSON, clusters them into session-style groups,
extracts key insights, compares this run to prior runs for simple trends, writes
structured JSON reports under `.learnings/reports/`, refreshes `.learnings/insights/`,
appends a short digest to the memory log (for long-lived agent context), and
optionally posts the summary (Telegram or generic webhook).

Example crontab (daily at 09:00 UTC):

    0 9 * * * cd /path/to/repo && /usr/bin/python3 -m scripts.auto_reflection

Environment (all optional unless posting):

- AUTO_REFLECTION_ROOT — workspace root (default: current working directory)
- AUTO_REFLECTION_SESSION_GLOBS — extra comma-separated glob patterns relative to root
- AUTO_REFLECTION_MEMORY_LOG — path relative to root for appended reflection digests
  (default: memory/auto_reflection_log.md); set empty to skip memory updates
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
REPORTS_SUBDIR = "reports"
SUMMARIES_SUBDIR = "summaries"
STATE_NAME = ".state.json"
DEFAULT_MEMORY_LOG_REL = Path("memory/auto_reflection_log.md")
SCHEMA_VERSION = 1

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


@dataclass
class Insight:
    """One deduplicated insight line derived from logs."""

    text: str
    source_paths: list[str] = field(default_factory=list)
    severity: str = "info"  # info | warning | error
    category: str = "general"


@dataclass
class SessionCluster:
    """Files grouped by coarse location (e.g. logs/<agent> vs memory)."""

    cluster_id: str
    files: list[str]
    file_count: int
    insight_count: int
    categories: dict[str, int]
    severities: dict[str, int]
    newest_mtime_utc: str | None


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
    session_clusters: list[SessionCluster] = field(default_factory=list)
    key_insights: list[Insight] = field(default_factory=list)
    trend_notes: list[str] = field(default_factory=list)
    structured_report_relpath: str | None = None


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


def cluster_key_for_rel(rel: str) -> str:
    """Group paths into stable session-style clusters for trend-friendly rollups."""
    parts = rel.split("/")
    if len(parts) >= 2 and parts[0] == "logs":
        return f"logs/{parts[1]}"
    return parts[0] if parts else "_"


def build_session_clusters(
    session_paths: Sequence[Path],
    insights: Sequence[Insight],
    root: Path,
) -> list[SessionCluster]:
    rels = [p.relative_to(root).as_posix() for p in session_paths]
    by_cluster: dict[str, list[str]] = {}
    for rel in rels:
        by_cluster.setdefault(cluster_key_for_rel(rel), []).append(rel)

    def primary_cluster(ins: Insight) -> str:
        if not ins.source_paths:
            return "_"
        return cluster_key_for_rel(ins.source_paths[0])

    insight_counts: dict[str, int] = {k: 0 for k in by_cluster}
    cat_by: dict[str, Counter[str]] = {k: Counter() for k in by_cluster}
    sev_by: dict[str, Counter[str]] = {k: Counter() for k in by_cluster}

    for ins in insights:
        ck = primary_cluster(ins)
        if ck not in insight_counts:
            insight_counts[ck] = 0
            cat_by.setdefault(ck, Counter())
            sev_by.setdefault(ck, Counter())
        insight_counts[ck] += 1
        cat_by[ck][ins.category] += 1
        sev_by[ck][ins.severity] += 1

    mtime_by_rel: dict[str, float] = {}
    for p in session_paths:
        try:
            mtime_by_rel[p.relative_to(root).as_posix()] = p.stat().st_mtime
        except OSError:
            continue

    clusters: list[SessionCluster] = []
    for cid in sorted(by_cluster.keys()):
        files = sorted(by_cluster[cid])
        newest: str | None = None
        mtimes = [mtime_by_rel[f] for f in files if f in mtime_by_rel]
        if mtimes:
            newest = datetime.fromtimestamp(max(mtimes), tz=timezone.utc).replace(microsecond=0).isoformat()
        clusters.append(
            SessionCluster(
                cluster_id=cid,
                files=files,
                file_count=len(files),
                insight_count=insight_counts.get(cid, 0),
                categories=dict(cat_by.get(cid, Counter())),
                severities=dict(sev_by.get(cid, Counter())),
                newest_mtime_utc=newest,
            )
        )
    clusters.sort(key=lambda c: (-c.file_count, c.cluster_id))
    return clusters


def pick_key_insights(insights: Sequence[Insight], limit: int = 12) -> list[Insight]:
    """Rank insights for executive-style bullets (severity, spread, lesson hints)."""
    sev_rank = {"error": 0, "warning": 1, "info": 2}

    def score(ins: Insight) -> tuple[int, int, int, str]:
        bonus = 2 if ins.category == "lesson" else 0
        spread = len(ins.source_paths)
        return (-bonus, sev_rank.get(ins.severity, 9), -spread, ins.text.lower())

    ranked = sorted(insights, key=score)
    return ranked[:limit]


@dataclass
class _PriorRunSnap:
    run_id: str
    fingerprints: set[str]
    category_counts: Counter[str]
    insight_count: int


def _load_prior_run_snapshots(root: Path, *, max_runs: int) -> list[_PriorRunSnap]:
    d = root / LEARNINGS_DIR / INSIGHTS_SUBDIR
    if not d.is_dir():
        return []
    snaps: list[_PriorRunSnap] = []
    for path in sorted(d.glob("run_*.json"), key=lambda p: p.name, reverse=True):
        try:
            raw = path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (OSError, json.JSONDecodeError):
            continue
        rid = str(data.get("run_id") or path.stem.replace("run_", ""))
        items = data.get("insights") or []
        fps: set[str] = set()
        cats: Counter[str] = Counter()
        for item in items:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                fps.add(insight_fingerprint(item["text"]))
                cats[str(item.get("category") or "general")] += 1
        snaps.append(_PriorRunSnap(run_id=rid, fingerprints=fps, category_counts=cats, insight_count=len(items)))
        if len(snaps) >= max_runs:
            break
    return snaps


def detect_trends(insights: Sequence[Insight], prior: Sequence[_PriorRunSnap]) -> list[str]:
    """Lightweight cross-run signals for cron-sized windows (no ML)."""
    notes: list[str] = []
    if not prior:
        notes.append("No prior reflection runs on disk yet; trends will populate after the next scheduled passes.")
        return notes

    cur_fps = {insight_fingerprint(i.text) for i in insights}
    cur_cat = Counter(i.category for i in insights)

    union_prior_fp: set[str] = set()
    for s in prior:
        union_prior_fp |= s.fingerprints
    cat_keys = set(cur_cat.keys())
    for s in prior:
        cat_keys |= set(s.category_counts.keys())

    avg_cat: dict[str, float] = {}
    for cat in cat_keys:
        vals = [s.category_counts.get(cat, 0) for s in prior]
        avg_cat[cat] = sum(vals) / max(len(vals), 1)

    for cat, n in cur_cat.most_common(5):
        avg = avg_cat.get(cat, 0.0)
        if n >= avg + 2 and n >= 3:
            notes.append(f"Category **{cat}** is above recent average (now {n}, avg {avg:.1f} over prior runs).")

    recurring = [fp for fp in cur_fps if sum(1 for s in prior if fp in s.fingerprints) >= 2]
    if recurring:
        notes.append(f"{len(recurring)} insight theme(s) recur across multiple prior runs (stable hotspots).")

    new_only = cur_fps - union_prior_fp
    if new_only and cur_fps:
        notes.append(f"{len(new_only)} new distinct signal(s) compared to merged history of prior runs.")

    if not insights and prior:
        avg_ins = sum(s.insight_count for s in prior) / len(prior)
        if avg_ins >= 2:
            notes.append(f"Quiet window: no extracted signals this run; prior average was {avg_ins:.1f} insights.")

    if not notes:
        notes.append("Activity mix is in line with recent reflection runs; no strong drift detected.")
    return notes


def append_memory_digest(
    root: Path,
    *,
    run: ReflectionRun,
    memory_relpath: Path | None,
) -> Path | None:
    """Append a compact digest so agent memory picks up recurring context."""
    if memory_relpath is None:
        return None
    path = (root / memory_relpath).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    bullets = [f"- {i.text}" for i in run.key_insights[:8]]
    if not bullets:
        bullets = ["- _No ranked key insights this pass._"]
    trend_lines = [f"- {t}" for t in run.trend_notes[:6]]
    cluster_lines = [
        f"- `{c.cluster_id}`: {c.file_count} file(s), {c.insight_count} insight(s)"
        for c in run.session_clusters[:10]
    ]
    if not cluster_lines:
        cluster_lines = ["- _No clustered session files._"]
    report = run.structured_report_relpath or "(see .learnings/insights/)"
    block = (
        f"\n## Auto-reflection `{run.run_id}` ({run.started_at_utc})\n\n"
        f"- Structured report: `{report}`\n"
        f"- Files scanned: {run.files_scanned}, distinct insights: {len(run.insights)}\n\n"
        "### Key insights\n"
        + "\n".join(bullets)
        + "\n\n### Trends\n"
        + "\n".join(trend_lines)
        + "\n\n### Session clusters\n"
        + "\n".join(cluster_lines)
        + "\n"
    )
    prev = ""
    if path.exists():
        prev = path.read_text(encoding="utf-8")
        if run.run_id in prev:
            return path
    path.write_text(prev.rstrip() + block, encoding="utf-8")
    return path


def write_structured_report(root: Path, run: ReflectionRun) -> Path:
    """Machine-readable report for automation and dashboards."""
    rep_dir = root / LEARNINGS_DIR / REPORTS_SUBDIR
    rep_dir.mkdir(parents=True, exist_ok=True)
    path = rep_dir / f"reflection_{run.run_id}.json"
    body = {
        "schema_version": SCHEMA_VERSION,
        "run": asdict(run),
    }
    path.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    return path


def extract_insights_from_text(path: Path, root: Path, raw: str) -> Iterator[Insight]:
    rel = path.relative_to(root).as_posix()
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
    *,
    session_clusters: Sequence[SessionCluster] | None = None,
    key_insights: Sequence[Insight] | None = None,
    trend_notes: Sequence[str] | None = None,
) -> str:
    lines = [
        f"# Reflection summary ({run_at.date().isoformat()} UTC)",
        "",
        f"- Session files scanned: **{files_scanned}**",
        f"- Distinct insights: **{len(insights)}**",
        "",
    ]
    if session_clusters:
        lines.append("## Session analysis (clusters)")
        for c in session_clusters[:20]:
            sev_bits = ", ".join(f"{k}:{v}" for k, v in sorted(c.severities.items(), key=lambda kv: -kv[1])[:4])
            cat_bits = ", ".join(f"{k}:{v}" for k, v in sorted(c.categories.items(), key=lambda kv: -kv[1])[:4])
            tail = f"; severities: {sev_bits}" if sev_bits else ""
            cat_part = f"; categories: {cat_bits}" if cat_bits else ""
            mt = f"; newest UTC mtime: `{c.newest_mtime_utc}`" if c.newest_mtime_utc else ""
            lines.append(
                f"- **`{c.cluster_id}`** — {c.file_count} file(s), {c.insight_count} insight(s){mt}{cat_part}{tail}"
            )
        lines.append("")

    if trend_notes:
        lines.append("## Trends (vs prior reflection runs)")
        for note in trend_notes:
            lines.append(f"- {note}")
        lines.append("")

    if key_insights:
        lines.append("## Key insights")
        rank = {"error": 0, "warning": 1, "info": 2}
        for ins in sorted(key_insights, key=lambda i: (rank.get(i.severity, 9), i.text.lower())):
            badge = ins.severity.upper()
            lines.append(f"- **[{badge}] [{ins.category}]** {ins.text}")
        lines.append("")

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
    report_path: Path | None = None,
) -> None:
    """Small files for automation consumers."""

    ptr = root / LEARNINGS_DIR / "latest.json"
    data: dict[str, Any] = {
        "insights_md": md_path.relative_to(root).as_posix(),
        "weekly_summary_md": weekly_path.relative_to(root).as_posix(),
        "generated_at_utc": utc_now().replace(microsecond=0).isoformat(),
    }
    if report_path is not None:
        data["structured_report_json"] = report_path.relative_to(root).as_posix()
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


def resolve_memory_log_rel() -> Path | None:
    """Relative path under root for digest appends; None disables updates."""
    if "AUTO_REFLECTION_MEMORY_LOG" in os.environ:
        v = os.environ["AUTO_REFLECTION_MEMORY_LOG"].strip()
        if not v:
            return None
        return Path(v)
    return DEFAULT_MEMORY_LOG_REL


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
    session_files = iter_session_files(root, globs, cutoff)

    insights: list[Insight] = []
    for sf in session_files:
        insights.extend(read_and_extract(sf, root))

    insights = dedupe_insights(insights)
    insights.sort(key=lambda x: (x.category, x.severity, x.text))

    prior_snaps = _load_prior_run_snapshots(root, max_runs=8)
    clusters = build_session_clusters(session_files, insights, root)
    key_ranked = pick_key_insights(insights, limit=12)
    trend_notes = detect_trends(insights, prior_snaps)

    top_sessions = [p.relative_to(root).as_posix() for p in session_files[:20]]
    summary = build_summary_markdown(
        started,
        len(session_files),
        insights,
        top_sessions,
        session_clusters=clusters,
        key_insights=key_ranked,
        trend_notes=trend_notes,
    )

    finished = utc_now()
    run_id = started.strftime("%Y%m%d_%H%M%S")
    report_rel = f"{LEARNINGS_DIR}/{REPORTS_SUBDIR}/reflection_{run_id}.json"

    run = ReflectionRun(
        run_id=run_id,
        started_at_utc=started.replace(microsecond=0).isoformat(),
        finished_at_utc=finished.replace(microsecond=0).isoformat(),
        files_scanned=len(session_files),
        session_files=[p.relative_to(root).as_posix() for p in session_files],
        insights=insights,
        summary_markdown=summary,
        session_clusters=clusters,
        key_insights=key_ranked,
        trend_notes=trend_notes,
        structured_report_relpath=report_rel,
    )

    if dry_run:
        return run

    md_path, _ = write_insight_artifacts(root, run)
    report_path = write_structured_report(root, run)
    weekly_path = update_weekly_summary(root, started, summary)
    write_latest_pointers(root, md_path, weekly_path, report_path)

    mem_rel = resolve_memory_log_rel()
    append_memory_digest(root, run=run, memory_relpath=mem_rel)

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
            "session_clusters": len(run.session_clusters),
            "key_insight_count": len(run.key_insights),
            "trend_note_count": len(run.trend_notes),
            "structured_report": run.structured_report_relpath,
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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

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