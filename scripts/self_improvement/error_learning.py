#!/usr/bin/env python3
"""
Capture agent-run failures, cluster them into patterns, suggest fixes, and audit
what was applied.

Stores append-only JSON lines under ``.learnings/captured_errors/`` (same root
convention as ``auto_reflection``). Decorate long-running agent entrypoints::

    from scripts.self_improvement.error_learning import capture_errors

    @capture_errors(tag="task_runner")
    def run_agent_task(*args, **kwargs):
        ...

Cron-friendly review::

    python -m scripts.self_improvement.error_learning --root /path/to/repo review
    python -m scripts.self_improvement.error_learning patterns --min-count 2

Environment (optional):

- ``ERROR_LEARNING_ROOT`` — workspace root (default: cwd); overridden by ``--root``.
- ``ERROR_LEARNING_RUN_ID`` — logical run id included on every captured event.
"""

from __future__ import annotations

import argparse
import asyncio
import functools
import hashlib
import inspect
import json
import os
import sys
import traceback
import uuid
from collections import defaultdict
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ParamSpec, TypeVar

from scripts.error_learning import error_signature

LEARNINGS_DIR = ".learnings"
CAPTURE_SUBDIR = "captured_errors"
EVENTS_NAME = "events.jsonl"
APPLIED_NAME = "applied.jsonl"
STATE_NAME = "state.json"

MAX_TB_LINES = 80
MAX_MSG_CHARS = 2_000
MAX_CONTEXT_JSON = 24_000

P = ParamSpec("P")
R = TypeVar("R")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def workspace_root(cli_root: Path | None) -> Path:
    env = os.environ.get("ERROR_LEARNING_ROOT", "").strip()
    if cli_root is not None:
        return cli_root.resolve()
    if env:
        return Path(env).resolve()
    return Path.cwd().resolve()


def default_run_id() -> str:
    return os.environ.get("ERROR_LEARNING_RUN_ID", "").strip() or utc_now().strftime("%Y%m%d_%H%M%S")


def _capture_dir(root: Path) -> Path:
    return root / LEARNINGS_DIR / CAPTURE_SUBDIR


def events_path(root: Path) -> Path:
    return _capture_dir(root) / EVENTS_NAME


def applied_path(root: Path) -> Path:
    return _capture_dir(root) / APPLIED_NAME


def state_path(root: Path) -> Path:
    return _capture_dir(root) / STATE_NAME


