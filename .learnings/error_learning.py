#!/usr/bin/env python3
"""Capture tool execution failures for later review.

This module appends structured records to a JSON Lines file under the user's
OpenClaw home directory (by default ``~/.openclaw/.learnings/errors.jsonl``).
It is designed to be safe to call from automation: I/O and serialization errors
are caught and do not propagate to callers.

Typical usage:

* Wrap a tool call with :func:`log_tool_error` inside an ``except`` block.
* Use :func:`run_tool_with_logging` to run a callable and persist failures
  automatically.
* Run this file as a script to print recent log lines: ``python .learnings/error_learning.py recent``

Because the directory name starts with a dot, this file is not importable as a
normal package module; load it with ``importlib`` or execute it as a script.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypeVar

__all__ = [
    "default_errors_path",
    "log_tool_error",
    "read_recent_errors",
    "run_tool_with_logging",
    "safe_args_for_log",
]

T = TypeVar("T")

_ENV_OPENCLAW_HOME = "OPENCLAW_HOME"
_DEFAULT_REL_LEARNINGS = Path(".learnings") / "errors.jsonl"


def default_errors_path(*, openclaw_home: Path | None = None) -> Path:
    """Return the path to the JSONL error log.

    The log lives at ``<openclaw_home>/.learnings/errors.jsonl``. When
    ``openclaw_home`` is omitted, ``OPENCLAW_HOME`` is used if set; otherwise
    ``Path.home() / ".openclaw"`` is used.
    """

    base = openclaw_home
    if base is None:
        raw = os.environ.get(_ENV_OPENCLAW_HOME)
        base = Path(raw).expanduser().resolve() if raw else Path.home() / ".openclaw"
    else:
        base = Path(base).expanduser().resolve()
    return (base / _DEFAULT_REL_LEARNINGS).resolve()


def safe_args_for_log(args: Any, *, max_text: int = 8000) -> Any:
    """Convert tool arguments into a JSON-serializable structure.

    Mappings and sequences are copied shallowly where possible. Values that
    are not JSON-compatible are converted with :func:`repr`, then truncated to
    ``max_text`` characters to keep log lines bounded.
    """

    try:
        return _safe_args_inner(args, max_text=max_text, _depth=0)
    except Exception:
        return {"_serialization_error": "safe_args_for_log failed", "_repr": _trunc_repr(args, max_text)}


def _trunc_repr(value: Any, max_text: int) -> str:
    text = repr(value)
    if len(text) > max_text:
        return text[: max_text - 3] + "..."
    return text


def _safe_args_inner(value: Any, *, max_text: int, _depth: int) -> Any:
    if _depth > 12:
        return _trunc_repr(value, max_text)

    if value is None or isinstance(value, (bool, int, float, str)):
        if isinstance(value, str) and len(value) > max_text:
            return value[: max_text - 3] + "..."
        return value

    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for i, (k, v) in enumerate(value.items()):
            if i >= 200:
                out["_truncated_keys"] = len(value) - i
                break
            key = str(k)
            if len(key) > 256:
                key = key[:253] + "..."
            out[key] = _safe_args_inner(v, max_text=max_text, _depth=_depth + 1)
        return out

    if isinstance(value, (list, tuple)):
        seq = list(value)
        limit = 200
        truncated = len(seq) > limit
        items = [_safe_args_inner(v, max_text=max_text, _depth=_depth + 1) for v in seq[:limit]]
        if isinstance(value, tuple):
            items = list(items)
            result: Any = tuple(items)
        else:
            result = items
        if truncated:
            return {"_type": "list", "_items": result, "_truncated": len(seq) - limit}
        return result

    if isinstance(value, (bytes, bytearray)):
        raw = bytes(value[:4000])
        try:
            decoded = raw.decode("utf-8", errors="replace")
        except Exception:
            decoded = repr(raw)
        suffix = "..." if len(value) > len(raw) else ""
        return {"_type": "bytes", "preview": decoded[:max_text] + suffix}

    return _trunc_repr(value, max_text)


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def log_tool_error(
    tool_name: str,
    args: Any,
    exc: BaseException,
    *,
    log_path: Path | None = None,
    extra: Mapping[str, Any] | None = None,
) -> None:
    """Append one error record to the JSONL log.

    ``tool_name`` identifies the tool or operation. ``args`` is passed through
    :func:`safe_args_for_log`. ``exc`` supplies the error message and type.
    Optional ``extra`` fields are merged into the record (values must be
    JSON-serializable or they are skipped).

    Failures while writing are swallowed after a short message to ``stderr`` so
    callers are never interrupted.
    """

    path = log_path if log_path is not None else default_errors_path()
    record: dict[str, Any] = {
        "timestamp": _utc_timestamp(),
        "tool": str(tool_name),
        "args": safe_args_for_log(args),
        "error_type": type(exc).__name__,
        "error_message": str(exc),
    }
    if extra:
        try:
            for key, val in extra.items():
                try:
                    json.dumps(val)
                    record[str(key)] = val
                except (TypeError, ValueError):
                    record[str(key)] = _trunc_repr(val, 2000)
        except Exception:
            record["extra_parse_error"] = True

    try:
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
    except Exception as conv_exc:
        line = (
            json.dumps(
                {
                    "timestamp": _utc_timestamp(),
                    "tool": str(tool_name),
                    "args": None,
                    "error_type": "LogSerializationError",
                    "error_message": str(conv_exc),
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n"
        )

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)
    except OSError as io_exc:
        try:
            print(f"[error_learning] failed to write log: {io_exc}", file=sys.stderr)
        except Exception:
            pass


def read_recent_errors(
    limit: int = 20,
    *,
    log_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Return up to ``limit`` parsed records from the end of the log (newest last).

    Malformed lines are skipped. If the file is missing, an empty list is
    returned. I/O errors result in an empty list and a message on ``stderr``.
    """

    path = log_path if log_path is not None else default_errors_path()
    if limit < 1:
        return []

    try:
        if not path.exists():
            return []
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        try:
            print(f"[error_learning] failed to read log: {exc}", file=sys.stderr)
        except Exception:
            pass
        return []

    lines = [ln for ln in text.splitlines() if ln.strip()]
    tail = lines[-limit:] if len(lines) > limit else lines
    out: list[dict[str, Any]] = []
    for line in tail:
        try:
            parsed = json.loads(line)
            if isinstance(parsed, dict):
                out.append(parsed)
        except json.JSONDecodeError:
            continue
    return out


