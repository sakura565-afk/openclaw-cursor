#!/usr/bin/env python3
"""Extract structured conversation data from OpenClaw session transcripts.

This module reads session transcripts produced by OpenClaw (typically stored
under ``~/.openclaw/sessions/``) and emits a normalized, structured
representation that can be consumed by downstream analytics or LLM tooling.

The extractor is deliberately defensive about input shape because session
files in the wild come from multiple OpenClaw versions and may be:

* A single JSON object describing the whole session.
* A JSON Lines (``.jsonl`` / ``.ndjson``) stream, one event per line.
* A directory containing one or more of the above files.

For each session it normalizes:

* user messages
* assistant responses
* tool calls (name, arguments, call id)
* tool results / outcomes (status, output, errors)

It then mines the normalized turns for:

* error patterns (tracebacks, stderr-style errors, non-zero exits, ...)
* successful patterns (tests passing, builds succeeding, edits applied, ...)
* learnings (assistant introspection: "next time", "I learned", "lesson:", ...)

The CLI writes a JSON report and, optionally, a Markdown summary. All paths
are resolved with :class:`pathlib.Path`, which makes the script work on
Windows, macOS, and Linux without modification.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePath
from typing import Any, Iterable, Iterator, Sequence


__all__ = [
    "ToolCall",
    "ToolResult",
    "Turn",
    "SessionTranscript",
    "ExtractionResult",
    "default_sessions_dir",
    "discover_session_files",
    "load_session",
    "extract_session",
    "extract_directory",
    "render_markdown_report",
    "main",
]


# ---------------------------------------------------------------------------
# Pattern catalogues
# ---------------------------------------------------------------------------

# Each pattern entry is ``(label, compiled_regex)``. Keeping them as data
# rather than ad-hoc ``if`` blocks lets callers override or extend the rules
# and keeps the detection logic uniform across content types.

ERROR_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("python_traceback", re.compile(r"Traceback \(most recent call last\):", re.IGNORECASE)),
    ("exception", re.compile(r"\b([A-Z][A-Za-z0-9_]*Error|Exception)\b\s*[:\(]")),
    ("stderr_error", re.compile(r"^\s*(?:error|fatal|panic)[: ]", re.IGNORECASE | re.MULTILINE)),
    ("permission_denied", re.compile(r"permission denied", re.IGNORECASE)),
    ("not_found", re.compile(r"\b(?:no such file or directory|command not found|module not found)\b", re.IGNORECASE)),
    ("timeout", re.compile(r"\b(?:timed? ?out|deadline exceeded)\b", re.IGNORECASE)),
    ("connection_error", re.compile(r"\b(?:connection (?:refused|reset|aborted)|network is unreachable)\b", re.IGNORECASE)),
    ("non_zero_exit", re.compile(r"\b(?:exit(?:ed)? (?:code|status)|returned)\s*[:= ]?\s*(?!0\b)\d+", re.IGNORECASE)),
    ("test_failure", re.compile(r"\b(?:FAIL(?:ED)?|AssertionError|tests? failed|\d+\s+failed)\b")),
    ("syntax_error", re.compile(r"\bSyntaxError\b", re.IGNORECASE)),
    ("http_error", re.compile(r"\bHTTP/\d\.\d\s+(?:4\d\d|5\d\d)\b|\b(?:status(?:_code)?\s*[:=]\s*(?:4\d\d|5\d\d))\b")),
)


SUCCESS_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("tests_passed", re.compile(r"\b(?:all tests pass(?:ed)?|\d+\s+passed(?:,\s*0\s+failed)?|ok\s+\d+\s+tests?)\b", re.IGNORECASE)),
    ("build_success", re.compile(r"\b(?:build succeeded|compiled successfully|build complete)\b", re.IGNORECASE)),
    ("deployed", re.compile(r"\b(?:deploy(?:ed|ment)? (?:succeeded|complete|successful))\b", re.IGNORECASE)),
    ("file_written", re.compile(r"\b(?:file (?:written|created|saved)|wrote\s+\d+\s+bytes?)\b", re.IGNORECASE)),
    ("edit_applied", re.compile(r"\b(?:edit applied|patch applied|changes (?:applied|saved))\b", re.IGNORECASE)),
    ("task_completed", re.compile(r"\b(?:task (?:completed|done)|all done|finished successfully)\b", re.IGNORECASE)),
    ("zero_exit", re.compile(r"\bexit(?:ed)?\s+(?:code|status)\s*[:= ]?\s*0\b", re.IGNORECASE)),
    ("ok_status", re.compile(r"\b(?:status\s*[:=]\s*(?:ok|success|succeeded))\b", re.IGNORECASE)),
)


LEARNING_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("explicit_learning", re.compile(r"\b(?:i (?:have )?learned|learned that|takeaway|lesson learned|key insight)\b[:\-]?\s*(.+)", re.IGNORECASE)),
    ("note_to_self", re.compile(r"\b(?:note to self|remember(?: that| to)?)\b[:\-]?\s*(.+)", re.IGNORECASE)),
    ("next_time", re.compile(r"\b(?:next time|in (?:the )?future|going forward)\b[:\-,]?\s*(.+)", re.IGNORECASE)),
    ("root_cause", re.compile(r"\b(?:root cause|the (?:real )?issue (?:was|is)|turned out to be)\b[:\-]?\s*(.+)", re.IGNORECASE)),
    ("fix_summary", re.compile(r"\b(?:the fix (?:was|is)|fixed by|resolved by)\b[:\-]?\s*(.+)", re.IGNORECASE)),
    ("avoid", re.compile(r"\b(?:avoid|do not|don't)\b\s+([^.\n]{8,200})", re.IGNORECASE)),
)


# Roles that should be treated as "user input" even if a transcript labels
# them differently. Lowercase comparisons are used everywhere.
USER_ROLES = frozenset({"user", "human", "you", "prompt"})
ASSISTANT_ROLES = frozenset({"assistant", "ai", "model", "claude", "openclaw"})
SYSTEM_ROLES = frozenset({"system", "developer"})
TOOL_ROLES = frozenset({"tool", "tool_result", "function", "observation"})


# Field name candidates used when destructuring heterogeneous payloads. The
# loaders walk these in order until a value is found.
_CONTENT_FIELDS: tuple[str, ...] = ("content", "text", "message", "body", "value", "output")
_TIMESTAMP_FIELDS: tuple[str, ...] = ("timestamp", "time", "created_at", "createdAt", "ts", "date")
_ROLE_FIELDS: tuple[str, ...] = ("role", "speaker", "author", "from", "type")
_TOOL_NAME_FIELDS: tuple[str, ...] = ("name", "tool", "tool_name", "function", "function_name")
_TOOL_INPUT_FIELDS: tuple[str, ...] = ("input", "arguments", "args", "parameters", "params")
_TOOL_OUTPUT_FIELDS: tuple[str, ...] = ("output", "result", "content", "stdout", "data")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class ToolCall:
    """A single tool invocation issued by the assistant."""

    call_id: str | None
    name: str
    arguments: Any
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "call_id": self.call_id,
            "name": self.name,
            "arguments": self.arguments,
        }


@dataclass
class ToolResult:
    """The recorded outcome of a tool invocation."""

    call_id: str | None
    name: str | None
    status: str
    output: str
    exit_code: int | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "call_id": self.call_id,
            "name": self.name,
            "status": self.status,
            "exit_code": self.exit_code,
            "output": self.output,
        }


@dataclass
class Turn:
    """One conversational turn within a session transcript."""

    index: int
    role: str
    content: str
    timestamp: datetime | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "role": self.role,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "content": self.content,
            "tool_calls": [call.to_dict() for call in self.tool_calls],
            "tool_results": [result.to_dict() for result in self.tool_results],
            "metadata": self.metadata,
        }


@dataclass
class SessionTranscript:
    """A normalized transcript loaded from a session file."""

    session_id: str
    source_path: Path
    turns: list[Turn]
    started_at: datetime | None = None
    ended_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "source_path": str(self.source_path),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "metadata": self.metadata,
            "turns": [turn.to_dict() for turn in self.turns],
        }

    @property
    def duration_seconds(self) -> float | None:
        if self.started_at and self.ended_at and self.ended_at >= self.started_at:
            return (self.ended_at - self.started_at).total_seconds()
        return None


@dataclass
class ExtractionResult:
    """Per-session extraction output, including detected patterns."""

    transcript: SessionTranscript
    user_messages: list[Turn]
    assistant_messages: list[Turn]
    tool_calls: list[ToolCall]
    tool_results: list[ToolResult]
    error_patterns: list[dict[str, Any]]
    success_patterns: list[dict[str, Any]]
    learnings: list[dict[str, Any]]

    def summary(self) -> dict[str, Any]:
        return {
            "session_id": self.transcript.session_id,
            "source_path": str(self.transcript.source_path),
            "turn_count": len(self.transcript.turns),
            "user_message_count": len(self.user_messages),
            "assistant_message_count": len(self.assistant_messages),
            "tool_call_count": len(self.tool_calls),
            "tool_result_count": len(self.tool_results),
            "error_pattern_count": len(self.error_patterns),
            "success_pattern_count": len(self.success_patterns),
            "learning_count": len(self.learnings),
            "started_at": self.transcript.started_at.isoformat() if self.transcript.started_at else None,
            "ended_at": self.transcript.ended_at.isoformat() if self.transcript.ended_at else None,
            "duration_seconds": self.transcript.duration_seconds,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary(),
            "transcript": self.transcript.to_dict(),
            "tool_calls": [call.to_dict() for call in self.tool_calls],
            "tool_results": [result.to_dict() for result in self.tool_results],
            "error_patterns": self.error_patterns,
            "success_patterns": self.success_patterns,
            "learnings": self.learnings,
        }


# ---------------------------------------------------------------------------
# Path discovery
# ---------------------------------------------------------------------------


def default_sessions_dir() -> Path:
    """Return the default OpenClaw sessions directory.

    Resolution order:

    1. ``$OPENCLAW_SESSIONS_DIR`` if set.
    2. ``%APPDATA%/openclaw/sessions`` on Windows when ``APPDATA`` exists.
    3. ``~/.openclaw/sessions`` on every platform as a final fallback.

    The returned path uses :class:`pathlib.Path` so callers can join, compare,
    and serialize it portably (forward slashes are used in JSON output even on
    Windows because :func:`Path.as_posix` is applied at serialization time).
    """

    env_override = os.environ.get("OPENCLAW_SESSIONS_DIR")
    if env_override:
        return Path(env_override).expanduser()

    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "openclaw" / "sessions"

    return Path.home() / ".openclaw" / "sessions"


def discover_session_files(target: Path) -> list[Path]:
    """Return a sorted list of session files under ``target``.

    ``target`` may be either a directory containing session files or a single
    file. Recognized extensions are ``.json``, ``.jsonl``, and ``.ndjson``.
    Hidden files (``.``-prefixed) are skipped to avoid picking up editor swap
    files. Sorting is deterministic so downstream reports are stable.
    """

    target = target.expanduser()
    if not target.exists():
        return []

    if target.is_file():
        return [target]

    discovered: list[Path] = []
    for pattern in ("*.json", "*.jsonl", "*.ndjson"):
        for path in target.rglob(pattern):
            if path.is_file() and not path.name.startswith("."):
                discovered.append(path)

    return sorted(set(discovered), key=lambda p: p.as_posix().lower())


# ---------------------------------------------------------------------------
# Loading & normalization
# ---------------------------------------------------------------------------


def _is_jsonl(path: Path) -> bool:
    return path.suffix.lower() in {".jsonl", ".ndjson"}


def _read_text(path: Path) -> str:
    # ``utf-8-sig`` transparently strips a BOM that Windows editors sometimes
    # add. ``errors="replace"`` keeps the loader resilient to corrupted bytes
    # rather than failing the whole batch on a single bad character.
    return path.read_text(encoding="utf-8-sig", errors="replace")


def _parse_jsonl(text: str, source: Path) -> list[Any]:
    records: list[Any] = []
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        stripped = raw_line.strip()
        if not stripped:
            continue
        try:
            records.append(json.loads(stripped))
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Invalid JSON on line {line_number} of {source}: {exc.msg}"
            ) from exc
    return records


def _coerce_payload(path: Path) -> Any:
    text = _read_text(path)
    if not text.strip():
        return []
    if _is_jsonl(path):
        return _parse_jsonl(text, path)

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Fall back to JSONL parsing when a ``.json`` file actually contains
        # one event per line. Many CLI tools do this in practice.
        return _parse_jsonl(text, path)


def _first_present(payload: dict[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        if key in payload and payload[key] is not None:
            return payload[key]
    return None


def _coerce_role(raw_role: Any) -> str:
    if raw_role is None:
        return "unknown"
    role = str(raw_role).strip().lower()
    if role in USER_ROLES:
        return "user"
    if role in ASSISTANT_ROLES:
        return "assistant"
    if role in SYSTEM_ROLES:
        return "system"
    if role in TOOL_ROLES:
        return "tool"
    return role or "unknown"


def _stringify_content(value: Any) -> str:
    """Flatten heterogeneous content shapes into a single string."""

    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        chunks: list[str] = []
        for item in value:
            if isinstance(item, dict):
                # Anthropic-style content blocks: {"type": "text", "text": "..."}
                # OpenAI-style:                   {"type": "text", "text": {"value": "..."}}
                text_value = item.get("text") or item.get("content")
                if isinstance(text_value, dict):
                    text_value = text_value.get("value")
                if text_value is None and item.get("type") == "tool_use":
                    text_value = json.dumps(
                        {"tool": item.get("name"), "input": item.get("input")},
                        ensure_ascii=False,
                    )
                if text_value is None and item.get("type") == "tool_result":
                    text_value = _stringify_content(item.get("content"))
                if text_value is None:
                    text_value = json.dumps(item, ensure_ascii=False, sort_keys=True)
                chunks.append(_stringify_content(text_value))
            else:
                chunks.append(_stringify_content(item))
        return "\n".join(chunk for chunk in chunks if chunk)
    if isinstance(value, dict):
        nested = _first_present(value, _CONTENT_FIELDS)
        if nested is not None:
            return _stringify_content(nested)
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _parse_timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        # Heuristic: values larger than ~10^12 are milliseconds, otherwise
        # they are POSIX seconds. Both branches return a UTC-aware datetime.
        seconds = value / 1000.0 if value > 1_000_000_000_000 else float(value)
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d"):
                try:
                    return datetime.strptime(text, fmt)
                except ValueError:
                    continue
    return None


def _extract_tool_calls(payload: dict[str, Any]) -> list[ToolCall]:
    calls: list[ToolCall] = []
    raw_calls = payload.get("tool_calls") or payload.get("toolCalls") or []
    if isinstance(raw_calls, dict):
        raw_calls = [raw_calls]
    if isinstance(raw_calls, list):
        for entry in raw_calls:
            if not isinstance(entry, dict):
                continue
            function = entry.get("function") if isinstance(entry.get("function"), dict) else None
            name = (
                _first_present(entry, _TOOL_NAME_FIELDS)
                or (function.get("name") if function else None)
                or "unknown"
            )
            arguments = (
                _first_present(entry, _TOOL_INPUT_FIELDS)
                or (function.get("arguments") if function else None)
            )
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except json.JSONDecodeError:
                    pass
            calls.append(
                ToolCall(
                    call_id=entry.get("id") or entry.get("call_id"),
                    name=str(name),
                    arguments=arguments,
                    raw=entry,
                )
            )

    # Inline Anthropic-style ``tool_use`` blocks live inside ``content``.
    content = payload.get("content")
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                calls.append(
                    ToolCall(
                        call_id=block.get("id"),
                        name=str(block.get("name", "unknown")),
                        arguments=block.get("input"),
                        raw=block,
                    )
                )
    return calls


def _classify_tool_status(output: str, payload: dict[str, Any]) -> tuple[str, int | None]:
    explicit_status = payload.get("status") or payload.get("state")
    exit_code = payload.get("exit_code")
    if exit_code is None:
        exit_code = payload.get("exitCode")
    if isinstance(exit_code, str) and exit_code.isdigit():
        exit_code = int(exit_code)
    if not isinstance(exit_code, int):
        exit_code = None

    if isinstance(explicit_status, str) and explicit_status.strip():
        status = explicit_status.strip().lower()
    elif payload.get("is_error") or payload.get("error"):
        status = "error"
    elif exit_code is not None:
        status = "ok" if exit_code == 0 else "error"
    elif _matches_any(output, ERROR_PATTERNS):
        status = "error"
    else:
        status = "ok"
    return status, exit_code


def _extract_tool_results(payload: dict[str, Any]) -> list[ToolResult]:
    results: list[ToolResult] = []

    def _build(result_payload: dict[str, Any]) -> ToolResult:
        output_value = _first_present(result_payload, _TOOL_OUTPUT_FIELDS)
        output_str = _stringify_content(output_value)
        status, exit_code = _classify_tool_status(output_str, result_payload)
        return ToolResult(
            call_id=result_payload.get("call_id") or result_payload.get("tool_call_id") or result_payload.get("id"),
            name=result_payload.get("name") or result_payload.get("tool_name"),
            status=status,
            output=output_str,
            exit_code=exit_code,
            raw=result_payload,
        )

    raw_results = payload.get("tool_results") or payload.get("toolResults")
    if isinstance(raw_results, dict):
        raw_results = [raw_results]
    if isinstance(raw_results, list):
        for entry in raw_results:
            if isinstance(entry, dict):
                results.append(_build(entry))

    role = _coerce_role(_first_present(payload, _ROLE_FIELDS))
    if role == "tool" and not raw_results:
        results.append(_build(payload))

    content = payload.get("content")
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                results.append(_build(block))

    return results


def _normalize_turn(index: int, payload: Any) -> Turn | None:
    """Convert an arbitrary message payload into a :class:`Turn`.

    Returns ``None`` when the payload is empty or cannot be interpreted as a
    message (for example, plain metadata objects without a role or content).
    """

    if not isinstance(payload, dict):
        if isinstance(payload, str) and payload.strip():
            return Turn(index=index, role="unknown", content=payload.strip())
        return None

    role = _coerce_role(_first_present(payload, _ROLE_FIELDS))
    content = _stringify_content(_first_present(payload, _CONTENT_FIELDS))
    timestamp = _parse_timestamp(_first_present(payload, _TIMESTAMP_FIELDS))

    tool_calls = _extract_tool_calls(payload)
    tool_results = _extract_tool_results(payload)

    if not content and not tool_calls and not tool_results:
        return None

    metadata: dict[str, Any] = {}
    for key in ("model", "stop_reason", "stopReason", "usage", "id"):
        if key in payload and payload[key] is not None:
            metadata[key] = payload[key]

    return Turn(
        index=index,
        role=role,
        content=content,
        timestamp=timestamp,
        tool_calls=tool_calls,
        tool_results=tool_results,
        metadata=metadata,
    )


def _iter_message_candidates(payload: Any) -> Iterator[Any]:
    """Yield candidate message dicts from a heterogeneous transcript root."""

    if isinstance(payload, list):
        for item in payload:
            yield from _iter_message_candidates(item)
        return

    if not isinstance(payload, dict):
        yield payload
        return

    for key in ("messages", "events", "turns", "history", "transcript", "conversation"):
        nested = payload.get(key)
        if isinstance(nested, list):
            for item in nested:
                yield from _iter_message_candidates(item)
            return

    yield payload


def _session_metadata(payload: Any, fallback_id: str) -> tuple[str, dict[str, Any]]:
    if not isinstance(payload, dict):
        return fallback_id, {}
    session_id = (
        payload.get("session_id")
        or payload.get("id")
        or payload.get("uuid")
        or fallback_id
    )
    metadata: dict[str, Any] = {}
    for key in ("model", "workspace", "cwd", "agent", "version", "title", "name"):
        value = payload.get(key)
        if value is not None:
            metadata[key] = value
    return str(session_id), metadata


def load_session(path: Path) -> SessionTranscript:
    """Load and normalize a single session transcript from disk."""

    payload = _coerce_payload(path)
    fallback_id = path.stem or path.name
    session_id, metadata = _session_metadata(payload, fallback_id)

    turns: list[Turn] = []
    next_index = 0
    for candidate in _iter_message_candidates(payload):
        turn = _normalize_turn(next_index, candidate)
        if turn is None:
            continue
        turns.append(turn)
        next_index += 1

    timestamps = [turn.timestamp for turn in turns if turn.timestamp is not None]
    started_at = min(timestamps) if timestamps else None
    ended_at = max(timestamps) if timestamps else None

    if isinstance(payload, dict):
        explicit_start = _parse_timestamp(payload.get("started_at") or payload.get("createdAt") or payload.get("created_at"))
        explicit_end = _parse_timestamp(payload.get("ended_at") or payload.get("updatedAt") or payload.get("updated_at"))
        started_at = explicit_start or started_at
        ended_at = explicit_end or ended_at

    return SessionTranscript(
        session_id=session_id,
        source_path=path,
        turns=turns,
        started_at=started_at,
        ended_at=ended_at,
        metadata=metadata,
    )


# ---------------------------------------------------------------------------
# Pattern detection
# ---------------------------------------------------------------------------


def _matches_any(text: str, patterns: Iterable[tuple[str, re.Pattern[str]]]) -> bool:
    return any(pattern.search(text) for _, pattern in patterns)


def _scan_text(
    text: str,
    patterns: Iterable[tuple[str, re.Pattern[str]]],
    max_snippet_length: int = 240,
) -> list[dict[str, Any]]:
    if not text:
        return []
    matches: list[dict[str, Any]] = []
    for label, pattern in patterns:
        for match in pattern.finditer(text):
            start = max(match.start() - 40, 0)
            end = min(match.end() + 80, len(text))
            snippet = text[start:end].strip()
            if len(snippet) > max_snippet_length:
                snippet = snippet[: max_snippet_length - 3] + "..."
            captured = match.group(1).strip() if match.groups() and match.group(1) else None
            matches.append(
                {
                    "label": label,
                    "match": match.group(0).strip(),
                    "captured": captured,
                    "snippet": snippet,
                }
            )
    return matches


def _detect_in_turn(
    turn: Turn,
    patterns: Iterable[tuple[str, re.Pattern[str]]],
    *,
    include_tool_output: bool,
) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for entry in _scan_text(turn.content, patterns):
        entry.update({"turn_index": turn.index, "role": turn.role, "source": "content"})
        matches.append(entry)
    if include_tool_output:
        for tool_result in turn.tool_results:
            for entry in _scan_text(tool_result.output, patterns):
                entry.update(
                    {
                        "turn_index": turn.index,
                        "role": turn.role,
                        "source": "tool_result",
                        "tool_name": tool_result.name,
                        "tool_status": tool_result.status,
                    }
                )
                matches.append(entry)
    return matches


def detect_error_patterns(turns: Sequence[Turn]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for turn in turns:
        findings.extend(_detect_in_turn(turn, ERROR_PATTERNS, include_tool_output=True))
        for tool_result in turn.tool_results:
            if tool_result.status == "error" and not any(
                entry.get("source") == "tool_result"
                and entry.get("turn_index") == turn.index
                and entry.get("tool_name") == tool_result.name
                for entry in findings
            ):
                findings.append(
                    {
                        "label": "tool_failure",
                        "match": tool_result.status,
                        "captured": None,
                        "snippet": tool_result.output[:240],
                        "turn_index": turn.index,
                        "role": turn.role,
                        "source": "tool_result",
                        "tool_name": tool_result.name,
                        "tool_status": tool_result.status,
                    }
                )
    return findings


def detect_success_patterns(turns: Sequence[Turn]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for turn in turns:
        findings.extend(_detect_in_turn(turn, SUCCESS_PATTERNS, include_tool_output=True))
    return findings


def detect_learnings(turns: Sequence[Turn]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for turn in turns:
        if turn.role != "assistant":
            continue
        findings.extend(_detect_in_turn(turn, LEARNING_PATTERNS, include_tool_output=False))
    return findings


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


def extract_session(transcript: SessionTranscript) -> ExtractionResult:
    """Run pattern detection over a normalized transcript."""

    user_messages = [turn for turn in transcript.turns if turn.role == "user"]
    assistant_messages = [turn for turn in transcript.turns if turn.role == "assistant"]

    tool_calls = [call for turn in transcript.turns for call in turn.tool_calls]
    tool_results = [result for turn in transcript.turns for result in turn.tool_results]

    return ExtractionResult(
        transcript=transcript,
        user_messages=user_messages,
        assistant_messages=assistant_messages,
        tool_calls=tool_calls,
        tool_results=tool_results,
        error_patterns=detect_error_patterns(transcript.turns),
        success_patterns=detect_success_patterns(transcript.turns),
        learnings=detect_learnings(transcript.turns),
    )


def extract_directory(target: Path) -> list[ExtractionResult]:
    """Discover and extract every session under ``target``."""

    results: list[ExtractionResult] = []
    for session_path in discover_session_files(target):
        try:
            transcript = load_session(session_path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(
                f"warning: skipping {session_path}: {exc}",
                file=sys.stderr,
            )
            continue
        results.append(extract_session(transcript))
    return results


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _aggregate_pattern_labels(
    results: Iterable[ExtractionResult],
    attribute: str,
) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for result in results:
        for entry in getattr(result, attribute):
            counter[str(entry.get("label", "unknown"))] += 1
    return dict(counter.most_common())


def _aggregate_tool_usage(results: Iterable[ExtractionResult]) -> dict[str, dict[str, int]]:
    usage: dict[str, Counter[str]] = {}
    for result in results:
        for tool_result in result.tool_results:
            tool_name = tool_result.name or "unknown"
            bucket = usage.setdefault(tool_name, Counter())
            bucket["total"] += 1
            bucket[tool_result.status] += 1
    return {name: dict(counter) for name, counter in usage.items()}


def build_aggregate_report(
    results: Sequence[ExtractionResult],
    sessions_dir: Path,
) -> dict[str, Any]:
    return {
        "generated_at": datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
        "sessions_dir": sessions_dir.as_posix(),
        "session_count": len(results),
        "totals": {
            "turns": sum(len(result.transcript.turns) for result in results),
            "user_messages": sum(len(result.user_messages) for result in results),
            "assistant_messages": sum(len(result.assistant_messages) for result in results),
            "tool_calls": sum(len(result.tool_calls) for result in results),
            "tool_results": sum(len(result.tool_results) for result in results),
            "error_patterns": sum(len(result.error_patterns) for result in results),
            "success_patterns": sum(len(result.success_patterns) for result in results),
            "learnings": sum(len(result.learnings) for result in results),
        },
        "error_label_counts": _aggregate_pattern_labels(results, "error_patterns"),
        "success_label_counts": _aggregate_pattern_labels(results, "success_patterns"),
        "learning_label_counts": _aggregate_pattern_labels(results, "learnings"),
        "tool_usage": _aggregate_tool_usage(results),
        "sessions": [result.to_dict() for result in results],
    }


def render_markdown_report(report: dict[str, Any]) -> str:
    """Render a human-friendly Markdown summary from an aggregate report."""

    totals = report["totals"]
    lines: list[str] = [
        "# OpenClaw Conversation Extraction Report",
        "",
        f"- Generated at: {report['generated_at']}",
        f"- Sessions directory: `{report['sessions_dir']}`",
        f"- Sessions analyzed: {report['session_count']}",
        "",
        "## Totals",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        f"| Turns | {totals['turns']} |",
        f"| User messages | {totals['user_messages']} |",
        f"| Assistant messages | {totals['assistant_messages']} |",
        f"| Tool calls | {totals['tool_calls']} |",
        f"| Tool results | {totals['tool_results']} |",
        f"| Error patterns | {totals['error_patterns']} |",
        f"| Success patterns | {totals['success_patterns']} |",
        f"| Learnings | {totals['learnings']} |",
        "",
    ]

    def _add_label_table(title: str, counts: dict[str, int]) -> None:
        lines.append(f"## {title}")
        lines.append("")
        if not counts:
            lines.extend(["_No matches detected._", ""])
            return
        lines.extend(["| Label | Count |", "| --- | ---: |"])
        for label, count in counts.items():
            lines.append(f"| `{label}` | {count} |")
        lines.append("")

    _add_label_table("Error Pattern Labels", report["error_label_counts"])
    _add_label_table("Success Pattern Labels", report["success_label_counts"])
    _add_label_table("Learning Labels", report["learning_label_counts"])

    lines.extend(["## Tool Usage", ""])
    if not report["tool_usage"]:
        lines.extend(["_No tool calls were observed._", ""])
    else:
        lines.extend(["| Tool | Total | OK | Error | Other |", "| --- | ---: | ---: | ---: | ---: |"])
        for tool, counts in report["tool_usage"].items():
            total = counts.get("total", 0)
            ok = counts.get("ok", 0)
            error = counts.get("error", 0)
            other = total - ok - error
            lines.append(f"| `{tool}` | {total} | {ok} | {error} | {other} |")
        lines.append("")

    lines.extend(["## Sessions", ""])
    if not report["sessions"]:
        lines.append("_No sessions were extracted._")
    else:
        for session in report["sessions"]:
            summary = session["summary"]
            lines.append(
                f"### `{summary['session_id']}`\n"
                f"- Source: `{summary['source_path']}`\n"
                f"- Turns: {summary['turn_count']} "
                f"(user: {summary['user_message_count']}, "
                f"assistant: {summary['assistant_message_count']})\n"
                f"- Tool calls: {summary['tool_call_count']}, "
                f"errors: {summary['error_pattern_count']}, "
                f"successes: {summary['success_pattern_count']}, "
                f"learnings: {summary['learning_count']}\n"
            )

    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class _JSONEncoder(json.JSONEncoder):
    """JSON encoder with first-class support for the dataclass model above."""

    def default(self, o: Any) -> Any:
        if isinstance(o, datetime):
            return o.isoformat()
        if isinstance(o, (Path, PurePath)):
            return o.as_posix()
        if dataclasses.is_dataclass(o):
            return dataclasses.asdict(o)
        if isinstance(o, set):
            return sorted(o)
        return super().default(o)


def _resolve_output_path(
    raw: str | None,
    *,
    default_name: str,
    default_dir: Path,
) -> Path:
    if raw:
        return Path(raw).expanduser()
    return default_dir / default_name


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="conversation_extractor",
        description="Extract structured conversation data from OpenClaw session transcripts.",
    )
    parser.add_argument(
        "--sessions-dir",
        default=None,
        help=(
            "Path to a directory or file containing OpenClaw session transcripts. "
            "Defaults to $OPENCLAW_SESSIONS_DIR or ~/.openclaw/sessions."
        ),
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Path to write the JSON report (defaults to logs/conversation_extraction_<date>.json).",
    )
    parser.add_argument(
        "--markdown",
        default=None,
        help="Optional path to write a Markdown summary report.",
    )
    parser.add_argument(
        "--logs-dir",
        default="logs",
        help="Directory used for default output locations (default: logs).",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress the console summary.",
    )
    return parser.parse_args(argv)


def _print_console_summary(report: dict[str, Any], output_path: Path, markdown_path: Path | None) -> None:
    totals = report["totals"]
    print("OpenClaw Conversation Extractor")
    print("================================")
    print(f"Sessions dir : {report['sessions_dir']}")
    print(f"Sessions     : {report['session_count']}")
    print(
        f"Turns        : {totals['turns']} "
        f"(user: {totals['user_messages']}, assistant: {totals['assistant_messages']})"
    )
    print(
        f"Tool activity: calls={totals['tool_calls']} results={totals['tool_results']}"
    )
    print(
        f"Patterns     : errors={totals['error_patterns']} "
        f"successes={totals['success_patterns']} learnings={totals['learnings']}"
    )
    print(f"JSON report  : {output_path.as_posix()}")
    if markdown_path:
        print(f"Markdown     : {markdown_path.as_posix()}")


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)

    sessions_dir = Path(args.sessions_dir).expanduser() if args.sessions_dir else default_sessions_dir()
    logs_dir = Path(args.logs_dir).expanduser()
    logs_dir.mkdir(parents=True, exist_ok=True)

    today = datetime.now(tz=timezone.utc).strftime("%Y%m%d")
    output_path = _resolve_output_path(
        args.output,
        default_name=f"conversation_extraction_{today}.json",
        default_dir=logs_dir,
    )
    markdown_path = Path(args.markdown).expanduser() if args.markdown else None

    if not sessions_dir.exists():
        print(
            f"error: sessions directory not found: {sessions_dir}",
            file=sys.stderr,
        )
        return 1

    results = extract_directory(sessions_dir)
    report = build_aggregate_report(results, sessions_dir)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, indent=2, cls=_JSONEncoder),
        encoding="utf-8",
    )

    if markdown_path is not None:
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.write_text(render_markdown_report(report), encoding="utf-8")

    if not args.quiet:
        _print_console_summary(report, output_path, markdown_path)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
