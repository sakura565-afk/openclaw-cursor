#!/usr/bin/env python3
"""
Cron-friendly self-reflection from recent ``memory/`` transcripts and daily logs.

Scans ``memory/**/*.md`` and ``memory/**/*.json`` modified within a rolling UTC
window (default: 7 days), extracts heuristic wins, failures, and lessons, then
writes a single markdown file under ``.learnings/auto/`` with stable section
headings. New **Insights** and **Action Items** lines are filtered against text
already present anywhere under ``.learnings/`` so recurring lessons are not
re-copied verbatim.

Manual or cron usage (from repository root)::

    python3 scripts/auto_reflection.py
    python3 -m scripts.auto_reflection --stdout

Environment (optional)::

    AUTO_REFLECTION_ROOT   — workspace root (default: current working directory)
    AUTO_REFLECTION_DAYS   — integer days to look back (default: 7)
    REFLECTION_WEBHOOK_URL — POST JSON ``{\"text\": \"...\", \"meta\": {...}}``
    TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID — optional Telegram ``sendMessage``
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
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

LEARNINGS_DIR = ".learnings"
AUTO_SUBDIR = "auto"
LATEST_NAME = "latest.json"

MEMORY_GLOBS_DEFAULT = (
    "memory/**/*.md",
    "memory/**/*.json",
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
FUZZY_DUPLICATE_RATIO = 0.88
MAX_CORPUS_LINES = 2500


@dataclass
class Insight:
    """One deduplicated signal derived from memory files."""

    text: str
    source_paths: list[str] = field(default_factory=list)
    severity: str = "info"  # info | warning | error
    category: str = "general"  # win | loss | lesson | general


@dataclass
class ReflectionRun:
    """Serializable result of one reflection pass."""

    run_id: str
    started_at_utc: str
    finished_at_utc: str
    files_scanned: int
    memory_files: list[str]
    insights: list[Insight]
    reflection_markdown: str
    reflection_rel_path: str = ""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def normalize_match_text(text: str) -> str:
    """Collapse whitespace and lowercase for deduplication keys."""

    return " ".join(text.strip().lower().split())


def insight_fingerprint(text: str) -> str:
    return hashlib.sha256(normalize_match_text(text).encode("utf-8")).hexdigest()[:16]


def normalize_insight_text(line: str) -> str:
    line = line.strip()
    line = re.sub(r"\s+", " ", line)
    return line[:500]


def rel_under_root(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


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


@functools.lru_cache(maxsize=1)
def _session_parser_pair() -> tuple[Any, Any] | None:
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
            out.append(Insight(text=text, source_paths=[rel], severity="info", category="win"))

    for item in digest.learnings:
        text = normalize_insight_text(item)
        if text:
            out.append(Insight(text=text, source_paths=[rel], severity="info", category="lesson"))

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


def extract_insights_from_text(path: Path, root: Path, raw: str) -> Iterator[Insight]:
    rel = rel_under_root(path, root)
    yield from _insights_from_raw_text(rel, raw)


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


def collect_memory_globs(extra: Sequence[str]) -> tuple[str, ...]:
    merged = list(MEMORY_GLOBS_DEFAULT)
    merged.extend(extra)
    return tuple(dict.fromkeys(merged))


def iter_recent_memory_files(
    root: Path,
    globs: Sequence[str],
    cutoff: datetime,
) -> list[Path]:
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
    if len(out) > MAX_SCANNED_FILES:
        return out[:MAX_SCANNED_FILES]
    return out


def _iter_markdown_bodies(line: str) -> str | None:
    s = line.strip()
    if len(s) < 8:
        return None
    for prefix in ("- ", "* ", "• "):
        if s.startswith(prefix):
            return s[len(prefix) :].strip()
    return None


def load_learning_corpus(root: Path, exclude_paths: set[Path]) -> tuple[set[str], list[str]]:
    """Fingerprints and sample lines from existing ``.learnings`` markdown."""

    learnings = root / LEARNINGS_DIR
    if not learnings.is_dir():
        return set(), []

    fps: set[str] = set()
    samples: list[str] = []
    exclude_resolved = {p.resolve() for p in exclude_paths}

    for path in sorted(learnings.rglob("*.md")):
        if path.resolve() in exclude_resolved:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for raw_line in text.splitlines():
            body = _iter_markdown_bodies(raw_line)
            if body is None:
                candidate = raw_line.strip()
                if len(candidate) < 24 or candidate.startswith("#"):
                    continue
                body = candidate
            norm = normalize_match_text(body)
            if len(norm) < 16:
                continue
            fps.add(insight_fingerprint(body))
            fps.add(hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16])
            if len(samples) < MAX_CORPUS_LINES:
                samples.append(norm)
    return fps, samples


def is_near_duplicate_of_corpus(text: str, fps: set[str], corpus_lines: Sequence[str]) -> bool:
    """True when this line matches a known lesson or bullet in ``.learnings``."""

    if insight_fingerprint(text) in fps:
        return True
    norm = normalize_match_text(text)
    if hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16] in fps:
        return True
    if len(norm) < 20:
        return False
    for other in corpus_lines:
        if len(other) < 16:
            continue
        ratio = SequenceMatcher(None, norm, other).ratio()
        if ratio >= FUZZY_DUPLICATE_RATIO:
            return True
    return False


def _insight_sort_key(ins: Insight) -> tuple[int, int, str]:
    sev = {"error": 0, "warning": 1, "info": 2}.get(ins.severity, 9)
    cat = {"loss": 0, "lesson": 1, "win": 2, "general": 3}.get(ins.category, 9)
    return sev, cat, ins.text.lower()


def build_action_candidates(issues: Sequence[Insight], lessons: Sequence[Insight]) -> list[str]:
    out: list[str] = []
    for ins in sorted(issues, key=_insight_sort_key)[:14]:
        t = normalize_insight_text(ins.text)
        if t:
            out.append(f"Investigate root cause and add a guardrail for: {t}")
    for ins in sorted(lessons, key=_insight_sort_key)[:10]:
        t = normalize_insight_text(ins.text)
        if t:
            out.append(f"Turn lesson into checklist or automation: {t}")
    deduped: dict[str, str] = {}
    for line in out:
        key = insight_fingerprint(line)
        deduped.setdefault(key, line)
    return list(deduped.values())


def build_reflection_markdown(
    run_at: datetime,
    window_start: datetime,
    window_end: datetime,
    sources: Sequence[str],
    wins: Sequence[Insight],
    issues: Sequence[Insight],
    insights: Sequence[str],
    actions: Sequence[str],
) -> str:
    src_preview = ", ".join(f"`{s}`" for s in sources[:12])
    if len(sources) > 12:
        src_preview += ", …"
    head = [
        f"# Auto reflection — {run_at.date().isoformat()} (UTC)",
        "",
        f"- **Generated (UTC)**: {run_at.replace(microsecond=0).isoformat()}",
        f"- **Window**: {window_start.date().isoformat()} → {window_end.date().isoformat()} (source mtimes)",
        f"- **Sources**: {src_preview or '_none_'}",
        "",
    ]

    def section(title: str, lines: Sequence[str] | Sequence[Insight], *, as_insight: bool) -> list[str]:
        block = [title, ""]
        if as_insight:
            seq = [normalize_insight_text(i.text) for i in lines]  # type: ignore[arg-type]
        else:
            seq = list(lines)
        seq = [s for s in seq if s]
        if not seq:
            block.append("_Nothing notable in this bucket for the scanned window._")
        else:
            for item in seq:
                block.append(f"- {item}")
        block.append("")
        return block

    head.extend(section("# Wins", wins, as_insight=True))
    head.extend(section("# Issues", issues, as_insight=True))
    head.extend(section("# Insights", insights, as_insight=False))
    head.extend(section("# Action Items", actions, as_insight=False))
    return "\n".join(head).rstrip() + "\n"


def reflection_file_path(root: Path, day: datetime) -> Path:
    return root / LEARNINGS_DIR / AUTO_SUBDIR / f"{day.date().isoformat()}_reflection.md"


def write_latest_pointer(root: Path, rel_md: str) -> None:
    ptr = root / LEARNINGS_DIR / LATEST_NAME
    ptr.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "auto_reflection_md": rel_md,
        "generated_at_utc": utc_now().replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }
    ptr.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


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


def maybe_post_results(body: str, meta: dict[str, Any], *, dry_run: bool) -> list[str]:
    log: list[str] = []
    webhook = os.environ.get("REFLECTION_WEBHOOK_URL", "").strip()
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

    payload = {"text": body, "meta": meta}

    if dry_run:
        log.append("[dry-run] Skipping webhook and Telegram.")
        return log

    if webhook:
        ok, msg = post_webhook(webhook, payload)
        log.append(f"Webhook {'ok' if ok else 'FAILED'}: {msg[:400]}")

    if token and chat_id:
        ok, msg = post_telegram_summary(token, chat_id, body)
        log.append(f"Telegram {'ok' if ok else 'FAILED'}: {msg[:400]}")
    elif token or chat_id:
        log.append("Telegram skipped: need both TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.")

    if not webhook and not (token and chat_id):
        log.append("No REFLECTION_WEBHOOK_URL or full Telegram credentials; reflection only on disk.")

    return log


def run_reflection(
    root: Path,
    *,
    since_hours: float = 24 * 7,
    extra_globs: Sequence[str] | None = None,
    output_day: datetime | None = None,
    dry_run: bool = False,
) -> ReflectionRun:
    """Scan ``memory/``, classify signals, optionally write ``.learnings/auto/…``."""

    started = utc_now()
    window_end = started
    cutoff = started - timedelta(hours=since_hours)
    globs = collect_memory_globs(extra_globs or ())

    memory_paths = iter_recent_memory_files(root, globs, cutoff)
    insights: list[Insight] = []
    for mp in memory_paths:
        insights.extend(read_and_extract(mp, root))

    insights = dedupe_insights(insights)
    insights.sort(key=_insight_sort_key)

    wins = [i for i in insights if i.category == "win"]
    issues = [
        i
        for i in insights
        if i.category == "loss"
        or (i.severity in ("error", "warning") and FAILURE_HINTS.search(i.text))
    ]
    issue_fps = {insight_fingerprint(i.text) for i in issues}
    win_fps = {insight_fingerprint(i.text) for i in wins}
    lesson_insights = [i for i in insights if i.category == "lesson" and insight_fingerprint(i.text) not in issue_fps]

    day_for_file = output_day or started
    out_path = reflection_file_path(root, day_for_file)
    corpus_fps, corpus_lines = load_learning_corpus(root, exclude_paths={out_path})

    insight_lines: list[str] = []
    for ins in lesson_insights:
        t = normalize_insight_text(ins.text)
        if not t or is_near_duplicate_of_corpus(t, corpus_fps, corpus_lines):
            continue
        insight_lines.append(t)
        corpus_fps.add(insight_fingerprint(t))
        if len(corpus_lines) < MAX_CORPUS_LINES:
            corpus_lines.append(normalize_match_text(t))

    general_for_insights = [
        i
        for i in insights
        if i.category == "general"
        and insight_fingerprint(i.text) not in win_fps
        and insight_fingerprint(i.text) not in issue_fps
        and LESSON_HINTS.search(i.text)
    ]
    for ins in general_for_insights:
        t = normalize_insight_text(ins.text)
        if not t or is_near_duplicate_of_corpus(t, corpus_fps, corpus_lines):
            continue
        insight_lines.append(t)
        corpus_fps.add(insight_fingerprint(t))
        if len(corpus_lines) < MAX_CORPUS_LINES:
            corpus_lines.append(normalize_match_text(t))

    action_lines = build_action_candidates(issues, lesson_insights)
    filtered_actions: list[str] = []
    for line in action_lines:
        if is_near_duplicate_of_corpus(line, corpus_fps, corpus_lines):
            continue
        filtered_actions.append(line)
        corpus_fps.add(insight_fingerprint(line))
        if len(corpus_lines) < MAX_CORPUS_LINES:
            corpus_lines.append(normalize_match_text(line))

    rel_sources = [rel_under_root(p, root) for p in memory_paths]
    md = build_reflection_markdown(
        started,
        cutoff,
        window_end,
        rel_sources,
        wins,
        issues,
        insight_lines,
        filtered_actions,
    )

    finished = utc_now()
    run_id = started.strftime("%Y%m%d_%H%M%S")
    try:
        intended_rel = out_path.relative_to(root).as_posix()
    except ValueError:
        intended_rel = out_path.as_posix()

    run = ReflectionRun(
        run_id=run_id,
        started_at_utc=started.replace(microsecond=0).isoformat(),
        finished_at_utc=finished.replace(microsecond=0).isoformat(),
        files_scanned=len(memory_paths),
        memory_files=rel_sources,
        insights=insights,
        reflection_markdown=md,
        reflection_rel_path=intended_rel,
    )

    if dry_run:
        return run

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(md, encoding="utf-8")
    write_latest_pointer(root, intended_rel)
    return replace(run, reflection_rel_path=intended_rel)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a daily self-reflection markdown file from recent memory/ logs.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Workspace root (default: AUTO_REFLECTION_ROOT or cwd).",
    )
    parser.add_argument(
        "--days",
        type=float,
        default=float(os.environ.get("AUTO_REFLECTION_DAYS", "7")),
        help="Rolling look-back in days (default: 7, or AUTO_REFLECTION_DAYS).",
    )
    parser.add_argument(
        "--since-hours",
        type=float,
        default=None,
        help="Override window size in hours (takes precedence over --days when set).",
    )
    parser.add_argument(
        "--output-date",
        type=str,
        default=None,
        metavar="YYYY-MM-DD",
        help="UTC calendar date for the output filename (default: today).",
    )
    parser.add_argument(
        "--glob",
        action="append",
        default=[],
        metavar="PATTERN",
        help="Extra glob relative to root, restricted to paths under memory/ (repeatable).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not write files or POST; still prints side-channel logs.",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="Print the reflection markdown to stdout.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    repo = Path(__file__).resolve().parent.parent
    repo_s = str(repo)
    if repo_s not in sys.path:
        sys.path.insert(0, repo_s)

    args = build_parser().parse_args(argv)

    root = args.root or Path(os.environ.get("AUTO_REFLECTION_ROOT", "") or ".").resolve()
    since_hours = args.since_hours if args.since_hours is not None else max(args.days, 0.01) * 24.0

    output_day: datetime | None = None
    if args.output_date:
        try:
            y, m, d = (int(p) for p in args.output_date.split("-", 2))
            output_day = datetime(y, m, d, tzinfo=timezone.utc)
        except ValueError:
            print(f"Invalid --output-date {args.output_date!r}; use YYYY-MM-DD.", file=sys.stderr)
            return 2

    extra = [g for g in args.glob if g.strip().startswith("memory/")]
    if args.glob and not extra:
        print("Ignored --glob patterns outside memory/ for safety.", file=sys.stderr)

    if args.dry_run:
        print(f"[dry-run] root={root}", file=sys.stderr)

    run = run_reflection(
        root,
        since_hours=since_hours,
        extra_globs=extra,
        output_day=output_day,
        dry_run=args.dry_run,
    )

    meta = {
        "run_id": run.run_id,
        "started_at": run.started_at_utc,
        "files_scanned": run.files_scanned,
        "insight_count": len(run.insights),
        "reflection_path": run.reflection_rel_path,
    }
    for line in maybe_post_results(run.reflection_markdown, meta, dry_run=args.dry_run):
        print(line, file=sys.stderr)

    if args.stdout:
        print(run.reflection_markdown)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
