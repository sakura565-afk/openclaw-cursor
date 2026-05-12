#!/usr/bin/env python3
"""
Cron-friendly self-reflection over OpenClaw session history.

Aggregates recent transcripts (from the sessions_history HTTP API when configured,
otherwise from session log files), computes session statistics, derives wins and
issues, and writes ``.learnings/YYYY-MM-DD.md`` with Wins / Issues / Insights /
Next Steps.

Example crontab (daily at 09:00 UTC)::

    0 9 * * * cd /path/to/repo && /usr/bin/python3 -m scripts.auto_reflection --days 1

Environment (all optional unless posting):

- ``AUTO_REFLECTION_ROOT`` — workspace root (default: current working directory)
- ``OPENCLAW_SESSIONS_HISTORY_URL`` — full URL for GET sessions history (query ``days`` appended)
- ``OPENCLAW_API_BASE`` — base URL; history is fetched from ``{base}/sessions/history``
- ``OPENCLAW_API_TOKEN`` — optional ``Authorization: Bearer …`` for the history request
- ``OPENCLAW_WORKSPACE`` — when set, an additional directory scanned for session logs (same globs)
- ``AUTO_REFLECTION_SESSION_GLOBS`` — comma-separated extra glob patterns relative to each scan root
- ``TELEGRAM_BOT_TOKEN`` / ``TELEGRAM_CHAT_ID`` — post summary via Telegram sendMessage
- ``REFLECTION_WEBHOOK_URL`` — POST JSON ``{\"text\": \"...\", \"meta\": {...}}``
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Sequence

from scripts.conversation_extractor import (
    _fallback_json_segments,
    _unpack_session_json,
    parse_session_log,
)

LEARNINGS_DIR = ".learnings"
STATE_NAME = ".state.json"

DEFAULT_DAYS = 7
MAX_FILE_BYTES = 8 * 1024 * 1024
TELEGRAM_TEXT_LIMIT = 4000

DEFAULT_SESSION_GLOBS = (
    "logs/**/*.json",
    "logs/**/*.log",
    "**/sessions/**/session.json",
    "**/sessions/**/*.json",
)

STOPWORDS = frozenset(
    """
    a an the and or but if in on for to of at by as is was are were be been being
    it this that these those with from into about not no yes we you i they he she
    can could should would will just than then so very what which when where who how
    do does did done has have had having was were been being
    """.split()
)

FAILURE_LINE = re.compile(
    r"(?i)(\b(traceback|exception|error|failed|failure|timed?\s*out|exit\s*code\s*[1-9]\d*)\b|"
    r"\b(fatal|critical)\b|^error:|\[\s*error\s*\]|tool\s+(?:call\s+)?failed|execution\s+error)"
)

TOOL_FAILURE = re.compile(
    r"(?i)(\b(?:tool|function)\s+(?:result\s+)?(?:error|failed)\b|"
    r"\bis_error\b|\"error\"\s*:\s*|\"?status\"?\s*:\s*[\"']?(?:4\d\d|5\d\d))",
)

@dataclass
class SessionStats:
    """Aggregated metrics across one or more sessions."""

    sessions_count: int = 0
    total_messages: int = 0
    user_messages: int = 0
    assistant_messages: int = 0
    tool_result_messages: int = 0
    ambiguous_messages: int = 0
    tool_invocations: int = 0
    failed_tool_signals: int = 0
    error_like_hits: int = 0
    assistant_chars: int = 0
    tool_names: Counter[str] = field(default_factory=Counter)
    topic_tokens: Counter[str] = field(default_factory=Counter)
    error_snippets: list[str] = field(default_factory=list)
    context_warnings: list[str] = field(default_factory=list)
    source_labels: list[str] = field(default_factory=list)

    @property
    def avg_assistant_response_len(self) -> float:
        if self.assistant_messages <= 0:
            return 0.0
        return round(self.assistant_chars / self.assistant_messages, 1)

    def topics_covered(self, limit: int = 12) -> list[tuple[str, int]]:
        return self.topic_tokens.most_common(limit)


@dataclass
class ReflectionRun:
    """Serializable result of one reflection pass."""

    run_id: str
    started_at_utc: str
    finished_at_utc: str
    days_window: int
    sessions_count: int
    stats: SessionStats
    daily_markdown: str
    daily_relative_path: str
    sources_used: list[str] = field(default_factory=list)


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


def segments_from_json_data(data: Any) -> list[tuple[int, str | None, str]]:
    structured = _unpack_session_json(data)
    if structured:
        return structured
    return _fallback_json_segments(data)


def _tokenize_topics(text: str) -> Iterator[str]:
    for raw in re.findall(r"[A-Za-z][A-Za-z0-9_\-./]{2,}", text.lower()):
        if raw in STOPWORDS or len(raw) < 3:
            continue
        if raw.isdigit():
            continue
        yield raw


def _append_error_snippet(bucket: list[str], text: str, limit: int = 8) -> None:
    line = text.strip().replace("\n", " ")
    if len(line) > 220:
        line = line[:217] + "..."
    if not line or line in bucket:
        return
    if len(bucket) >= limit:
        return
    bucket.append(line)


def ingest_segments(stats: SessionStats, segments: list[tuple[int, str | None, str]], source: str) -> None:
    stats.sessions_count += 1
    stats.source_labels.append(source)

    for _turn, role, text in segments:
        if not text.strip():
            continue
        rl = (role or "").lower()

        if rl == "tool":
            stats.tool_invocations += 1
            name = text.strip().split("(", 1)[0].strip()
            if name:
                stats.tool_names[name] += 1
            continue

        stats.total_messages += 1

        if rl in {"user", "human"}:
            stats.user_messages += 1
            for tok in _tokenize_topics(text):
                stats.topic_tokens[tok] += 1
        elif rl in {"assistant", "agent"}:
            stats.assistant_messages += 1
            stats.assistant_chars += len(text)
        elif rl == "tool_output":
            stats.tool_result_messages += 1
        else:
            stats.ambiguous_messages += 1
            if role is None and len(text) > 80:
                stats.assistant_messages += 1
                stats.assistant_chars += len(text)

        if TOOL_FAILURE.search(text):
            stats.failed_tool_signals += 1
            _append_error_snippet(stats.error_snippets, f"[tool] {text}")

        if FAILURE_LINE.search(text):
            stats.error_like_hits += 1
            _append_error_snippet(stats.error_snippets, text)

        if re.search(r"(?i)\b(context|token)\s+(limit|overflow|exceeded|too\s+large)\b", text):
            snippet = text.strip().replace("\n", " ")
            if snippet not in stats.context_warnings:
                stats.context_warnings.append(snippet[:200])


def _sessions_history_url() -> str | None:
    direct = os.environ.get("OPENCLAW_SESSIONS_HISTORY_URL", "").strip()
    if direct:
        return direct
    base = os.environ.get("OPENCLAW_API_BASE", "").strip()
    if base:
        return base.rstrip("/") + "/sessions/history"
    return None


def fetch_sessions_via_history_api(
    days: int,
    *,
    timeout: float = 45.0,
) -> list[tuple[str, list[tuple[int, str | None, str]]]]:
    """GET OpenClaw sessions_history-style JSON and return (label, segments) pairs."""

    base_url = _sessions_history_url()
    if not base_url:
        return []

    parsed = urllib.parse.urlparse(base_url)
    q = urllib.parse.parse_qs(parsed.query)
    q.setdefault("days", [str(days)])
    new_query = urllib.parse.urlencode({k: v[0] for k, v in q.items()}, doseq=False)
    url = urllib.parse.urlunparse(parsed._replace(query=new_query))

    token = os.environ.get("OPENCLAW_API_TOKEN", "").strip()
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except (urllib.error.HTTPError, urllib.error.URLError, OSError):
        return []

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []

    out: list[tuple[str, list[tuple[int, str | None, str]]]] = []

    def add_session(label: str, blob: Any) -> None:
        if isinstance(blob, (str, bytes)):
            return
        if isinstance(blob, dict) and "messages" not in blob and "turns" not in blob:
            inner = (
                blob.get("transcript")
                or blob.get("session")
                or blob.get("payload")
                or blob.get("data")
            )
            if isinstance(inner, (dict, list)):
                blob = inner
        if isinstance(blob, dict):
            segs = segments_from_json_data(blob)
        elif isinstance(blob, list):
            segs = segments_from_json_data(blob)
        else:
            return
        if segs:
            out.append((label, segs))

    if isinstance(data, dict):
        sessions = (
            data.get("sessions")
            or data.get("history")
            or data.get("items")
            or data.get("data")
        )
        if isinstance(sessions, list):
            for i, item in enumerate(sessions):
                if isinstance(item, dict):
                    sid = str(item.get("id") or item.get("key") or item.get("path") or f"session_{i}")
                    payload = (
                        item.get("messages")
                        or item.get("transcript")
                        or item.get("session")
                        or item
                    )
                    add_session(f"api:{sid}", payload)
                else:
                    add_session(f"api:item_{i}", item)
        else:
            add_session("api:root", data)
    elif isinstance(data, list):
        for i, item in enumerate(data):
            add_session(f"api:list_{i}", item)

    return out


def collect_globs(extra: Sequence[str]) -> tuple[str, ...]:
    merged = list(DEFAULT_SESSION_GLOBS)
    env_extra = os.environ.get("AUTO_REFLECTION_SESSION_GLOBS", "")
    if env_extra.strip():
        merged.extend(p.strip() for p in env_extra.split(",") if p.strip())
    merged.extend(extra)
    return tuple(dict.fromkeys(merged))


def _scan_roots(workspace_root: Path) -> list[Path]:
    roots = [workspace_root.resolve()]
    oc = os.environ.get("OPENCLAW_WORKSPACE", "").strip()
    if not oc:
        return roots
    try:
        rp = Path(oc).expanduser().resolve()
        if rp.is_dir():
            if not any(r == rp for r in roots):
                roots.append(rp)
    except OSError:
        pass
    return roots


def iter_session_files(
    roots: Sequence[Path],
    globs: Sequence[str],
    cutoff: datetime,
) -> list[Path]:
    seen: set[Path] = set()
    out: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
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


def load_file_segments(path: Path) -> list[tuple[int, str | None, str]]:
    return parse_session_log(path.resolve())


def build_daily_markdown(
    run_at: datetime,
    stats: SessionStats,
    *,
    api_used: bool,
    days_window: int,
) -> str:
    """Emit the required section headers: Wins, Issues, Insights, Next Steps."""

    headline = f"# OpenClaw self-reflection — {run_at.date().isoformat()} (UTC)"
    summary_bits = [
        f"- Sessions analyzed: **{stats.sessions_count}**",
        (
            f"- Total messages (excl. tool-name rows): **{stats.total_messages}** "
            f"(user {stats.user_messages}, assistant {stats.assistant_messages}, "
            f"tool results {stats.tool_result_messages}, other {stats.ambiguous_messages})"
        ),
        f"- Tool invocations recorded: **{stats.tool_invocations}**",
        f"- Error-like signals: **{stats.error_like_hits}**; tool-failure hints: **{stats.failed_tool_signals}**",
        f"- Average assistant response length: **{stats.avg_assistant_response_len}** characters",
    ]
    topics = stats.topics_covered(10)
    if topics:
        topic_str = ", ".join(f"{w} ({n})" for w, n in topics)
        summary_bits.append(f"- Topics (heuristic from user text): {topic_str}")
    else:
        summary_bits.append("- Topics (heuristic from user text): *(none extracted)*")

    if api_used:
        summary_bits.append("- Data sources: **sessions_history API** plus any local files in range")
    else:
        summary_bits.append("- Data sources: **session log files** (no API URL configured or API empty)")

    wins: list[str] = []
    if stats.tool_invocations:
        top_tools = ", ".join(f"`{n}`×{c}" for n, c in stats.tool_names.most_common(5))
        wins.append(f"Recorded **{stats.tool_invocations}** structured tool uses; most common: {top_tools}.")
    if stats.sessions_count and stats.error_like_hits == 0:
        wins.append("No strong error/traceback patterns surfaced in the scanned window.")
    elif stats.total_messages and stats.error_like_hits < max(3, stats.total_messages // 25):
        wins.append("Relatively low density of error-like lines versus overall assistant/user traffic.")
    if stats.assistant_messages and stats.avg_assistant_response_len < 3500:
        wins.append("Assistant replies stayed within a moderate size on average (helps context hygiene).")
    if not wins:
        wins.append("*(No standout automated wins — window may be quiet or logs lack structure.)*")

    issues: list[str] = []
    for snip in stats.error_snippets[:6]:
        issues.append(snip)
    if stats.failed_tool_signals:
        issues.append(
            f"**{stats.failed_tool_signals}** segments mention tool/function failures or HTTP error shapes."
        )
    for cw in stats.context_warnings[:4]:
        issues.append(f"Possible context pressure: {cw}")
    if stats.avg_assistant_response_len > 6000:
        issues.append(
            f"Very long average assistant responses ({stats.avg_assistant_response_len} chars) — consider summarizing mid-task."
        )
    if not issues:
        issues.append("*(No major automated issue signals in this window.)*")

    insights: list[str] = []
    insights.append(
        f"Aggregate load: **{stats.total_messages}** messages across **{stats.sessions_count}** sessions "
        f"in the last **{days_window}** day(s)."
    )
    if stats.tool_names:
        insights.append(
            "Tooling focus skews toward: "
            + ", ".join(f"`{n}`" for n, _ in stats.tool_names.most_common(8))
            + "."
        )
    if stats.topic_tokens:
        insights.append(
            "User language clusters around: "
            + ", ".join(w for w, _ in stats.topics_covered(8))
            + " — align automation/docs with those themes."
        )
    if stats.error_like_hits:
        insights.append(
            f"**{stats.error_like_hits}** error-like hits warrant spot-checking the underlying turns "
            "(search logs for matching timestamps)."
        )

    next_steps: list[str] = []
    if stats.error_snippets:
        next_steps.append("Triage listed errors: fix the top root cause, then re-run this reflection to confirm counts drop.")
    if stats.failed_tool_signals:
        next_steps.append("Inspect tool schemas and permissions for the failing tools; add retries only after fixing root errors.")
    if stats.context_warnings or stats.avg_assistant_response_len > 4500:
        next_steps.append("Schedule a context pass (trim attachments, summarize long threads) before the next long session.")
    if stats.tool_invocations == 0 and stats.sessions_count:
        next_steps.append("If tools should have run, verify session exports include structured tool blocks (not only plain text).")
    next_steps.append(
        f"Keep ``python -m scripts.auto_reflection --days {days_window}`` on a cron to accumulate "
        f"dated learnings under ``{LEARNINGS_DIR}/``."
    )

    sections = [
        headline,
        "",
        *summary_bits,
        "",
        "## Wins",
        *(f"- {w}" for w in wins),
        "",
        "## Issues",
        *(f"- {issue}" for issue in issues),
        "",
        "## Insights",
        *(f"- {ins}" for ins in insights),
        "",
        "## Next Steps",
        *(f"- {ns}" for ns in next_steps),
        "",
    ]
    return "\n".join(sections).rstrip() + "\n"


def write_daily_file(root: Path, run_at: datetime, body: str) -> Path:
    learnings = root / LEARNINGS_DIR
    learnings.mkdir(parents=True, exist_ok=True)
    path = learnings / f"{run_at.date().isoformat()}.md"
    path.write_text(body, encoding="utf-8")
    return path


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


def run_reflection(
    root: Path,
    *,
    days: int = DEFAULT_DAYS,
    extra_globs: Sequence[str] | None = None,
    dry_run: bool = False,
) -> ReflectionRun:
    started = utc_now()
    cutoff = started - timedelta(days=max(days, 1))
    globs = collect_globs(extra_globs or ())

    stats = SessionStats()
    sources: list[str] = []

    api_pairs = fetch_sessions_via_history_api(days)
    api_used = bool(api_pairs)
    for label, segs in api_pairs:
        ingest_segments(stats, segs, label)
        sources.append(label)

    scan_roots = _scan_roots(root)
    for sf in iter_session_files(scan_roots, globs, cutoff):
        try:
            rel = sf.relative_to(root.resolve()).as_posix()
        except ValueError:
            rel = sf.as_posix()
        segs = load_file_segments(sf)
        if not segs:
            continue
        ingest_segments(stats, segs, rel)
        sources.append(rel)

    daily_md = build_daily_markdown(started, stats, api_used=api_used, days_window=days)
    rel_daily = f"{LEARNINGS_DIR}/{started.date().isoformat()}.md"
    finished = utc_now()
    run_id = started.strftime("%Y%m%d_%H%M%S")

    run = ReflectionRun(
        run_id=run_id,
        started_at_utc=started.replace(microsecond=0).isoformat(),
        finished_at_utc=finished.replace(microsecond=0).isoformat(),
        days_window=days,
        sessions_count=stats.sessions_count,
        stats=stats,
        daily_markdown=daily_md,
        daily_relative_path=rel_daily,
        sources_used=sources,
    )

    if dry_run:
        return run

    write_daily_file(root, started, daily_md)

    state = load_state(root)
    state["last_run_utc"] = finished.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    state["last_run_id"] = run_id
    state["last_days"] = days
    state["sessions_count"] = stats.sessions_count
    state["sources_count"] = len(sources)
    save_state(root, state)

    try:
        from src.coordination.iskra_kara_shared_memory import notify_kara_from_iskra

        notify_kara_from_iskra(
            "reflection",
            {
                "daily_markdown": run.daily_markdown,
                "run_id": run.run_id,
                "sessions_count": run.sessions_count,
                "daily_path": run.daily_relative_path,
            },
        )
    except Exception:
        pass

    return run


def maybe_post_results(run: ReflectionRun, *, dry_run: bool) -> list[str]:
    log: list[str] = []
    text = run.daily_markdown
    webhook = os.environ.get("REFLECTION_WEBHOOK_URL", "").strip()
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

    payload = {
        "text": text,
        "meta": {
            "run_id": run.run_id,
            "started_at": run.started_at_utc,
            "sessions_count": run.sessions_count,
            "days": run.days_window,
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
        description="Analyze OpenClaw session history and write .learnings/YYYY-MM-DD.md.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Workspace root (default: AUTO_REFLECTION_ROOT or cwd).",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=DEFAULT_DAYS,
        help=f"Analyze sessions touched in the last N days (default: {DEFAULT_DAYS}).",
    )
    parser.add_argument(
        "--since-hours",
        type=float,
        default=None,
        help="Deprecated: converted to days = ceil(hours/24) when --days is left default-only.",
    )
    parser.add_argument(
        "--glob",
        action="append",
        default=[],
        metavar="PATTERN",
        help="Additional glob relative to each scan root (repeatable).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not write files or POST; print the markdown report to stdout.",
    )
    parser.add_argument(
        "--stdout-summary",
        action="store_true",
        help="Print the markdown report to stdout (in addition to normal writes unless --dry-run).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    root = args.root or Path(os.environ.get("AUTO_REFLECTION_ROOT", "") or ".").resolve()

    days = max(1, int(args.days))
    if args.since_hours is not None and args.days == DEFAULT_DAYS:
        days = max(1, int(math.ceil(float(args.since_hours) / 24.0)))

    if args.dry_run:
        print(f"[dry-run] root={root} days={days}", file=sys.stderr)

    run = run_reflection(
        root,
        days=days,
        extra_globs=args.glob,
        dry_run=args.dry_run,
    )

    for line in maybe_post_results(run, dry_run=args.dry_run):
        print(line, file=sys.stderr)

    if args.dry_run or args.stdout_summary:
        print(run.daily_markdown)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
