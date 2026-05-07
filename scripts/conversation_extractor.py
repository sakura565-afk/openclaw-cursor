#!/usr/bin/env python3
"""Extract structured conversation data from OpenClaw session transcripts.

This module reads session transcripts produced by OpenClaw (typically stored
under ``~/.openclaw/sessions/``) and emits a normalized, analysis-friendly
representation of each conversation:

    * user messages
    * assistant responses
    * tool calls and their outcomes
    * detected error patterns, success patterns, and learnings

The reader is intentionally permissive about input format because session
storage layouts vary across OpenClaw versions. It supports:

    * JSONL files where each line is a message/event object.
    * JSON files containing a single conversation object with a
      ``messages`` / ``turns`` / ``events`` / ``conversation`` array.
    * JSON files containing a top-level array of message objects.

Path handling uses :mod:`pathlib` so the script works correctly on Linux,
macOS, and Windows. The default session directory resolves to
``%USERPROFILE%\\.openclaw\\sessions`` on Windows and
``$HOME/.openclaw/sessions`` on POSIX systems.

Run ``python -m scripts.conversation_extractor --help`` for CLI usage.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePath
from typing import Any, Iterable, Iterator, Sequence

LOGGER = logging.getLogger("conversation_extractor")

DEFAULT_SESSIONS_DIR = Path("~/.openclaw/sessions")
DEFAULT_SESSION_GLOBS: tuple[str, ...] = (
    "*.json",
    "*.jsonl",
    "*.ndjson",
    "**/*.json",
    "**/*.jsonl",
    "**/*.ndjson",
)

ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"
ROLE_TOOL = "tool"
ROLE_SYSTEM = "system"
KNOWN_ROLES = {ROLE_USER, ROLE_ASSISTANT, ROLE_TOOL, ROLE_SYSTEM}

OUTCOME_SUCCESS = "success"
OUTCOME_ERROR = "error"
OUTCOME_UNKNOWN = "unknown"

ERROR_KEYWORDS: tuple[str, ...] = (
    "error",
    "exception",
    "traceback",
    "failed",
    "failure",
    "timeout",
    "timed out",
    "permission denied",
    "not found",
    "stack trace",
    "command not found",
    "syntaxerror",
    "typeerror",
    "valueerror",
    "keyerror",
    "modulenotfounderror",
)

SUCCESS_KEYWORDS: tuple[str, ...] = (
    "ok",
    "success",
    "succeeded",
    "completed",
    "done",
    "passing",
    "passed",
)

# Patterns capture the entire phrase up to end-of-line or a sentence
# terminator followed by whitespace/EOL, so identifiers like "Path.iterdir"
# are preserved.
_LEARNING_TAIL = r"(.+?)(?:$|\n|\.(?=\s|$)|!(?=\s|$)|\?(?=\s|$))"
LEARNING_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(?im)\b(?:lessons? learned|learning|takeaway|key takeaway|"
        r"note to self|important|tip)\s*[:\-]\s*" + _LEARNING_TAIL
    ),
    re.compile(r"(?im)\bI(?:'ve| have)? learned (?:that\s+)?" + _LEARNING_TAIL),
    re.compile(r"(?im)\bnext time(?:,)?\s+" + _LEARNING_TAIL),
    re.compile(r"(?im)\bremember(?: that)?[:\s]+" + _LEARNING_TAIL),
)

CODE_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
WHITESPACE_RE = re.compile(r"\s+")


@dataclass
class ToolCall:
    """A single tool invocation made by the assistant."""

    tool_name: str
    arguments: Any = None
    result: Any = None
    status: str = OUTCOME_UNKNOWN
    duration_ms: float | None = None
    call_id: str | None = None
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "arguments": self.arguments,
            "result": self.result,
            "status": self.status,
            "duration_ms": self.duration_ms,
            "call_id": self.call_id,
            "error_message": self.error_message,
        }


@dataclass
class Turn:
    """A normalized conversation turn (single message / event)."""

    index: int
    role: str
    content: str
    timestamp: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    outcome: str = OUTCOME_UNKNOWN
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp,
            "tool_calls": [call.to_dict() for call in self.tool_calls],
            "outcome": self.outcome,
        }


@dataclass
class Session:
    """A parsed session transcript with normalized turns."""

    session_id: str
    source_path: str
    started_at: str | None = None
    ended_at: str | None = None
    turns: list[Turn] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    parse_warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "source_path": self.source_path,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "turns": [turn.to_dict() for turn in self.turns],
            "metadata": self.metadata,
            "parse_warnings": self.parse_warnings,
        }

    @property
    def user_turns(self) -> list[Turn]:
        return [turn for turn in self.turns if turn.role == ROLE_USER]

    @property
    def assistant_turns(self) -> list[Turn]:
        return [turn for turn in self.turns if turn.role == ROLE_ASSISTANT]

    @property
    def tool_turns(self) -> list[Turn]:
        return [turn for turn in self.turns if turn.role == ROLE_TOOL]

    @property
    def all_tool_calls(self) -> list[ToolCall]:
        calls: list[ToolCall] = []
        for turn in self.turns:
            calls.extend(turn.tool_calls)
        return calls


def default_sessions_dir() -> Path:
    """Return the default OpenClaw session directory for the current user.

    Uses :func:`Path.expanduser` so that ``~`` resolves to ``%USERPROFILE%``
    on Windows and ``$HOME`` on POSIX systems.
    """

    return DEFAULT_SESSIONS_DIR.expanduser()


def resolve_path(path_value: str | os.PathLike[str]) -> Path:
    """Expand user references and resolve a path in a cross-platform way."""

    path = Path(os.fspath(path_value)).expanduser()
    try:
        return path.resolve()
    except OSError:
        return path


def _coerce_text(value: Any) -> str:
    """Convert arbitrary message content into a plain-text string.

    Supports the common shapes used by chat APIs:

    * ``str`` -> returned as-is
    * ``list`` of dicts with ``text`` / ``content`` / ``value`` keys
      (e.g. multimodal content blocks) -> joined with newlines.
    * ``dict`` with a ``text`` key -> the text value.
    * Any other value -> JSON-serialized representation.
    """

    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                for key in ("text", "content", "value", "output", "message"):
                    if key in item and isinstance(item[key], (str, int, float)):
                        parts.append(str(item[key]))
                        break
                else:
                    parts.append(json.dumps(item, sort_keys=True, default=str))
            else:
                parts.append(str(item))
        return "\n".join(parts)
    if isinstance(value, dict):
        for key in ("text", "content", "value", "output", "message"):
            if key in value and isinstance(value[key], (str, int, float)):
                return str(value[key])
        return json.dumps(value, sort_keys=True, default=str)
    return str(value)


def _normalize_role(raw_role: Any) -> str:
    if not isinstance(raw_role, str):
        return ROLE_SYSTEM
    role = raw_role.strip().lower()
    aliases = {
        "human": ROLE_USER,
        "you": ROLE_USER,
        "prompt": ROLE_USER,
        "ai": ROLE_ASSISTANT,
        "model": ROLE_ASSISTANT,
        "bot": ROLE_ASSISTANT,
        "agent": ROLE_ASSISTANT,
        "tool_result": ROLE_TOOL,
        "tool-output": ROLE_TOOL,
        "function": ROLE_TOOL,
        "function_call": ROLE_TOOL,
    }
    role = aliases.get(role, role)
    if role in KNOWN_ROLES:
        return role
    return ROLE_SYSTEM


def _parse_tool_calls(raw_calls: Any) -> list[ToolCall]:
    if not isinstance(raw_calls, list):
        return []
    parsed: list[ToolCall] = []
    for index, raw in enumerate(raw_calls):
        if not isinstance(raw, dict):
            continue
        function_block = raw.get("function") if isinstance(raw.get("function"), dict) else None
        tool_name = (
            raw.get("tool_name")
            or raw.get("name")
            or raw.get("tool")
            or (function_block.get("name") if function_block else None)
            or "unknown_tool"
        )
        arguments = (
            raw.get("arguments")
            or raw.get("args")
            or raw.get("input")
            or raw.get("parameters")
            or (function_block.get("arguments") if function_block else None)
        )
        result = (
            raw.get("result")
            or raw.get("output")
            or raw.get("response")
            or raw.get("return_value")
        )
        status = str(raw.get("status") or raw.get("outcome") or OUTCOME_UNKNOWN).lower()
        if status not in {OUTCOME_SUCCESS, OUTCOME_ERROR, OUTCOME_UNKNOWN}:
            status = OUTCOME_ERROR if "error" in status or "fail" in status else status
        if status == OUTCOME_UNKNOWN and (raw.get("error") or raw.get("error_message")):
            status = OUTCOME_ERROR
        if status == OUTCOME_UNKNOWN and isinstance(result, str) and _looks_like_error(result):
            status = OUTCOME_ERROR
        duration = raw.get("duration_ms")
        if duration is None and isinstance(raw.get("duration"), (int, float)):
            duration = float(raw["duration"]) * 1000.0
        try:
            duration_ms = float(duration) if duration is not None else None
        except (TypeError, ValueError):
            duration_ms = None
        error_message = raw.get("error") or raw.get("error_message")
        if error_message is not None and not isinstance(error_message, str):
            error_message = json.dumps(error_message, default=str)
        parsed.append(
            ToolCall(
                tool_name=str(tool_name),
                arguments=arguments,
                result=result,
                status=status,
                duration_ms=duration_ms,
                call_id=str(raw.get("id") or raw.get("call_id") or f"call_{index}"),
                error_message=error_message,
            )
        )
    return parsed


def _looks_like_error(text: str) -> bool:
    if not text:
        return False
    sample = text.lower()
    return any(keyword in sample for keyword in ERROR_KEYWORDS)


def _looks_like_success(text: str) -> bool:
    if not text:
        return False
    sample = text.lower()
    return any(re.search(rf"\b{re.escape(keyword)}\b", sample) for keyword in SUCCESS_KEYWORDS)


def _normalize_timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned:
            return None
        return cleaned
    return None


def _normalize_message(raw_message: Any, index: int) -> Turn | None:
    """Convert a raw message/event dict into a normalized :class:`Turn`."""

    if not isinstance(raw_message, dict):
        return None

    role = _normalize_role(
        raw_message.get("role")
        or raw_message.get("speaker")
        or raw_message.get("type")
        or raw_message.get("author")
    )

    content_raw = (
        raw_message.get("content")
        if "content" in raw_message
        else raw_message.get("text")
        or raw_message.get("message")
        or raw_message.get("body")
    )
    content = _coerce_text(content_raw).strip()

    tool_calls = _parse_tool_calls(
        raw_message.get("tool_calls")
        or raw_message.get("toolCalls")
        or raw_message.get("function_calls")
        or raw_message.get("tools")
    )

    if role == ROLE_TOOL and not tool_calls:
        single_call = ToolCall(
            tool_name=str(
                raw_message.get("tool_name")
                or raw_message.get("name")
                or raw_message.get("tool")
                or "unknown_tool"
            ),
            arguments=raw_message.get("arguments") or raw_message.get("input"),
            result=content_raw,
            status=str(raw_message.get("status") or OUTCOME_UNKNOWN).lower(),
            call_id=str(raw_message.get("tool_call_id") or raw_message.get("id") or f"call_{index}"),
            error_message=raw_message.get("error"),
        )
        if single_call.status not in {OUTCOME_SUCCESS, OUTCOME_ERROR, OUTCOME_UNKNOWN}:
            single_call.status = OUTCOME_ERROR if "error" in single_call.status else OUTCOME_UNKNOWN
        if single_call.status == OUTCOME_UNKNOWN and _looks_like_error(content):
            single_call.status = OUTCOME_ERROR
        tool_calls = [single_call]

    outcome = OUTCOME_UNKNOWN
    if role == ROLE_TOOL and tool_calls:
        statuses = {call.status for call in tool_calls}
        if OUTCOME_ERROR in statuses:
            outcome = OUTCOME_ERROR
        elif statuses == {OUTCOME_SUCCESS}:
            outcome = OUTCOME_SUCCESS
    elif role == ROLE_ASSISTANT:
        if _looks_like_error(content):
            outcome = OUTCOME_ERROR
        elif _looks_like_success(content):
            outcome = OUTCOME_SUCCESS

    return Turn(
        index=index,
        role=role,
        content=content,
        timestamp=_normalize_timestamp(
            raw_message.get("timestamp")
            or raw_message.get("created_at")
            or raw_message.get("createdAt")
            or raw_message.get("time")
        ),
        tool_calls=tool_calls,
        outcome=outcome,
        raw=raw_message,
    )


def _iter_message_candidates(payload: Any) -> Iterator[dict[str, Any]]:
    """Yield message-like dicts from common transcript container shapes."""

    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                yield item
        return

    if not isinstance(payload, dict):
        return

    for key in ("messages", "turns", "events", "conversation", "history", "log", "items"):
        candidate = payload.get(key)
        if isinstance(candidate, list):
            for item in candidate:
                if isinstance(item, dict):
                    yield item
            return

    if "role" in payload or "speaker" in payload or "type" in payload:
        yield payload


def _read_jsonl(path: Path) -> list[Any] | None:
    """Try to read ``path`` as JSON Lines. Returns ``None`` if it does not parse."""

    records: list[Any] = []
    with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                LOGGER.debug(
                    "JSONL parse failed at %s:%s (%s); falling back.", path, line_number, exc.msg
                )
                return None
    return records or None


def _read_session_payload(path: Path) -> tuple[Any, list[str]]:
    """Read a session file and return ``(payload, warnings)``."""

    warnings: list[str] = []

    if path.suffix.lower() in {".jsonl", ".ndjson"}:
        records = _read_jsonl(path)
        if records is not None:
            return records, warnings

    raw_text = path.read_text(encoding="utf-8", errors="replace")
    stripped = raw_text.strip()
    if not stripped:
        warnings.append("File is empty")
        return [], warnings

    try:
        return json.loads(stripped), warnings
    except json.JSONDecodeError as exc:
        LOGGER.debug("JSON parse failed for %s: %s; trying JSONL.", path, exc.msg)

    records = _read_jsonl(path)
    if records is not None:
        return records, warnings

    warnings.append("Could not parse file as JSON or JSONL")
    return [], warnings


def parse_session_file(path: Path) -> Session:
    """Parse a single session transcript file into a :class:`Session`."""

    resolved = resolve_path(path)
    payload, warnings = _read_session_payload(resolved)

    metadata: dict[str, Any] = {}
    started_at: str | None = None
    ended_at: str | None = None
    session_id: str | None = None

    if isinstance(payload, dict):
        for key in ("session_id", "id", "sessionId", "uuid"):
            if key in payload and payload[key]:
                session_id = str(payload[key])
                break
        started_at = _normalize_timestamp(
            payload.get("started_at") or payload.get("startedAt") or payload.get("start_time")
        )
        ended_at = _normalize_timestamp(
            payload.get("ended_at") or payload.get("endedAt") or payload.get("end_time")
        )
        meta = payload.get("metadata")
        if isinstance(meta, dict):
            metadata = meta
        for extra_key in ("model", "agent", "workspace", "title"):
            if extra_key in payload and extra_key not in metadata:
                metadata[extra_key] = payload[extra_key]

    if session_id is None:
        session_id = resolved.stem

    turns: list[Turn] = []
    for index, candidate in enumerate(_iter_message_candidates(payload)):
        turn = _normalize_message(candidate, index)
        if turn is None:
            continue
        turns.append(turn)

    if not turns and not warnings:
        warnings.append("No messages were extracted from the file")

    if started_at is None and turns:
        started_at = turns[0].timestamp
    if ended_at is None and turns:
        ended_at = turns[-1].timestamp

    return Session(
        session_id=session_id,
        source_path=str(PurePath(resolved)),
        started_at=started_at,
        ended_at=ended_at,
        turns=turns,
        metadata=metadata,
        parse_warnings=warnings,
    )


def discover_session_files(
    sessions_dir: Path,
    *,
    patterns: Sequence[str] = DEFAULT_SESSION_GLOBS,
) -> list[Path]:
    """Return the set of session files under ``sessions_dir`` matching ``patterns``."""

    resolved_dir = resolve_path(sessions_dir)
    if not resolved_dir.exists():
        LOGGER.warning("Sessions directory does not exist: %s", resolved_dir)
        return []
    if not resolved_dir.is_dir():
        LOGGER.warning("Sessions path is not a directory: %s", resolved_dir)
        return []

    discovered: dict[Path, None] = {}
    for pattern in patterns:
        for path in resolved_dir.glob(pattern):
            if path.is_file():
                discovered[path.resolve()] = None
    return sorted(discovered)


def _normalize_text_for_pattern(text: str) -> str:
    cleaned = CODE_FENCE_RE.sub(" ", text)
    cleaned = WHITESPACE_RE.sub(" ", cleaned).strip().lower()
    return cleaned


def _summarize_error_message(message: str) -> str:
    if not message:
        return ""
    snippet = message.strip().splitlines()[0]
    snippet = WHITESPACE_RE.sub(" ", snippet)
    return snippet[:160]


def detect_error_patterns(sessions: Iterable[Session]) -> list[dict[str, Any]]:
    """Cluster failing tool calls by tool name and error message signature."""

    counter: Counter[tuple[str, str]] = Counter()
    examples: dict[tuple[str, str], dict[str, Any]] = {}

    for session in sessions:
        for turn in session.turns:
            for call in turn.tool_calls:
                if call.status != OUTCOME_ERROR:
                    continue
                signature_source = call.error_message or _coerce_text(call.result)
                signature = _summarize_error_message(signature_source) or "(no error message)"
                key = (call.tool_name, signature)
                counter[key] += 1
                examples.setdefault(
                    key,
                    {
                        "tool_name": call.tool_name,
                        "error_signature": signature,
                        "session_id": session.session_id,
                        "turn_index": turn.index,
                    },
                )

    patterns: list[dict[str, Any]] = []
    for key, count in counter.most_common():
        entry = dict(examples[key])
        entry["occurrences"] = count
        patterns.append(entry)
    return patterns


def detect_success_patterns(sessions: Iterable[Session]) -> list[dict[str, Any]]:
    """Identify tool sequences that complete successfully across sessions."""

    sequence_counter: Counter[tuple[str, ...]] = Counter()
    tool_success_counter: Counter[str] = Counter()

    for session in sessions:
        sequence: list[str] = []
        for call in session.all_tool_calls:
            if call.status == OUTCOME_SUCCESS:
                sequence.append(call.tool_name)
                tool_success_counter[call.tool_name] += 1
            elif call.status == OUTCOME_ERROR:
                if len(sequence) >= 2:
                    sequence_counter[tuple(sequence)] += 1
                sequence = []
        if len(sequence) >= 2:
            sequence_counter[tuple(sequence)] += 1

    patterns: list[dict[str, Any]] = []
    for tool, count in tool_success_counter.most_common():
        patterns.append(
            {
                "kind": "tool_success_count",
                "tool_name": tool,
                "occurrences": count,
            }
        )
    for sequence, count in sequence_counter.most_common():
        patterns.append(
            {
                "kind": "tool_sequence",
                "sequence": list(sequence),
                "occurrences": count,
            }
        )
    return patterns


def extract_learnings(sessions: Iterable[Session]) -> list[dict[str, Any]]:
    """Pull out explicit learnings/lessons mentioned by the assistant."""

    learnings: list[dict[str, Any]] = []
    seen: set[str] = set()
    for session in sessions:
        for turn in session.assistant_turns:
            text = turn.content
            if not text:
                continue
            normalized = _normalize_text_for_pattern(text)
            for pattern in LEARNING_PATTERNS:
                for match in pattern.finditer(text):
                    captured = match.group(1).strip().rstrip(".") if match.groups() else match.group(0).strip()
                    if not captured:
                        continue
                    captured = WHITESPACE_RE.sub(" ", captured)
                    dedup_key = captured.lower()
                    if dedup_key in seen:
                        continue
                    seen.add(dedup_key)
                    learnings.append(
                        {
                            "session_id": session.session_id,
                            "turn_index": turn.index,
                            "learning": captured[:280],
                            "match_pattern": pattern.pattern,
                        }
                    )
            # Cheap retention of normalized text helps future debugging:
            if normalized and len(normalized) < 5:
                continue
    return learnings


def summarize_session(session: Session) -> dict[str, Any]:
    """Compute per-session aggregate counts."""

    tool_calls = session.all_tool_calls
    successful = sum(1 for call in tool_calls if call.status == OUTCOME_SUCCESS)
    errored = sum(1 for call in tool_calls if call.status == OUTCOME_ERROR)
    return {
        "session_id": session.session_id,
        "source_path": session.source_path,
        "started_at": session.started_at,
        "ended_at": session.ended_at,
        "turn_count": len(session.turns),
        "user_turns": len(session.user_turns),
        "assistant_turns": len(session.assistant_turns),
        "tool_turns": len(session.tool_turns),
        "tool_calls": len(tool_calls),
        "tool_call_success": successful,
        "tool_call_errors": errored,
        "parse_warnings": list(session.parse_warnings),
    }


def build_report(sessions: Sequence[Session]) -> dict[str, Any]:
    """Build the structured report covering all parsed sessions."""

    session_summaries = [summarize_session(session) for session in sessions]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "session_count": len(sessions),
        "totals": {
            "turns": sum(summary["turn_count"] for summary in session_summaries),
            "user_turns": sum(summary["user_turns"] for summary in session_summaries),
            "assistant_turns": sum(summary["assistant_turns"] for summary in session_summaries),
            "tool_calls": sum(summary["tool_calls"] for summary in session_summaries),
            "tool_call_success": sum(summary["tool_call_success"] for summary in session_summaries),
            "tool_call_errors": sum(summary["tool_call_errors"] for summary in session_summaries),
        },
        "sessions": session_summaries,
        "error_patterns": detect_error_patterns(sessions),
        "success_patterns": detect_success_patterns(sessions),
        "learnings": extract_learnings(sessions),
    }


def parse_sessions(
    paths: Iterable[Path],
    *,
    on_error: str = "warn",
) -> list[Session]:
    """Parse a collection of session files.

    Parameters
    ----------
    paths:
        Iterable of file paths.
    on_error:
        ``"warn"`` (default) logs a warning and continues, ``"raise"``
        re-raises the underlying exception.
    """

    if on_error not in {"warn", "raise"}:
        raise ValueError("on_error must be 'warn' or 'raise'")

    sessions: list[Session] = []
    for path in paths:
        try:
            sessions.append(parse_session_file(path))
        except (OSError, ValueError) as exc:
            if on_error == "raise":
                raise
            LOGGER.warning("Failed to parse %s: %s", path, exc)
    return sessions


def write_json(data: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True, default=str), encoding="utf-8")


def write_jsonl(records: Iterable[Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, sort_keys=True, default=str))
            handle.write("\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Extract structured conversation data from OpenClaw session "
            "transcripts under ~/.openclaw/sessions/."
        ),
    )
    parser.add_argument(
        "--sessions-dir",
        default=str(DEFAULT_SESSIONS_DIR),
        help="Directory containing session transcripts (default: %(default)s).",
    )
    parser.add_argument(
        "--session-file",
        action="append",
        default=[],
        help="Explicit session file path. May be repeated to process multiple files.",
    )
    parser.add_argument(
        "--output",
        default="logs/conversation_report.json",
        help="Path for the structured JSON report (default: %(default)s).",
    )
    parser.add_argument(
        "--turns-output",
        default=None,
        help="Optional JSONL file to dump every normalized turn.",
    )
    parser.add_argument(
        "--pattern",
        action="append",
        default=None,
        help=(
            "Optional glob pattern used when scanning the sessions directory. "
            "Repeat to combine patterns. Defaults to common JSON/JSONL globs."
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit the number of session files processed.",
    )
    parser.add_argument(
        "--on-error",
        choices=("warn", "raise"),
        default="warn",
        help="What to do when a session fails to parse (default: %(default)s).",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress informational logging output.",
    )
    parser.add_argument(
        "--print-summary",
        action="store_true",
        help="Print a short human-readable summary to stdout.",
    )
    return parser


def _print_console_summary(report: dict[str, Any], output_path: Path) -> None:
    totals = report["totals"]
    print("OpenClaw Conversation Extractor")
    print("================================")
    print(f"Sessions parsed:    {report['session_count']}")
    print(f"Total turns:        {totals['turns']}")
    print(f"User turns:         {totals['user_turns']}")
    print(f"Assistant turns:    {totals['assistant_turns']}")
    print(
        "Tool calls:         "
        f"{totals['tool_calls']} "
        f"(success={totals['tool_call_success']}, errors={totals['tool_call_errors']})"
    )
    print(f"Error patterns:     {len(report['error_patterns'])}")
    print(f"Success patterns:   {len(report['success_patterns'])}")
    print(f"Learnings extracted:{len(report['learnings'])}")
    print(f"Report written to:  {output_path}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    paths: list[Path] = []
    for explicit in args.session_file:
        paths.append(resolve_path(explicit))

    if not paths:
        sessions_dir = resolve_path(args.sessions_dir)
        patterns = tuple(args.pattern) if args.pattern else DEFAULT_SESSION_GLOBS
        paths = discover_session_files(sessions_dir, patterns=patterns)

    if args.limit is not None:
        paths = paths[: max(args.limit, 0)]

    if not paths:
        LOGGER.error(
            "No session files were found. Looked in '%s'. "
            "Pass --session-file or adjust --sessions-dir.",
            args.sessions_dir,
        )
        return 1

    LOGGER.info("Parsing %d session file(s).", len(paths))
    sessions = parse_sessions(paths, on_error=args.on_error)
    report = build_report(sessions)

    output_path = resolve_path(args.output)
    write_json(report, output_path)

    if args.turns_output:
        turns_path = resolve_path(args.turns_output)
        records = (
            {
                "session_id": session.session_id,
                "source_path": session.source_path,
                **turn.to_dict(),
            }
            for session in sessions
            for turn in session.turns
        )
        write_jsonl(records, turns_path)
        LOGGER.info("Wrote turn-level dump to %s", turns_path)

    LOGGER.info("Wrote conversation report to %s", output_path)
    if args.print_summary or not args.quiet:
        _print_console_summary(report, output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
