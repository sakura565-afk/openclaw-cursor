#!/usr/bin/env python3
"""Error learning system for OpenClaw.

Logs errors with timestamps, categories, and context to ``.learnings/ERRORS.md``
so that later runs can read them back and avoid repeating the same mistakes.

The module can be used in two ways:

1. As a library::

       from scripts.error_learning import log_error, get_recent_errors, suggest_fixes

       try:
           ...
       except Exception as exc:
           log_error(exc, category="ollama_pull", context={"model": "llama3.2"})

       for record in get_recent_errors(limit=5):
           print(record.summary())

       for tip in suggest_fixes("ModuleNotFoundError: No module named 'foo'"):
           print("-", tip)

2. As a CLI::

       python -m scripts.error_learning log --message "boom" --category demo
       python -m scripts.error_learning list --limit 5
       python -m scripts.error_learning suggest --message "No module named 'foo'"

The on-disk format is human-readable Markdown, append-only, with stable section
markers that the module can parse on subsequent reads. Each error becomes one
``## `` section identified by a short signature hash so repeated errors update
their occurrence counter and ``Last Seen`` timestamp instead of polluting the
log with duplicates.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


__all__ = [
    "ErrorRecord",
    "log_error",
    "get_recent_errors",
    "suggest_fixes",
    "DEFAULT_ERRORS_PATH",
]


DEFAULT_ERRORS_DIR = Path(".learnings")
DEFAULT_ERRORS_FILE = "ERRORS.md"
DEFAULT_ERRORS_PATH = DEFAULT_ERRORS_DIR / DEFAULT_ERRORS_FILE

DOCUMENT_HEADER = "# Error Learnings\n"
DOCUMENT_PREAMBLE = (
    "<!-- Managed by scripts/error_learning.py. -->\n"
    "<!-- Each `## ` section is one learned error keyed by signature. -->\n\n"
)

SECTION_RE = re.compile(r"(?ms)^## .+?(?=^## |\Z)")
HEADING_RE = re.compile(r"^## (?P<title>.+?)\s*$", re.MULTILINE)
META_LINE_RE = re.compile(r"^- \*\*(?P<key>[A-Za-z ][A-Za-z 0-9]*)\*\*:\s*(?P<value>.*?)\s*$")
SUBSECTION_RE = re.compile(r"^### (?P<name>.+?)\s*$")
FENCE_RE = re.compile(r"^```")

UNCATEGORIZED = "uncategorized"
RESET = "\033[0m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
DIM = "\033[2m"

# Built-in heuristics keyed by a pattern that matches against the error
# message (case-insensitive). The first matching pattern's tips are returned
# in addition to anything we learn from prior records.
BUILTIN_PATTERNS: tuple[tuple[re.Pattern[str], tuple[str, ...]], ...] = (
    (
        re.compile(r"no module named ['\"]?(?P<name>[\w\.\-]+)", re.IGNORECASE),
        (
            "Install the missing module: `pip install {name}` "
            "(or add it to requirements.txt and re-sync your environment).",
            "Verify the import path matches the installed package name and "
            "that the active interpreter is the one with the dependency.",
            "If the module is local, confirm `__init__.py` files exist and the "
            "project root is on `sys.path` / `PYTHONPATH`.",
        ),
    ),
    (
        re.compile(r"modulenotfounderror|importerror", re.IGNORECASE),
        (
            "Check that the dependency is installed in the current "
            "interpreter (`python -m pip list`).",
            "Look for circular imports or shadowing local files with the same "
            "name as a third-party package.",
        ),
    ),
    (
        re.compile(r"filenotfounderror|no such file or directory", re.IGNORECASE),
        (
            "Verify the path exists and is reachable from the current working "
            "directory; prefer absolute paths for scripts.",
            "Create parent directories first with `Path(p).parent.mkdir(parents=True, exist_ok=True)`.",
        ),
    ),
    (
        re.compile(r"permissionerror|permission denied", re.IGNORECASE),
        (
            "Inspect file/dir permissions (`ls -l`) and adjust with `chmod` "
            "or run with the appropriate user.",
            "On Windows, ensure the file is not held open by another process.",
        ),
    ),
    (
        re.compile(r"connection (?:refused|reset|aborted|error)|connectionerror", re.IGNORECASE),
        (
            "Confirm the target service is running and reachable on the "
            "expected host/port.",
            "Add retry-with-backoff around transient network calls and "
            "surface the underlying URL in the log context.",
        ),
    ),
    (
        re.compile(r"timeout|timed out|read timed out", re.IGNORECASE),
        (
            "Increase the timeout, or split the work into smaller chunks.",
            "Add a retry loop with exponential backoff; capture the elapsed "
            "time as part of the error context for future tuning.",
        ),
    ),
    (
        re.compile(r"jsondecodeerror|expecting value: line", re.IGNORECASE),
        (
            "Log the raw response body and confirm it is valid JSON before "
            "parsing; many APIs return HTML on errors.",
            "Wrap `json.loads` calls and fall back to a structured error "
            "with the offending payload truncated to a safe length.",
        ),
    ),
    (
        re.compile(r"unicodedecodeerror|unicodeencodeerror|codec can't (?:de|en)code", re.IGNORECASE),
        (
            "Open files with an explicit `encoding=\"utf-8\"` argument.",
            "If the byte stream is binary, read as bytes and decode lazily "
            "with `errors=\"replace\"` to keep the pipeline running.",
        ),
    ),
    (
        re.compile(r"keyerror", re.IGNORECASE),
        (
            "Use `dict.get(key, default)` or check membership before access.",
            "Validate inbound payloads against an explicit schema so missing "
            "keys are caught at the boundary.",
        ),
    ),
    (
        re.compile(r"indexerror", re.IGNORECASE),
        (
            "Guard list access with a length check or use slicing which "
            "tolerates out-of-range bounds.",
        ),
    ),
    (
        re.compile(r"attributeerror.*has no attribute ['\"](?P<attr>[\w_]+)['\"]", re.IGNORECASE),
        (
            "The object does not expose `{attr}`. Confirm the type and "
            "version of the imported library; this often signals an API "
            "change between releases.",
        ),
    ),
    (
        re.compile(r"typeerror.*positional arguments?|missing \d+ required", re.IGNORECASE),
        (
            "The function signature changed or the call site is stale. "
            "Re-read the function definition and update each call site.",
        ),
    ),
    (
        re.compile(r"typeerror|valueerror", re.IGNORECASE),
        (
            "Add an explicit type/range check at the boundary and convert "
            "input early; surface the offending value in the log context.",
        ),
    ),
    (
        re.compile(r"recursionerror", re.IGNORECASE),
        (
            "Detect the recursion base case or convert to an iterative "
            "implementation; also bump `sys.setrecursionlimit` only as a "
            "last resort.",
        ),
    ),
    (
        re.compile(r"memoryerror|out of memory|oom", re.IGNORECASE),
        (
            "Stream the input instead of loading it all at once, and look "
            "for accidental N^2 buffering of intermediate results.",
        ),
    ),
    (
        re.compile(r"sqlite|database is locked", re.IGNORECASE),
        (
            "Close cursors/connections promptly, and avoid sharing a "
            "connection across threads. Wrap writes in a single transaction.",
        ),
    ),
    (
        re.compile(r"ratelimit|too many requests|429", re.IGNORECASE),
        (
            "Back off using the `Retry-After` header, and consider batching "
            "or caching to reduce the request rate.",
        ),
    ),
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _format_ts(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_ts(value: str) -> datetime | None:
    value = value.strip()
    if not value:
        return None
    candidates = [value]
    if value.endswith("Z"):
        candidates.append(value[:-1] + "+00:00")
    for candidate in candidates:
        try:
            dt = datetime.fromisoformat(candidate)
        except ValueError:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    return None


def _normalize_message(message: str) -> str:
    """Return a fingerprint of ``message`` that ignores volatile bits.

    Numbers, hex addresses, absolute paths and quoted strings are squashed so
    that "line 12" and "line 99" map to the same signature.
    """

    text = message.strip().lower()
    text = re.sub(r"0x[0-9a-f]+", "0xN", text)
    text = re.sub(r"\b\d+\b", "N", text)
    text = re.sub(r"['\"][^'\"]{0,200}['\"]", "'S'", text)
    text = re.sub(r"(?:[a-z]:)?[/\\][\w./\\-]+", "P", text)
    text = re.sub(r"\s+", " ", text)
    return text


def _signature(category: str, message: str) -> str:
    payload = f"{category}\n{_normalize_message(message)}".encode("utf-8")
    return hashlib.sha1(payload).hexdigest()[:12]


def _slugify_title(category: str, message: str) -> str:
    first_line = message.strip().splitlines()[0] if message.strip() else "error"
    if len(first_line) > 80:
        first_line = first_line[:77].rstrip() + "..."
    return f"[{category}] {first_line}"


def _looks_like_exception(value: Any) -> bool:
    return isinstance(value, BaseException)


def _format_traceback(exc: BaseException) -> str:
    return "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).rstrip()


def _format_context(context: Any) -> str:
    if context is None:
        return ""
    if isinstance(context, str):
        return context.strip()
    try:
        return json.dumps(context, indent=2, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return repr(context)


def _ensure_path(path: str | os.PathLike[str] | None) -> Path:
    if path is None:
        return DEFAULT_ERRORS_PATH
    p = Path(path)
    if p.is_dir():
        return p / DEFAULT_ERRORS_FILE
    return p


@dataclass
class ErrorRecord:
    """One learned error in the registry."""

    signature: str
    category: str
    message: str
    first_seen: datetime
    last_seen: datetime
    occurrences: int = 1
    context: str = ""
    traceback_text: str = ""
    suggested_fix: str = ""
    title: str = ""
    extra: dict[str, str] = field(default_factory=dict)

    @property
    def first_message_line(self) -> str:
        return self.message.strip().splitlines()[0] if self.message.strip() else ""

    def summary(self) -> str:
        return (
            f"[{self.category}] {self.first_message_line} "
            f"(x{self.occurrences}, last {_format_ts(self.last_seen)}, sig {self.signature})"
        )

    def to_markdown(self) -> str:
        title = self.title or _slugify_title(self.category, self.message)
        lines: list[str] = [f"## {title}"]
        lines.append(f"- **Signature**: {self.signature}")
        lines.append(f"- **Category**: {self.category}")
        lines.append(f"- **First Seen**: {_format_ts(self.first_seen)}")
        lines.append(f"- **Last Seen**: {_format_ts(self.last_seen)}")
        lines.append(f"- **Occurrences**: {self.occurrences}")
        if self.suggested_fix:
            fix = self.suggested_fix.strip().replace("\n", " ")
            lines.append(f"- **Suggested Fix**: {fix}")
        for key, value in sorted(self.extra.items()):
            value_clean = value.strip().replace("\n", " ")
            lines.append(f"- **{key}**: {value_clean}")

        lines.append("")
        lines.append("### Message")
        lines.append("```")
        lines.append(self.message.rstrip() or "(empty)")
        lines.append("```")

        if self.context:
            lines.append("")
            lines.append("### Context")
            lines.append("```")
            lines.append(self.context.rstrip())
            lines.append("```")

        if self.traceback_text:
            lines.append("")
            lines.append("### Traceback")
            lines.append("```")
            lines.append(self.traceback_text.rstrip())
            lines.append("```")

        lines.append("")
        return "\n".join(lines)


def _read_document(path: Path) -> tuple[str, list[ErrorRecord]]:
    if not path.exists():
        return "", []
    text = path.read_text(encoding="utf-8")
    sections = SECTION_RE.findall(text)
    if sections:
        head_end = text.find(sections[0])
        preamble = text[:head_end]
    else:
        preamble = text
    records = [_parse_section(section) for section in sections]
    records = [record for record in records if record is not None]
    return preamble, records


def _parse_section(section: str) -> ErrorRecord | None:
    heading_match = HEADING_RE.search(section)
    if not heading_match:
        return None
    title = heading_match.group("title").strip()

    body = section[heading_match.end():]
    lines = body.splitlines()

    meta: dict[str, str] = {}
    subsections: dict[str, list[str]] = {}
    current_sub: str | None = None
    in_fence = False
    fence_buffer: list[str] = []

    i = 0
    while i < len(lines):
        line = lines[i]
        if FENCE_RE.match(line) and current_sub is not None:
            if not in_fence:
                in_fence = True
                fence_buffer = []
            else:
                in_fence = False
                subsections.setdefault(current_sub, []).extend(fence_buffer)
                fence_buffer = []
            i += 1
            continue
        if in_fence:
            fence_buffer.append(line)
            i += 1
            continue

        sub_match = SUBSECTION_RE.match(line)
        if sub_match:
            current_sub = sub_match.group("name").strip()
            subsections.setdefault(current_sub, [])
            i += 1
            continue

        if current_sub is None:
            meta_match = META_LINE_RE.match(line)
            if meta_match:
                meta[meta_match.group("key").strip()] = meta_match.group("value").strip()
            i += 1
            continue

        if line.strip():
            subsections.setdefault(current_sub, []).append(line)
        i += 1

    signature = meta.get("Signature", "").strip()
    category = meta.get("Category", UNCATEGORIZED).strip() or UNCATEGORIZED
    first_seen = _parse_ts(meta.get("First Seen", "")) or _utcnow()
    last_seen = _parse_ts(meta.get("Last Seen", "")) or first_seen
    try:
        occurrences = max(1, int(meta.get("Occurrences", "1")))
    except ValueError:
        occurrences = 1
    suggested_fix = meta.get("Suggested Fix", "").strip()

    message = "\n".join(subsections.get("Message", [])).strip()
    if not message:
        message = title
    context = "\n".join(subsections.get("Context", [])).strip()
    traceback_text = "\n".join(subsections.get("Traceback", [])).strip()

    if not signature:
        signature = _signature(category, message)

    extra = {
        key: value
        for key, value in meta.items()
        if key
        not in {
            "Signature",
            "Category",
            "First Seen",
            "Last Seen",
            "Occurrences",
            "Suggested Fix",
        }
    }

    return ErrorRecord(
        signature=signature,
        category=category,
        message=message,
        first_seen=first_seen,
        last_seen=last_seen,
        occurrences=occurrences,
        context=context,
        traceback_text=traceback_text,
        suggested_fix=suggested_fix,
        title=title,
        extra=extra,
    )


def _write_document(path: Path, preamble: str, records: Sequence[ErrorRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not preamble.strip():
        preamble = DOCUMENT_HEADER + "\n" + DOCUMENT_PREAMBLE
    elif not preamble.endswith("\n"):
        preamble += "\n"

    sorted_records = sorted(records, key=lambda r: r.last_seen, reverse=True)
    body = "\n".join(record.to_markdown() for record in sorted_records)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(preamble + body, encoding="utf-8")
    tmp.replace(path)


def log_error(
    error: str | BaseException,
    *,
    category: str | None = None,
    context: Any = None,
    traceback_text: str | None = None,
    suggested_fix: str | None = None,
    extra: Mapping[str, str] | None = None,
    path: str | os.PathLike[str] | None = None,
    now: datetime | None = None,
) -> ErrorRecord:
    """Append (or merge) an error into ``.learnings/ERRORS.md``.

    Parameters
    ----------
    error:
        Either a string message or a live :class:`BaseException` instance. When
        an exception is supplied its traceback is captured automatically.
    category:
        Free-form bucket the error belongs to (for example ``"ollama_pull"``).
        When omitted, defaults to the exception class name or ``"uncategorized"``.
    context:
        Optional JSON-serializable mapping (or string) describing what was
        happening when the error occurred.
    traceback_text:
        Pre-formatted traceback. Overrides the auto-captured one when given.
    suggested_fix:
        Manual hint to record alongside this error. When omitted the function
        attempts to derive one from :func:`suggest_fixes`.
    extra:
        Additional metadata keys to write under the section.
    path:
        Override the location of the errors file. Defaults to
        ``.learnings/ERRORS.md`` relative to CWD.
    now:
        Override timestamp source (mainly for tests).

    Returns
    -------
    ErrorRecord
        The persisted record. ``occurrences`` reflects the post-write value, so
        a brand-new error has ``occurrences == 1``.
    """

    target = _ensure_path(path)
    timestamp = (now or _utcnow()).astimezone(timezone.utc).replace(microsecond=0)

    if _looks_like_exception(error):
        exc = error
        message = f"{type(exc).__name__}: {exc}".strip()
        derived_category = type(exc).__name__
        captured_tb = traceback_text or _format_traceback(exc)
    else:
        message = str(error).strip()
        derived_category = None
        captured_tb = traceback_text or ""

    if not message:
        raise ValueError("error message must not be empty")

    final_category = (category or derived_category or UNCATEGORIZED).strip() or UNCATEGORIZED
    sig = _signature(final_category, message)

    preamble, records = _read_document(target)
    by_sig = {record.signature: record for record in records}

    formatted_context = _format_context(context)
    extra_clean = {str(k): str(v) for k, v in (extra or {}).items()}

    existing = by_sig.get(sig)
    if existing is not None:
        existing.occurrences += 1
        existing.last_seen = timestamp
        existing.message = message  # refresh text in case of small drift
        if formatted_context:
            existing.context = formatted_context
        if captured_tb:
            existing.traceback_text = captured_tb
        if suggested_fix:
            existing.suggested_fix = suggested_fix.strip()
        elif not existing.suggested_fix:
            tips = suggest_fixes(message, category=final_category, path=target, _records=records)
            if tips:
                existing.suggested_fix = tips[0]
        for key, value in extra_clean.items():
            existing.extra[key] = value
        record = existing
    else:
        derived_fix = ""
        if suggested_fix:
            derived_fix = suggested_fix.strip()
        else:
            tips = suggest_fixes(message, category=final_category, path=target, _records=records)
            if tips:
                derived_fix = tips[0]
        record = ErrorRecord(
            signature=sig,
            category=final_category,
            message=message,
            first_seen=timestamp,
            last_seen=timestamp,
            occurrences=1,
            context=formatted_context,
            traceback_text=captured_tb,
            suggested_fix=derived_fix,
            title=_slugify_title(final_category, message),
            extra=extra_clean,
        )
        records.append(record)
        by_sig[sig] = record

    _write_document(target, preamble, list(by_sig.values()))
    return record


def get_recent_errors(
    limit: int = 10,
    *,
    category: str | None = None,
    since: datetime | timedelta | None = None,
    path: str | os.PathLike[str] | None = None,
) -> list[ErrorRecord]:
    """Return the most recently seen errors.

    Parameters
    ----------
    limit:
        Maximum number of records to return. Use a non-positive value to
        return them all.
    category:
        Optional filter; only records whose ``category`` matches (case-insensitive)
        are returned.
    since:
        Either an absolute ``datetime`` or a ``timedelta`` relative to now.
        Records older than this cutoff are skipped.
    path:
        Override the source path.
    """

    target = _ensure_path(path)
    _, records = _read_document(target)

    cutoff: datetime | None = None
    if isinstance(since, timedelta):
        cutoff = _utcnow() - since
    elif isinstance(since, datetime):
        cutoff = since if since.tzinfo else since.replace(tzinfo=timezone.utc)

    filtered: list[ErrorRecord] = []
    for record in records:
        if category and record.category.lower() != category.lower():
            continue
        if cutoff and record.last_seen < cutoff:
            continue
        filtered.append(record)

    filtered.sort(key=lambda r: r.last_seen, reverse=True)
    if limit and limit > 0:
        return filtered[:limit]
    return filtered


def suggest_fixes(
    message: str | BaseException | None = None,
    *,
    category: str | None = None,
    path: str | os.PathLike[str] | None = None,
    limit: int = 5,
    _records: Sequence[ErrorRecord] | None = None,
) -> list[str]:
    """Generate fix suggestions for an error.

    Combines:

    1. Tips harvested from prior records with a matching signature or category.
    2. Pattern-based heuristics from :data:`BUILTIN_PATTERNS`.

    The returned list preserves order and removes duplicates, capped to
    ``limit`` entries.
    """

    if isinstance(message, BaseException):
        text = f"{type(message).__name__}: {message}"
    else:
        text = (message or "").strip()

    suggestions: list[str] = []

    records: Sequence[ErrorRecord]
    if _records is not None:
        records = _records
    else:
        target = _ensure_path(path)
        _, records = _read_document(target)

    if text:
        sig = _signature((category or UNCATEGORIZED), text)
        for record in records:
            if record.signature == sig and record.suggested_fix:
                suggestions.append(
                    f"Seen {record.occurrences}x before "
                    f"(last {_format_ts(record.last_seen)}): {record.suggested_fix}"
                )
                break

    if category:
        for record in records:
            if (
                record.category.lower() == category.lower()
                and record.suggested_fix
                and record.suggested_fix not in "\n".join(suggestions)
            ):
                suggestions.append(
                    f"Same category `{record.category}`: {record.suggested_fix}"
                )
                if len(suggestions) >= limit:
                    break

    if text:
        for pattern, tips in BUILTIN_PATTERNS:
            match = pattern.search(text)
            if not match:
                continue
            for tip in tips:
                try:
                    rendered = tip.format(**match.groupdict())
                except (KeyError, IndexError):
                    rendered = tip
                suggestions.append(rendered)

    seen: set[str] = set()
    deduped: list[str] = []
    for tip in suggestions:
        clean = tip.strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        deduped.append(clean)
        if limit and len(deduped) >= limit:
            break

    if not deduped:
        deduped.append(
            "No matching pattern; capture more context (inputs, env, "
            "stack trace) and rerun to grow the learnings file."
        )

    return deduped


def _color(stream: Any, color: str, text: str) -> str:
    if not getattr(stream, "isatty", lambda: False)():
        return text
    if os.environ.get("NO_COLOR"):
        return text
    return f"{color}{text}{RESET}"


def _cmd_log(args: argparse.Namespace) -> int:
    context: Any = args.context
    if args.context_json:
        try:
            context = json.loads(args.context_json)
        except json.JSONDecodeError as exc:
            print(f"error: --context-json is not valid JSON: {exc}", file=sys.stderr)
            return 2

    extra: dict[str, str] = {}
    for item in args.extra or []:
        if "=" not in item:
            print(f"error: --extra entries must be KEY=VALUE (got {item!r})", file=sys.stderr)
            return 2
        key, value = item.split("=", 1)
        extra[key.strip()] = value.strip()

    try:
        record = log_error(
            args.message,
            category=args.category,
            context=context,
            traceback_text=args.traceback,
            suggested_fix=args.fix,
            extra=extra or None,
            path=args.path,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(_color(sys.stdout, GREEN, "logged: ") + record.summary())
    if record.suggested_fix:
        print(_color(sys.stdout, CYAN, "fix: ") + record.suggested_fix)
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    since: timedelta | None = None
    if args.since_days is not None:
        if args.since_days < 0:
            print("error: --since-days must be >= 0", file=sys.stderr)
            return 2
        since = timedelta(days=args.since_days)

    records = get_recent_errors(
        limit=args.limit,
        category=args.category,
        since=since,
        path=args.path,
    )
    if not records:
        print("(no errors recorded)")
        return 0

    if args.json:
        payload = [
            {
                "signature": r.signature,
                "category": r.category,
                "message": r.message,
                "first_seen": _format_ts(r.first_seen),
                "last_seen": _format_ts(r.last_seen),
                "occurrences": r.occurrences,
                "suggested_fix": r.suggested_fix,
            }
            for r in records
        ]
        print(json.dumps(payload, indent=2))
        return 0

    for record in records:
        print(_color(sys.stdout, YELLOW, record.signature) + "  " + record.summary())
        if record.suggested_fix:
            print("  " + _color(sys.stdout, CYAN, "fix: ") + record.suggested_fix)
    return 0


def _cmd_suggest(args: argparse.Namespace) -> int:
    tips = suggest_fixes(
        args.message,
        category=args.category,
        path=args.path,
        limit=args.limit,
    )
    for tip in tips:
        print("- " + tip)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="error_learning",
        description="Persist and reason about errors learned during OpenClaw runs.",
    )
    parser.add_argument(
        "--path",
        help=f"Errors markdown file (default: {DEFAULT_ERRORS_PATH}).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    log_p = sub.add_parser("log", help="Append an error to the learnings file.")
    log_p.add_argument("--message", "-m", required=True, help="Error message text.")
    log_p.add_argument("--category", "-c", help="Bucket name (e.g. ollama_pull).")
    log_p.add_argument("--context", help="Free-form context string.")
    log_p.add_argument("--context-json", help="Context as a JSON-encoded value.")
    log_p.add_argument("--traceback", help="Pre-formatted traceback text.")
    log_p.add_argument("--fix", help="Manual fix suggestion to record.")
    log_p.add_argument(
        "--extra",
        action="append",
        metavar="KEY=VALUE",
        help="Extra metadata; repeat for multiple entries.",
    )
    log_p.set_defaults(func=_cmd_log)

    list_p = sub.add_parser("list", help="Show recently learned errors.")
    list_p.add_argument("--limit", "-n", type=int, default=10, help="Maximum entries to show.")
    list_p.add_argument("--category", "-c", help="Filter by category.")
    list_p.add_argument("--since-days", type=int, help="Only entries seen within the last N days.")
    list_p.add_argument("--json", action="store_true", help="Emit JSON instead of text.")
    list_p.set_defaults(func=_cmd_list)

    suggest_p = sub.add_parser("suggest", help="Suggest fixes for an error message.")
    suggest_p.add_argument("--message", "-m", required=True, help="Error message text.")
    suggest_p.add_argument("--category", "-c", help="Optional category hint.")
    suggest_p.add_argument("--limit", "-n", type=int, default=5, help="Maximum suggestions.")
    suggest_p.set_defaults(func=_cmd_suggest)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    except OSError as exc:
        print(f"i/o error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