def pattern_id(exc_type: str, message: str) -> str:
    blob = f"{exc_type.strip()}|{error_signature(message)}"
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _tail_traceback(tb: str) -> str:
    lines = tb.strip().splitlines()
    if len(lines) <= MAX_TB_LINES:
        return tb.strip()
    head = lines[: max(5, MAX_TB_LINES // 4)]
    tail = lines[-(MAX_TB_LINES - len(head) - 2) :]
    return "\n".join(head + ["  …", "…"] + tail)


def suggest_fixes(exc_type: str, message: str) -> list[str]:
    """Heuristic remediation lines (same spirit as triage buckets in ``scripts.error_learning``)."""

    t = f"{exc_type} {message}".lower()
    out: list[str] = []

    def add(s: str) -> None:
        if s not in out:
            out.append(s)

    if "filenotfounderror" in t or "no such file" in t:
        add("Confirm the path exists from the process cwd; prefer Path.resolve() and explicit roots.")
    if "permissionerror" in t or "permission denied" in t:
        add("Check file mode, ownership, and sandbox write boundaries before retrying.")
    if "modulenotfounderror" in t or "no module named" in t:
        add("Verify the active venv/PYTHONPATH and package name; reinstall deps if imports moved.")
    if "importerror" in t or "import error" in t:
        add("Resolve circular imports or optional native wheels; pin versions if ABI mismatched.")
    if "keyerror" in t:
        add("Guard with dict.get, validate schema keys, or narrow the key source.")
    if "typeerror" in t or "attributeerror" in t:
        add("Re-check types at the callsite; add a small repro test around the failing contract.")
    if "jsondecodeerror" in t or "invalid json" in t or "yaml" in t and "error" in t:
        add("Validate payload shape; use strict parsing and surface the first bad offset.")
    if "timeout" in t or "timed out" in t:
        add("Increase deadline where appropriate; add retries with backoff for flaky I/O.")
    if "connection" in t or "econnrefused" in t or "network" in t:
        add("Check host/port, TLS, proxies, and firewall; confirm the remote service is up.")
    if "subprocess" in t or "calledprocesserror" in t or "exit status" in t:
        add("Log stdout/stderr from the child; reproduce the shell command outside the agent.")
    if "rate limit" in t or "429" in t:
        add("Throttle requests; honor Retry-After and cache idempotent reads.")
    if "401" in t or "403" in t or "unauthorized" in t or "forbidden" in t:
        add("Rotate or scope credentials; confirm clock skew and token expiry.")
    if "memory" in t or "cannot allocate" in t:
        add("Reduce batch size or streaming; profile peak RSS before widening limits.")
    if not out:
        add("Capture a minimal repro, tighten logging around inputs, and bisect recent changes.")
    return out


def _serialize_context(ctx: Any) -> dict[str, Any]:
    if ctx is None:
        return {}
    if isinstance(ctx, Mapping):
        return dict(ctx)
    raise TypeError("context must be a mapping or callable returning a mapping")


def resolve_context(
    binding: Mapping[str, Any] | Callable[..., Mapping[str, Any]] | None,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> dict[str, Any]:
    if binding is None:
        return {}
    if isinstance(binding, Mapping):
        return _serialize_context(binding)
    sig = inspect.signature(binding)
    params = list(sig.parameters)
    if not params:
        return _serialize_context(binding())
    if len(params) == 1 and params[0] in ("exc", "error", "exception"):
        return _serialize_context(binding(args[0] if args else None))  # type: ignore[misc]
    if len(params) == 2 and params[0] == "args" and params[1] == "kwargs":
        return _serialize_context(binding(args, kwargs))  # type: ignore[misc]
    try:
        return _serialize_context(binding(*args, **kwargs))  # type: ignore[misc]
    except TypeError:
        return _serialize_context(binding())  # type: ignore[misc]


@dataclass
class ErrorEvent:
    """One captured failure from a decorated call."""

    event_id: str
    captured_at_utc: str
    run_id: str
    tag: str | None
    qualname: str
    exc_type: str
    exc_message: str
    traceback_tail: str
    pattern_id: str
    context: dict[str, Any] = field(default_factory=dict)
    suggested_fixes: list[str] = field(default_factory=list)

    def to_json_line(self) -> str:
        payload = asdict(self)
        raw = json.dumps(payload, ensure_ascii=False)
        if len(raw) > MAX_CONTEXT_JSON:
            payload["context"] = {"_truncated": True, "_original_keys": list(self.context.keys())}
            payload["exc_message"] = self.exc_message[:800] + "…"
            payload["traceback_tail"] = self.traceback_tail[:1200] + "…"
            raw = json.dumps(payload, ensure_ascii=False)
        return raw + "\n"


def log_failure(
    exc: BaseException,
    *,
    root: Path,
    run_id: str,
    tag: str | None,
    qualname: str,
    context: Mapping[str, Any],
) -> ErrorEvent:
    exc_type = type(exc).__qualname__
    msg = str(exc).strip() or "(no message)"
    if len(msg) > MAX_MSG_CHARS:
        msg = msg[: MAX_MSG_CHARS - 1] + "…"
    tb = traceback.format_exc()
    pid = pattern_id(exc_type, msg)
    fixes = suggest_fixes(exc_type, msg)
    event = ErrorEvent(
        event_id=uuid.uuid4().hex,
        captured_at_utc=utc_now().replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        run_id=run_id,
        tag=tag,
        qualname=qualname,
        exc_type=exc_type,
        exc_message=msg,
        traceback_tail=_tail_traceback(tb),
        pattern_id=pid,
        context=dict(context),
        suggested_fixes=fixes,
    )
    path = events_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(event.to_json_line())
    _bump_state(root, event.pattern_id)
    return event


def _bump_state(root: Path, pid: str) -> None:
    sp = state_path(root)
    data: dict[str, Any] = {}
    if sp.exists():
        try:
            data = json.loads(sp.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = {}
    counts = data.get("pattern_hits")
    if not isinstance(counts, dict):
        counts = {}
    counts[pid] = int(counts.get(pid, 0)) + 1
    data["pattern_hits"] = counts
    data["last_event_at_utc"] = utc_now().replace(microsecond=0).isoformat().replace("+00:00", "Z")
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def capture_errors(
    _func: Callable[P, R] | None = None,
    *,
    tag: str | None = None,
    run_id: str | None = None,
    context: Mapping[str, Any] | Callable[..., Mapping[str, Any]] | None = None,
    root: Path | None = None,
    reraise: bool = True,
) -> Callable[[Callable[P, R]], Callable[P, R]] | Callable[P, R]:
    """
    Decorator: log exceptions with context to ``.learnings/captured_errors/events.jsonl``.

    ``context`` may be a static mapping or a callable. If callable, it is tried as
    ``fn(*args, **kwargs)``; on ``TypeError``, ``fn()`` is used.
    """

    def decorator(fn: Callable[P, R]) -> Callable[P, R]:
        rid_factory = lambda: run_id or default_run_id()
        root_path = workspace_root(root)

        @functools.wraps(fn)
        def sync_wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            rid = rid_factory()
            try:
                return fn(*args, **kwargs)
            except BaseException as exc:
                ctx = resolve_context(context, args, kwargs)
                ctx.setdefault("args_preview", _preview_args(args))
                ctx.setdefault("kwargs_keys", sorted(kwargs.keys()))
                log_failure(
                    exc,
                    root=root_path,
                    run_id=rid,
                    tag=tag,
                    qualname=fn.__qualname__,
                    context=ctx,
                )
                if reraise:
                    raise
                return None  # type: ignore[return-value]

        @functools.wraps(fn)
        async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            rid = rid_factory()
            try:
                return await fn(*args, **kwargs)  # type: ignore[misc]
            except BaseException as exc:
                ctx = resolve_context(context, args, kwargs)
                ctx.setdefault("args_preview", _preview_args(args))
                ctx.setdefault("kwargs_keys", sorted(kwargs.keys()))
                log_failure(
                    exc,
                    root=root_path,
                    run_id=rid,
                    tag=tag,
                    qualname=fn.__qualname__,
                    context=ctx,
                )
                if reraise:
                    raise
                return None  # type: ignore[return-value]

        if asyncio.iscoroutinefunction(fn):
            return async_wrapper  # type: ignore[return-value]
        return sync_wrapper  # type: ignore[return-value]

    if _func is None:
        return decorator
    return decorator(_func)


def _preview_args(args: tuple[Any, ...], *, limit: int = 4) -> list[str]:
    out: list[str] = []
    for i, a in enumerate(args[:limit]):
        label = f"arg{i}"
        out.append(f"{label}={_preview_value(a)}")
    if len(args) > limit:
        out.append(f"…(+{len(args) - limit} more)")
    return out


def _preview_value(val: Any, *, max_len: int = 120) -> str:
    try:
        s = repr(val)
    except Exception:
        s = f"<{type(val).__name__}>"
    s = s.replace("\n", " ")
    if len(s) > max_len:
        return s[: max_len - 1] + "…"
    return s


def iter_events(root: Path) -> Iterator[ErrorEvent]:
    path = events_path(root)
    if not path.exists():
        return
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            try:
                yield ErrorEvent(
                    event_id=str(raw["event_id"]),
                    captured_at_utc=str(raw["captured_at_utc"]),
                    run_id=str(raw["run_id"]),
                    tag=raw.get("tag"),  # type: ignore[arg-type]
                    qualname=str(raw["qualname"]),
                    exc_type=str(raw["exc_type"]),
                    exc_message=str(raw["exc_message"]),
                    traceback_tail=str(raw["traceback_tail"]),
                    pattern_id=str(raw["pattern_id"]),
                    context=dict(raw.get("context") or {}),
                    suggested_fixes=list(raw.get("suggested_fixes") or []),
                )
            except (KeyError, TypeError, ValueError):
                continue


@dataclass
class ErrorPattern:
    pattern_id: str
    exc_type: str
    normalized_signature: str
    count: int
    last_seen_utc: str
    sample_qualnames: list[str]
    sample_messages: list[str]
    suggested_fixes: list[str]


def detect_patterns(root: Path, *, limit_samples: int = 3) -> list[ErrorPattern]:
    groups: dict[str, list[ErrorEvent]] = defaultdict(list)
    for ev in iter_events(root):
        groups[ev.pattern_id].append(ev)

    patterns: list[ErrorPattern] = []
    for pid, events in groups.items():
        events.sort(key=lambda e: e.captured_at_utc, reverse=True)
        last = events[0]
        sig = error_signature(last.exc_message)
        quals = []
        msgs = []
        for ev in events[:limit_samples]:
            if ev.qualname not in quals:
                quals.append(ev.qualname)
            if ev.exc_message not in msgs:
                msgs.append(ev.exc_message)
        fixes: list[str] = []
        for ev in events:
            for f in ev.suggested_fixes:
                if f not in fixes:
                    fixes.append(f)
        if not fixes:
            fixes = suggest_fixes(last.exc_type, last.exc_message)
        patterns.append(
            ErrorPattern(
                pattern_id=pid,
                exc_type=last.exc_type,
                normalized_signature=sig,
                count=len(events),
                last_seen_utc=last.captured_at_utc,
                sample_qualnames=quals,
                sample_messages=msgs[:limit_samples],
                suggested_fixes=fixes,
            )
        )
    patterns.sort(key=lambda p: (-p.count, p.last_seen_utc), reverse=False)
    return patterns


def record_applied_fix(
    root: Path,
    *,
    pattern_id: str,
    note: str,
    event_id: str | None = None,
) -> dict[str, Any]:
    row = {
        "applied_at_utc": utc_now().replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "pattern_id": pattern_id,
        "event_id": event_id,
        "note": note.strip(),
    }
    path = applied_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def find_event(root: Path, event_id: str) -> ErrorEvent | None:
    for ev in iter_events(root):
        if ev.event_id == event_id:
            return ev
    return None


def format_event(ev: ErrorEvent) -> str:
    lines = [
        f"[{ev.captured_at_utc}] {ev.exc_type} pattern={ev.pattern_id} event={ev.event_id}",
        f"  run={ev.run_id} tag={ev.tag!r} fn={ev.qualname}",
        f"  message: {ev.exc_message}",
    ]
    if ev.context:
        lines.append(f"  context: {json.dumps(ev.context, ensure_ascii=False)[:400]}")
    if ev.suggested_fixes:
        lines.append("  suggested:")
        for s in ev.suggested_fixes[:6]:
            lines.append(f"    - {s}")
    return "\n".join(lines)


def format_pattern(p: ErrorPattern) -> str:
    lines = [
        f"×{p.count}  {p.exc_type}  pattern_id={p.pattern_id}",
        f"  last_seen: {p.last_seen_utc}",
        f"  normalized: {p.normalized_signature[:200]}{'…' if len(p.normalized_signature) > 200 else ''}",
        f"  seen_in: {', '.join(p.sample_qualnames)}",
    ]
    for m in p.sample_messages[:2]:
        msg = m.replace("\n", " ")
        lines.append(f"  example: {msg[:220]}{'…' if len(msg) > 220 else ''}")
    lines.append("  suggested fixes:")
    for s in p.suggested_fixes[:8]:
        lines.append(f"    - {s}")
    return "\n".join(lines)


def cmd_list(root: Path, limit: int) -> int:
    events = list(iter_events(root))
    events.sort(key=lambda e: e.captured_at_utc, reverse=True)
    if not events:
        print("No captured events yet.", file=sys.stderr)
        return 0
    for ev in events[:limit]:
        print(format_event(ev))
        print()
    return 0


def cmd_patterns(root: Path, min_count: int, limit: int) -> int:
    rows = [p for p in detect_patterns(root) if p.count >= min_count][:limit]
    if not rows:
        print("No patterns at or above the count threshold.", file=sys.stderr)
        return 0
    for p in rows:
        print(format_pattern(p))
        print()
    return 0


def cmd_review(root: Path, pattern_limit: int) -> int:
    patterns = detect_patterns(root)[:pattern_limit]
    if not patterns:
        print("No captured failures yet.", file=sys.stderr)
        return 0
    print(f"# Error learning review ({len(patterns)} patterns)\n")
    for p in patterns:
        print(format_pattern(p))
        print("---")
    return 0


def cmd_suggest(root: Path, pattern_id: str | None, text: str | None, event_id: str | None) -> int:
    if event_id:
        ev = find_event(root, event_id)
        if ev is None:
            print(f"Unknown event_id {event_id!r}.", file=sys.stderr)
            return 1
        print(format_event(ev))
        return 0
    if pattern_id:
        for p in detect_patterns(root):
            if p.pattern_id == pattern_id:
                print(format_pattern(p))
                return 0
        print(f"Unknown pattern_id {pattern_id!r}.", file=sys.stderr)
        return 1
    if text:
        et = "UserText"
        fixes = suggest_fixes(et, text)
        print(f"Heuristic suggestions for free-text signal:\n  {text[:400]}")
        for s in fixes:
            print(f"  - {s}")
        return 0
    print("Provide --pattern-id, --event-id, or --text.", file=sys.stderr)
    return 1


def cmd_apply(root: Path, pattern_id: str, note: str, event_id: str | None) -> int:
    if event_id and find_event(root, event_id) is None:
        print(f"Unknown event_id {event_id!r}; apply aborted.", file=sys.stderr)
        return 1
    row = record_applied_fix(root, pattern_id=pattern_id, note=note, event_id=event_id)
    print(json.dumps(row, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Capture agent failures, detect patterns, suggest fixes, and log applied mitigations.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Workspace root (default: ERROR_LEARNING_ROOT or cwd).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="Show recent captured events (newest first).")
    p_list.add_argument("--limit", type=int, default=25)

    p_pat = sub.add_parser("patterns", help="Show recurring failure patterns.")
    p_pat.add_argument("--min-count", type=int, default=2)
    p_pat.add_argument("--limit", type=int, default=40)

    p_rev = sub.add_parser("review", help="Print a compact pattern dashboard for triage.")
    p_rev.add_argument("--limit", type=int, default=30)

    p_sug = sub.add_parser("suggest", help="Show fixes for a pattern, event, or raw text.")
    g = p_sug.add_mutually_exclusive_group(required=True)
    g.add_argument("--pattern-id", type=str, default=None)
    g.add_argument("--event-id", type=str, default=None)
    g.add_argument("--text", type=str, default=None)

    p_app = sub.add_parser(
        "apply",
        help="Record that a mitigation was applied (audit trail in applied.jsonl).",
    )
    p_app.add_argument("--pattern-id", type=str, required=True)
    p_app.add_argument("--note", type=str, default="acknowledged")
    p_app.add_argument("--event-id", type=str, default=None)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    root = workspace_root(args.root)

    if args.command == "list":
        return cmd_list(root, max(1, args.limit))
    if args.command == "patterns":
        return cmd_patterns(root, max(1, args.min_count), max(1, args.limit))
    if args.command == "review":
        return cmd_review(root, max(1, args.limit))
    if args.command == "suggest":
        return cmd_suggest(root, args.pattern_id, args.text, args.event_id)
    if args.command == "apply":
        return cmd_apply(root, args.pattern_id, args.note, args.event_id)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
