#!/usr/bin/env python3
"""
Self-reflection cron helper for OpenClaw.

Uses the same session entry points as the agent tools ``sessions_list`` and
``sessions_history``: this script shells out to the OpenClaw CLI
(``openclaw sessions …`` / ``openclaw sessions history …``), which implements
those tool surfaces.

Typical crontab (daily 09:00 UTC, reflect on the last 24 hours):

    0 9 * * * cd /path/to/repo && python3 scripts/auto_reflection.py --hours 24

Environment:

- ``OPENCLAW_BIN`` — how to invoke the CLI (default: ``openclaw``). Use
  ``npx openclaw`` or a full path if the binary is not on ``PATH``.
- ``OPENCLAW_WORKSPACE`` — workspace root containing ``memory/`` (falls back to
  ``~/.openclaw/workspace`` via ``resolve_openclaw_workspace`` when available).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.conversation_extractor import (  # noqa: E402
    DECISION_LINE_PATTERNS,
    LEARNING_LINE_PATTERNS,
    analyze_segments,
    match_patterns,
)
from scripts.conversation_extractor import _unpack_session_json  # noqa: E402

ERROR_HINT = re.compile(
    r"(?i)(traceback|exception|error:|failed:|failure|\bHTTP\s*5\d\d\b|"
    r"exit\s*code\s*[1-9]\d*|pairing\s*required|ECONNREFUSED)"
)
TASK_DONE = re.compile(
    r"(?i)(\[[xX]\]\s*.+|✓|completed\s+successfully|task\s+completed|"
    r"merged\b|shipped\b|implemented\b|resolved\b\s+(?:the\s+)?(?:bug|issue))"
)


class OpenClawCliError(RuntimeError):
    """The OpenClaw CLI returned a non-zero exit code or invalid JSON."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def resolve_workspace(explicit: Path | None) -> Path:
    if explicit is not None:
        return explicit.expanduser().resolve()
    env = os.environ.get("OPENCLAW_WORKSPACE", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    try:
        from src.coordination.iskra_kara_shared_memory import resolve_openclaw_workspace

        return resolve_openclaw_workspace()
    except Exception:
        return Path.cwd().resolve()


def openclaw_argv(*tail: str) -> list[str]:
    raw = os.environ.get("OPENCLAW_BIN", "openclaw").strip() or "openclaw"
    return shlex.split(raw) + list(tail)


def _run_openclaw_json(argv: list[str], *, timeout: float = 180.0) -> Any:
    cmd = openclaw_argv(*argv)
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    out = (proc.stdout or "").strip()
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[:2000]
        raise OpenClawCliError(f"openclaw failed ({proc.returncode}): {detail}")
    if not out:
        return {}
    try:
        return json.loads(out)
    except json.JSONDecodeError as exc:
        raise OpenClawCliError(f"invalid JSON from openclaw: {exc}") from exc


def sessions_list(
    *,
    active_minutes: int | None,
    limit: int,
    all_agents: bool = True,
) -> dict[str, Any]:
    """Mirror of the ``sessions_list`` tool via ``openclaw sessions --json``."""

    args: list[str] = ["sessions", "--json", "--limit", str(limit)]
    if all_agents:
        args.append("--all-agents")
    if active_minutes is not None:
        args.extend(["--active", str(active_minutes)])
    data = _run_openclaw_json(args)
    if isinstance(data, dict):
        return data
    return {"sessions": data if isinstance(data, list) else []}


def sessions_history(
    session_key: str,
    *,
    last_messages: int | None = None,
    include_tools: bool = True,
) -> Any:
    """Mirror of the ``sessions_history`` tool via ``openclaw sessions history``."""

    _ = include_tools  # Reserved for CLI parity with the tool (flags vary by OpenClaw version).

    variants: list[list[str]] = []
    base = ["sessions", "history", session_key, "--json"]
    if last_messages is not None:
        base = ["sessions", "history", session_key, "--json", "--last", str(last_messages)]
    variants.append(base)
    variants.append(
        ["sessions", "history", "--session-key", session_key, "--json"]
        + (["--last", str(last_messages)] if last_messages is not None else [])
    )

    last_err: OpenClawCliError | None = None
    for spec in variants:
        try:
            return _run_openclaw_json(spec)
        except OpenClawCliError as exc:
            last_err = exc
            continue
    if last_err:
        raise last_err
    raise OpenClawCliError("sessions_history: no variants attempted")


def _session_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("sessions")
    if isinstance(rows, list):
        return [r for r in rows if isinstance(r, dict)]
    return []


def _session_key(row: dict[str, Any]) -> str | None:
    for k in ("key", "sessionKey", "session_key", "id"):
        v = row.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _session_updated_at(row: dict[str, Any]) -> datetime | None:
    for k in ("updatedAt", "lastUpdated", "lastMessageAt", "modifiedAt", "ts"):
        ts = _parse_ts(row.get(k))
        if ts is not None:
            return ts
    return None


def filter_sessions_since(rows: Iterable[dict[str, Any]], since: datetime) -> list[dict[str, Any]]:
    """Keep rows whose metadata is at or after ``since`` (rows without timestamps are kept)."""

    out: list[dict[str, Any]] = []
    for row in rows:
        ts = _session_updated_at(row)
        if ts is None or ts >= since:
            out.append(row)
    return out


@dataclass
class ReflectionExtract:
    errors: list[str]
    wins: list[str]
    decisions: list[str]
    learnings: list[str]
    tools: Counter[str]

    def merge(self, other: ReflectionExtract) -> None:
        self.errors.extend(other.errors)
        self.wins.extend(other.wins)
        self.decisions.extend(other.decisions)
        self.learnings.extend(other.learnings)
        self.tools.update(other.tools)


def _uniq_cap(xs: Iterable[str], cap: int) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in xs:
        t = x.strip()
        if len(t) < 4 or t in seen:
            continue
        seen.add(t)
        out.append(t)
        if len(out) >= cap:
            break
    return out


def extract_from_segments(
    segments: list[tuple[int, str | None, str]],
    *,
    source_tag: str,
) -> ReflectionExtract:
    digest = analyze_segments(segments, source_tag)
    errors: list[str] = []
    wins: list[str] = []
    blobs: list[str] = []

    for _turn, role, text in segments:
        rl = (role or "").lower()
        if rl in {"tool", "tool_output"}:
            blobs.append(text)
            continue
        blobs.append(text)
        for line in text.splitlines():
            s = line.strip()
            if len(s) < 8:
                continue
            if ERROR_HINT.search(s):
                errors.append(f"[{source_tag}] {s[:400]}")
            if TASK_DONE.search(s):
                wins.append(f"[{source_tag}] {s[:400]}")

    decisions = list(digest.decisions)
    learnings = list(digest.learnings)
    combined = "\n".join(blobs)
    decisions.extend(match_patterns(combined, DECISION_LINE_PATTERNS))
    learnings.extend(match_patterns(combined, LEARNING_LINE_PATTERNS))

    for item in digest.decisions:
        if re.search(r"(?i)(ship|fix|complete|resolve|done|landed)", item):
            wins.append(item)

    tools = digest.all_tools()
    return ReflectionExtract(
        errors=_uniq_cap(errors, 80),
        wins=_uniq_cap(wins, 80),
        decisions=_uniq_cap(decisions, 80),
        learnings=_uniq_cap(learnings, 120),
        tools=tools,
    )


def history_to_segments(history_blob: Any) -> list[tuple[int, str | None, str]]:
    if isinstance(history_blob, (dict, list)):
        return _unpack_session_json(history_blob)
    return []


def render_reflection_markdown(
    *,
    window_start: datetime,
    window_end: datetime,
    session_keys: list[str],
    agg: ReflectionExtract,
    cron_note: str | None,
) -> str:
    iso_start = window_start.strftime("%Y-%m-%d %H:%M UTC")
    iso_end = window_end.strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        f"# OpenClaw reflection — {window_end.date().isoformat()}",
        "",
        f"- **Window**: {iso_start} → {iso_end}",
        f"- **Sessions reviewed**: {len(session_keys)}",
    ]
    if cron_note:
        lines.append(f"- **Schedule note (cron)**: `{cron_note}`")
    lines.extend(["", "## Summary", ""])

    if not session_keys:
        lines.append("_No sessions in the selected window._")
    else:
        lines.append(
            f"Across **{len(session_keys)}** conversation session(s), transcripts were scanned for "
            "errors, completed work, explicit decisions, and learning-style statements. "
            "Highlights are grouped below."
        )
        preview = ", ".join(f"`{k}`" for k in session_keys[:12])
        if preview:
            lines.append("")
            lines.append(f"**Session keys:** {preview}" + (" …" if len(session_keys) > 12 else ""))

    def section(title: str, items: list[str], empty: str) -> None:
        lines.extend(["", f"## {title}", ""])
        if items:
            for it in items:
                lines.append(f"- {it}")
        else:
            lines.append(empty)

    section("Wins", agg.wins, "_No clear wins detected in phrasing or structured cues._")
    section("Challenges", agg.errors, "_No obvious error signals in the sampled transcript text._")
    section(
        "Learnings",
        _uniq_cap(agg.learnings + agg.decisions, 120),
        "_No explicit learnings or decisions parsed from transcripts._",
    )

    lines.extend(["", "## Next Steps", ""])
    next_steps: list[str] = []
    if agg.errors:
        next_steps.append("Revisit sessions with errors and confirm whether follow-up fixes landed.")
    if agg.tools:
        top = ", ".join(f"`{n}`" for n, _ in agg.tools.most_common(8))
        next_steps.append(f"Review heavy tool usage for automation or caching opportunities: {top}.")
    if not next_steps:
        next_steps.append("Keep brief end-of-task notes in assistant messages to improve future reflections.")
    for ns in next_steps[:12]:
        lines.append(f"- {ns}")

    lines.extend(
        [
            "",
            "---",
            "*Generated by `scripts/auto_reflection.py` using OpenClaw "
            "`sessions_list` / `sessions_history` (CLI: `openclaw sessions`).*",
            "",
        ]
    )
    return "\n".join(lines)


