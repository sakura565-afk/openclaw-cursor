#!/usr/bin/env python3
"""
Cron-friendly self-reflection over recent session transcripts and memory logs.

Scans configurable paths (including ``memory/**/*.md``), classifies signals into
what went well vs what went wrong, writes concise actionable notes under
``.learnings/``, merges recent runs into ``MEMORY.md`` (between HTML markers), and
optionally posts a summary (Telegram or generic webhook).

**Single run (typical crontab):**

    0 9 * * * cd /path/to/repo && /usr/bin/python3 -m scripts.auto_reflection

**Periodic loop (uses the ``schedule`` library):**

    python -m scripts.auto_reflection --interval-minutes 360 --root /path/to/repo

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
import os
import re
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
SUCCESS_HINTS = re.compile(
    r"(?i)(\b(successfully|completed successfully|all (?:tests|checks) passed|"
    r"tests passed|worked well|great work|looks good|lgtm|shipped|deployed cleanly|"
    r"goal (?:achieved|met)|task complete|resolved (?:fully|cleanly)|nailed it)\b|"
    r"\b(ci|build|pipeline)\s+(?:is\s+)?green\b)"
)
MAX_FILE_BYTES = 2 * 1024 * 1024
TELEGRAM_TEXT_LIMIT = 4000

MEMORY_MARKER_START = "<!-- auto-reflection:start -->"
MEMORY_MARKER_END = "<!-- auto-reflection:end -->"
MEMORY_CONSOLIDATE_RUNS = 10
MAX_MEMORY_BULLETS_PER_COLUMN = 28


@dataclass
class Insight:
    """One deduplicated insight line derived from logs."""

    text: str
    source_paths: list[str] = field(default_factory=list)
    severity: str = "info"  # info | warning | error
    category: str = "general"
    polarity: str = "neutral"  # positive | negative | neutral (went well / went wrong / other)


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


def _polarity_for_line(line: str) -> str:
    """Classify a line for summary buckets (went well vs went wrong)."""

    failed = bool(FAILURE_HINTS.search(line))
    success = bool(SUCCESS_HINTS.search(line))
    lesson = bool(LESSON_HINTS.search(line))
    if failed and not success:
        return "negative"
    if success and not failed:
        return "positive"
    if failed:
        return "negative"
    if lesson:
        return "negative"
    return "neutral"


def _severity_for_line(line: str) -> str:
    if re.search(r"(?i)\b(traceback|exception|fatal|critical)\b", line):
        return "error"
    if FAILURE_HINTS.search(line):
        return "warning"
    return "info"


def _category_for_line(line: str) -> str:
    if SUCCESS_HINTS.search(line) and not FAILURE_HINTS.search(line):
        return "success"
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


def extract_insights_from_text(path: Path, root: Path, raw: str) -> Iterator[Insight]:
    rel = path.relative_to(root).as_posix()
    for line in raw.splitlines():
        stripped = line.strip()
        if len(stripped) < 12:
            continue
        if FAILURE_HINTS.search(stripped) or LESSON_HINTS.search(stripped) or SUCCESS_HINTS.search(stripped):
            text = normalize_insight_text(stripped)
            if not text:
                continue
            yield Insight(
                text=text,
                source_paths=[rel],
                severity=_severity_for_line(stripped),
                category=_category_for_line(stripped),
                polarity=_polarity_for_line(stripped),
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
                polarity=ins.polarity,
            )
        else:
            for p in ins.source_paths:
                if p not in existing.source_paths:
                    existing.source_paths.append(p)
            sev_rank = {"error": 3, "warning": 2, "info": 1}
            if sev_rank.get(ins.severity, 0) > sev_rank.get(existing.severity, 0):
                existing.severity = ins.severity
            pol_rank = {"negative": 3, "positive": 2, "neutral": 1}
            if pol_rank.get(ins.polarity, 0) > pol_rank.get(existing.polarity, 0):
                existing.polarity = ins.polarity
    return list(buckets.values())


def build_summary_markdown(
    run_at: datetime,
    files_scanned: int,
    insights: Sequence[Insight],
    top_sessions: Sequence[str],
) -> str:
    went_well = [i for i in insights if i.polarity == "positive"]
    went_wrong = [i for i in insights if i.polarity == "negative"]
    other = [i for i in insights if i.polarity == "neutral"]

    lines = [
        f"# Reflection summary ({run_at.date().isoformat()} UTC)",
        "",
        f"- Session files scanned: **{files_scanned}**",
        f"- Distinct insights: **{len(insights)}** (positive: {len(went_well)}, negative: {len(went_wrong)}, other: {len(other)})",
        "",
    ]
    if top_sessions:
        lines.append("## Recently touched logs / memory")
        for p in top_sessions[:15]:
            lines.append(f"- `{p}`")
        lines.append("")

    if not insights:
        lines.append("_No notable patterns in the scanned window._")
        return "\n".join(lines)

    def _emit_bucket(title: str, bucket: Sequence[Insight]) -> None:
        lines.append(title)
        if not bucket:
            lines.append("_None captured in this window._")
            lines.append("")
            return
        rank = {"error": 0, "warning": 1, "info": 2}
        for ins in sorted(bucket, key=lambda i: (rank.get(i.severity, 9), i.text.lower())):
            badge = ins.severity.upper()
            sources = ", ".join(f"`{s}`" for s in ins.source_paths[:2])
            if len(ins.source_paths) > 2:
                sources += ", …"
            lines.append(f"- **[{badge}]** {ins.text} _(sources: {sources})_")
        lines.append("")

    _emit_bucket("## What went well", went_well)
    _emit_bucket("## What went wrong / lessons", went_wrong)
    if other:
        _emit_bucket("## Other signals", other)

    lines.append("## Actionable next steps")
    lines.extend(build_actionable_lines(insights, for_memory=False))
    lines.append("")

    ctr = Counter(i.category for i in insights)
    lines.append("## Category counts")
    for cat, n in ctr.most_common():
        lines.append(f"- **{cat}**: {n}")
    return "\n".join(lines).rstrip() + "\n"


def build_actionable_lines(insights: Sequence[Insight], *, for_memory: bool) -> list[str]:
    """Concise bullets: reinforce wins, address failures."""

    lines: list[str] = []
    positives = [i for i in insights if i.polarity == "positive"]
    negatives = [i for i in insights if i.polarity == "negative"]

    if positives:
        lines.append("- **Keep doing**")
        for ins in positives[:12]:
            lines.append(f"  - {ins.text}")
    if negatives:
        lines.append("- **Improve / watch**")
        cap = 16 if for_memory else 20
        for ins in negatives[:cap]:
            lines.append(f"  - {ins.text}")
    if not positives and not negatives:
        lines.append("_No polarized signals; skim raw logs for nuance._")
    return lines


def _polarity_from_record(row: dict[str, Any]) -> str:
    raw = row.get("polarity")
    if raw in ("positive", "negative", "neutral"):
        return str(raw)
    sev = str(row.get("severity") or "info")
    if sev in ("error", "warning"):
        return "negative"
    return "neutral"


def load_recent_insights_from_disk(root: Path, limit_json: int) -> list[Insight]:
    """Load and dedupe insights from the newest ``run_*.json`` artifacts."""

    insight_dir = root / LEARNINGS_DIR / INSIGHTS_SUBDIR
    if not insight_dir.is_dir():
        return []
    paths = sorted(insight_dir.glob("run_*.json"), reverse=True)[:limit_json]
    collected: list[Insight] = []
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for row in data.get("insights") or []:
            if not isinstance(row, dict) or not isinstance(row.get("text"), str):
                continue
            src = row.get("source_paths")
            paths_list = list(src) if isinstance(src, list) else []
            collected.append(
                Insight(
                    text=row["text"],
                    source_paths=[str(p) for p in paths_list],
                    severity=str(row.get("severity") or "info"),
                    category=str(row.get("category") or "general"),
                    polarity=_polarity_from_record(row),
                )
            )
    return dedupe_insights(collected)


def _ordered_unique_texts(insights: Sequence[Insight], polarity: str, cap: int) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for ins in insights:
        if ins.polarity != polarity:
            continue
        fp = insight_fingerprint(ins.text)
        if fp in seen:
            continue
        seen.add(fp)
        out.append(ins.text)
        if len(out) >= cap:
            break
    return out


def build_memory_consolidation_markdown(root: Path, run_at: datetime, current_run: ReflectionRun) -> str:
    """Section body placed between ``MEMORY_MARKER_*`` in MEMORY.md."""

    disk = load_recent_insights_from_disk(root, MEMORY_CONSOLIDATE_RUNS)
    merged = dedupe_insights(list(current_run.insights) + disk)
    ts = run_at.replace(microsecond=0).strftime("%Y-%m-%d %H:%M UTC")
    well = _ordered_unique_texts(merged, "positive", MAX_MEMORY_BULLETS_PER_COLUMN)
    bad = _ordered_unique_texts(merged, "negative", MAX_MEMORY_BULLETS_PER_COLUMN)
    lines = [
        "### Self-reflection (auto-consolidated)",
        f"_Updated {ts} from the last {MEMORY_CONSOLIDATE_RUNS} reflection runs (deduped)._",
        "",
        "#### What went well",
    ]
    if well:
        for t in well:
            lines.append(f"- {t}")
    else:
        lines.append("- _(none recorded recently)_")
    lines.extend(["", "#### What went wrong / fix next", ""])
    if bad:
        for t in bad:
            lines.append(f"- {t}")
    else:
        lines.append("- _(none recorded recently)_")
    lines.extend(["", "#### Actionable checklist", ""])
    lines.extend(build_actionable_lines(merged, for_memory=True))
    return "\n".join(lines).rstrip() + "\n"


def consolidate_memory_md(memory_path: Path, root: Path, run: ReflectionRun, *, dry_run: bool) -> None:
    if dry_run:
        return
    try:
        run_at = datetime.fromisoformat(run.started_at_utc.replace("Z", "+00:00"))
        if run_at.tzinfo is None:
            run_at = run_at.replace(tzinfo=timezone.utc)
    except ValueError:
        run_at = utc_now()
    block = build_memory_consolidation_markdown(root, run_at, run)
    memory_path.parent.mkdir(parents=True, exist_ok=True)
    if not memory_path.exists():
        content = f"# MEMORY\n\n{MEMORY_MARKER_START}\n\n{block}\n\n{MEMORY_MARKER_END}\n"
    else:
        content = memory_path.read_text(encoding="utf-8")
        if MEMORY_MARKER_START in content and MEMORY_MARKER_END in content:
            pre, rest = content.split(MEMORY_MARKER_START, 1)
            _mid, post = rest.split(MEMORY_MARKER_END, 1)
            content = pre + MEMORY_MARKER_START + "\n\n" + block + "\n\n" + MEMORY_MARKER_END + post
        else:
            content = (
                content.rstrip() + f"\n\n{MEMORY_MARKER_START}\n\n{block}\n\n{MEMORY_MARKER_END}\n"
            )
    memory_path.write_text(content, encoding="utf-8")


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

    actionable = "\n".join(
        ["## Actionable insights (concise)", ""] + build_actionable_lines(run.insights, for_memory=False) + [""]
    )
    md_lines = [
        f"# Run {run.run_id}",
        f"- Started: {run.started_at_utc}",
        f"- Finished: {run.finished_at_utc}",
        f"- Files scanned: {run.files_scanned}",
        "",
        run.summary_markdown,
        "",
        actionable,
    ]
    md_path.write_text("\n".join(md_lines), encoding="utf-8")

    json_path.write_text(json.dumps(asdict(run), indent=2) + "\n", encoding="utf-8")
    return md_path, json_path


def write_latest_pointers(root: Path, md_path: Path, weekly_path: Path) -> None:
    """Small files for automation consumers."""

    ptr = root / LEARNINGS_DIR / "latest.json"
    data = {
        "insights_md": md_path.relative_to(root).as_posix(),
        "weekly_summary_md": weekly_path.relative_to(root).as_posix(),
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
    memory_path: Path | None = None,
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

    if dry_run:
        return run

    md_path, _ = write_insight_artifacts(root, run)
    weekly_path = update_weekly_summary(root, started, summary)
    write_latest_pointers(root, md_path, weekly_path)
    consolidate_memory_md(memory_path or (root / "MEMORY.md"), root, run, dry_run=False)

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
        description=(
            "Reflect on recent session logs and memory transcripts: write `.learnings/`, "
            "update MEMORY.md, optional webhook/Telegram, or run on a schedule."
        ),
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
        "--memory",
        type=Path,
        default=None,
        help="MEMORY.md to merge consolidated learnings into (default: <root>/MEMORY.md).",
    )
    parser.add_argument(
        "--interval-minutes",
        type=int,
        default=0,
        metavar="N",
        help="If N > 0, repeat every N minutes using the `schedule` library (blocking).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    root = args.root or Path(os.environ.get("AUTO_REFLECTION_ROOT", "") or ".").resolve()
    memory_path = args.memory
    if memory_path is None:
        memory_path = root / "MEMORY.md"
    elif not memory_path.is_absolute():
        memory_path = (root / memory_path).resolve()
    else:
        memory_path = memory_path.resolve()

    if args.dry_run:
        print(f"[dry-run] root={root} memory={memory_path}", file=sys.stderr)

    def tick() -> None:
        run = run_reflection(
            root,
            since_hours=args.since_hours,
            extra_globs=args.glob,
            dry_run=args.dry_run,
            memory_path=memory_path,
        )
        for line in maybe_post_results(run, dry_run=args.dry_run):
            print(line, file=sys.stderr)
        if args.stdout_summary:
            print(run.summary_markdown)

    if args.interval_minutes > 0:
        import schedule

        schedule.every(args.interval_minutes).minutes.do(tick)
        tick()
        print(
            f"[schedule] Running every {args.interval_minutes} minute(s); Ctrl+C to stop.",
            file=sys.stderr,
        )
        while True:
            schedule.run_pending()
            time.sleep(1)

    tick()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())