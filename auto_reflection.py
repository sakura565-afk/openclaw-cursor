#!/usr/bin/env python3
"""
Self-reflection cron: scan recent session logs under ``memory/``, detect patterns in
errors, corrections, and successes, and write concise markdown with action items to
``memory/reflection/``.

Cron example (daily 09:00 UTC)::

    0 9 * * * cd /path/to/repo && /usr/bin/python3 auto_reflection.py

Environment (optional):

- ``AUTO_REFLECTION_ROOT`` — workspace root (default: current working directory)
- ``AUTO_REFLECTION_SINCE_HOURS`` — lookback when no state file exists (default: 168)
- ``AUTO_REFLECTION_INTERVAL_HOURS`` — interval ``schedule_next()`` uses (default: 24)
- ``AUTO_REFLECTION_SESSION_GLOBS`` — extra comma-separated globs relative to root
- ``TELEGRAM_BOT_TOKEN`` / ``TELEGRAM_CHAT_ID`` — optional summary post
- ``REFLECTION_WEBHOOK_URL`` — optional JSON POST of the summary
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

REFLECTION_DIR = Path("memory/reflection")
STATE_NAME = ".state.json"
SCHEDULE_NAME = ".schedule.json"
LATEST_NAME = "latest.md"

DEFAULT_SINCE_HOURS = 24 * 7
DEFAULT_INTERVAL_HOURS = 24
OVERLAP_MINUTES = 90
MAX_FILE_BYTES = 2 * 1024 * 1024
TELEGRAM_TEXT_LIMIT = 4000

DEFAULT_SESSION_GLOBS: tuple[str, ...] = (
    "memory/**/*_log.md",
    "memory/**/*.md",
)

EXCLUDE_GLOB_PARTS = ("memory/reflection/",)

FAILURE_HINTS = re.compile(
    r"(?i)(\b(traceback|exception|error|failed|failure|timed?\s*out|exit\s*code\s*[1-9]\d*)\b|"
    r"\b(fatal|critical)\b|^error:|\[\s*error\s*\])"
)
LESSON_HINTS = re.compile(
    r"(?i)(\blesson learned\b|\btakeaway\b|\bremember to\b|\bnext time\b|"
    r"\bavoid\b|\bshould have\b|\broot cause\b)"
)
CORRECTION_HINTS = re.compile(
    r"(?i)(\bfixed\b|\bcorrected\b|\bupdated\b|\bretry\b|\bworkaround\b|\bpatch(ed)?\b|"
    r"\binstead\b|\brolled back\b|\breverted\b|\bmitigation\b)"
)
SUCCESS_HINTS = re.compile(
    r"(?i)(\b(ok|success|completed|passed|done)\b|✓|✔|\|\s*ok\s*\||status\s*[|:]\s*ok)"
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Insight:
    """One deduplicated insight line derived from logs."""

    text: str
    source_paths: list[str] = field(default_factory=list)
    severity: str = "info"
    category: str = "general"


@dataclass
class SessionAnalysis:
    """Structured view of a single session log file."""

    rel_path: str
    errors: list[str] = field(default_factory=list)
    corrections: list[str] = field(default_factory=list)
    successes: list[str] = field(default_factory=list)
    insights: list[Insight] = field(default_factory=list)


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


def _state_path(root: Path) -> Path:
    return root / REFLECTION_DIR / STATE_NAME


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


def _severity_for_line(line: str) -> str:
    if re.search(r"(?i)\b(traceback|exception|fatal|critical)\b", line):
        return "error"
    if FAILURE_HINTS.search(line):
        return "warning"
    return "info"


def _category_for_line(line: str) -> str:
    if LESSON_HINTS.search(line):
        return "lesson"
    if CORRECTION_HINTS.search(line):
        return "correction"
    if re.search(r"(?i)\b(test|pytest|unittest)\b", line):
        return "testing"
    if re.search(r"(?i)\b(git|commit|merge|branch)\b", line):
        return "git"
    if re.search(r"(?i)\b(api|http|request|timeout)\b", line):
        return "integration"
    return "general"


def normalize_snippet(line: str, limit: int = 400) -> str:
    line = line.strip()
    line = re.sub(r"\s+", " ", line)
    return line[:limit]


def insight_fingerprint(text: str) -> str:
    return hashlib.sha256(text.lower().encode("utf-8")).hexdigest()[:16]


def extract_insights_from_text(path: Path, root: Path, raw: str) -> Iterator[Insight]:
    rel = path.relative_to(root).as_posix()
    for line in raw.splitlines():
        stripped = line.strip()
        if len(stripped) < 12:
            continue
        if FAILURE_HINTS.search(stripped) or LESSON_HINTS.search(stripped):
            text = normalize_snippet(stripped)
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


def _should_skip_path(rel_posix: str) -> bool:
    return any(part in rel_posix for part in EXCLUDE_GLOB_PARTS)


def collect_globs(extra: Sequence[str]) -> tuple[str, ...]:
    merged = list(DEFAULT_SESSION_GLOBS)
    env_extra = os.environ.get("AUTO_REFLECTION_SESSION_GLOBS", "")
    if env_extra.strip():
        merged.extend(p.strip() for p in env_extra.split(",") if p.strip())
    merged.extend(extra)
    return tuple(dict.fromkeys(merged))


def iter_session_files(root: Path, globs: Sequence[str], cutoff: datetime) -> list[Path]:
    seen: set[Path] = set()
    out: list[Path] = []
    for pattern in globs:
        for path in root.glob(pattern):
            if not path.is_file():
                continue
            try:
                rel = path.resolve().relative_to(root.resolve()).as_posix()
            except ValueError:
                rel = path.as_posix()
            if _should_skip_path(rel):
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


def _classify_session_lines(raw: str) -> tuple[list[str], list[str], list[str]]:
    errors: list[str] = []
    corrections: list[str] = []
    successes: list[str] = []
    for line in raw.splitlines():
        s = line.strip()
        if len(s) < 6:
            continue
        if FAILURE_HINTS.search(s):
            errors.append(normalize_snippet(s))
        elif CORRECTION_HINTS.search(s) or LESSON_HINTS.search(s):
            corrections.append(normalize_snippet(s))
        elif SUCCESS_HINTS.search(s):
            successes.append(normalize_snippet(s))
    return errors, corrections, successes


class SelfReflectionCron:
    """
    Periodic self-reflection over ``memory/`` session logs.

    Use :meth:`analyze_session` per file, :meth:`write_insights` for markdown output,
    and :meth:`schedule_next` to persist the next suggested run for external schedulers.
    """

    def __init__(
        self,
        root: Path,
        *,
        since_hours: float | None = None,
        interval_hours: float | None = None,
        extra_globs: Sequence[str] = (),
        overlap_minutes: int = OVERLAP_MINUTES,
    ) -> None:
        self.root = root.resolve()
        env_since = os.environ.get("AUTO_REFLECTION_SINCE_HOURS", "")
        env_interval = os.environ.get("AUTO_REFLECTION_INTERVAL_HOURS", "")
        self.since_hours = float(env_since) if env_since.strip() else (since_hours or DEFAULT_SINCE_HOURS)
        self.interval_hours = float(env_interval) if env_interval.strip() else (
            interval_hours or DEFAULT_INTERVAL_HOURS
        )
        self.extra_globs = tuple(extra_globs)
        self.overlap_minutes = overlap_minutes

    def _cutoff(self, started: datetime) -> datetime:
        state = load_state(self.root)
        last_run_s = state.get("last_run_utc")
        if last_run_s:
            try:
                last_dt = datetime.fromisoformat(last_run_s.replace("Z", "+00:00"))
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=timezone.utc)
                return last_dt - timedelta(minutes=self.overlap_minutes)
            except ValueError:
                pass
        return started - timedelta(hours=self.since_hours)

    def discover_session_files(self, started: datetime | None = None) -> list[Path]:
        started = started or utc_now()
        globs = collect_globs(self.extra_globs)
        return iter_session_files(self.root, globs, self._cutoff(started))

    def analyze_session(self, path: Path) -> SessionAnalysis:
        """Parse one session log and classify errors, corrections, and successes."""
        rel = path.relative_to(self.root).as_posix()
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return SessionAnalysis(rel_path=rel)

        errors, corrections, successes = _classify_session_lines(raw)
        insights = read_and_extract(path, self.root)
        return SessionAnalysis(
            rel_path=rel,
            errors=errors,
            corrections=corrections,
            successes=successes,
            insights=insights,
        )

    def write_insights(
        self,
        run: ReflectionRun,
        *,
        sessions: Sequence[SessionAnalysis],
        dry_run: bool = False,
    ) -> tuple[Path, Path]:
        """
        Write markdown report with action items and ``latest.md`` under
        ``memory/reflection/``.
        """
        out_dir = self.root / REFLECTION_DIR
        if dry_run:
            return out_dir / f"run_{run.run_id}.md", out_dir / LATEST_NAME

        out_dir.mkdir(parents=True, exist_ok=True)
        stamp_path = out_dir / f"reflection_{run.run_id}.md"

        action_items = self._build_action_items(run.insights, sessions)
        body = self._format_report(run, sessions, action_items)
        stamp_path.write_text(body, encoding="utf-8")

        latest_path = out_dir / LATEST_NAME
        latest_path.write_text(body, encoding="utf-8")

        json_path = out_dir / f"reflection_{run.run_id}.json"
        session_payload: list[dict[str, Any]] = []
        for s in sessions:
            row = asdict(s)
            row["insights"] = [asdict(i) for i in s.insights]
            session_payload.append(row)
        payload = {**asdict(run), "sessions": session_payload}
        json_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

        return stamp_path, latest_path

    def _build_action_items(
        self,
        insights: Sequence[Insight],
        sessions: Sequence[SessionAnalysis],
    ) -> list[str]:
        items: list[str] = []
        err_ctr: Counter[str] = Counter()
        for s in sessions:
            for e in s.errors:
                key = e[:120].lower()
                err_ctr[key] += 1
        for text, n in err_ctr.most_common(5):
            if n >= 2:
                items.append(f"Address recurring error pattern ({n}×): {text}")
        for ins in insights:
            if ins.severity == "error" and LESSON_HINTS.search(ins.text):
                items.append(f"Document recovery for: {ins.text[:200]}")
        if not items and any(s.errors for s in sessions):
            items.append("Triage the highest-severity errors above and add runbook steps.")
        if not items and insights:
            items.append("Review warning-level insights and add monitoring or alerts where gaps exist.")
        if not items:
            items.append("No urgent actions; keep logging consistently for richer future reflections.")
        deduped: list[str] = []
        seen: set[str] = set()
        for it in items:
            k = it[:80]
            if k in seen:
                continue
            seen.add(k)
            deduped.append(it)
        return deduped[:12]

    def _format_report(
        self,
        run: ReflectionRun,
        sessions: Sequence[SessionAnalysis],
        action_items: Sequence[str],
    ) -> str:
        lines: list[str] = [
            f"# Self-reflection — {run.run_id}",
            "",
            f"- **Started (UTC):** {run.started_at_utc}",
            f"- **Finished (UTC):** {run.finished_at_utc}",
            f"- **Session files scanned:** {run.files_scanned}",
            "",
            "## Summary",
            "",
            run.summary_markdown.strip(),
            "",
            "## What went well",
            "",
        ]
        all_ok: list[str] = []
        for s in sessions:
            for x in s.successes[:5]:
                all_ok.append(f"- `{s.rel_path}`: {x}")
        lines.extend(all_ok[:20] if all_ok else ["- _No explicit success markers in recent lines._", ""])

        lines.extend(
            [
                "## Corrections and lessons",
                "",
            ]
        )
        fixes: list[str] = []
        for s in sessions:
            for x in s.corrections[:5]:
                fixes.append(f"- `{s.rel_path}`: {x}")
        lines.extend(fixes[:20] if fixes else ["- _No correction or lesson phrases detected._", ""])

        lines.extend(["## Error patterns (recent)", ""])
        err_lines: list[str] = []
        for s in sessions:
            for x in s.errors[:5]:
                err_lines.append(f"- `{s.rel_path}`: {x}")
        lines.extend(err_lines[:25] if err_lines else ["- _No error-pattern lines in this window._", ""])

        lines.extend(
            [
                "## Action items",
                "",
            ]
        )
        for i, it in enumerate(action_items, 1):
            lines.append(f"{i}. {it}")
        lines.append("")
        lines.append("---")
        lines.append("*Generated by `auto_reflection.py` — run on a schedule for continuous improvement.*")
        lines.append("")
        return "\n".join(lines)

    def schedule_next(self, *, last_finished: datetime | None = None) -> dict[str, Any]:
        """
        Persist the next suggested run time and a crontab hint for operators.

        External cron can run ``auto_reflection.py`` on or after ``next_run_utc``.
        """
        finished = last_finished or utc_now()
        nxt = finished + timedelta(hours=self.interval_hours)
        cron_line = (
            f"0 9 * * * cd {self.root} && python3 auto_reflection.py "
            f"# daily 09:00 UTC; configured interval_hours={self.interval_hours}"
        )

        data = {
            "next_run_utc": nxt.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "interval_hours": self.interval_hours,
            "last_run_utc": finished.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "cron_suggestion": cron_line,
            "module": "auto_reflection.py",
        }
        sched_path = self.root / REFLECTION_DIR / SCHEDULE_NAME
        sched_path.parent.mkdir(parents=True, exist_ok=True)
        sched_path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return data

    def build_summary_markdown(
        self,
        run_at: datetime,
        files_scanned: int,
        insights: Sequence[Insight],
        top_sessions: Sequence[str],
        sessions: Sequence[SessionAnalysis],
    ) -> str:
        lines = [
            f"Window ending **{run_at.date().isoformat()}** (UTC).",
            "",
            f"- Distinct insight lines: **{len(insights)}**",
            f"- Sessions with errors: **{sum(1 for s in sessions if s.errors)}**",
            f"- Sessions with corrections/lessons: **{sum(1 for s in sessions if s.corrections)}**",
            f"- Sessions with success markers: **{sum(1 for s in sessions if s.successes)}**",
            "",
        ]
        if top_sessions:
            lines.append("### Recently modified logs")
            for p in top_sessions[:15]:
                lines.append(f"- `{p}`")
            lines.append("")
        if not insights:
            lines.append("_No failure/lesson keyword hits in the scanned window._")
            return "\n".join(lines)

        lines.append("### Insights by category")
        by_cat: dict[str, list[Insight]] = {}
        for ins in insights:
            by_cat.setdefault(ins.category, []).append(ins)

        for cat in sorted(by_cat.keys()):
            lines.append(f"**{cat}**")
            rank = {"error": 0, "warning": 1, "info": 2}
            for ins in sorted(
                by_cat[cat],
                key=lambda i: (rank.get(i.severity, 9), i.text.lower()),
            )[:12]:
                badge = ins.severity.upper()
                sources = ", ".join(f"`{s}`" for s in ins.source_paths[:2])
                lines.append(f"- [{badge}] {ins.text} _(sources: {sources})_")
            lines.append("")

        ctr = Counter(i.category for i in insights)
        lines.append("### Category counts")
        for cat, n in ctr.most_common():
            lines.append(f"- **{cat}**: {n}")
        return "\n".join(lines).rstrip() + "\n"

    def run(self, *, dry_run: bool = False) -> ReflectionRun:
        started = utc_now()
        session_paths = self.discover_session_files(started)
        sessions = [self.analyze_session(p) for p in session_paths]

        all_insights: list[Insight] = []
        for s in sessions:
            all_insights.extend(s.insights)
        all_insights = dedupe_insights(all_insights)
        all_insights.sort(key=lambda x: (x.category, x.severity, x.text))

        top_sessions = [p.relative_to(self.root).as_posix() for p in session_paths[:20]]
        summary = self.build_summary_markdown(
            started, len(session_paths), all_insights, top_sessions, sessions
        )

        finished = utc_now()
        run_id = started.strftime("%Y%m%d_%H%M%S")

        run = ReflectionRun(
            run_id=run_id,
            started_at_utc=started.replace(microsecond=0).isoformat(),
            finished_at_utc=finished.replace(microsecond=0).isoformat(),
            files_scanned=len(session_paths),
            session_files=[p.relative_to(self.root).as_posix() for p in session_paths],
            insights=all_insights,
            summary_markdown=summary,
        )

        if not dry_run:
            self.write_insights(run, sessions=sessions, dry_run=False)
            self.schedule_next(last_finished=finished)
            state = load_state(self.root)
            state["last_run_utc"] = finished.replace(microsecond=0).isoformat().replace("+00:00", "Z")
            state["last_run_id"] = run_id
            state["last_insight_count"] = len(all_insights)
            save_state(self.root, state)

        return run


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


def run_reflection(
    root: Path,
    *,
    since_hours: float,
    extra_globs: Sequence[str],
    overlap_minutes: int = OVERLAP_MINUTES,
    dry_run: bool = False,
) -> ReflectionRun:
    """Run one reflection cycle (used by CLI and tests)."""
    cron = SelfReflectionCron(
        root,
        since_hours=since_hours,
        extra_globs=extra_globs,
        overlap_minutes=overlap_minutes,
    )
    return cron.run(dry_run=dry_run)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Analyze recent memory/ session logs and write reports to memory/reflection/.",
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
        help="Hours of history when no state file exists (default: env or 168).",
    )
    parser.add_argument(
        "--interval-hours",
        type=float,
        default=None,
        help="Hours until next_run in schedule file (default: env or 24).",
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
        help="Print the markdown summary section to stdout.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    root = args.root or Path(os.environ.get("AUTO_REFLECTION_ROOT", "") or ".").resolve()
    if args.dry_run:
        print(f"[dry-run] root={root}", file=sys.stderr)

    since = args.since_hours if args.since_hours is not None else DEFAULT_SINCE_HOURS
    interval = args.interval_hours if args.interval_hours is not None else DEFAULT_INTERVAL_HOURS

    cron = SelfReflectionCron(
        root,
        since_hours=since,
        interval_hours=interval,
        extra_globs=args.glob,
    )
    run = cron.run(dry_run=args.dry_run)

    for line in maybe_post_results(run, dry_run=args.dry_run):
        print(line, file=sys.stderr)

    if args.stdout_summary:
        print(run.summary_markdown)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
