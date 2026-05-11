#!/usr/bin/env python3
"""Extract tasks, decisions, insights, learnings, errors, corrections, and preferences from session transcripts."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from scripts.optimize_context import TURN_PATTERN, read_text, repo_root

# -----------------------------------------------------------------------------
# Patterns: decisions / commitments / takeaways (English + common mixed usage)
# -----------------------------------------------------------------------------

DECISION_LINE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"^\s*(?:decision|resolution|resolved|outcome)\s*[:\-—]\s*(.+)",
        r"^\s*\*{0,2}(?:decision|resolution)\*{0,2}\s*[:\-—]\s*(.+)",
        r"(?:we(?:'ve)?\s+(?:decided|agreed|chose)|let'?s\s+go\s+with|final(?:ly)?\s*:\s*)(.+)",
        r"(?:\bapproved\b|\bfinalize[ds]?\b|\bchosen\b\s+(?:approach|option|path))\s*[:\-]?\s*(.+)",
        r"^\s*(?:TL;DR|TDLR|takeaway)s?\s*[:\-—]\s*(.+)",
        r"\b(?:concluded|conclusion)\s+(?:that\s+)?(.{10,})",
        r"\bdecision\s*[:\-—]\s*(.+)",
    )
)

LEARNING_LINE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"^\s*(?:learning|lesson|takeaway|key\s+learning)\s*[:\-—]\s*(.+)",
        r"^\s*\*{0,2}(?:important|remember|note)\*{0,2}\s*[:\-—]\s*(.+)",
        r"(?:核心价值|关键点|经验教训|结论是|需要注意的是)\s*[：:]\s*(.+)",
    )
)

INSIGHT_LINE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"^\s*(?:insight|observation|realization|aha)\s*[:\-—]\s*(.+)",
        r"\b(?:the\s+)?(?:key\s+)?insight\s+(?:is|here)\s*[:\-]?\s*(.+)",
        r"\b(?:it\s+)?turns\s+out\s+(?:that\s+)?(.{15,})",
        r"\b(?:what\s+)?(?:this\s+)?(?:shows|reveals|means)\s+(?:is\s+)?(.{15,})",
    )
)

TASK_LINE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"^\s*(?:TODO|FIXME|ACTION\s+ITEM|follow[-\s]?up|next\s+step)s?\s*[:\-—]\s*(.+)",
        r"^\s*[-*]\s*\[(?:\s|x|X)\]\s+(.+)",
        r"\b(?:need\s+to|must|should|will)\s+(?:still\s+)?(?:implement|add|fix|update|create|refactor|migrate|deploy)\b[^.]{5,200}\.",
        r"\b(?:reminder|don't\s+forget)\s*[:\-]\s*(.+)",
        r"(?:待办|下一步|需要)\s*[：:]\s*(.+)",
    )
)

ERROR_LINE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE | re.MULTILINE)
    for p in (
        r"(?:^|\n)\s*(?:Error|Exception|Traceback)\s*(?:\(|\:|\.)\s*(.{10,400})",
        r"\b(?:failed|failure|fatal)\s*(?:with|error)?\s*[:\-]\s*(.{10,300})",
        r"`(?:[A-Za-z_][\w]*Error)`[:\s]+(.{5,200})",
        r"(?:exit\s+code|status)\s*[:\-]\s*(\d+)\s*[—\-]\s*(.{5,120})",
        r"(?:HTTP|https?://[^\s]+)\s+(?:4\d\d|5\d\d)\b[^\n]{0,120}",
    )
)

CORRECTION_LINE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"^\s*(?:correction|retract|update)\s*[:\-—]\s*(.+)",
        r"\b(?:actually|sorry|my\s+mistake|I\s+meant|to\s+clarify)[,:\s]+(.{10,400})",
        r"\b(?:that\s+was\s+wrong|incorrect\s+earlier)[^.]{0,80}\.\s*(.{10,300})",
    )
)

PREFERENCE_LINE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"^\s*(?:preference|prefer|always|never)\s*[:\-—]\s*(.+)",
        r"\b(?:I\s+prefer|we\s+prefer|please\s+always|do\s+not|don't)\s+(.{8,300})",
        r"\b(?:style|convention)\s*[:\-]\s*(.{8,200})",
    )
)

# Inline tool/function references in transcripts
TOOL_REGEXES: tuple[re.Pattern[str], ...] = (
    re.compile(r'\b(?:invoke|calling|called)\s+(?:tool\s+)?[`"]?([\w\-./:]+)[`"]?', re.I),
    re.compile(r'`(?:functions?\.)?([\w\-]+)', re.I),
    re.compile(r'"(?:tool|toolName|name|function)"\s*:\s*"([^"]+)"'),
    re.compile(r"\[tool\s*:\s*([\w\-./:]+)\]", re.I),
    re.compile(r"\b(?:mcp|MCP)[_\s:]+([\w\-]+)", re.I),
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

# Markdown / alternate transcript headers
MD_ROLE_HEADER = re.compile(
    r"^\s{0,3}(?:#{1,6}\s+|\*\*)?"
    r"(?P<role>user|human|assistant|agent|tool|system)"
    r"(?:\*\*)?\s*:?\s*$",
    re.IGNORECASE,
)

ROLE_PREFIX = re.compile(
    r"^\s*(?P<role>user|human|assistant|agent|tool|system)\s*[:|\\-]+\s*(?P<body>.+)$",
    re.IGNORECASE,
)

MAX_CAP_LINE = 400
MAX_CATEGORY_ITEMS = 150


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")


def _infer_turn(blob: dict[str, Any], index: int) -> int:
    for key in ("turn", "step", "message_index", "index", "message"):
        raw = blob.get(key)
        if isinstance(raw, int) and raw > 0:
            return raw
        if isinstance(raw, str) and raw.isdigit():
            return max(int(raw), 1)
    return index + 1


def _stringify_toolish_dict(d: dict[str, Any]) -> str | None:
    """Best-effort name for a structured tool/function block."""

    fn = d.get("function")
    if isinstance(fn, dict) and isinstance(fn.get("name"), str):
        return fn["name"]
    for key in ("name", "tool", "toolName", "tool_name", "id"):
        raw = d.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return None


def _flatten_content_piece(piece: Any) -> tuple[str, list[str]]:
    """Turn a message content blob into (assistant text fragment, tool names)."""

    tools: list[str] = []

    if piece is None:
        return "", tools
    if isinstance(piece, str):
        return piece, tools

    if isinstance(piece, dict):
        typ = str(piece.get("type") or "").lower()

        # Anthropic/OpenClaw-style tool_use blocks
        if typ in ("tool_use", "tool-use", "toolcall", "function", "tool_invocation"):
            name = _stringify_toolish_dict(piece)
            if name:
                tools.append(name)
                return "", tools

        if typ == "tool_result" or typ == "tool-result":
            return "", tools

        if isinstance(piece.get("text"), str):
            return piece["text"], tools
        if isinstance(piece.get("content"), str):
            return piece["content"], tools

        nested = piece.get("text") or piece.get("content")
        txt, subt = _flatten_content_piece(nested)
        tools.extend(subt)

        inner = piece.get("input") or piece.get("arguments")
        if isinstance(inner, dict) and txt == "":
            # sometimes model echoes tool inputs as learning context
            try:
                snippet = json.dumps(inner, ensure_ascii=False)[:500]
                if snippet:
                    return snippet, tools
            except (TypeError, ValueError):
                pass
        return txt, tools

    if isinstance(piece, list):
        parts: list[str] = []
        for item in piece:
            t, tt = _flatten_content_piece(item)
            if t.strip():
                parts.append(t.strip())
            tools.extend(tt)
        return "\n".join(parts), tools

    return str(piece), tools


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
            # OpenAI-ish id + name siblings
            n2 = item.get("name") or item.get("tool_name")
            if isinstance(n2, str) and n2.strip() and n2 not in names:
                names.append(n2.strip())
    return names


def _segments_from_messages(messages: list[Any]) -> list[tuple[int, str | None, str]]:
    segments: list[tuple[int, str | None, str]] = []

    for i, raw in enumerate(messages):
        if not isinstance(raw, dict):
            continue
        turn = _infer_turn(raw, i)
        role = raw.get("role") or raw.get("speaker") or raw.get("from")
        if isinstance(role, str):
            rl = role.lower().strip()
        else:
            rl = ""

        blob_tools: list[str] = []

        for key in STRUCT_TOOL_KEYS:
            if key in raw:
                blob_tools.extend(_tool_names_from_calls(raw[key]))

        text = ""
        c = raw.get("content") or raw.get("text") or raw.get("body")

        frag, inner_tools = _flatten_content_piece(c)
        blob_tools.extend(inner_tools)
        text += frag

        summary = raw.get("summary") or raw.get("output")
        if isinstance(summary, str) and summary.strip():
            text = (text + "\n" + summary).strip() if text.strip() else summary

        extra = raw.get("thinking") or raw.get("reasoning")
        if isinstance(extra, str) and extra.strip():
            text = (text + "\n" + extra).strip()

        seen = set()
        for tn in blob_tools:
            tn = tn.strip()
            if not tn or tn in seen:
                continue
            seen.add(tn)
            segments.append((turn, "tool", f"{tn}"))
        # fall back name field on structured tool-only rows
        if not text.strip() and not blob_tools:
            maybe = raw.get("name") or raw.get("tool")
            if isinstance(maybe, str) and rl in {"tool", "assistant", "", "assistant_tool"}:
                segments.append((turn, "tool", maybe))

        rest = raw.get("message") if isinstance(raw.get("message"), str) else None
        if rest:
            text = (text + "\n" + rest).strip() if text else rest

        if text.strip():
            # Distinguish tool *invocation* rows (handled above) from provider "tool" role *outputs*.
            eff_role = "tool_output" if rl == "tool" else (rl if rl else None)
            segments.append((turn, eff_role, text.strip()))

    return segments


def _messages_from_openai_completion(data: dict[str, Any]) -> list[dict[str, Any]] | None:
    """OpenAI-style chat completion export."""

    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    first = choices[0]
    if not isinstance(first, dict):
        return None
    msg = first.get("message")
    if isinstance(msg, dict) and ("role" in msg or "content" in msg):
        return [msg]
    return None


def _flatten_session_dict(data: dict[str, Any]) -> list[dict[str, Any]] | None:
    """Collect message dicts from nested session envelopes."""

    for key in ("sessions", "chats", "threads", "runs"):
        lst = data.get(key)
        if not isinstance(lst, list) or not lst:
            continue
        acc: list[dict[str, Any]] = []
        for pack in lst:
            if not isinstance(pack, dict):
                continue
            inner = pack.get("messages") or pack.get("conversation") or pack.get("history")
            if isinstance(inner, list):
                for m in inner:
                    if isinstance(m, dict):
                        acc.append(m)
        if acc:
            return acc
    return None


def _unpack_session_json(data: Any) -> list[tuple[int, str | None, str]]:
    """Interpret common OpenClaw / chat export envelopes."""

    candidates: Any = None

    if isinstance(data, dict):
        oa = _messages_from_openai_completion(data)
        if oa is not None:
            return _segments_from_messages(oa)

        flat_sess = _flatten_session_dict(data)
        if flat_sess is not None:
            return _segments_from_messages(flat_sess)

        inner = (
            data.get("messages")
            or data.get("conversation")
            or data.get("transcript")
            or data.get("history")
            or data.get("chat")
            or data.get("dialog")
        )
        if isinstance(inner, list):
            candidates = inner
        else:
            for key in ("sessions", "turns"):
                lst = data.get(key)
                if isinstance(lst, list) and lst and isinstance(lst[0], dict):
                    if "messages" in lst[0]:
                        flat: list[dict[str, Any]] = []
                        for pack in lst:
                            if isinstance(pack.get("messages"), list):
                                flat.extend(pack["messages"])
                        candidates = flat
                        break

    elif isinstance(data, list):
        if data and all(isinstance(x, dict) for x in data):
            if data and any(k in data[0] for k in ("role", "content", "speaker", "turn")):
                candidates = data
            elif len(data) == 1 and isinstance(data[0], dict):
                cand2 = (
                    data[0].get("messages")
                    or data[0].get("conversation")
                    or data[0].get("history")
                )
                if isinstance(cand2, list):
                    candidates = cand2

    if isinstance(candidates, list):
        return _segments_from_messages(candidates)

    return []


def _fallback_json_segments(data: Any) -> list[tuple[int, str | None, str]]:
    """When structure is unknown, emit long string leaves and nested tool call names."""

    out: list[tuple[int, str | None, str]] = []

    def walk(node: Any, turn: int) -> None:
        if isinstance(node, str):
            s = node.strip()
            if len(s) >= 160:
                out.append((turn, None, s))
            return

        if isinstance(node, dict):
            t_next = turn
            for k in ("turn", "step", "message_index", "index"):
                v = node.get(k)
                if isinstance(v, int):
                    t_next = max(t_next, v)
                elif isinstance(v, str) and v.isdigit():
                    t_next = max(t_next, int(v))

            aliases: list[str] = []
            for key in STRUCT_TOOL_KEYS:
                if key in node:
                    aliases.extend(_tool_names_from_calls(node[key]))
            for tn in aliases:
                out.append((t_next, "tool", tn))

            for vv in node.values():
                walk(vv, t_next)
            return

        if isinstance(node, list):
            for i, vv in enumerate(node):
                walk(vv, max(turn, i + 1))

    walk(data, 1)

    uniq: list[tuple[int, str | None, str]] = []
    seen: set[tuple[int, str | None, str]] = set()
    for seg in out:
        if seg not in seen:
            seen.add(seg)
            uniq.append(seg)
    return uniq


def _try_json_fence(text: str) -> Any | None:
    """If the file is markdown wrapping a single JSON block, parse it."""

    m = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
    if not m:
        return None
    blob = m.group(1).strip()
    if not blob.startswith("{") and not blob.startswith("["):
        return None
    try:
        return json.loads(blob)
    except json.JSONDecodeError:
        return None


def parse_ndjson_text(raw: str) -> list[tuple[int, str | None, str]]:
    """One JSON object per line; each row treated as a message when it looks like one."""

    if not raw.strip():
        return []
    messages: list[dict[str, Any]] = []
    for i, line in enumerate(raw.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and any(k in obj for k in ("role", "content", "text", "speaker", "message")):
            if "turn" not in obj and "message_index" not in obj:
                obj = {**obj, "message_index": i}
            messages.append(obj)
    if not messages:
        return []
    return _segments_from_messages(messages)


def parse_ndjson_session(path: Path) -> list[tuple[int, str | None, str]]:
    return parse_ndjson_text(read_text(path))


def parse_json_session(path: Path) -> list[tuple[int, str | None, str]]:
    raw = read_text(path)
    if not raw.strip():
        return []

    suf = path.suffix.lower()
    if suf in (".jsonl", ".ndjson"):
        return parse_ndjson_session(path)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        fenced = _try_json_fence(raw)
        if fenced is not None:
            data = fenced
        else:
            nd = parse_ndjson_text(raw)
            if nd:
                return nd
            return []

    structured = _unpack_session_json(data)
    if structured:
        return structured

    return _fallback_json_segments(data)


def parse_markdown_session(path: Path) -> list[tuple[int, str | None, str]]:
    """Transcripts with markdown role headings and body paragraphs."""

    segments: list[tuple[int, str | None, str]] = []
    current_turn = 1
    current_role: str | None = None
    buf: list[str] = []

    def flush() -> None:
        nonlocal buf, current_role, current_turn
        body = "\n".join(buf).strip()
        buf = []
        if not body:
            return
        rl = current_role
        if rl == "human":
            rl = "user"
        eff = "tool_output" if rl == "tool" else rl
        segments.append((current_turn, eff, body))

    for line_number, line in enumerate(read_text(path).splitlines(), start=1):
        m_turn = TURN_PATTERN.search(line)
        if m_turn:
            current_turn = int(m_turn.group(1))

        mr_head = MD_ROLE_HEADER.match(line.strip())
        if mr_head:
            flush()
            r = mr_head.group("role").lower()
            current_role = "user" if r == "human" else r
            continue

        m_role = ROLE_PREFIX.match(line)
        if m_role:
            flush()
            rl = m_role.group("role").lower()
            current_role = "user" if rl == "human" else rl
            segments.append((current_turn, current_role, m_role.group("body").strip()))
            current_role = None
            continue

        stripped = line.rstrip("\n\r")
        if stripped.strip():
            buf.append(stripped)
        elif buf:
            buf.append("")

        current_turn = max(current_turn, line_number)

    flush()
    return segments


def parse_text_session(path: Path) -> list[tuple[int, str | None, str]]:
    """Line-oriented logs with optional turn hints (compatible with optimize_context)."""

    suf = path.suffix.lower()
    if suf in (".md", ".markdown"):
        md_segs = parse_markdown_session(path)
        if md_segs:
            return md_segs

    segments: list[tuple[int, str | None, str]] = []
    current_turn = 1

    for line_number, line in enumerate(read_text(path).splitlines(), start=1):
        m_turn = TURN_PATTERN.search(line)
        if m_turn:
            current_turn = int(m_turn.group(1))
        else:
            current_turn = max(current_turn, line_number)

        m_role = ROLE_PREFIX.match(line)
        if m_role:
            rl = m_role.group("role").lower()
            mapping = {"human": "user"}
            rl = mapping.get(rl, rl)
            segments.append((current_turn, rl, m_role.group("body").strip()))
        elif line.rstrip("\n\r").strip():
            segments.append((current_turn, None, line.rstrip("\n\r").strip()))

    return segments


def parse_session_log(path: Path) -> list[tuple[int, str | None, str]]:
    if not path.exists():
        return []

    suf = path.suffix.lower()
    if suf in (".json", ".jsonl", ".ndjson"):
        segs = parse_json_session(path)
        if segs:
            return segs
        return parse_text_session(path)

    return parse_text_session(path)


def normalize_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _cap(s: str, limit: int) -> str:
    s = s.strip()
    if len(s) <= limit:
        return s
    return s[: max(0, limit - 3)] + "..."


def match_patterns(text: str, patterns: tuple[re.Pattern[str], ...]) -> list[str]:
    hits: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        for pat in patterns:
            m = pat.search(line)
            if m:
                cap = (m.group(1) if m.lastindex else m.group(0)).strip()
                hits.append(_cap(cap, 80 if pat.pattern.startswith("^") else MAX_CAP_LINE))
                break

    compact = normalize_ws(text.replace("\n", " "))
    for pat in patterns:
        if pat.pattern.startswith("^"):
            continue
        m = pat.search(compact)
        if m:
            cap = (m.group(1) if m.lastindex else m.group(0)).strip()
            hits.append(_cap(cap, 240))

    return hits


def extract_tool_signals(text: str) -> Counter[str]:
    counts: Counter[str] = Counter()
    for rg in TOOL_REGEXES:
        for m in rg.finditer(text):
            name = (m.group(1) or "").strip()
            if len(name) < 2 or len(name) > 80:
                continue
            lowered = name.lower()
            if lowered in {"true", "false", "null", "object", "string", "inputs"}:
                continue
            counts[name] += 1
    return counts


def _norm_dedupe_key(text: str) -> str:
    t = normalize_ws(text.lower())
    t = re.sub(r"[`'\"]+", "", t)
    return t[:500]


def dedupe_strings(items: Iterable[str], max_items: int = MAX_CATEGORY_ITEMS) -> list[str]:
    """Order-preserving dedupe with normalization; drops near-subsumed short strings."""

    ordered: list[str] = []
    seen_keys: set[str] = set()
    for raw in items:
        s = normalize_ws(raw)
        if not s or len(s) < 3:
            continue
        key = _norm_dedupe_key(s)
        if key in seen_keys:
            continue
        dup = False
        for existing in ordered:
            ek = _norm_dedupe_key(existing)
            if key == ek:
                dup = True
                break
            shorter, longer = (s, existing) if len(s) < len(existing) else (existing, s)
            if len(shorter) >= 25 and shorter.lower() in longer.lower():
                dup = True
                break
        if dup:
            continue
        seen_keys.add(key)
        ordered.append(s)
        if len(ordered) >= max_items:
            break
    return ordered


@dataclass
class ExtractedFinding:
    category: str
    text: str
    turn: int
    role: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"category": self.category, "text": self.text, "turn": self.turn, "role": self.role}


@dataclass
class AnalysisReport:
    """Aggregate stats for markdown / JSON report sections."""

    segment_count: int
    role_counts: dict[str, int]
    turns_span: tuple[int, int]
    char_estimate: int
    extraction_counts: dict[str, int]
    dedupe_dropped_estimate: dict[str, int]
    top_tools: list[tuple[str, int]]

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["turns_span"] = {"min": self.turns_span[0], "max": self.turns_span[1]}
        return d


@dataclass
class ConversationDigest:
    """Structured output for markdown + JSON."""

    source: str
    generated_at_utc: str
    segments: list[tuple[int, str | None, str]]
    decisions: list[str]
    learnings: list[str]
    tasks: list[str] = field(default_factory=list)
    insights: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    corrections: list[str] = field(default_factory=list)
    preferences: list[str] = field(default_factory=list)
    findings: list[ExtractedFinding] = field(default_factory=list)
    tool_structured: Counter[str] = field(default_factory=Counter)
    tool_textual: Counter[str] = field(default_factory=Counter)
    analysis: AnalysisReport | None = None

    def all_tools(self) -> Counter[str]:
        merged: Counter[str] = Counter(self.tool_structured)
        merged.update(self.tool_textual)
        return merged


def analyze_segments(segments: list[tuple[int, str | None, str]], source_display: str) -> ConversationDigest:
    decisions_acc: list[str] = []
    learnings_acc: list[str] = []
    tasks_acc: list[str] = []
    insights_acc: list[str] = []
    errors_acc: list[str] = []
    corrections_acc: list[str] = []
    preferences_acc: list[str] = []
    findings: list[ExtractedFinding] = []
    structured_tools: Counter[str] = Counter()
    blobs_for_text_tools: list[str] = []

    def add_findings(category: str, texts: list[str], turn: int, role: str | None) -> None:
        for t in texts:
            tt = normalize_ws(t)
            if len(tt) < 4:
                continue
            findings.append(ExtractedFinding(category=category, text=tt, turn=turn, role=role))

    for turn, role, text in segments:
        rl = (role or "").lower()
        if rl == "tool":
            tn = normalize_ws(text.replace("[tool:", "").replace("]", "").strip())
            if tn:
                structured_tools[tn.split("(", 1)[0].strip()] += 1
            continue

        if rl == "tool_output":
            blobs_for_text_tools.append(text)
            errs = match_patterns(text, ERROR_LINE_PATTERNS)
            errors_acc.extend(errs)
            add_findings("error", errs, turn, role)
            continue

        blobs_for_text_tools.append(text)
        eff_role = role

        if rl in {"", "assistant", "agent"} or rl is None:
            for cat, pats, acc in (
                ("decision", DECISION_LINE_PATTERNS, decisions_acc),
                ("learning", LEARNING_LINE_PATTERNS, learnings_acc),
                ("insight", INSIGHT_LINE_PATTERNS, insights_acc),
                ("task", TASK_LINE_PATTERNS, tasks_acc),
                ("preference", PREFERENCE_LINE_PATTERNS, preferences_acc),
                ("correction", CORRECTION_LINE_PATTERNS, corrections_acc),
                ("error", ERROR_LINE_PATTERNS, errors_acc),
            ):
                got = match_patterns(text, pats)
                acc.extend(got)
                add_findings(cat, got, turn, eff_role)
        elif rl == "user":
            for cat, pats, acc in (
                ("decision", DECISION_LINE_PATTERNS, decisions_acc),
                ("task", TASK_LINE_PATTERNS, tasks_acc),
                ("preference", PREFERENCE_LINE_PATTERNS, preferences_acc),
                ("learning", LEARNING_LINE_PATTERNS, learnings_acc),
            ):
                got = match_patterns(text, pats)
                acc.extend(got)
                add_findings(cat, got, turn, eff_role)
        elif rl == "system":
            got = match_patterns(text, TASK_LINE_PATTERNS)
            tasks_acc.extend(got)
            add_findings("task", got, turn, eff_role)

    combined = "\n\n".join(blobs_for_text_tools)
    textual_tools = extract_tool_signals(combined)

    role_counts: Counter[str] = Counter()
    turns: list[int] = []
    char_est = 0
    for turn, role, text in segments:
        rkey = role or "unknown"
        role_counts[rkey] += 1
        turns.append(turn)
        char_est += len(text)

    turns_span = (min(turns), max(turns)) if turns else (0, 0)

    def dedupe_list(xs: list[str]) -> list[str]:
        return dedupe_strings(xs)

    decisions_d = dedupe_list(decisions_acc)
    learnings_d = dedupe_list(learnings_acc)
    tasks_d = dedupe_list(tasks_acc)
    insights_d = dedupe_list(insights_acc)
    errors_d = dedupe_list(errors_acc)
    corrections_d = dedupe_list(corrections_acc)
    preferences_d = dedupe_list(preferences_acc)

    dedupe_dropped: dict[str, int] = {
        "decisions": max(0, len(decisions_acc) - len(decisions_d)),
        "learnings": max(0, len(learnings_acc) - len(learnings_d)),
        "tasks": max(0, len(tasks_acc) - len(tasks_d)),
        "insights": max(0, len(insights_acc) - len(insights_d)),
        "errors": max(0, len(errors_acc) - len(errors_d)),
        "corrections": max(0, len(corrections_acc) - len(corrections_d)),
        "preferences": max(0, len(preferences_acc) - len(preferences_d)),
    }

    extraction_counts = {
        "decisions": len(decisions_d),
        "learnings": len(learnings_d),
        "tasks": len(tasks_d),
        "insights": len(insights_d),
        "errors": len(errors_d),
        "corrections": len(corrections_d),
        "preferences": len(preferences_d),
    }

    analysis = AnalysisReport(
        segment_count=len(segments),
        role_counts=dict(sorted(role_counts.items(), key=lambda kv: (-kv[1], kv[0]))),
        turns_span=turns_span,
        char_estimate=char_est,
        extraction_counts=extraction_counts,
        dedupe_dropped_estimate=dedupe_dropped,
        top_tools=(Counter(structured_tools) + Counter(textual_tools)).most_common(25),
    )

    # Dedupe findings list (keep earliest turn per normalized key)
    seen_f: set[tuple[str, str]] = set()
    findings_unique: list[ExtractedFinding] = []
    for f in sorted(findings, key=lambda x: (x.turn, x.category)):
        k = (f.category, _norm_dedupe_key(f.text))
        if k in seen_f:
            continue
        seen_f.add(k)
        findings_unique.append(f)
    findings_unique = findings_unique[:300]

    return ConversationDigest(
        source=source_display,
        generated_at_utc=datetime.now(timezone.utc).isoformat(),
        segments=segments,
        decisions=decisions_d,
        learnings=learnings_d,
        tasks=tasks_d,
        insights=insights_d,
        errors=errors_d,
        corrections=corrections_d,
        preferences=preferences_d,
        findings=findings_unique,
        tool_structured=structured_tools,
        tool_textual=textual_tools,
        analysis=analysis,
    )


def _section(title: str, items: list[str], empty_note: str) -> list[str]:
    lines = [f"## {title}", ""]
    if items:
        for item in items:
            lines.append(f"- {item}")
    else:
        lines.append(f"- *({empty_note})*")
    lines.append("")
    return lines


def render_markdown(d: ConversationDigest) -> str:
    lines = [
        "# Conversation extract & analysis",
        "",
        f"- **Source**: `{d.source}`",
        f"- **Generated (UTC)**: {d.generated_at_utc}",
        f"- **Segments indexed**: {len(d.segments)}",
        "",
    ]

    if d.analysis:
        a = d.analysis
        lines.extend(
            [
                "## Analysis report",
                "",
                f"- **Turn span**: {a.turns_span[0]} → {a.turns_span[1]}",
                f"- **Approx. transcript characters** (segment bodies): {a.char_estimate}",
                "",
                "### Segments by role",
                "",
            ]
        )
        for role, ct in list(a.role_counts.items())[:30]:
            lines.append(f"- `{role}`: {ct}")
        lines.extend(["", "### Extraction summary", ""])
        for k, v in a.extraction_counts.items():
            lines.append(f"- **{k}** (after dedupe): {v}")
        lines.extend(["", "### Duplicates merged (raw − unique)", ""])
        for k, v in a.dedupe_dropped_estimate.items():
            if v > 0:
                lines.append(f"- {k}: {v}")
        if not any(v > 0 for v in a.dedupe_dropped_estimate.values()):
            lines.append("- *(no duplicate merges beyond exact/near-substring rules)*")
        lines.append("")

    lines.extend(_section("Decisions", d.decisions, "no explicit decision lines detected"))
    lines.extend(_section("Tasks & follow-ups", d.tasks, "no explicit task cues detected"))
    lines.extend(_section("Insights", d.insights, "no explicit insight cues detected"))
    lines.extend(_section("Learnings & takeaways", d.learnings, "no explicit learning cues detected"))
    lines.extend(_section("Errors & failures", d.errors, "no explicit error cues detected"))
    lines.extend(_section("Corrections", d.corrections, "no explicit correction cues detected"))
    lines.extend(_section("Preferences", d.preferences, "no explicit preference cues detected"))

    lines.extend(["## Tool usage", ""])

    merged = d.all_tools()
    if merged:
        for name, ct in merged.most_common(40):
            lines.append(f"- `{name}` — {ct} mentions")
        if d.tool_structured and d.tool_textual:
            lines.extend(
                [
                    "",
                    "Structured tool rows count toward assistant tool blocks;"
                    " additional matches capture names embedded in prose or JSON echoes.",
                ]
            )
    else:
        lines.append("- *(no tool mentions parsed)*")

    if d.findings:
        lines.extend(["", "## Structured findings (traceable)", ""])
        for f in d.findings[:80]:
            role = f.role or "?"
            lines.append(f"- **[{f.category}]** (turn {f.turn}, `{role}`) {f.text}")

    lines.extend(["", "---", "*OpenClaw conversation_extractor.py — distill session value into `memory/`.*"])
    return "\n".join(lines) + "\n"


def digest_to_dict(d: ConversationDigest) -> dict[str, Any]:
    base: dict[str, Any] = {
        "source": d.source,
        "generated_at_utc": d.generated_at_utc,
        "counts": {
            "segments": len(d.segments),
            "decisions": len(d.decisions),
            "learnings": len(d.learnings),
            "tasks": len(d.tasks),
            "insights": len(d.insights),
            "errors": len(d.errors),
            "corrections": len(d.corrections),
            "preferences": len(d.preferences),
            "tool_names_distinct": len(d.all_tools()),
            "findings": len(d.findings),
        },
        "decisions": d.decisions,
        "learnings": d.learnings,
        "tasks": d.tasks,
        "insights": d.insights,
        "errors": d.errors,
        "corrections": d.corrections,
        "preferences": d.preferences,
        "findings": [x.to_dict() for x in d.findings],
        "tools_ranked": d.all_tools().most_common(),
        "tools_structured": dict(d.tool_structured),
        "tools_from_text_heuristic": dict(d.tool_textual),
    }
    if d.analysis:
        base["analysis_report"] = d.analysis.to_dict()
    return base


def write_digest(
    digest: ConversationDigest,
    memory_dir: Path,
    stem: str,
) -> tuple[Path, Path]:
    memory_dir.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^\w\-_.]+", "_", stem).strip("_") or "session"
    tag = utc_stamp()

    md_path = memory_dir / f"conversation_extract_{safe}_{tag}.md"
    js_path = memory_dir / f"conversation_extract_{safe}_{tag}.json"

    md_path.write_text(render_markdown(digest), encoding="utf-8")
    js_path.write_text(json.dumps(digest_to_dict(digest), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    return md_path, js_path


def run_extraction(session_path: Path, memory_dir: Path, workspace_root: Path) -> tuple[Path, Path]:
    segments = parse_session_log(session_path.resolve())

    digest = analyze_segments(segments, session_path.resolve().as_posix())

    stem = session_path.stem
    # prefer session id from parent folder if standard layout .../sessions/foo/session.json
    parent = session_path.parent.name
    if parent and parent not in {".", ""}:
        stem = f"{parent}__{session_path.stem}"

    relative = session_path.resolve().as_posix()
    workspace_posix = workspace_root.resolve().as_posix()
    if relative.startswith(workspace_posix):
        short = Path(relative[len(workspace_posix) :].lstrip("/")).as_posix()
        digest.source = short

    return write_digest(digest, memory_dir, stem)


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Extract tasks, decisions, insights, learnings, errors, corrections, and preferences into memory/"
    )
    p.add_argument(
        "session_log",
        type=Path,
        nargs="?",
        help="Path to session transcript (.json, .jsonl, .md, .log, or text). Omit with --stdin.",
    )
    p.add_argument(
        "--stdin",
        action="store_true",
        help="Read transcript JSON/text from stdin (written to a temp file under memory/).",
    )
    p.add_argument(
        "--memory-dir",
        type=Path,
        default=None,
        help="Destination directory (default: <repo>/memory).",
    )
    p.add_argument(
        "--workspace-root",
        type=Path,
        default=None,
        help="Workspace root for relative paths in output (defaults to repo root).",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    ws = (args.workspace_root or repo_root()).resolve()
    memory_dir = (args.memory_dir or ws / "memory").resolve()

    if args.stdin:
        import tempfile

        memory_dir.mkdir(parents=True, exist_ok=True)
        payload = sys.stdin.read()
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".txt", prefix="stdin_session_", dir=memory_dir)
        try:
            path = Path(tmp.name)
            path.write_text(payload, encoding="utf-8")
        finally:
            tmp.close()

        md_path, json_path = run_extraction(path, memory_dir, ws)
        try:
            path.unlink(missing_ok=True)  # type: ignore[arg-type]
        except OSError:
            pass

        print(f"Wrote {md_path.as_posix()}")
        print(f"Wrote {json_path.as_posix()}")
        return 0

    if not args.session_log:
        sys.stderr.write("error: session_log path required unless --stdin is set.\n")
        return 2

    sp = args.session_log.resolve()
    if not sp.exists():
        sys.stderr.write(f"error: file not found: {sp}\n")
        return 2

    md_path, json_path = run_extraction(sp, memory_dir, ws)
    print(f"Wrote {md_path.as_posix()}")
    print(f"Wrote {json_path.as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
