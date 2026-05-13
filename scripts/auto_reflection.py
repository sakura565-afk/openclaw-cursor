#!/usr/bin/env python3
"""
Cron-friendly self-reflection over recent agent-style logs and session artifacts.

Scans configurable paths for logs and JSON, extracts wins and losses (failures,
tracebacks, regressions vs fixes, decisions, passing checks), writes structured
outputs under `.learnings/`, builds a periodic summary, and optionally posts the
summary (Telegram or generic webhook).

Integrates with OpenClaw-style session transcripts the same way as
`scripts/conversation_extractor.py` (messages / conversation JSON and line logs).

Example crontab (daily at 09:00 UTC):

    0 9 * * * cd /path/to/repo && /usr/bin/python3 -m scripts.auto_reflection

Standalone (from repo root):

    python3 scripts/auto_reflection.py --stdout-summary

Environment (all optional unless posting):

- AUTO_REFLECTION_ROOT — workspace root (default: current working directory)
- AUTO_REFLECTION_SESSION_GLOBS — extra comma-separated glob patterns relative to root
- AUTO_REFLECTION_SESSION_DIRS — comma-separated extra directories (absolute or ~) to
  scan with the same glob set (e.g. ~/.openclaw/workspace for live session.json trees)
- TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID — post summary via Telegram sendMessage
- REFLECTION_WEBHOOK_URL — POST JSON {\"text\": \"...\", \"meta\": {...}} to this URL
"""

from __future__ import annotations

import argparse
import functools
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
WINS_LOSSES_SUBDIR = "wins_losses"
STATE_NAME = ".state.json"

DEFAULT_SINCE_HOURS = 24 * 7
DEFAULT_SESSION_GLOBS = (
    "logs/**/*.log",
    "logs/**/*.json",
    "memory/**/*_log.md",
    "memory/**/*.md",
    "memory/**/conversation_extract_*.json",
    "**/session.json",
)

FAILURE_HINTS = re.compile(
    r"(?i)(\b(traceback|exception|error|failed|failure|timed?\s*out|exit\s*code\s*[1-9]\d*)\b|"
    r"\b(Fatal|Critical)\b|^Error:|\[\s*ERROR\s*\])"
)
LESSON_HINTS = re.compile(
    r"(?i)(\blesson learned\b|\btakeaway\b|\bremember to\b|\bnext time\b|"
    r"\bavoid\b|\bshould have\b|\broot cause\b)"
)
WIN_HINTS = re.compile(
    r"(?i)(\b(fixed|resolved|completed|success(?:ful)?|passed|passing|"
    r"all\s+tests\s+pass|tests?\s+pass|green\b|shipped|merged|deployed|"
    r"works\s+now|verified|unblocked|achievement)\b|✅|🎉)"
)
LOSS_EXTRA = re.compile(
    r"(?i)(\b(regression|blocked|rollback|revert(?:ed)?|"
    r"tests?\s+fail|ci\s+fail|build\s+fail|incident|"
    r"root\s+cause\s*:\s*failure)\b)"
)
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_SCANNED_FILES = 400
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


def session_dirs_from_env() -> tuple[Path, ...]:
    """Optional extra directories (e.g. ~/.openclaw/workspace) scanned with the same globs."""

    raw = os.environ.get("AUTO_REFLECTION_SESSION_DIRS", "")
    roots: list[Path] = []
    for part in raw.split(","):
        p = Path(part.strip()).expanduser()
        if p.is_dir():
            roots.append(p.resolve())
    return tuple(dict.fromkeys(roots))


def iter_session_files(
    root: Path,
    globs: Sequence[str],
    cutoff: datetime,
    *,
    extra_roots: Sequence[Path] | None = None,
) -> list[Path]:
    seen: set[Path] = set()
    out: list[Path] = []
    bases = [root]
    if extra_roots:
        bases.extend(Path(p).resolve() for p in extra_roots if p.is_dir())

    for base in bases:
        for pattern in globs:
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
    if len(out) > MAX_SCANNED_FILES:
        return out[:MAX_SCANNED_FILES]
    return out