def default_output_path(memory_dir: Path, day: datetime) -> Path:
    return memory_dir / f"{day.date().isoformat()}-reflection.md"


def resolve_output_path(
    output: str | None,
    *,
    memory_dir: Path,
    day: datetime,
) -> Path:
    if not output:
        return default_output_path(memory_dir, day)
    p = Path(output).expanduser()
    if p.suffix.lower() == ".md":
        return p.resolve()
    return (p / f"{day.date().isoformat()}-reflection.md").resolve()


def validate_cron_expression(expr: str) -> bool:
    expr = expr.strip()
    if not expr:
        return False
    # Five- or six-field cron (seconds optional); accept standard five-field.
    parts = expr.split()
    return 5 <= len(parts) <= 6


def suggest_crontab_line(cron: str, hours: float) -> str:
    repo = PROJECT_ROOT
    return (
        f"{cron.strip()} cd {repo} && "
        f"{sys.executable} scripts/auto_reflection.py --hours {hours:g}"
    )


def run_daemon(cron_expr: str, fn: Callable[[], int]) -> int:
    try:
        from croniter import croniter  # type: ignore[import-untyped]
    except ImportError:
        print(
            "error: --daemon requires the `croniter` package (`pip install croniter`).",
            file=sys.stderr,
        )
        return 2
    while True:
        base = utc_now()
        nxt = croniter(cron_expr, base).get_next(datetime)
        if nxt.tzinfo is None:
            nxt = nxt.replace(tzinfo=timezone.utc)
        delay = (nxt - utc_now()).total_seconds()
        if delay > 0:
            time.sleep(delay)
        rc = fn()
        if rc != 0:
            return rc


