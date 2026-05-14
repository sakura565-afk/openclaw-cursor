#!/usr/bin/env python3
"""Extract categorized Q&A and signal-rich exchanges from OpenClaw session logs.

Reads transcript-style text logs or JSON message exports (typically under
``sessions/``), classifies exchanges for self-improvement pipelines, and writes
structured Markdown under ``.learnings/conversations/``. Includes deduplication
against previously extracted files and a small CLI (extract / list / search).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.optimize_context import TURN_PATTERN, read_text, repo_root

# -----------------------------------------------------------------------------
# Conversation taxonomy (stored in front matter and filenames)
# -----------------------------------------------------------------------------

ConversationType = Literal[
    "error_corrections",
    "decisions",
    "tool_usages",
    "insights",
    "questions",
]

CONVERSATION_TYPES: tuple[ConversationType, ...] = (
    "error_corrections",
    "decisions",
    "tool_usages",
    "insights",
    "questions",
)

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
        r"^\s*(?:learning|lesson|takeaway|insight|key\s+learning)\s*[:\-—]\s*(.+)",
        r"^\s*\*{0,2}(?:important|remember|note)\*{0,2}\s*[:\-—]\s*(.+)",
        r"(?:核心价值|关键点|经验教训|结论是|需要注意的是)\s*[：:]\s*(.+)",
    )
)

PATTERN_LINE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"^\s*(?:pattern|playbook|workflow|recipe|checklist)\s*[:\-—]\s*(.+)",
        r"^\s*(?:convention|standard|rule\s+of\s+thumb|guideline)\s*[:\-—]\s*(.+)",
        r"^\s*(?:reusable|repeatable)\s+(?:approach|pattern|steps?)\s*[:\-—]?\s*(.+)",
        r"(?:when\s+you|when\s+we|if\s+you)\s+.{5,120}?\s+(?:use|prefer|run|call|apply)\s+(.{10,})",
        r"(?:always|never)\s+(?:do|use|run|prefer|avoid|call)\s+(.+)",
    )
)

ERROR_CORRECTION_HINT = re.compile(
    r"(?i)\b("
    r"actually|correction|corrected|my mistake|i was wrong|sorry[, ]|"
    r"that was wrong|misread|misunderstood|mis-?stated|"
    r"instead of|not quite|not correct|erroneous|"
    r"\bfix(?:ed|ing)?\b.*\b(error|bug)|"
    r"rollback|revert(?:ed)?|undo that"
    r")\b"
)

USER_QUESTIONISH = re.compile(
    r"(?i)(?:^|\n)\s*(?:"
    r".*\?|"  # explicit question mark
    r"(?:how|what|why|when|where|who|which)\b.{6,}|"
    r"(?:could|can|should|would|is|are|do|does|did)\s+you\b.{6,}|"
    r"(?:please|pls)\s+(?:explain|clarify|help)"
    r")"
)

SCHEMA_VERSION = 1
ARTIFACT_TYPE = "conversation_knowledge"

TOOL_REGEXES: tuple[re.Pattern[str], ...] = (
    re.compile(r'\b(?:invoke|calling|called)\s+(?:tool\s+)?[`"]?([\w\-./:]+)[`"]?', re.I),
    re.compile(r"`(?:functions?\.)?([\w\-]+)", re.I),
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


MIN_EXCHANGE_CHARS = 24


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")


def default_conversations_dir(workspace_root: Path) -> Path:
    """Directory for per-exchange Markdown extracts (gitignored by default)."""

    return (workspace_root / ".learnings" / "conversations").resolve()


def fingerprint_for_text(*parts: str) -> str:
    """Stable SHA-256 hex digest of normalized joined text (full length for YAML)."""

    blob = "\n".join(normalize_ws(p) for p in parts if p)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def load_existing_fingerprints(conversations_dir: Path) -> set[str]:
    """Collect ``content_fingerprint`` values already stored on disk."""

    found: set[str] = set()
    if not conversations_dir.is_dir():
        return found
    for path in conversations_dir.glob("*.md"):
        for line in read_text(path).splitlines():
            stripped = line.strip()
            if stripped.startswith("content_fingerprint:"):
                fp = stripped.split(":", 1)[1].strip()
                if fp:
                    found.add(fp)
                break
    return found


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

        if typ in ("tool_use", "tool-use", "toolcall", "function", "tool_invocation"):
            name = _stringify_toolish_dict(piece)
            if name:
                tools.append(name)
                return "", tools

        if typ in ("tool_result", "tool-result"):
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

        if not text.strip() and not blob_tools:
            maybe = raw.get("name") or raw.get("tool")
            if isinstance(maybe, str) and rl in {"tool", "assistant", "", "assistant_tool"}:
                segments.append((turn, "tool", maybe))

        rest = raw.get("message") if isinstance(raw.get("message"), str) else None
        if rest:
            text = (text + "\n" + rest).strip() if text else rest

        if text.strip():
            eff_role = "tool_output" if rl == "tool" else (rl if rl else None)
            segments.append((turn, eff_role, text.strip()))

    return segments


def _unpack_session_json(data: Any) -> list[tuple[int, str | None, str]]:
    """Interpret common OpenClaw / chat export envelopes."""

    candidates: Any = None

    if isinstance(data, dict):
        inner = (
            data.get("messages")
            or data.get("conversation")
            or data.get("transcript")
            or data.get("history")
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


def parse_json_session(path: Path) -> list[tuple[int, str | None, str]]:
    raw = read_text(path)
    if not raw.strip():
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []

    structured = _unpack_session_json(data)
    if structured:
        return structured

    return _fallback_json_segments(data)


def parse_text_session(path: Path) -> list[tuple[int, str | None, str]]:
    """Line-oriented logs with optional turn hints (compatible with optimize_context)."""

    segments: list[tuple[int, str | None, str]] = []
    current_turn = 1
    ROLE_PREFIX = re.compile(
        r"^\s*(?P<role>user|human|assistant|agent|tool|system)\s*[:|\\-]+\s*(?P<body>.+)$",
        re.IGNORECASE,
    )

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
    if suf == ".json":
        segs = parse_json_session(path)
        if segs:
            return segs
        return parse_text_session(path)

    return parse_text_session(path)


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
                if len(cap) > 80:
                    cap = cap[:77] + "..."
                hits.append(cap)
                break

    compact = normalize_ws(text.replace("\n", " "))
    for pat in patterns:
        if pat.pattern.startswith("^"):
            continue
        m = pat.search(compact)
        if m:
            cap = (m.group(1) if m.lastindex else m.group(0)).strip()
            short = cap if len(cap) <= 240 else cap[:237] + "..."
            hits.append(short)
    return hits


def normalize_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _dedupe_preserve_order(items: list[str], *, max_items: int) -> list[str]:
    return list(dict.fromkeys([x for x in items if x]))[:max_items]


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


@dataclass
class ConversationDigest:
    """Aggregate session view for JSON summaries and legacy consumers."""

    source: str
    generated_at_utc: str
    segments: list[tuple[int, str | None, str]]
    decisions: list[str]
    learnings: list[str]
    patterns: list[str]
    tool_structured: Counter[str] = field(default_factory=Counter)
    tool_textual: Counter[str] = field(default_factory=Counter)

    def all_tools(self) -> Counter[str]:
        merged: Counter[str] = Counter(self.tool_structured)
        merged.update(self.tool_textual)
        return merged


@dataclass
class ExtractedExchange:
    """One categorized slice of a session suitable for a standalone Markdown file."""

    conversation_type: ConversationType
    source: str
    source_turn: int
    title: str
    user_text: str
    assistant_text: str
    tools_mentioned: list[str]
    content_fingerprint: str

    def body_for_fingerprint(self) -> str:
        tools_line = ", ".join(self.tools_mentioned)
        return "\n".join(
            (
                self.conversation_type,
                self.user_text,
                self.assistant_text,
                tools_line,
            )
        )


def analyze_segments(segments: list[tuple[int, str | None, str]], source_display: str) -> ConversationDigest:
    decisions_acc: list[str] = []
    learnings_acc: list[str] = []
    patterns_acc: list[str] = []
    structured_tools: Counter[str] = Counter()
    blobs_for_text_tools: list[str] = []

    for turn, role, text in segments:
        rl = (role or "").lower()
        if rl == "tool":
            tn = normalize_ws(text.replace("[tool:", "").replace("]", "").strip())
            if tn:
                structured_tools[tn.split("(", 1)[0].strip()] += 1
            continue

        if rl == "tool_output":
            blobs_for_text_tools.append(text)
            continue

        if rl in {"", "assistant", "agent"} or role is None:
            decisions_acc.extend(match_patterns(text, DECISION_LINE_PATTERNS))
            learnings_acc.extend(match_patterns(text, LEARNING_LINE_PATTERNS))
            patterns_acc.extend(match_patterns(text, PATTERN_LINE_PATTERNS))
        elif rl == "user":
            decisions_acc.extend(match_patterns(text, DECISION_LINE_PATTERNS))
            patterns_acc.extend(match_patterns(text, PATTERN_LINE_PATTERNS))

        blobs_for_text_tools.append(text)

    combined = "\n\n".join(blobs_for_text_tools)
    textual_tools = extract_tool_signals(combined)

    cap = 120
    return ConversationDigest(
        source=source_display,
        generated_at_utc=datetime.now(timezone.utc).isoformat(),
        segments=segments,
        decisions=_dedupe_preserve_order(decisions_acc, max_items=cap),
        learnings=_dedupe_preserve_order(learnings_acc, max_items=cap),
        patterns=_dedupe_preserve_order(patterns_acc, max_items=cap),
        tool_structured=structured_tools,
        tool_textual=textual_tools,
    )


def _classify_exchange(
    user_blob: str,
    assistant_blob: str,
    tools: list[str],
) -> ConversationType:
    """Pick a single primary label using a fixed priority ladder."""

    combined = f"{user_blob}\n{assistant_blob}"
    scores: dict[ConversationType, int] = {t: 0 for t in CONVERSATION_TYPES}

    if ERROR_CORRECTION_HINT.search(assistant_blob) or ERROR_CORRECTION_HINT.search(user_blob):
        scores["error_corrections"] += 6
    if match_patterns(combined, DECISION_LINE_PATTERNS):
        scores["decisions"] += 5
    if match_patterns(assistant_blob, LEARNING_LINE_PATTERNS) or match_patterns(
        assistant_blob, PATTERN_LINE_PATTERNS
    ):
        scores["insights"] += 4
    if tools or extract_tool_signals(assistant_blob) or extract_tool_signals(user_blob):
        scores["tool_usages"] += 3
    if "?" in user_blob or USER_QUESTIONISH.search(user_blob.strip()):
        scores["questions"] += 2

    best = max(scores, key=lambda k: scores[k])
    if scores[best] == 0 and len(normalize_ws(assistant_blob)) >= MIN_EXCHANGE_CHARS:
        return "insights"
    if scores[best] == 0:
        return "questions" if user_blob.strip() else "insights"
    return best


def _truncate_title(text: str, limit: int = 72) -> str:
    one = normalize_ws(text)
    if len(one) <= limit:
        return one or "exchange"
    return one[: limit - 1] + "…"


def build_extracted_exchanges(
    segments: list[tuple[int, str | None, str]],
    source_display: str,
) -> list[ExtractedExchange]:
    """Group segments into user/assistant windows and emit classified exchanges."""

    exchanges: list[ExtractedExchange] = []

    user_buf: list[str] = []
    asst_buf: list[str] = []
    tool_buf: list[str] = []
    block_min_turn = 1

    def note_turn(turn: int) -> None:
        nonlocal block_min_turn
        if not (user_buf or asst_buf or tool_buf):
            block_min_turn = turn
        else:
            block_min_turn = min(block_min_turn, turn)

    def flush() -> None:
        nonlocal user_buf, asst_buf, tool_buf, block_min_turn
        u = "\n\n".join(x for x in user_buf if x.strip()).strip()
        a_parts: list[str] = []
        if tool_buf:
            a_parts.append("**Tools (structured):** " + ", ".join(f"`{t}`" for t in dict.fromkeys(tool_buf)))
        if asst_buf:
            a_parts.append("\n\n".join(x for x in asst_buf if x.strip()))
        a = "\n\n".join(a_parts).strip()
        if len(normalize_ws(u + a)) < MIN_EXCHANGE_CHARS and not tool_buf:
            user_buf, asst_buf, tool_buf = [], [], []
            return
        ctype = _classify_exchange(u, "\n".join(asst_buf), tool_buf)
        title = _truncate_title(u or a or (tool_buf[0] if tool_buf else "exchange"))
        fp = fingerprint_for_text(ctype, source_display, str(block_min_turn), u, a, ",".join(tool_buf))
        exchanges.append(
            ExtractedExchange(
                conversation_type=ctype,
                source=source_display,
                source_turn=block_min_turn,
                title=title,
                user_text=u,
                assistant_text=a,
                tools_mentioned=list(dict.fromkeys(tool_buf)),
                content_fingerprint=fp,
            )
        )
        user_buf, asst_buf, tool_buf = [], [], []

    for turn, role, text in segments:
        rl = (role or "").lower()

        if rl == "user":
            if user_buf or asst_buf or tool_buf:
                flush()
            note_turn(turn)
            user_buf.append(text)
            continue

        if rl == "tool":
            note_turn(turn)
            tool_buf.append(text.strip())
            continue

        if rl == "tool_output":
            note_turn(turn)
            asst_buf.append(f"_(tool output)_\n{text}")
            continue

        if rl in {"assistant", "agent", "system"} or role is None:
            note_turn(turn)
            asst_buf.append(text)
            continue

        note_turn(turn)
        asst_buf.append(text)

    if user_buf or asst_buf or tool_buf:
        flush()

    return exchanges


def render_exchange_markdown(ex: ExtractedExchange, extracted_at_utc: str) -> str:
    """YAML front matter plus readable sections for human review."""

    lines = [
        "---",
        f'conversation_type: "{ex.conversation_type}"',
        f"content_fingerprint: {ex.content_fingerprint}",
        f'source: "{ex.source.replace(chr(34), chr(39))}"',
        f"source_turn: {ex.source_turn}",
        f'title: "{ex.title.replace(chr(34), chr(39))}"',
        f"extracted_at_utc: {extracted_at_utc}",
        "---",
        "",
        f"## {ex.title}",
        "",
    ]
    if ex.user_text.strip():
        lines.extend(["### User", "", ex.user_text.strip(), ""])
    if ex.assistant_text.strip():
        lines.extend(["### Assistant", "", ex.assistant_text.strip(), ""])
    if not ex.user_text.strip() and not ex.assistant_text.strip() and ex.tools_mentioned:
        lines.extend(["### Tool usage", "", ", ".join(f"`{t}`" for t in ex.tools_mentioned), ""])

    lines.extend(
        [
            "---",
            "*Generated by `scripts/conversation_extractor.py` for self-improvement memory.*",
            "",
        ]
    )
    return "\n".join(lines)


def render_markdown(d: ConversationDigest) -> str:
    """Aggregate digest view (session-level summary Markdown)."""

    lines = [
        "# Conversation knowledge extract",
        "",
        f"- **Source**: `{d.source}`",
        f"- **Generated (UTC)**: {d.generated_at_utc}",
        f"- **Segments indexed**: {len(d.segments)}",
        "",
        "## Key decisions",
    ]

    if d.decisions:
        for item in d.decisions:
            lines.append(f"- {item}")
    else:
        lines.append("- *(no explicit decision lines detected)*")

    lines.extend(["", "## Lessons learned & takeaways"])
    if d.learnings:
        for item in d.learnings:
            lines.append(f"- {item}")
    else:
        lines.append("- *(no explicit learning cues detected)*")

    lines.extend(["", "## Reusable patterns"])
    if d.patterns:
        for item in d.patterns:
            lines.append(f"- {item}")
    else:
        lines.append("- *(no workflow or convention cues detected)*")

    lines.extend(["", "## Tool usage"])

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

    lines.extend(
        [
            "",
            "---",
            "*OpenClaw `conversation_extractor.py` — distill session value into `.learnings/conversations/`.*",
        ]
    )
    return "\n".join(lines) + "\n"


def digest_to_dict(d: ConversationDigest) -> dict[str, Any]:
    """JSON envelope for downstream memory pipelines (skills, nightly merge, etc.)."""

    ranked = d.all_tools().most_common()
    return {
        "artifact_type": ARTIFACT_TYPE,
        "schema_version": SCHEMA_VERSION,
        "source": d.source,
        "generated_at_utc": d.generated_at_utc,
        "counts": {
            "segments": len(d.segments),
            "key_decisions": len(d.decisions),
            "lessons_learned": len(d.learnings),
            "reusable_patterns": len(d.patterns),
            "tool_names_distinct": len(d.all_tools()),
        },
        "key_decisions": d.decisions,
        "lessons_learned": d.learnings,
        "reusable_patterns": d.patterns,
        "tools_ranked": [{"name": n, "count": c} for n, c in ranked],
        "tools_structured": dict(d.tool_structured),
        "tools_from_text_heuristic": dict(d.tool_textual),
        "memory_integration": {
            "suggested_tags": _suggested_tags(d),
            "summary_one_liners": _summary_one_liners(d),
        },
        "decisions": d.decisions,
        "learnings": d.learnings,
    }


def _suggested_tags(d: ConversationDigest) -> list[str]:
    tags: list[str] = []
    if d.decisions:
        tags.append("decisions")
    if d.learnings:
        tags.append("lessons")
    if d.patterns:
        tags.append("patterns")
    if d.all_tools():
        tags.append("tools")
    return tags


def _summary_one_liners(d: ConversationDigest) -> list[str]:
    out: list[str] = []
    if d.decisions:
        out.append(f"Decisions captured: {len(d.decisions)}")
    if d.learnings:
        out.append(f"Learnings captured: {len(d.learnings)}")
    if d.patterns:
        out.append(f"Reusable patterns: {len(d.patterns)}")
    top = d.all_tools().most_common(3)
    if top:
        names = ", ".join(f"{n} ({c})" for n, c in top)
        out.append(f"Top tools: {names}")
    return out


def _safe_filename_part(s: str) -> str:
    return re.sub(r"[^\w\-_.]+", "_", s).strip("_") or "session"


def write_exchange_files(
    exchanges: list[ExtractedExchange],
    conversations_dir: Path,
    *,
    existing_fingerprints: set[str] | None = None,
) -> tuple[int, list[Path]]:
    """Write per-exchange Markdown; skip fingerprints already on disk or in ``existing_fingerprints``."""

    conversations_dir.mkdir(parents=True, exist_ok=True)
    known = set(existing_fingerprints or set())
    known |= load_existing_fingerprints(conversations_dir)

    written: list[Path] = []
    skipped = 0
    extracted_at = datetime.now(timezone.utc).isoformat()

    for ex in exchanges:
        if ex.content_fingerprint in known:
            skipped += 1
            continue
        short = ex.content_fingerprint[:12]
        base = f"{ex.conversation_type}__{short}.md"
        path = conversations_dir / base
        suffix = 0
        while path.exists():
            suffix += 1
            path = conversations_dir / f"{ex.conversation_type}__{short}_{suffix}.md"
        path.write_text(render_exchange_markdown(ex, extracted_at), encoding="utf-8")
        known.add(ex.content_fingerprint)
        written.append(path)

    return skipped, written


def write_digest(
    digest: ConversationDigest,
    out_dir: Path,
    stem: str,
) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    safe = _safe_filename_part(stem)
    tag = utc_stamp()

    md_path = out_dir / f"conversation_digest_{safe}_{tag}.md"
    js_path = out_dir / f"conversation_digest_{safe}_{tag}.json"

    md_path.write_text(render_markdown(digest), encoding="utf-8")
    js_path.write_text(json.dumps(digest_to_dict(digest), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    return md_path, js_path


def run_extraction(
    session_path: Path,
    conversations_dir: Path,
    workspace_root: Path,
) -> tuple[Path, Path]:
    """Parse a session file, write digest JSON/Markdown and categorized exchanges."""

    segments = parse_session_log(session_path.resolve())

    stem = session_path.stem
    parent = session_path.parent.name
    if parent and parent not in {".", ""}:
        stem = f"{parent}__{session_path.stem}"

    relative = session_path.resolve().as_posix()
    workspace_posix = workspace_root.resolve().as_posix()
    source_display = relative
    if relative.startswith(workspace_posix):
        source_display = Path(relative[len(workspace_posix) :].lstrip("/")).as_posix()

    digest = analyze_segments(segments, source_display)
    exchanges = build_extracted_exchanges(segments, source_display)

    digest_payload = digest_to_dict(digest)
    digest_payload["exchanges"] = [
        {
            "conversation_type": e.conversation_type,
            "content_fingerprint": e.content_fingerprint,
            "source_turn": e.source_turn,
            "title": e.title,
        }
        for e in exchanges
    ]

    conversations_dir.mkdir(parents=True, exist_ok=True)
    safe = _safe_filename_part(stem)
    tag = utc_stamp()
    md_path = conversations_dir / f"conversation_digest_{safe}_{tag}.md"
    js_path = conversations_dir / f"conversation_digest_{safe}_{tag}.json"

    md_path.write_text(render_markdown(digest), encoding="utf-8")
    js_path.write_text(json.dumps(digest_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    skipped, _written = write_exchange_files(exchanges, conversations_dir)
    digest_payload["exchanges_written"] = len(_written)
    digest_payload["exchanges_skipped_duplicate"] = skipped
    js_path.write_text(json.dumps(digest_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    return md_path, js_path


def iter_session_files(sessions_root: Path) -> list[Path]:
    """Collect likely transcript files under ``sessions/``."""

    if not sessions_root.is_dir():
        return []
    out: list[Path] = []
    for pattern in ("**/*.json", "**/*.txt", "**/*.log", "**/*.md"):
        out.extend(p for p in sessions_root.glob(pattern) if p.is_file())
    return sorted(set(out))


def list_extracted_conversations(conversations_dir: Path, *, limit: int = 200) -> list[Path]:
    """Return recent Markdown extracts (newest first by mtime)."""

    if not conversations_dir.is_dir():
        return []
    files = [p for p in conversations_dir.glob("*.md") if p.is_file()]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return files[:limit]


def search_extracted_conversations(conversations_dir: Path, keyword: str, *, limit: int = 50) -> list[Path]:
    """Case-insensitive substring search across Markdown bodies and front matter."""

    if not keyword.strip() or not conversations_dir.is_dir():
        return []
    needle = keyword.casefold()
    hits: list[Path] = []
    for path in sorted(conversations_dir.glob("*.md")):
        if not path.is_file():
            continue
        blob = read_text(path).casefold()
        if needle in blob:
            hits.append(path)
        if len(hits) >= limit:
            break
    return hits


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Parse OpenClaw session transcripts (text or JSON), extract categorized "
            "exchanges, and store Markdown under .learnings/conversations/."
        )
    )
    p.add_argument(
        "--workspace-root",
        type=Path,
        default=None,
        help="Repository root (defaults to parent of scripts/).",
    )
    p.add_argument(
        "--conversations-dir",
        type=Path,
        default=None,
        help="Override output directory (default: <repo>/.learnings/conversations).",
    )

    sub = p.add_subparsers(dest="command", required=False)

    ex = sub.add_parser("extract", help="Extract from one transcript or scan sessions/")
    ex.add_argument(
        "session_log",
        type=Path,
        nargs="?",
        default=None,
        help="Path to session transcript (.json, .log, .txt, .md).",
    )
    ex.add_argument(
        "--stdin",
        action="store_true",
        help="Read transcript JSON/text from stdin (spooled to a temp file).",
    )
    ex.add_argument(
        "--all-sessions",
        action="store_true",
        help="Process every candidate file under <repo>/sessions/.",
    )
    ex.add_argument(
        "--sessions-dir",
        type=Path,
        default=None,
        help="Root folder to scan with --all-sessions (default: <repo>/sessions).",
    )

    list_p = sub.add_parser("list", help="List extracted Markdown files (most recent first).")
    list_p.add_argument("--limit", type=int, default=200, help="Max paths to print.")

    sr = sub.add_parser("search", help="Search extracted Markdown by keyword")
    sr.add_argument("keyword", help="Case-insensitive substring to match.")
    sr.add_argument("--limit", type=int, default=200, help="Max paths to print.")

    return p


def _normalize_argv(argv: list[str] | None) -> list[str] | None:
    if not argv:
        return argv
    first = argv[0]
    if first in ("-h", "--help", "extract", "list", "search"):
        return argv
    if first == "--stdin":
        return ["extract", *argv]
    # Legacy positional session path: `conversation_extractor.py path/to/log`
    if Path(first).exists():
        return ["extract", *argv]
    return argv


def main(argv: list[str] | None = None) -> int:
    argv = _normalize_argv(list(sys.argv[1:] if argv is None else argv))
    args = build_arg_parser().parse_args(argv)

    ws = (args.workspace_root or repo_root()).resolve()
    conversations_dir = (args.conversations_dir or default_conversations_dir(ws)).resolve()

    if not getattr(args, "command", None):
        sys.stderr.write(
            "error: specify a subcommand (extract, list, search) or pass a session file path.\n"
            "example: python scripts/conversation_extractor.py extract sessions/foo/transcript.json\n"
        )
        return 2

    if args.command == "list":
        paths = list_extracted_conversations(conversations_dir, limit=args.limit)
        if not paths:
            print(f"(no markdown files in {conversations_dir})")
            return 0
        for p in paths:
            print(p.as_posix())
        return 0

    if args.command == "search":
        paths = search_extracted_conversations(conversations_dir, args.keyword, limit=args.limit)
        if not paths:
            print(f"(no matches for {args.keyword!r} under {conversations_dir})")
            return 0
        for p in paths:
            print(p.as_posix())
        return 0

    # extract
    if args.stdin:
        import tempfile

        conversations_dir.mkdir(parents=True, exist_ok=True)
        payload = sys.stdin.read()
        tmp = tempfile.NamedTemporaryFile(
            delete=False, suffix=".txt", prefix="stdin_session_", dir=conversations_dir
        )
        try:
            path = Path(tmp.name)
            path.write_text(payload, encoding="utf-8")
        finally:
            tmp.close()

        md_path, json_path = run_extraction(path, conversations_dir, ws)
        try:
            path.unlink(missing_ok=True)  # type: ignore[arg-type]
        except OSError:
            pass

        print(f"Wrote {md_path.as_posix()}")
        print(f"Wrote {json_path.as_posix()}")
        return 0

    if args.all_sessions:
        sessions_dir = (args.sessions_dir or ws / "sessions").resolve()
        files = iter_session_files(sessions_dir)
        if not files:
            sys.stderr.write(f"error: no session files found under {sessions_dir}\n")
            return 2
        for f in files:
            md_path, json_path = run_extraction(f, conversations_dir, ws)
            print(f"{f.as_posix()} -> {md_path.name}, {json_path.name}")
        return 0

    if not args.session_log:
        sys.stderr.write("error: session_log path required unless --stdin or --all-sessions.\n")
        return 2

    sp = args.session_log.resolve()
    if not sp.exists():
        sys.stderr.write(f"error: file not found: {sp}\n")
        return 2

    md_path, json_path = run_extraction(sp, conversations_dir, ws)
    print(f"Wrote {md_path.as_posix()}")
    print(f"Wrote {json_path.as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
