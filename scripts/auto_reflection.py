#!/usr/bin/env python3
"""
Auto self-reflection over recent session transcripts (OpenClaw / agent logs).

**Purpose**

Scan recent log and transcript files, derive lightweight structured notes (what
went well, what went wrong, actionable insights), persist them for review, and
optionally notify via webhook or Telegram.

**Outputs**

- Daily: ``.learnings/daily/YYYY-MM-DD.md`` (UTC date by default; override with
  ``--date``).
- Monthly rollup (updated by the **weekly** job): ``.learnings/monthly/YYYY-MM.md``
  distills the last seven daily files into recurring themes.
- State: ``.learnings/.state.json`` tracks the last successful scan time so cron
  runs only process new material (with overlap).

**Transcript locations**

By default the scanner looks under (each directory only if it exists):

- ``$OPENCLAW_HOME/logs`` and ``$OPENCLAW_HOME/workspace/logs`` (default
  ``OPENCLAW_HOME`` is ``~/.openclaw``).
- ``<workspace>/.openclaw/logs``.
- The workspace root itself (repo ``logs/``, ``memory/``, etc.).

Override or extend with ``AUTO_REFLECTION_SESSION_GLOBS`` (comma-separated globs
**relative to the workspace root only**) and ``--glob``.

**Cron examples**

Daily at 09:00 UTC (from the workspace that should own ``.learnings/``)::

    0 9 * * * cd /path/to/workspace && /usr/bin/python3 scripts/auto_reflection.py --daily

Weekly distillation every Sunday::

    0 10 * * 0 cd /path/to/workspace && /usr/bin/python3 scripts/auto_reflection.py --weekly

On-demand (defaults to **daily** when no mode flag is given)::

    python3 scripts/auto_reflection.py
    python3 scripts/auto_reflection.py --view
    python3 scripts/auto_reflection.py --view 2026-05-10
    python3 scripts/auto_reflection.py --view monthly:2026-05

**Environment (optional)**

- ``AUTO_REFLECTION_ROOT`` — workspace root (default: current working directory).
- ``AUTO_REFLECTION_SESSION_GLOBS`` — extra globs relative to workspace root.
- ``AUTO_REFLECTION_LOG_BASES`` — extra absolute or ``~`` transcript directories
  (comma-separated).
- ``TELEGRAM_BOT_TOKEN`` / ``TELEGRAM_CHAT_ID`` — post summary via Telegram.
- ``REFLECTION_WEBHOOK_URL`` — POST JSON ``{\"text\": \"...\", \"meta\": {...}}``.
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
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

LEARNINGS_DIR = ".learnings"
DAILY_SUBDIR = "daily"
MONTHLY_SUBDIR = "monthly"
STATE_NAME = ".state.json"

DEFAULT_SINCE_HOURS = 24 * 7
DEFAULT_SESSION_GLOBS = (
    "logs/**/*.log",
    "logs/**/*.json",
    "logs/**/*.jsonl",
    "memory/**/*_log.md",
    "memory/**/*.md",
)
# Relative patterns applied under each OpenClaw-style log root.
OPENCLAW_LOG_GLOBS = ("**/*.log", "**/*.json", "**/*.jsonl", "**/*.md", "**/*.txt")

FAILURE_HINTS = re.compile(
    r"(?i)(\b(traceback|exception|error|failed|failure|timed?\s*out|exit\s*code\s*[1-9]\d*)\b|"
    r"\b(Fatal|Critical)\b|^Error:|\[\s*ERROR\s*\])"
)
SUCCESS_HINTS = re.compile(
    r"(?i)(\b(success|succeeded|completed|resolved|fixed|passed|passing|"
    r"no errors|looks good|worked|green|done)\b|✓|✔)"
)
LESSON_HINTS = re.compile(
    r"(?i)(\blesson learned\b|\btakeaway\b|\bremember to\b|\bnext time\b|"
    r"\bavoid\b|\bshould have\b|\broot cause\b|\baction items?\b|\bTODO\b|\bconsider\b)"
)
ACTION_HINTS = re.compile(
    r"(?i)(\b(should|must|need to|ensure|add a check|retry with|try\b.*\binstead)\b|"
    r"\bmonitor\b|\bwatch out\b)"
)

MAX_FILE_BYTES = 2 * 1024 * 1024
TELEGRAM_TEXT_LIMIT = 4000


def _default_openclaw_home() -> Path:
    override = os.environ.get("OPENCLAW_HOME")
    if override:
        return Path(override).expanduser().resolve()
    return (Path.home() / ".openclaw").resolve()


def transcript_search_bases(workspace: Path) -> list[Path]:
    """Directories to scan for session transcripts (deduplicated, existing only)."""
    bases: list[Path] = []
    home = _default_openclaw_home()
    for p in (
        home / "logs",
        home / "workspace" / "logs",
        workspace / ".openclaw" / "logs",
        workspace,
    ):
        rp = p.resolve()
        if rp not in bases:
            bases.append(rp)
    extra = os.environ.get("AUTO_REFLECTION_LOG_BASES", "")
    if extra.strip():
        for part in extra.split(","):
            part = part.strip()
            if not part:
                continue
            rp = Path(part).expanduser().resolve()
            if rp not in bases:
                bases.insert(0, rp)
    return bases


def display_rel(path: Path, bases: Sequence[Path]) -> str:
    """Stable short label for a log path (prefer path relative to a known base)."""
    path = path.resolve()
    best: str | None = None
    for base in bases:
        try:
            rel = path.relative_to(base.resolve())
            s = rel.as_posix()
        except ValueError:
            continue
        if best is None or len(s) < len(best):
            best = s
    return best or path.as_posix()


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


def _glob_under(base: Path, pattern: str, cutoff: datetime, seen: set[Path], out: list[Path]) -> None:
    if not base.is_dir():
        return
    try:
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
    except OSError:
        return


def discover_session_files(
    workspace: Path,
    bases: Sequence[Path],
    workspace_globs: Sequence[str],
    cutoff: datetime,
) -> list[Path]:
    """Collect recently modified transcript files from OpenClaw dirs and the workspace."""
    seen: set[Path] = set()
    out: list[Path] = []
    for base in bases:
        br = base.resolve()
        if not br.exists():
            continue
        # Under OpenClaw log roots, use broad patterns; under workspace, only user globs.
        if br == workspace.resolve():
            for pattern in workspace_globs:
                _glob_under(br, pattern, cutoff, seen, out)
        else:
            for pattern in OPENCLAW_LOG_GLOBS:
                _glob_under(br, pattern, cutoff, seen, out)
    out.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return out


def collect_workspace_globs(extra: Sequence[str]) -> tuple[str, ...]:
    merged = list(DEFAULT_SESSION_GLOBS)
    env_extra = os.environ.get("AUTO_REFLECTION_SESSION_GLOBS", "")
    if env_extra.strip():
        merged.extend(p.strip() for p in env_extra.split(",") if p.strip())
    merged.extend(extra)
    return tuple(dict.fromkeys(merged))


@dataclass
class Insight:
    """One deduplicated line derived from transcripts."""

    text: str
    source_paths: list[str] = field(default_factory=list)
    severity: str = "info"  # info | warning | error
    category: str = "general"


@dataclass
class ReflectionRun:
    """Serializable result of one daily reflection pass."""

    run_id: str
    started_at_utc: str
    finished_at_utc: str
    files_scanned: int
    session_files: list[str]
    went_well: list[str]
    went_wrong: list[str]
    actionable: list[str]
    summary_markdown: str


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


def normalize_line(line: str, max_len: int = 400) -> str:
    line = line.strip()
    line = re.sub(r"\s+", " ", line)
    return line[:max_len]


def insight_fingerprint(text: str) -> str:
    return hashlib.sha256(text.lower().encode("utf-8")).hexdigest()[:16]


def extract_insights_from_text(display_rel_path: str, raw: str) -> Iterator[Insight]:
    for line in raw.splitlines():
        stripped = line.strip()
        if len(stripped) < 12:
            continue
        if FAILURE_HINTS.search(stripped) or LESSON_HINTS.search(stripped):
            text = normalize_line(stripped)
            if not text:
                continue
            yield Insight(
                text=text,
                source_paths=[display_rel_path],
                severity=_severity_for_line(stripped),
                category=_category_for_line(stripped),
            )


def extract_insights_from_json(display_rel_path: str, raw: str) -> Iterator[Insight]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        yield from extract_insights_from_text(display_rel_path, raw)
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
        for insight in extract_insights_from_text(display_rel_path, s):
            if display_rel_path not in insight.source_paths:
                insight.source_paths.insert(0, display_rel_path)
            yield insight


def read_and_extract(path: Path, label: str) -> list[Insight]:
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    if path.suffix.lower() == ".json":
        return list(extract_insights_from_json(label, raw))
    return list(extract_insights_from_text(label, raw))


def dedupe_lines(lines: Iterable[str]) -> list[str]:
    buckets: dict[str, str] = {}
    for line in lines:
        key = insight_fingerprint(line)
        if key not in buckets:
            buckets[key] = line
    return list(buckets.values())


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


def classify_highlights(raw_lines: Sequence[str]) -> tuple[list[str], list[str], list[str]]:
    """Split raw log lines into went-well, went-wrong, and actionable buckets (heuristic)."""
    good: list[str] = []
    bad: list[str] = []
    act: list[str] = []
    for line in raw_lines:
        s = line.strip()
        if len(s) < 16:
            continue
        if FAILURE_HINTS.search(s):
            bad.append(normalize_line(s))
        elif SUCCESS_HINTS.search(s) and not FAILURE_HINTS.search(s):
            good.append(normalize_line(s))
        if LESSON_HINTS.search(s) or ACTION_HINTS.search(s):
            act.append(normalize_line(s))
    return dedupe_lines(good), dedupe_lines(bad), dedupe_lines(act)


def _read_raw_lines(paths: Sequence[Path], max_lines: int = 8000) -> list[str]:
    collected: list[str] = []
    for path in paths:
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for ln in raw.splitlines():
            collected.append(ln)
            if len(collected) >= max_lines:
                return collected
    return collected


def build_daily_markdown(
    run_at: datetime,
    files_scanned: int,
    went_well: Sequence[str],
    went_wrong: Sequence[str],
    actionable: Sequence[str],
    insights: Sequence[Insight],
    top_sources: Sequence[str],
) -> str:
    lines = [
        f"# Daily reflection — {run_at.date().isoformat()} (UTC)",
        "",
        f"- **Generated**: `{run_at.replace(microsecond=0).isoformat()}`",
        f"- **Transcript files scanned**: {files_scanned}",
        "",
        "## What went well",
        "",
    ]
    if went_well:
        for item in went_well[:40]:
            lines.append(f"- {item}")
    else:
        lines.append("_No explicit success signals detected in the scanned window._")
    lines.extend(["", "## What went wrong", ""])
    if went_wrong:
        for item in went_wrong[:50]:
            lines.append(f"- {item}")
    else:
        lines.append("_No clear failure lines matched heuristics in this window._")
    lines.extend(["", "## Actionable insights", ""])
    combined_action = list(actionable)
    for ins in insights:
        if ins.category == "lesson" or ins.severity in {"warning", "error"}:
            src = ", ".join(f"`{s}`" for s in ins.source_paths[:2])
            combined_action.append(f"{ins.text} _(sources: {src})_")
    combined_action = dedupe_lines(combined_action)
    if combined_action:
        for item in combined_action[:45]:
            lines.append(f"- {item}")
    else:
        lines.append("_No strong actionable patterns extracted; skim sources manually if needed._")
    if top_sources:
        lines.extend(["", "## Sources (recent)", ""])
        for p in top_sources[:25]:
            lines.append(f"- `{p}`")
    return "\n".join(lines).rstrip() + "\n"


def daily_reflection_path(root: Path, day: datetime) -> Path:
    return root / LEARNINGS_DIR / DAILY_SUBDIR / f"{day.date().isoformat()}.md"


def monthly_reflection_path(root: Path, month_dt: datetime) -> Path:
    return root / LEARNINGS_DIR / MONTHLY_SUBDIR / f"{month_dt.year:04d}-{month_dt.month:02d}.md"


def write_latest_pointers(root: Path, daily_path: Path, monthly_path: Path | None) -> None:
    ptr = root / LEARNINGS_DIR / "latest.json"
    data: dict[str, Any] = {
        "daily_md": daily_path.relative_to(root).as_posix(),
        "generated_at_utc": utc_now().replace(microsecond=0).isoformat(),
    }
    if monthly_path is not None:
        data["monthly_md"] = monthly_path.relative_to(root).as_posix()
    ptr.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def run_reflection(
    root: Path,
    *,
    since_hours: float,
    extra_globs: Sequence[str],
    overlap_minutes: int = 90,
    dry_run: bool = False,
    day: datetime | None = None,
) -> ReflectionRun:
    """Scan transcripts since last run (or ``since_hours``) and write ``.learnings/daily/``."""
    started = utc_now()
    run_day = day or started
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

    bases = transcript_search_bases(root)
    ws_globs = collect_workspace_globs(extra_globs)
    session_paths = discover_session_files(root, bases, ws_globs, cutoff)
    labels = [display_rel(p, bases) for p in session_paths]

    insights: list[Insight] = []
    for path, label in zip(session_paths, labels):
        insights.extend(read_and_extract(path, label))
    insights = dedupe_insights(insights)

    raw_lines = _read_raw_lines(session_paths)
    went_well, went_wrong, actionable = classify_highlights(raw_lines)
    # Enrich "went wrong" from structured insights if not already present
    for ins in insights:
        if ins.severity in {"error", "warning"} and FAILURE_HINTS.search(ins.text):
            went_wrong.append(ins.text)
    went_wrong = dedupe_lines(went_wrong)

    top_sources = labels[:20]
    summary = build_daily_markdown(
        run_day,
        len(session_paths),
        went_well,
        went_wrong,
        actionable,
        insights,
        top_sources,
    )

    finished = utc_now()
    run_id = started.strftime("%Y%m%d_%H%M%S")

    run = ReflectionRun(
        run_id=run_id,
        started_at_utc=started.replace(microsecond=0).isoformat(),
        finished_at_utc=finished.replace(microsecond=0).isoformat(),
        files_scanned=len(session_paths),
        session_files=labels,
        went_well=went_well,
        went_wrong=went_wrong,
        actionable=actionable,
        summary_markdown=summary,
    )

    if dry_run:
        return run

    out_path = daily_reflection_path(root, run_day)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(summary, encoding="utf-8")

    month_path = monthly_reflection_path(root, run_day)
    write_latest_pointers(root, out_path, month_path if month_path.exists() else None)

    today = utc_now().date()
    if run_day.date() == today:
        state["last_run_utc"] = finished.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        state["last_run_id"] = run_id
        state["last_daily_path"] = out_path.relative_to(root).as_posix()
        save_state(root, state)
    else:
        state["last_daily_path"] = out_path.relative_to(root).as_posix()
        save_state(root, state)

    try:
        from src.coordination.iskra_kara_shared_memory import notify_kara_from_iskra

        notify_kara_from_iskra(
            "reflection",
            {
                "summary_markdown": run.summary_markdown,
                "run_id": run.run_id,
                "files_scanned": run.files_scanned,
                "daily_path": str(out_path),
            },
        )
    except Exception:
        pass

    return run


def _parse_daily_file(path: Path) -> dict[str, list[str]]:
    """Extract bullet lists under known section headings from a daily markdown file."""
    sections: dict[str, list[str]] = {
        "well": [],
        "wrong": [],
        "action": [],
    }
    current: str | None = None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return sections
    for line in text.splitlines():
        h = line.strip().lower()
        if h.startswith("## what went well"):
            current = "well"
            continue
        if h.startswith("## what went wrong"):
            current = "wrong"
            continue
        if h.startswith("## actionable"):
            current = "action"
            continue
        if line.startswith("## "):
            current = None
            continue
        if current and line.strip().startswith("- ") and not line.strip().startswith("_"):
            item = line.strip()[2:].strip()
            if item:
                sections[current].append(item)
    return sections


def run_weekly_distill(root: Path, *, end: datetime | None = None, dry_run: bool = False) -> Path | None:
    """
    Read the last seven UTC daily reflections and append a distilled section to
    ``.learnings/monthly/YYYY-MM.md`` for the month of ``end`` (default: now).
    """
    anchor = end or utc_now()
    daily_dir = root / LEARNINGS_DIR / DAILY_SUBDIR
    if not daily_dir.is_dir():
        return None

    days: list[datetime] = [anchor - timedelta(days=i) for i in range(7)]
    aggregated_well: list[str] = []
    aggregated_wrong: list[str] = []
    aggregated_action: list[str] = []

    for d in reversed(days):
        p = daily_reflection_path(root, d)
        if not p.exists():
            continue
        sec = _parse_daily_file(p)
        aggregated_well.extend(sec["well"])
        aggregated_wrong.extend(sec["wrong"])
        aggregated_action.extend(sec["action"])

    iso = anchor.isocalendar()
    week_heading = f"## Week {iso.year}-W{iso.week:02d} (distilled {anchor.date().isoformat()} UTC)\n\n"

    def bullets(title: str, items: Sequence[str], limit: int = 25) -> list[str]:
        uniq = dedupe_lines(items)
        if not uniq:
            return [f"### {title}", "", f"_{title}: nothing captured in the window._", ""]
        body = [f"### {title}", ""]
        for line in uniq[:limit]:
            body.append(f"- {line}")
        body.append("")
        return body

    chunk_lines = [week_heading]
    chunk_lines.extend(bullets("Themes — what went well", aggregated_well))
    chunk_lines.extend(bullets("Themes — what went wrong", aggregated_wrong))
    chunk_lines.extend(bullets("Themes — actionable", aggregated_action))
    chunk = "\n".join(chunk_lines).rstrip() + "\n"

    month_path = monthly_reflection_path(root, anchor)
    if dry_run:
        return month_path

    month_path.parent.mkdir(parents=True, exist_ok=True)
    if month_path.exists():
        existing = month_path.read_text(encoding="utf-8")
        week_anchor = f"## Week {iso.year}-W{iso.week:02d} "
        if week_anchor in existing:
            write_latest_pointers(root, daily_reflection_path(root, anchor), month_path)
            return month_path
        path_text = existing.rstrip() + "\n\n" + chunk
    else:
        path_text = (
            f"# Monthly insights — {anchor.year:04d}-{anchor.month:02d}\n\n"
            f"_Updated automatically from daily reflections; weekly distill sections below._\n\n"
            + chunk
        )
    month_path.write_text(path_text, encoding="utf-8")
    daily_p = daily_reflection_path(root, anchor)
    write_latest_pointers(root, daily_p if daily_p.exists() else month_path, month_path)

    st = load_state(root)
    st["last_weekly_distill_utc"] = utc_now().replace(microsecond=0).isoformat().replace("+00:00", "Z")
    st["last_monthly_path"] = month_path.relative_to(root).as_posix()
    save_state(root, st)
    return month_path


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


def cmd_view(root: Path, spec: str | None) -> int:
    """Print recent reflections or a specific daily/monthly file."""
    daily_dir = root / LEARNINGS_DIR / DAILY_SUBDIR
    monthly_dir = root / LEARNINGS_DIR / MONTHLY_SUBDIR

    if spec is None or spec == "" or spec.upper() == "LIST":
        if not daily_dir.is_dir():
            print(f"No daily reflections directory at {daily_dir}", file=sys.stderr)
            return 1
        files = sorted(daily_dir.glob("*.md"), reverse=True)[:20]
        if not files:
            print("No daily reflection files yet.", file=sys.stderr)
            return 1
        print("Recent daily reflections:\n")
        for p in files:
            print(f"  {p.name}")
        print("\nView one:  --view YYYY-MM-DD   or   --view monthly:YYYY-MM")
        return 0

    if spec.lower().startswith("monthly:"):
        month = spec.split(":", 1)[1].strip()
        mp = monthly_dir / f"{month}.md"
        if not mp.is_file():
            print(f"Not found: {mp}", file=sys.stderr)
            return 1
        print(mp.read_text(encoding="utf-8"))
        return 0

    # Daily YYYY-MM-DD
    dp = daily_dir / f"{spec}.md"
    if not dp.is_file():
        # allow passing filename only
        dp2 = daily_dir / spec if not spec.endswith(".md") else daily_dir / spec
        if dp2.is_file():
            dp = dp2
        else:
            print(f"Not found: {dp}", file=sys.stderr)
            return 1
    print(dp.read_text(encoding="utf-8"))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Auto self-reflection: scan session transcripts, write .learnings/daily/, "
        "periodically distill to .learnings/monthly/.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Workspace root where .learnings/ is stored (default: AUTO_REFLECTION_ROOT or cwd).",
    )
    parser.add_argument(
        "--daily",
        action="store_true",
        help="Run the daily transcript scan and write or refresh today's daily markdown.",
    )
    parser.add_argument(
        "--weekly",
        action="store_true",
        help="Distill the last seven daily files into this month's monthly rollup.",
    )
    parser.add_argument(
        "--view",
        nargs="?",
        const="LIST",
        default=None,
        metavar="SPEC",
        help="View reflections: omit value to list recent dailies; YYYY-MM-DD for a day; "
        "monthly:YYYY-MM for a month file.",
    )
    parser.add_argument(
        "--date",
        type=str,
        default=None,
        metavar="YYYY-MM-DD",
        help="UTC date label for the daily file (with --daily). Default: today (UTC).",
    )
    parser.add_argument(
        "--since-hours",
        type=float,
        default=DEFAULT_SINCE_HOURS,
        help=f"Hours of history when no prior state exists (default: {DEFAULT_SINCE_HOURS}).",
    )
    parser.add_argument(
        "--glob",
        action="append",
        default=[],
        metavar="PATTERN",
        help="Extra glob relative to workspace root only (repeatable).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not write files or POST notifications.",
    )
    parser.add_argument(
        "--stdout-summary",
        action="store_true",
        help="Print the daily markdown summary to stdout.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    root = args.root or Path(os.environ.get("AUTO_REFLECTION_ROOT", "") or ".").resolve()

    if args.view is not None:
        spec = None if args.view == "LIST" else args.view
        return cmd_view(root, spec)

    do_daily = args.daily or not args.weekly
    do_weekly = args.weekly

    day: datetime | None = None
    if args.date:
        try:
            d = datetime.strptime(args.date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            day = d
        except ValueError:
            print("--date must be YYYY-MM-DD", file=sys.stderr)
            return 2

    if args.dry_run:
        print(f"[dry-run] root={root}", file=sys.stderr)

    exit_code = 0
    if do_daily:
        run = run_reflection(
            root,
            since_hours=args.since_hours,
            extra_globs=args.glob,
            dry_run=args.dry_run,
            day=day,
        )
        for line in maybe_post_results(run, dry_run=args.dry_run):
            print(line, file=sys.stderr)
        if args.stdout_summary:
            print(run.summary_markdown)

    if do_weekly:
        path = run_weekly_distill(root, dry_run=args.dry_run)
        if path is None:
            print("Weekly distill: no daily directory or nothing to read.", file=sys.stderr)
            exit_code = 1 if not do_daily else exit_code
        elif args.dry_run:
            print(f"[dry-run] would update {path}", file=sys.stderr)
        else:
            print(f"Weekly distill written under {path}", file=sys.stderr)

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