def run_once(
    *,
    hours: float,
    output: str | None,
    workspace: Path | None,
    all_agents: bool,
    list_limit: int,
    history_last: int | None,
    max_sessions: int,
    dry_run: bool,
    cron_note: str | None,
) -> tuple[int, Path | None]:
    ws = resolve_workspace(workspace)
    memory_dir = ws / "memory"
    now = utc_now()
    since = now - timedelta(hours=hours)
    active_minutes = max(1, int(hours * 60))

    try:
        listed = sessions_list(
            active_minutes=active_minutes,
            limit=list_limit,
            all_agents=all_agents,
        )
    except OpenClawCliError as exc:
        print(f"error: sessions_list failed: {exc}", file=sys.stderr)
        return 1, None

    rows = filter_sessions_since(_session_rows(listed), since)
    keys: list[str] = []
    for row in rows:
        sk = _session_key(row)
        if sk:
            keys.append(sk)
    keys = keys[:max_sessions]

    if not keys:
        print(
            f"No sessions found in the last {hours:g} hour(s); skipping reflection file.",
            file=sys.stderr,
        )
        return 0, None

    agg = ReflectionExtract([], [], [], [], Counter())
    for sk in keys:
        try:
            hist = sessions_history(sk, last_messages=history_last, include_tools=True)
        except OpenClawCliError as exc:
            agg.errors.append(f"[{sk}] sessions_history failed: {exc}")
            continue
        segs = history_to_segments(hist)
        tagged = [(t, r, f"{txt}") for t, r, txt in segs]
        part = extract_from_segments(tagged, source_tag=sk)
        agg.merge(part)

    md = render_reflection_markdown(
        window_start=since,
        window_end=now,
        session_keys=keys,
        agg=agg,
        cron_note=cron_note,
    )
    out_path = resolve_output_path(output, memory_dir=memory_dir, day=now)
    if dry_run:
        print(md)
        print(f"[dry-run] would write: {out_path}", file=sys.stderr)
        return 0, out_path

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(md, encoding="utf-8")
    print(f"Wrote {out_path}", file=sys.stderr)
    return 0, out_path


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Build a markdown reflection from recent OpenClaw sessions (CLI tools).",
    )
    p.add_argument(
        "--hours",
        type=float,
        default=24.0,
        help="Look-back window in hours (default: 24). Passed to sessions --active as minutes.",
    )
    p.add_argument(
        "--output",
        default=None,
        help="Output file (.md) or directory (default: memory/YYYY-MM-DD-reflection.md under workspace).",
    )
    p.add_argument(
        "--workspace",
        type=Path,
        default=None,
        help="OpenClaw workspace root (default: OPENCLAW_WORKSPACE or ~/.openclaw/workspace).",
    )
    p.add_argument(
        "--cron",
        default=None,
        help="Cron schedule string for documentation, crontab hint, and optional daemon loop.",
    )
    p.add_argument(
        "--daemon",
        action="store_true",
        help="Run forever on the schedule given by --cron (requires croniter).",
    )
    p.add_argument(
        "--no-all-agents",
        action="store_true",
        help="Omit --all-agents from sessions_list (default agent store only).",
    )
    p.add_argument(
        "--session-limit",
        type=int,
        default=200,
        help="Max rows for sessions_list --limit (default: 200).",
    )
    p.add_argument(
        "--history-last",
        type=int,
        default=120,
        help="Approximate max messages per session (--last for sessions history).",
    )
    p.add_argument(
        "--max-sessions",
        type=int,
        default=30,
        help="Cap how many sessions receive a history fetch (default: 30).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print markdown to stdout; do not write a file.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    if args.cron and not validate_cron_expression(args.cron):
        print("error: --cron must be a 5- or 6-field cron expression.", file=sys.stderr)
        return 2

    if args.daemon and not args.cron:
        print("error: --daemon requires --cron.", file=sys.stderr)
        return 2

    if args.cron and not args.daemon:
        print(f"Crontab suggestion:\n{suggest_crontab_line(args.cron, args.hours)}", file=sys.stderr)

    def cycle() -> int:
        rc, _ = run_once(
            hours=args.hours,
            output=args.output,
            workspace=args.workspace,
            all_agents=not args.no_all_agents,
            list_limit=args.session_limit,
            history_last=args.history_last,
            max_sessions=args.max_sessions,
            dry_run=args.dry_run,
            cron_note=args.cron,
        )
        return rc

    if args.daemon:
        return run_daemon(args.cron, cycle)
    return cycle()


if __name__ == "__main__":
    raise SystemExit(main())