def run_tool_with_logging(
    tool_name: str,
    args: Any,
    func: Callable[[], T],
    *,
    log_path: Path | None = None,
) -> T:
    """Run ``func`` and log to JSONL if it raises.

    The exception is re-raised after logging. If logging itself fails, the
    original exception is still raised.
    """

    try:
        return func()
    except BaseException as exc:
        try:
            log_tool_error(tool_name, args, exc, log_path=log_path)
        except Exception:
            pass
        raise


def _format_record(record: dict[str, Any]) -> str:
    ts = record.get("timestamp", "?")
    tool = record.get("tool", "?")
    err_t = record.get("error_type", "?")
    msg = record.get("error_message", "")
    args_preview = record.get("args")
    try:
        args_str = json.dumps(args_preview, ensure_ascii=False, indent=2) if args_preview is not None else ""
    except Exception:
        args_str = repr(args_preview)
    lines = [
        f"--- {ts}  tool={tool!r}  type={err_t}",
        f"message: {msg}",
        "args:",
        args_str or "  (none)",
    ]
    return "\n".join(lines)


def _cmd_recent(args: argparse.Namespace) -> int:
    try:
        path = Path(args.log_path).expanduser().resolve() if args.log_path else default_errors_path()
        records = read_recent_errors(args.limit, log_path=path)
    except Exception as exc:
        print(f"[error_learning] {exc}", file=sys.stderr)
        return 1

    if args.json:
        try:
            print(json.dumps(records, ensure_ascii=False, indent=2))
        except Exception as exc:
            print(f"[error_learning] {exc}", file=sys.stderr)
            return 1
        return 0

    if not records:
        print(f"No entries (log: {path})")
        return 0

    for i, rec in enumerate(records):
        if i:
            print()
        print(_format_record(rec))
    return 0


def _cmd_path(args: argparse.Namespace) -> int:
    try:
        path = Path(args.log_path).expanduser().resolve() if args.log_path else default_errors_path()
        print(path)
    except Exception as exc:
        print(f"[error_learning] {exc}", file=sys.stderr)
        return 1
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Review tool execution errors logged to ~/.openclaw/.learnings/errors.jsonl",
    )
    parser.add_argument(
        "--log-path",
        type=Path,
        default=None,
        help="Override JSONL path (default: OPENCLAW_HOME/.learnings/errors.jsonl).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    recent = sub.add_parser("recent", help="Show the last N log entries (plain text by default).")
    recent.add_argument("--limit", type=int, default=20, help="Maximum entries to show (default: 20).")
    recent.add_argument("--json", action="store_true", help="Print raw JSON array instead of text.")
    recent.set_defaults(func=_cmd_recent)

    path_p = sub.add_parser("path", help="Print the resolved JSONL log path and exit.")
    path_p.set_defaults(func=_cmd_path)

    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for reviewing stored errors."""

    try:
        parser = _build_parser()
        ns = parser.parse_args(argv)
        func = getattr(ns, "func", None)
        if func is None:
            parser.print_help()
            return 1
        return int(func(ns))
    except SystemExit as e:
        code = e.code
        if code is None:
            return 0
        if isinstance(code, int):
            return code
        return 1
    except Exception as exc:
        print(f"[error_learning] fatal: {exc}", file=sys.stderr)
        traceback.print_exc(limit=3, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
