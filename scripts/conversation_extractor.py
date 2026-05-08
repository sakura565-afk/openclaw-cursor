#!/usr/bin/env python3
"""Extract structured conversations from OpenClaw session logs and memory files."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

# -----------------------------------------------------------------------------
# Paths & small I/O (avoid requiring PYTHONPATH for sibling script imports)
# -----------------------------------------------------------------------------


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _default_openclaw_home() -> Path:
    override = os.environ.get("OPENCLAW_HOME")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".openclaw"


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


TURN_PATTERN = re.compile(r"(?i)\b(?:turn|step|message)\s*[:#-]?\s*(\d+)\b")

# -----------------------------------------------------------------------------
# Intent taxonomy
# -----------------------------------------------------------------------------

INTENT_LABELS = (
    "code_generation",
    "debugging",
    "information_query",
    "creative_work",
)

INTENT_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "debugging",
        re.compile(
            r"\b(traceback|stack\s*trace|exception|error:|err\.|errno|"
            r"segmentation\s*fault|syntaxerror|valueerror|typeerror|keyerror|"
            r"runtimeerror|importerror|assertionerror|failed|failure|timed?\s*out|"
            r"debug(?:ging)?|broken|not\s+working|doesn'?t\s+work|bug\b|fix\s+the)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "code_generation",
        re.compile(
            r"\b(implement|refactor|write\s+(?:the\s+)?code|pull\s*request|commit|"
            r"typescript|javascript|python|rust|golang|\bdef\s+\w+|\bclass\s+\w+|"
            r"add\s+(?:a\s+)?(?:function|method|test|file)|create\s+(?:the\s+)?file|"
            r"patch|diff|merge\s+conflict)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "information_query",
        re.compile(
            r"^(?:what|why|when|where|who|how)\b|"
            r"\b(explain|describe|documentation|docs?\b|reference|"
            r"what\s+is|what\s+are|how\s+(?:do|does|to|can|should)|"
            r"can\s+you\s+tell|clarify|meaning\s+of)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "creative_work",
        re.compile(
            r"\b(creative|brainstorm|story|poem|prose|marketing\s+copy|"
            r"brand\s+voice|slogan|ideate|fiction|character\s+sheet)\b",
            re.IGNORECASE,
        ),
    ),
)

TOOL_FAILURE_LINE = re.compile(
    r"\b(error|failed|failure|exception|traceback|non-zero\s+exit|exit\s+code\s*[1-9]|"
    r"command\s+not\s+found|not\s+found:|eacces|eio|enoent|eprint)\b",
    re.IGNORECASE,
)

STRUCT_TOOL_KEYS = frozenset(
    {
        "tool_calls",
        "toolCalls",
        "tool_use",
        "toolUses",
        "tools",
        "function_call",
        "functionCall",
        "calls",
    }
)

ROLE_PREFIX = re.compile(
    r"^\s*(?P<role>user|human|assistant|agent|tool|system)\s*[:|\\-]+\s*(?P<body>.+)$",
    re.IGNORECASE,
)


# -----------------------------------------------------------------------------
# Data model
# -----------------------------------------------------------------------------


@dataclass
class ConversationSegment:
    """One meaningful slice of a transcript or memory file."""

    segment_id: str
    turn: int
    segment_type: str
    role: str | None
    text: str
    tool_name: str | None = None
    tool_call_id: str | None = None
    intent: str = "information_query"
    intent_scores: dict[str, float] = field(default_factory=dict)
    tool_status: str | None = None
    failure_reason: str | None = None
    tags: list[str] = field(default_factory=list)
    source_line: int | None = None
    entry_date: str | None = None

    def to_json_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


@dataclass
class ExtractedConversation:
    source_path: str
    source_kind: str
    file_modified_utc: str | None
    segments: list[ConversationSegment]
    failed_tool_count: int = 0

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "source_path": self.source_path,
            "source_kind": self.source_kind,
            "file_modified_utc": self.file_modified_utc,
            "failed_tool_count": self.failed_tool_count,
            "segment_count": len(self.segments),
            "segments": [s.to_json_dict() for s in self.segments],
        }


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------


def _infer_turn(blob: dict[str, Any], index: int) -> int:
    for key in ("turn", "step", "message_index", "index", "message"):
        raw = blob.get(key)
        if isinstance(raw, int) and raw > 0:
            return raw
        if isinstance(raw, str) and raw.isdigit():
            return max(int(raw), 1)
    return index + 1


def _stringify_toolish_dict(d: dict[str, Any]) -> str | None:
    fn = d.get("function")
    if isinstance(fn, dict) and isinstance(fn.get("name"), str):
        return fn["name"]
    for key in ("name", "tool", "toolName", "tool_name", "id"):
        raw = d.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return None


def _tool_names_from_calls(value: Any) -> list[str]:
    names: list[str] = []
    if not isinstance(value, list):
        return names
    for item in value:
        if isinstance(item, dict):
            t = _stringify_toolish_dict(item)
            if t:
                names.append(t)
            fn = item.get("function")
            if isinstance(fn, dict):
                n = fn.get("name")
                if isinstance(n, str) and n.strip():
                    names.append(n.strip())
            n2 = item.get("name") or item.get("tool_name")
            if isinstance(n2, str) and n2.strip() and n2 not in names:
                names.append(n2.strip())
    return names


def _normalize_role(role: Any) -> str:
    if not isinstance(role, str):
        return ""
    r = role.lower().strip()
    if r == "human":
        return "user"
    return r


def _score_intents(text: str) -> dict[str, float]:
    if not text or not text.strip():
        return {label: 0.0 for label in INTENT_LABELS}
    scores = {label: 0.0 for label in INTENT_LABELS}
    for label, pattern in INTENT_RULES:
        if pattern.search(text):
            scores[label] = 1.0
    return scores


def _primary_intent(scores: dict[str, float]) -> str:
    best = max(scores.values()) if scores else 0.0
    if best <= 0.0:
        return "information_query"
    for label in INTENT_LABELS:
        if scores.get(label, 0) >= best:
            return label
    return "information_query"


def _tool_failure_from_text(body: str) -> tuple[bool, str | None]:
    if not body or not body.strip():
        return False, None
    if TOOL_FAILURE_LINE.search(body):
        snippet = " ".join(body.strip().split())[:400]
        return True, snippet
    return False, None


def _parse_iso_date(s: str) -> date | None:
    s = s.strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _parse_iso_datetime(s: str) -> datetime | None:
    s = s.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        d = _parse_iso_date(s)
        if d:
            return datetime.combine(d, time.min, tzinfo=timezone.utc)
    return None


def _file_mtime_utc(path: Path) -> datetime | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return None


def _segment_id(prefix: str, turn: int, idx: int) -> str:
    safe = re.sub(r"[^\w\-]+", "_", prefix)[:80]
    return f"{safe}__t{turn}__{idx}"


def _unpack_messages(data: Any) -> list[dict[str, Any]] | None:
    if isinstance(data, dict):
        inner = (
            data.get("messages")
            or data.get("conversation")
            or data.get("transcript")
            or data.get("history")
        )
        if isinstance(inner, list):
            return [x for x in inner if isinstance(x, dict)]
        for key in ("sessions", "turns"):
            lst = data.get(key)
            if isinstance(lst, list) and lst and isinstance(lst[0], dict):
                if "messages" in lst[0]:
                    flat: list[dict[str, Any]] = []
                    for pack in lst:
                        if isinstance(pack, dict) and isinstance(pack.get("messages"), list):
                            flat.extend(pack["messages"])  # type: ignore[arg-type]
                    return flat
    elif isinstance(data, list) and data:
        if all(isinstance(x, dict) for x in data):
            if any(k in data[0] for k in ("role", "content", "speaker", "turn")):
                return data  # type: ignore[return-value]
            if len(data) == 1 and isinstance(data[0], dict):
                inner = (
                    data[0].get("messages")
                    or data[0].get("conversation")
                    or data[0].get("history")
                )
                if isinstance(inner, list):
                    return [x for x in inner if isinstance(x, dict)]
    return None


def _extract_tool_blocks_from_content(
    content: Any,
    turn: int,
    prefix: str,
    counter: list[int],
) -> list[ConversationSegment]:
    """Pull tool_use / tool_result style blocks from message content."""
    out: list[ConversationSegment] = []

    def walk(piece: Any) -> None:
        if isinstance(piece, dict):
            typ = str(piece.get("type") or "").lower().replace("_", "-")
            if typ in ("tool-use", "tooluse", "tool-invocation", "function"):
                name = _stringify_toolish_dict(piece)
                tid = piece.get("id") or piece.get("tool_use_id")
                tid_s = tid if isinstance(tid, str) else None
                inp = piece.get("input") or piece.get("arguments")
                body = ""
                if isinstance(inp, str):
                    body = inp
                elif isinstance(inp, dict):
                    try:
                        body = json.dumps(inp, ensure_ascii=False)[:2000]
                    except (TypeError, ValueError):
                        body = str(inp)[:2000]
                counter[0] += 1
                scores = _score_intents(body)
                out.append(
                    ConversationSegment(
                        segment_id=_segment_id(prefix, turn, counter[0]),
                        turn=turn,
                        segment_type="tool_call",
                        role="assistant",
                        text=body or (name or ""),
                        tool_name=name,
                        tool_call_id=tid_s,
                        intent=_primary_intent(scores),
                        intent_scores=scores,
                        tool_status=None,
                        tags=[],
                    )
                )
                return
            if typ in ("tool-result", "toolresult"):
                tid = piece.get("tool_use_id") or piece.get("tool_call_id")
                tid_s = tid if isinstance(tid, str) else None
                text = ""
                if isinstance(piece.get("content"), str):
                    text = piece["content"]
                elif isinstance(piece.get("content"), list):
                    for c in piece["content"]:
                        if isinstance(c, dict) and isinstance(c.get("text"), str):
                            text += c["text"]
                        elif isinstance(c, str):
                            text += c
                is_err = bool(piece.get("is_error") or piece.get("error"))
                failed, reason = _tool_failure_from_text(text)
                failed = failed or is_err
                if is_err and not reason:
                    reason = "tool_result marked is_error"
                status = "failure" if failed else "success"
                tags = ["failed_tool", "error_learning"] if failed else []
                counter[0] += 1
                scores = _score_intents(text)
                out.append(
                    ConversationSegment(
                        segment_id=_segment_id(prefix, turn, counter[0]),
                        turn=turn,
                        segment_type="tool_result",
                        role="tool",
                        text=text.strip(),
                        tool_name=None,
                        tool_call_id=tid_s,
                        intent=_primary_intent(scores),
                        intent_scores=scores,
                        tool_status=status,
                        failure_reason=reason,
                        tags=tags,
                    )
                )
                return
            for v in piece.values():
                walk(v)
        elif isinstance(piece, list):
            for item in piece:
                walk(item)

    walk(content)
    return out


def messages_to_segments(messages: list[dict[str, Any]], stem: str) -> list[ConversationSegment]:
    segments: list[ConversationSegment] = []
    counter = [0]

    for i, raw in enumerate(messages):
        turn = _infer_turn(raw, i)
        role = _normalize_role(raw.get("role") or raw.get("speaker") or raw.get("from"))
        content = raw.get("content") or raw.get("text") or raw.get("body")

        # OpenAI-style tool_calls on assistant message
        for key in STRUCT_TOOL_KEYS:
            calls = raw.get(key)
            if isinstance(calls, list):
                for item in calls:
                    if not isinstance(item, dict):
                        continue
                    fn = item.get("function")
                    name = None
                    args = ""
                    if isinstance(fn, dict):
                        name = fn.get("name") if isinstance(fn.get("name"), str) else None
                        raw_args = fn.get("arguments")
                        if isinstance(raw_args, str):
                            args = raw_args[:4000]
                    tid = item.get("id")
                    tid_s = tid if isinstance(tid, str) else None
                    if name:
                        counter[0] += 1
                        scores = _score_intents(args)
                        segments.append(
                            ConversationSegment(
                                segment_id=_segment_id(stem, turn, counter[0]),
                                turn=turn,
                                segment_type="tool_call",
                                role="assistant",
                                text=args,
                                tool_name=name,
                                tool_call_id=tid_s,
                                intent=_primary_intent(scores),
                                intent_scores=scores,
                            )
                        )

        segments.extend(_extract_tool_blocks_from_content(content, turn, stem, counter))

        # Flatten remaining text for user/assistant/system/tool roles
        text_parts: list[str] = []
        if isinstance(content, str):
            text_parts.append(content)
        elif isinstance(content, list):
            for piece in content:
                if not isinstance(piece, dict):
                    continue
                typ = str(piece.get("type") or "").lower().replace("_", "-")
                if typ in ("tool-use", "tooluse", "tool-result", "toolresult", "function"):
                    continue
                if isinstance(piece.get("text"), str):
                    text_parts.append(piece["text"])
                elif typ == "text" and isinstance(piece.get("content"), str):
                    text_parts.append(piece["content"])

        merged = "\n".join(t for t in text_parts if t.strip()).strip()

        summary = raw.get("summary") or raw.get("output")
        if isinstance(summary, str) and summary.strip():
            merged = (merged + "\n" + summary).strip() if merged else summary.strip()

        extra = raw.get("thinking") or raw.get("reasoning")
        if isinstance(extra, str) and extra.strip():
            merged = (merged + "\n" + extra).strip() if merged else extra.strip()

        if role == "tool" and merged:
            failed, reason = _tool_failure_from_text(merged)
            status = "failure" if failed else "success"
            tags = ["failed_tool", "error_learning"] if failed else []
            counter[0] += 1
            scores = _score_intents(merged)
            segments.append(
                ConversationSegment(
                    segment_id=_segment_id(stem, turn, counter[0]),
                    turn=turn,
                    segment_type="tool_result",
                    role="tool",
                    text=merged,
                    tool_call_id=raw.get("tool_call_id") if isinstance(raw.get("tool_call_id"), str) else None,
                    intent=_primary_intent(scores),
                    intent_scores=scores,
                    tool_status=status,
                    failure_reason=reason,
                    tags=tags,
                )
            )
            continue

        if merged:
            eff_role = role
            if role == "user":
                seg_type = "user_request"
            elif role in ("assistant", "agent", "system") or not role:
                seg_type = "agent_response" if role in ("assistant", "agent") or not role else "system_message"
            else:
                seg_type = "unstructured"

            scores = _score_intents(merged)
            counter[0] += 1
            segments.append(
                ConversationSegment(
                    segment_id=_segment_id(stem, turn, counter[0]),
                    turn=turn,
                    segment_type=seg_type,
                    role=eff_role or None,
                    text=merged,
                    intent=_primary_intent(scores),
                    intent_scores=scores,
                )
            )

    return segments


def parse_json_transcript(path: Path, stem: str) -> list[ConversationSegment] | None:
    raw = read_text(path)
    if not raw.strip():
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None

    messages = _unpack_messages(data)
    if messages:
        return messages_to_segments(messages, stem)
    return None


def parse_text_transcript(path: Path, stem: str) -> list[ConversationSegment]:
    segments: list[ConversationSegment] = []
    current_turn = 1
    counter = [0]

    for line_number, line in enumerate(read_text(path).splitlines(), start=1):
        m_turn = TURN_PATTERN.search(line)
        if m_turn:
            current_turn = int(m_turn.group(1))
        else:
            current_turn = max(current_turn, line_number)

        m_role = ROLE_PREFIX.match(line)
        if m_role:
            rl = m_role.group("role").lower()
            if rl == "human":
                rl = "user"
            body = m_role.group("body").strip()
            scores = _score_intents(body)
            counter[0] += 1
            if rl == "user":
                seg_type = "user_request"
            elif rl == "tool":
                failed, reason = _tool_failure_from_text(body)
                seg_type = "tool_result"
                st = "failure" if failed else "success"
                tags = ["failed_tool", "error_learning"] if failed else []
                segments.append(
                    ConversationSegment(
                        segment_id=_segment_id(stem, current_turn, counter[0]),
                        turn=current_turn,
                        segment_type=seg_type,
                        role=rl,
                        text=body,
                        intent=_primary_intent(scores),
                        intent_scores=scores,
                        tool_status=st,
                        failure_reason=reason,
                        tags=tags,
                    )
                )
                continue
            elif rl in ("assistant", "agent"):
                seg_type = "agent_response"
            else:
                seg_type = "unstructured"
            segments.append(
                ConversationSegment(
                    segment_id=_segment_id(stem, current_turn, counter[0]),
                    turn=current_turn,
                    segment_type=seg_type,
                    role=rl,
                    text=body,
                    source_line=line_number,
                    intent=_primary_intent(scores),
                    intent_scores=scores,
                )
            )
        elif line.strip():
            body = line.strip()
            if re.search(r"\b(?:tool|calling|invoke)\s*[:(]", body, re.I):
                counter[0] += 1
                scores = _score_intents(body)
                segments.append(
                    ConversationSegment(
                        segment_id=_segment_id(stem, current_turn, counter[0]),
                        turn=current_turn,
                        segment_type="tool_call",
                        role="assistant",
                        text=body,
                        intent=_primary_intent(scores),
                        intent_scores=scores,
                    )
                )
            else:
                counter[0] += 1
                scores = _score_intents(body)
                segments.append(
                    ConversationSegment(
                        segment_id=_segment_id(stem, current_turn, counter[0]),
                        turn=current_turn,
                        segment_type="unstructured",
                        role=None,
                        text=body,
                        source_line=line_number,
                        intent=_primary_intent(scores),
                        intent_scores=scores,
                    )
                )

    return segments


MEMORY_DATE_HEAD = re.compile(r"^\s*(?:[-*+]|\d+\.)\s*(\d{4}-\d{2}-\d{2})\b")


def parse_memory_markdown(path: Path, stem: str) -> list[ConversationSegment]:
    """Split MEMORY-style markdown into dated bullets and paragraph chunks."""
    text = read_text(path)
    segments: list[ConversationSegment] = []
    counter = [0]
    buf: list[str] = []
    buf_start = 1

    def flush_para(start_ln: int, lines: list[str]) -> None:
        chunk = "\n".join(lines).strip()
        if not chunk:
            return
        m = MEMORY_DATE_HEAD.match(lines[0]) if lines else None
        entry_dt = m.group(1) if m else None
        scores = _score_intents(chunk)
        counter[0] += 1
        segments.append(
            ConversationSegment(
                segment_id=_segment_id(stem + "_mem", start_ln, counter[0]),
                turn=start_ln,
                segment_type="memory_entry",
                role="user",
                text=chunk,
                source_line=start_ln,
                entry_date=entry_dt,
                intent=_primary_intent(scores),
                intent_scores=scores,
            )
        )

    for i, line in enumerate(text.splitlines(), start=1):
        if line.startswith("#") and buf:
            flush_para(buf_start, buf)
            buf = []
        if line.startswith("#"):
            buf_start = i
            continue
        if not line.strip():
            if buf:
                flush_para(buf_start, buf)
                buf = []
            continue
        if not buf:
            buf_start = i
        buf.append(line)

    if buf:
        flush_para(buf_start, buf)

    return segments


def extract_conversation(path: Path, workspace_root: Path) -> ExtractedConversation:
    stem = path.stem
    parent = path.parent.name
    if parent:
        stem = f"{parent}__{path.stem}"

    suf = path.suffix.lower()
    rel = path.resolve().as_posix()
    ws = workspace_root.resolve().as_posix()
    if rel.startswith(ws):
        display = Path(rel[len(ws) :].lstrip("/")).as_posix()
    else:
        display = rel

    mtime = _file_mtime_utc(path)
    mtime_s = mtime.isoformat(timespec="seconds") if mtime else None

    if suf == ".md":
        segments = parse_memory_markdown(path, stem)
        kind = "memory_file"
    elif suf == ".json":
        parsed = parse_json_transcript(path, stem)
        if parsed is not None:
            segments = parsed
            kind = "session_transcript"
        else:
            segments = parse_text_transcript(path, stem)
            kind = "session_transcript"
    else:
        segments = parse_text_transcript(path, stem)
        kind = "session_transcript"

    failed = sum(1 for s in segments if "failed_tool" in s.tags)
    return ExtractedConversation(
        source_path=display,
        source_kind=kind,
        file_modified_utc=mtime_s,
        segments=segments,
        failed_tool_count=failed,
    )


def default_scan_roots(source: str, home: Path) -> list[Path]:
    roots: list[Path] = []
    if source in ("logs", "both"):
        roots.extend([home / "logs", home / "workspace" / "logs"])
    if source in ("memory", "both"):
        roots.append(home / "workspace" / "memory")
        roots.append(home / "workspace" / "MEMORY.md")
    return roots


def iter_candidate_files(
    roots: Iterable[Path],
    log_globs: tuple[str, ...],
    recursive: bool,
) -> Iterator[Path]:
    extensions = {".json", ".log", ".txt", ".md"}
    for root in roots:
        if not root.exists():
            continue
        if root.is_file():
            if root.suffix.lower() in extensions:
                yield root
            continue
        for pattern in log_globs:
            if recursive:
                for p in root.rglob(pattern):
                    if p.is_file() and p.suffix.lower() in extensions:
                        yield p
            else:
                for p in root.glob(pattern):
                    if p.is_file() and p.suffix.lower() in extensions:
                        yield p


def conversation_in_date_range(
    conv: ExtractedConversation,
    start: datetime | None,
    end: datetime | None,
) -> bool:
    """Filter by file mtime; for memory segments also allow entry_date inside range."""
    if start is None and end is None:
        return True

    def in_range(dt: datetime | None) -> bool:
        if dt is None:
            return False
        if start and dt < start:
            return False
        if end and dt > end:
            return False
        return True

    if conv.file_modified_utc:
        fdt = _parse_iso_datetime(conv.file_modified_utc)
        if fdt and in_range(fdt):
            return True

    for seg in conv.segments:
        if seg.entry_date:
            d = _parse_iso_date(seg.entry_date)
            if d:
                dt = datetime.combine(d, time.min, tzinfo=timezone.utc)
                if in_range(dt):
                    return True

    if start or end:
        return False
    return True


def filter_segments_by_date(
    conv: ExtractedConversation,
    start: datetime | None,
    end: datetime | None,
) -> ExtractedConversation:
    """When date filters set, drop memory segments outside range; keep session transcripts if file matched."""
    if start is None and end is None:
        return conv

    def ok_seg(seg: ConversationSegment) -> bool:
        if seg.entry_date:
            d = _parse_iso_date(seg.entry_date)
            if not d:
                return True
            dt = datetime.combine(d, time.min, tzinfo=timezone.utc)
            if start and dt < start:
                return False
            if end and dt > end:
                return False
        return True

    if conv.source_kind != "memory_file":
        return conv

    kept = [s for s in conv.segments if ok_seg(s)]
    failed = sum(1 for s in kept if "failed_tool" in s.tags)
    return ExtractedConversation(
        source_path=conv.source_path,
        source_kind=conv.source_kind,
        file_modified_utc=conv.file_modified_utc,
        segments=kept,
        failed_tool_count=failed,
    )


def build_output_document(
    conversations: list[ExtractedConversation],
    filters: dict[str, Any],
) -> dict[str, Any]:
    total_failed = sum(c.failed_tool_count for c in conversations)
    return {
        "schema_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "filters": filters,
        "summary": {
            "conversation_count": len(conversations),
            "segment_count": sum(len(c.segments) for c in conversations),
            "failed_tool_call_count": total_failed,
        },
        "conversations": [c.to_json_dict() for c in conversations],
    }


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Extract OpenClaw conversations from ~/.openclaw/logs, memory files, or explicit paths. "
            "Outputs structured JSON with intents and failed tool-call tags for error learning."
        ),
    )
    p.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="Transcript or memory files (.json, .log, .txt, .md). Optional if --scan-defaults is set.",
    )
    p.add_argument(
        "--scan-defaults",
        action="store_true",
        help=f"Scan default OpenClaw locations under OPENCLAW_HOME or {_default_openclaw_home()}.",
    )
    p.add_argument(
        "--source",
        choices=("logs", "memory", "both"),
        default="both",
        help="Which default subtrees to scan with --scan-defaults.",
    )
    p.add_argument(
        "--openclaw-home",
        type=Path,
        default=None,
        help="Override OpenClaw home (default: OPENCLAW_HOME or ~/.openclaw).",
    )
    p.add_argument(
        "--glob",
        dest="glob_patterns",
        action="append",
        default=[],
        metavar="PATTERN",
        help="Glob relative to each scan root (repeatable). Default: *",
    )
    p.add_argument(
        "--recursive",
        action="store_true",
        help="Recurse into scan roots when matching globs.",
    )
    p.add_argument(
        "--from-date",
        type=str,
        default=None,
        help="Inclusive start date (YYYY-MM-DD) in UTC for file mtime and memory entry dates.",
    )
    p.add_argument(
        "--to-date",
        type=str,
        default=None,
        help="Inclusive end date (YYYY-MM-DD) in UTC.",
    )
    p.add_argument(
        "--workspace-root",
        type=Path,
        default=None,
        help="Strip paths relative to this root in JSON source_path fields.",
    )
    p.add_argument(
        "--output",
        "-o",
        type=str,
        default=None,
        help="Write JSON to this path. Use '-' for stdout (default: stdout).",
    )
    p.add_argument(
        "--indent",
        type=int,
        default=2,
        help="JSON indent (default: 2). Use 0 for compact.",
    )
    p.add_argument(
        "--stdin-json",
        action="store_true",
        help="Read one JSON transcript from stdin instead of files.",
    )
    p.add_argument(
        "--max-files",
        type=int,
        default=0,
        help="Stop after N input files (0 = no limit).",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    ws = (args.workspace_root or repo_root()).resolve()
    home = (args.openclaw_home or _default_openclaw_home()).expanduser().resolve()

    start_dt = end_dt = None
    if args.from_date:
        d0 = _parse_iso_date(args.from_date)
        if not d0:
            sys.stderr.write("error: invalid --from-date (use YYYY-MM-DD)\n")
            return 2
        start_dt = datetime.combine(d0, time.min, tzinfo=timezone.utc)
    if args.to_date:
        d1 = _parse_iso_date(args.to_date)
        if not d1:
            sys.stderr.write("error: invalid --to-date (use YYYY-MM-DD)\n")
            return 2
        end_dt = datetime.combine(d1, time.max, tzinfo=timezone.utc)

    file_paths: list[Path] = [p.expanduser().resolve() for p in args.paths]

    if args.stdin_json:
        payload = sys.stdin.read()
        if not payload.strip():
            sys.stderr.write("error: empty stdin\n")
            return 2
        try:
            data = json.loads(payload)
        except json.JSONDecodeError as e:
            sys.stderr.write(f"error: stdin JSON: {e}\n")
            return 2
        messages = _unpack_messages(data)
        if not messages:
            sys.stderr.write("error: stdin JSON has no messages list\n")
            return 2
        segments = messages_to_segments(messages, "stdin")
        failed = sum(1 for s in segments if "failed_tool" in s.tags)
        conv = ExtractedConversation(
            source_path="<stdin>",
            source_kind="session_transcript",
            file_modified_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            segments=segments,
            failed_tool_count=failed,
        )
        doc = build_output_document(
            [conv],
            {"from_date": args.from_date, "to_date": args.to_date, "stdin": True},
        )
        text = json.dumps(doc, indent=args.indent or None, ensure_ascii=False)
        if args.output == "-" or args.output is None:
            sys.stdout.write(text + "\n")
        else:
            Path(args.output).write_text(text + "\n", encoding="utf-8")
        return 0

    globs = tuple(args.glob_patterns) if args.glob_patterns else ("*",)
    if args.scan_defaults:
        for root in default_scan_roots(args.source, home):
            if root.is_file():
                if root.exists():
                    file_paths.append(root)
            elif root.exists():
                file_paths.extend(iter_candidate_files([root], globs, args.recursive))

    # de-duplicate preserving order
    seen: set[str] = set()
    unique_paths: list[Path] = []
    for p in file_paths:
        key = p.resolve().as_posix()
        if key not in seen:
            seen.add(key)
            unique_paths.append(p)

    if not unique_paths:
        sys.stderr.write("error: provide file paths, --scan-defaults, or --stdin-json\n")
        return 2

    conversations: list[ExtractedConversation] = []
    for idx, path in enumerate(unique_paths):
        if args.max_files and idx >= args.max_files:
            break
        if not path.exists() or not path.is_file():
            continue
        conv = extract_conversation(path, ws)
        if not conversation_in_date_range(conv, start_dt, end_dt):
            continue
        conv = filter_segments_by_date(conv, start_dt, end_dt)
        if conv.segments:
            conversations.append(conv)

    filters: dict[str, Any] = {
        "from_date": args.from_date,
        "to_date": args.to_date,
        "scan_defaults": bool(args.scan_defaults),
        "source": args.source,
        "openclaw_home": home.as_posix(),
    }
    doc = build_output_document(conversations, filters)
    indent = None if args.indent == 0 else args.indent
    text = json.dumps(doc, indent=indent, ensure_ascii=False)

    out = args.output
    if out is None or out == "-":
        sys.stdout.write(text + "\n")
    else:
        Path(out).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        Path(out).expanduser().write_text(text + "\n", encoding="utf-8")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
