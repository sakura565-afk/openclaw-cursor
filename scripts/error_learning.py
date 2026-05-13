#!/usr/bin/env python3
"""Reusable error learning: capture exceptions, persist history, warn on repeat runs."""

from __future__ import annotations

import argparse
import functools
import json
import os
import sys
import traceback
import uuid
from collections.abc import Callable, Generator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, TextIO, TypeVar


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = ROOT / "data" / "error_history.json"
SCHEMA_VERSION = 2

ANSI = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "cyan": "\033[36m",
}

F = TypeVar("F", bound=Callable[..., Any])


class ErrorLearningError(RuntimeError):
    """Raised when the error history database cannot be read or written."""


def colorize(text: str, color: str, *, stream: TextIO | None = None) -> str:
    out = stream or sys.stderr
    if os.environ.get("NO_COLOR") or not out.isatty():
        return text
    prefix = ANSI.get(color, "")
    if not prefix:
        return text
    return f"{prefix}{text}{ANSI['reset']}"


def default_db_path() -> Path:
    """Return the default JSON path for learned errors (under the repo root)."""

    return DEFAULT_DB_PATH


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def normalize_message(message: str, *, max_len: int = 800) -> str:
    """Collapse whitespace for stable fingerprints."""

    text = " ".join(message.strip().split())
    if len(text) > max_len:
        return text[:max_len]
    return text


def resolve_script_key(path: str | Path | None) -> str:
    """Return a repo-relative script path when possible, else an absolute path string."""

    if path is None:
        raw = sys.argv[0] if sys.argv else ""
        candidate = Path(raw).resolve() if raw else Path.cwd() / "<unknown>"
    else:
        candidate = Path(path).resolve()
    try:
        return str(candidate.relative_to(ROOT))
    except ValueError:
        return str(candidate)


def fingerprint_for(script_key: str, exc: BaseException) -> str:
    """Stable id for deduplicating the same failure mode per script."""

    etype = type(exc).__name__
    msg = normalize_message(str(exc))
    raw = f"{script_key}\0{etype}\0{msg}"
    return sha256(raw.encode("utf-8")).hexdigest()[:20]


def mitigation_hint(exc: BaseException) -> str:
    """Lightweight suggestions keyed by exception type (extend as needed)."""

    mapping: dict[type[BaseException], str] = {
        FileNotFoundError: "Confirm the path exists and cwd matches what the script expects.",
        PermissionError: "Check file permissions or run from a directory you can write to.",
        IsADirectoryError: "A directory was used where a file was expected; adjust the path.",
        NotADirectoryError: "Expected a directory but found a file (or a missing parent path).",
        json.JSONDecodeError: "Validate JSON input; look for trailing commas or truncated files.",
        KeyError: "Verify dict keys / config fields and defaults for missing entries.",
        ValueError: "Inspect arguments and parsed values; often bad input format or range.",
        TypeError: "Check argument types and None values passed into APIs.",
        OSError: "Often network mounts, disk, or OS-level limits; retry after checking resources.",
        MemoryError: "Reduce batch sizes or streaming; free memory before retrying.",
        TimeoutError: "Increase timeouts, reduce workload, or check remote service health.",
        ImportError: "Verify the environment, PYTHONPATH, and optional dependency installs.",
        ModuleNotFoundError: "Install the missing package or fix the import path.",
        UnicodeDecodeError: "Open the file with the correct encoding or fix binary/text mode.",
    }
    for cls in type(exc).__mro__:
        if cls in mapping:
            return mapping[cls]
    return "Review the stack trace and recent code or config changes around the failure site."


def empty_store() -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "records": []}


def _validate_record(raw: object) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ErrorLearningError("Each record must be a JSON object.")
    required = ("id", "fingerprint", "script", "first_seen", "last_seen", "error_type", "message", "traceback")
    for key in required:
        val = raw.get(key)
        if not isinstance(val, str) or not val.strip():
            raise ErrorLearningError(f"Record field '{key}' must be a non-empty string.")
    count = raw.get("occurrence_count", 1)
    if not isinstance(count, int) or count < 1:
        raise ErrorLearningError("Record field 'occurrence_count' must be a positive integer.")
    mitigation = raw.get("mitigation")
    if mitigation is not None and not isinstance(mitigation, str):
        raise ErrorLearningError("Record field 'mitigation' must be a string when present.")
    out = {k: str(raw[k]) for k in required}
    out["occurrence_count"] = int(count)
    if mitigation is not None:
        out["mitigation"] = str(mitigation)
    return out