def _severity_for_line(line: str) -> str:
    if re.search(r"(?i)\b(traceback|exception|fatal|critical)\b", line):
        return "error"
    if FAILURE_HINTS.search(line):
        return "warning"
    return "info"


def _category_for_line(line: str) -> str:
    if FAILURE_HINTS.search(line) or LOSS_EXTRA.search(line):
        if LESSON_HINTS.search(line):
            return "lesson"
        return "loss"
    if WIN_HINTS.search(line):
        return "win"
    if LESSON_HINTS.search(line):
        return "lesson"
    if re.search(r"(?i)\b(test|pytest|unittest)\b", line):
        return "testing"
    if re.search(r"(?i)\b(git|commit|merge|branch)\b", line):
        return "git"
    if re.search(r"(?i)\b(api|http|request|timeout)\b", line):
        return "integration"
    return "general"


def rel_under_root(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


@functools.lru_cache(maxsize=1)
def _session_parser_pair() -> tuple[Any, Any] | None:
    """OpenClaw transcript parsers from conversation_extractor (optional)."""

    try:
        from scripts.conversation_extractor import analyze_segments, parse_session_log

        return parse_session_log, analyze_segments
    except ImportError:
        try:
            from conversation_extractor import analyze_segments, parse_session_log

            return parse_session_log, analyze_segments
        except ImportError:
            return None


def extract_insights_from_openclaw_session(path: Path, root: Path) -> list[Insight] | None:
    """Use the same transcript model as conversation_extractor when JSON is a session."""

    pair = _session_parser_pair()
    if pair is None:
        return None
    parse_session_log, analyze_segments = pair
    segments = parse_session_log(path)
    if not segments:
        return None

    rel = rel_under_root(path, root)
    digest = analyze_segments(segments, rel)
    out: list[Insight] = []

    for d in digest.decisions:
        text = normalize_insight_text(d)
        if text:
            out.append(
                Insight(text=text, source_paths=[rel], severity="info", category="win"),
            )

    for item in digest.learnings:
        text = normalize_insight_text(item)
        if text:
            out.append(
                Insight(text=text, source_paths=[rel], severity="info", category="lesson"),
            )

    for _turn, _role, text in segments:
        out.extend(_insights_from_raw_text(rel, text))

    if not out:
        return None
    return out


def _insights_from_raw_text(rel: str, raw: str) -> list[Insight]:
    found: list[Insight] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if len(stripped) < 12:
            continue
        if not (
            FAILURE_HINTS.search(stripped)
            or LESSON_HINTS.search(stripped)
            or LOSS_EXTRA.search(stripped)
            or (WIN_HINTS.search(stripped) and len(stripped) >= 16)
        ):
            continue
        norm = normalize_insight_text(stripped)
        if not norm:
            continue
        found.append(
            Insight(
                text=norm,
                source_paths=[rel],
                severity=_severity_for_line(stripped),
                category=_category_for_line(stripped),
            ),
        )
    return found


def normalize_insight_text(line: str) -> str:
    line = line.strip()
    line = re.sub(r"\s+", " ", line)
    return line[:500]


def insight_fingerprint(text: str) -> str:
    return hashlib.sha256(text.lower().encode("utf-8")).hexdigest()[:16]


def extract_insights_from_text(path: Path, root: Path, raw: str) -> Iterator[Insight]:
    rel = rel_under_root(path, root)
    for ins in _insights_from_raw_text(rel, raw):
        yield ins


def extract_insights_from_json(path: Path, root: Path, raw: str) -> Iterator[Insight]:
    rel = rel_under_root(path, root)
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
        structured = extract_insights_from_openclaw_session(path, root)
        if structured is not None:
            return structured
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
            cat_pri = {
                "loss": 5,
                "lesson": 4,
                "win": 3,
                "testing": 2,
                "integration": 2,
                "git": 2,
                "general": 1,
            }
            if cat_pri.get(ins.category, 0) > cat_pri.get(existing.category, 0):
                existing.category = ins.category
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

    wins = [i for i in insights if i.category == "win"]
    losses = [i for i in insights if i.category == "loss"]
    if wins or losses:
        rank = {"error": 0, "warning": 1, "info": 2}
        if wins:
            lines.append("## Wins")
            for ins in sorted(wins, key=lambda i: (rank.get(i.severity, 9), i.text.lower()))[:30]:
                src = ", ".join(f"`{s}`" for s in ins.source_paths[:2])
                if len(ins.source_paths) > 2:
                    src += ", …"
                lines.append(f"- {ins.text} _(sources: {src})_")
            lines.append("")
        if losses:
            lines.append("## Losses")
            for ins in sorted(losses, key=lambda i: (rank.get(i.severity, 9), i.text.lower()))[:30]:
                src = ", ".join(f"`{s}`" for s in ins.source_paths[:2])
                if len(ins.source_paths) > 2:
                    src += ", …"
                lines.append(f"- **[{ins.severity.upper()}]** {ins.text} _(sources: {src})_")
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


def write_wins_losses_markdown(root: Path, run: ReflectionRun) -> Path | None:
    wins = [i for i in run.insights if i.category == "win"]
    losses = [i for i in run.insights if i.category == "loss"]
    if not wins and not losses:
        return None
    out_dir = root / LEARNINGS_DIR / WINS_LOSSES_SUBDIR
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"run_{run.run_id}.md"
    lines = [
        f"# Wins and losses ({run.run_id})",
        "",
        f"- UTC window: {run.started_at_utc} → {run.finished_at_utc}",
        f"- Files scanned: {run.files_scanned}",
        "",
    ]
    if wins:
        lines.append("## Wins")
        for ins in sorted(wins, key=lambda i: i.text.lower())[:50]:
            lines.append(f"- {ins.text}")
        lines.append("")
    if losses:
        lines.append("## Losses")
        for ins in sorted(losses, key=lambda i: (i.severity, i.text.lower()))[:50]:
            lines.append(f"- [{ins.severity}] {ins.text}")
        lines.append("")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return path


def write_latest_pointers(
    root: Path,
    md_path: Path,
    weekly_path: Path,
    wins_losses_md: Path | None = None,
) -> None:
    """Small files for automation consumers."""

    ptr = root / LEARNINGS_DIR / "latest.json"
    data = {
        "insights_md": md_path.relative_to(root).as_posix(),
        "weekly_summary_md": weekly_path.relative_to(root).as_posix(),
        "generated_at_utc": utc_now().replace(microsecond=0).isoformat(),
    }
    if wins_losses_md is not None:
        data["wins_losses_md"] = wins_losses_md.relative_to(root).as_posix()
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
    session_files = iter_session_files(
        root,
        globs,
        cutoff,
        extra_roots=session_dirs_from_env(),
    )

    insights: list[Insight] = []
    for sf in session_files:
        insights.extend(read_and_extract(sf, root))

    insights = dedupe_insights(insights)
    insights.sort(key=lambda x: (x.category, x.severity, x.text))

    top_sessions = [rel_under_root(p, root) for p in session_files[:20]]
    summary = build_summary_markdown(started, len(session_files), insights, top_sessions)

    finished = utc_now()
    run_id = started.strftime("%Y%m%d_%H%M%S")

    run = ReflectionRun(
        run_id=run_id,
        started_at_utc=started.replace(microsecond=0).isoformat(),
        finished_at_utc=finished.replace(microsecond=0).isoformat(),
        files_scanned=len(session_files),
        session_files=[rel_under_root(p, root) for p in session_files],
        insights=insights,
        summary_markdown=summary,
    )

    if dry_run:
        return run

    md_path, _ = write_insight_artifacts(root, run)
    weekly_path = update_weekly_summary(root, started, summary)
    wl_path = write_wins_losses_markdown(root, run)
    write_latest_pointers(root, md_path, weekly_path, wl_path)

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
    repo = Path(__file__).resolve().parent.parent
    repo_s = str(repo)
    if repo_s not in sys.path:
        sys.path.insert(0, repo_s)

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