def load_store(db_path: Path) -> dict[str, Any]:
    if not db_path.exists():
        return empty_store()
    try:
        raw = json.loads(db_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ErrorLearningError(f"Unable to parse JSON from {db_path}: {exc}") from exc
    if isinstance(raw, list):
        return {"schema_version": SCHEMA_VERSION, "records": [_validate_record(x) for x in raw]}
    if not isinstance(raw, dict):
        raise ErrorLearningError("History file must contain a JSON object.")
    recs = raw.get("records", raw.get("entries"))
    if recs is None:
        raise ErrorLearningError("History file must include a 'records' array.")
    if not isinstance(recs, list):
        raise ErrorLearningError("'records' must be a JSON array.")
    return {
        "schema_version": int(raw.get("schema_version", SCHEMA_VERSION)),
        "records": [_validate_record(x) for x in recs],
    }


def save_store(db_path: Path, store: dict[str, Any]) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = db_path.with_suffix(db_path.suffix + f".{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(json.dumps(store, indent=2) + "\n", encoding="utf-8")
        tmp.replace(db_path)
    except BaseException:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        raise


def record_exception(
    exc: BaseException,
    script_key: str | None = None,
    *,
    db_path: Path | None = None,
    extra_traceback: str | None = None,
) -> dict[str, Any]:
    """Persist one exception (or merge with an existing fingerprint). Returns the stored record."""

    path = db_path or default_db_path()
    script = resolve_script_key(script_key)
    fp = fingerprint_for(script, exc)
    tb = extra_traceback if extra_traceback is not None else "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    store = load_store(path)
    records: list[dict[str, Any]] = store["records"]
    now = _utc_now_iso()
    hint = mitigation_hint(exc)

    for rec in records:
        if rec.get("fingerprint") == fp:
            rec["last_seen"] = now
            rec["occurrence_count"] = int(rec["occurrence_count"]) + 1
            rec["traceback"] = tb
            rec["message"] = str(exc)
            if "mitigation" not in rec or not str(rec.get("mitigation", "")).strip():
                rec["mitigation"] = hint
            save_store(path, store)
            return rec

    rec = {
        "id": uuid.uuid4().hex[:12],
        "fingerprint": fp,
        "script": script,
        "first_seen": now,
        "last_seen": now,
        "error_type": type(exc).__name__,
        "message": str(exc),
        "traceback": tb,
        "occurrence_count": 1,
        "mitigation": hint,
    }
    records.append(rec)
    records.sort(key=lambda r: str(r["last_seen"]), reverse=True)
    save_store(path, store)
    return rec


def iter_script_records(script_key: str | None, store: dict[str, Any]) -> Iterator[dict[str, Any]]:
    script = resolve_script_key(script_key)
    for rec in store["records"]:
        if rec["script"] == script:
            yield rec


def mitigation_hint_from_record(rec: dict[str, Any]) -> str:
    m = rec.get("mitigation")
    if isinstance(m, str) and m.strip():
        return m.strip()
    return "Review traceback in the history file or re-run with logging enabled."


@dataclass(frozen=True)
class KnownErrorWarning:
    record: dict[str, Any]

    def format(self, *, stream: TextIO | None = None) -> str:
        r = self.record
        out = stream or sys.stderr
        n = int(r["occurrence_count"])
        sug = r.get("mitigation")
        if not (isinstance(sug, str) and sug.strip()):
            sug = mitigation_hint_from_record(r)
        lines = [
            colorize(
                f"[error-learning] This script has failed before with {r['error_type']} ({n}x).",
                "yellow",
                stream=out,
            ),
            colorize(f"  Last message: {r['message']}", "cyan", stream=out),
            colorize(f"  Suggestion: {sug}", "green", stream=out),
        ]
        return "\n".join(lines)


def preflight_warnings(
    script_key: str | None = None,
    *,
    db_path: Path | None = None,
    limit: int = 5,
) -> list[KnownErrorWarning]:
    """Load history for this script and return structured warnings (past failures)."""

    path = db_path or default_db_path()
    if not path.exists():
        return []
    try:
        store = load_store(path)
    except ErrorLearningError:
        return []
    ranked = sorted(iter_script_records(script_key, store), key=lambda r: int(r["occurrence_count"]), reverse=True)
    out: list[KnownErrorWarning] = []
    for rec in ranked[: max(limit, 0)]:
        out.append(KnownErrorWarning(rec))
    return out


def print_preflight_warnings(
    script_key: str | None = None,
    *,
    db_path: Path | None = None,
    limit: int = 5,
    stream: TextIO | None = None,
) -> None:
    """Emit human-readable warnings to *stream* when this script has prior failures."""

    out = stream or sys.stderr
    warns = preflight_warnings(script_key, db_path=db_path, limit=limit)
    if not warns:
        return
    out.write(
        colorize(
            "[error-learning] Prior errors were recorded for this script; summary below.",
            "yellow",
            stream=out,
        )
        + "\n"
    )
    for w in warns:
        out.write(w.format(stream=out) + "\n")


@contextmanager
def capture_errors(
    script_key: str | Path | None = None,
    *,
    db_path: Path | None = None,
    log_to_stderr: bool = True,
    re_raise: bool = True,
) -> Generator[None, None, None]:
    """Context manager: log any BaseException to the learning DB (and optionally stderr)."""

    script = resolve_script_key(script_key)
    try:
        yield
    except BaseException as exc:
        rec = record_exception(exc, script, db_path=db_path)
        if log_to_stderr:
            err = sys.stderr
            err.write(
                colorize(
                    f"[error-learning] Logged {rec['error_type']} for {rec['script']} (id={rec['id']}).",
                    "red",
                    stream=err,
                )
                + "\n"
            )
        if re_raise:
            raise


def guard_main(
    *,
    script_key: str | Path | None = None,
    db_path: Path | None = None,
    preflight: bool = True,
    preflight_limit: int = 5,
) -> Callable[[F], F]:
    """Decorator for a main function: optional preflight warnings and exception logging."""

    def deco(fn: F) -> F:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            sk = resolve_script_key(script_key)
            if preflight:
                print_preflight_warnings(sk, db_path=db_path, limit=preflight_limit)
            try:
                return fn(*args, **kwargs)
            except BaseException as exc:
                record_exception(exc, sk, db_path=db_path)
                raise

        return wrapper  # type: ignore[return-value]

    return deco


def clear_history(db_path: Path | None = None) -> int:
    """Remove all records. Returns number of records deleted."""

    path = db_path or default_db_path()
    if not path.exists():
        return 0
    store = load_store(path)
    n = len(store["records"])
    save_store(path, empty_store())
    return n


def export_history(destination: Path, *, db_path: Path | None = None, script_filter: str | None = None) -> int:
    """Write a JSON snapshot of the store (optionally filtered by script). Returns record count written."""

    path = db_path or default_db_path()
    store = load_store(path) if path.exists() else empty_store()
    records: list[dict[str, Any]] = list(store["records"])
    if script_filter is not None:
        sk = resolve_script_key(script_filter)
        records = [r for r in records if r["script"] == sk]
    payload = {"schema_version": SCHEMA_VERSION, "exported_at": _utc_now_iso(), "records": records}
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return len(records)


def format_record_line(rec: dict[str, Any], *, stream: TextIO | None = None) -> str:
    out = stream or sys.stdout
    n = rec["occurrence_count"]
    return (
        f"{colorize(rec['script'], 'cyan', stream=out)} "
        f"{colorize(rec['error_type'], 'red', stream=out)} "
        f"{colorize(f'x{n}', 'yellow', stream=out)} "
        f"{colorize(rec['last_seen'], 'green', stream=out)}\n"
        f"  id={rec['id']} fp={rec['fingerprint']}\n"
        f"  {rec['message']}\n"
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Learn from script errors: log, review, export, and pre-run warnings.",
    )
    parser.add_argument(
        "--db",
        "--db-path",
        dest="db_path",
        type=Path,
        default=default_db_path(),
        help=f"Path to error history JSON (default: {default_db_path()})",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    show = sub.add_parser("show", aliases=["list"], help="View recorded errors (optional --script filter).")
    show.add_argument("--script", help="Only show rows for this script path or argv-style name.")
    show.add_argument("--limit", type=int, default=50, help="Max rows to print.")

    sub.add_parser("clear", help="Delete all learned error records.")

    ex = sub.add_parser("export", help="Copy history to another JSON file.")
    ex.add_argument("--output", "-o", type=Path, required=True, help="Destination JSON path.")
    ex.add_argument("--script", help="Export only rows for this script.")

    pf = sub.add_parser("preflight", help="Print warnings for a script based on prior errors.")
    pf.add_argument("--script", help="Script path; defaults to sys.argv[0] of the invoking process.")

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    db_path: Path = args.db_path

    try:
        if args.command in ("show", "list"):
            store = load_store(db_path) if db_path.exists() else empty_store()
            recs: list[dict[str, Any]] = list(store["records"])
            if args.script:
                sk = resolve_script_key(args.script)
                recs = [r for r in recs if r["script"] == sk]
            recs.sort(key=lambda r: str(r["last_seen"]), reverse=True)
            recs = recs[: max(args.limit, 1)]
            if not recs:
                print(colorize("No error history found.", "yellow", stream=sys.stdout))
                return 0
            print(colorize("Error history", "bold", stream=sys.stdout))
            print(colorize("=============", "cyan", stream=sys.stdout))
            for i, rec in enumerate(recs):
                if i:
                    print()
                print(format_record_line(rec, stream=sys.stdout), end="")
                tb_preview = rec["traceback"].strip().splitlines()
                tail = "\n".join(tb_preview[-12:]) if tb_preview else ""
                if tail:
                    print(colorize("  Traceback (tail):", "yellow", stream=sys.stdout))
                    for line in tail.splitlines():
                        print(f"    {line}")
            return 0

        if args.command == "clear":
            n = clear_history(db_path)
            print(colorize(f"Cleared {n} record(s) from {db_path}.", "green", stream=sys.stdout))
            return 0

        if args.command == "export":
            count = export_history(args.output, db_path=db_path, script_filter=args.script)
            print(
                colorize(
                    f"Exported {count} record(s) to {args.output.resolve()}.",
                    "green",
                    stream=sys.stdout,
                )
            )
            return 0

        if args.command == "preflight":
            print_preflight_warnings(args.script, db_path=db_path)
            return 0

    except ErrorLearningError as exc:
        print(colorize(str(exc), "red"), file=sys.stderr)
        return 1

    print(colorize(f"Unknown command: {args.command}", "red"), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
