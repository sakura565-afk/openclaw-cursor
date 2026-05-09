#!/usr/bin/env python3
"""Extract decisions, errors, tools, follow-ups, and learnings from OpenClaw session transcripts.

Public entry points: ``parse_session_log``, ``analyze_segments``, ``ConversationDigest``,
``render_markdown``, ``render_summary_text``, ``run_extraction``.

CLI (no prompts): ``extract`` writes markdown + JSON under ``memory/``; ``summarize`` prints
a short stdout digest (optionally ``--json``).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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
        r"\b(?:concluded|conclusion)\s+(?:that\s+)?(.{10,})",  # allow inline
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

ERROR_LINE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"^\s*(?:error|exception|failure|failed|traceback)\s*[:\-—]\s*(.+)",
        r"^\s*(?:stderr|exit\s*code)\s*[:\-—]\s*(.+)",
        r"(?:^|\s)(?:Traceback\s*\(most recent call last\)|Error:\s*)(.+)",
        r"\b(?:HTTP\s*\d{3}|Connection(?:Error|Refused)|Timeout(?:Error)?|ECONNREFUSED)\b[:\s]?\s*(.{0,200})",
        r"\b(?:RuntimeError|TypeError|ValueError|KeyError|OSError|AssertionError)\s*:\s*(.+)",
        r"(?:命令|脚本).*(?:失败|出错)\s*[：:]\s*(.+)",
    )
)

FOLLOWUP_LINE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"^\s*(?:TODO|FIXME|FOLLOW[_\s-]?UP|follow[-\s]?up)\s*[:\-—]?\s*(.+)",
        r"\b(?:next\s+steps?|action\s+items?|we\s+should\s+(?:revisit|follow\s+up))\b[:.]?\s*(.+)",
        r"\b(?:remind\s+(?:me|us)\s+to|don'?t\s+forget\s+to|still\s+need\s+to)\b\s+(.+)",
        r"\b(?:open\s+question|unresolved|TBD)\s*[:\-—]?\s*(.+)",
        r"(?:待办|后续|跟进)\s*[：:]\s*(.+)",
    )
)

_HEDGE_RE = re.compile(
    r"\b(?:maybe|perhaps|might|unclear|not\s+sure|possibly|probably|TBD)\b",
    re.IGNORECASE,
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
        stripped = line.rstrip("\n\r")
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
        elif stripped.strip():
            segments.append((current_turn, None, stripped.strip()))

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
class TaggedEntry:
    """One extracted line with heuristic flags for triage."""

    text: str
    flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"text": self.text, "flags": list(self.flags)}


def _item_flags(text: str, role: str | None, category: str) -> list[str]:
    """Attach lightweight review flags to an extracted string."""

    flags: list[str] = []
    t = text.strip()
    if len(t) < 18:
        flags.append("brief")
    if len(t) > 220:
        flags.append("verbose")
    rl = (role or "").lower()
    if rl == "user" and category in {"decision", "error", "followup"}:
        flags.append("user_stated")
    if rl == "tool_output" and category == "error":
        flags.append("tool_channel")
    if _HEDGE_RE.search(t):
        flags.append("hedged_language")
    if category == "followup" and re.search(r"\b(?:TODO|FIXME)\b", t, re.I):
        flags.append("explicit_todo")
    return flags


def _merge_tagged(store: dict[str, TaggedEntry], text: str, role: str | None, category: str) -> None:
    cap = text.strip()
    if not cap:
        return
    new_flags = _item_flags(cap, role, category)
    if cap not in store:
        store[cap] = TaggedEntry(text=cap, flags=list(dict.fromkeys(new_flags)))
        return
    ex = store[cap]
    for f in new_flags:
        if f not in ex.flags:
            ex.flags.append(f)


def _ordered_tagged(store: dict[str, TaggedEntry], limit: int) -> list[TaggedEntry]:
    return [store[k] for k in list(store.keys())[:limit]]


def _session_flags(
    decisions: dict[str, TaggedEntry],
    errors: dict[str, TaggedEntry],
    followups: dict[str, TaggedEntry],
    tools: Counter[str],
    segment_count: int,
) -> list[str]:
    out: list[str] = []
    if segment_count == 0:
        out.append("empty_transcript")
    if not decisions:
        out.append("no_decisions_detected")
    if errors:
        out.append("errors_detected")
    if followups:
        out.append("followups_present")
    if not tools:
        out.append("no_tools_detected")
    total_mentions = sum(tools.values())
    if total_mentions > 40:
        out.append("tool_heavy_session")
    if len(errors) > 8:
        out.append("high_error_signal_count")
    if len(followups) > 12:
        out.append("many_followups")
    top = tools.most_common(1)
    if top and total_mentions >= 10 and top[0][1] / total_mentions >= 0.55:
        out.append("dominant_single_tool")
    return out


@dataclass
class ConversationDigest:
    """Structured output for markdown + JSON."""

    source: str
    generated_at_utc: str
    segments: list[tuple[int, str | None, str]]
    decisions: list[TaggedEntry]
    learnings: list[TaggedEntry]
    errors: list[TaggedEntry]
    followups: list[TaggedEntry]
    session_flags: list[str]
    tool_structured: Counter[str] = field(default_factory=Counter)
    tool_textual: Counter[str] = field(default_factory=Counter)

    def all_tools(self) -> Counter[str]:
        merged: Counter[str] = Counter(self.tool_structured)
        merged.update(self.tool_textual)
        return merged


def analyze_segments(segments: list[tuple[int, str | None, str]], source_display: str) -> ConversationDigest:
    decisions_m: dict[str, TaggedEntry] = {}
    learnings_m: dict[str, TaggedEntry] = {}
    errors_m: dict[str, TaggedEntry] = {}
    followups_m: dict[str, TaggedEntry] = {}
    structured_tools: Counter[str] = Counter()
    blobs_for_text_tools: list[str] = []

    assistantish = {"", "assistant", "agent", None}

    for _turn, role, text in segments:
        rl = (role or "").lower()
        if rl == "tool":
            tn = normalize_ws(text.replace("[tool:", "").replace("]", "").strip())
            if tn:
                structured_tools[tn.split("(", 1)[0].strip()] += 1
            continue

        if rl == "tool_output":
            blobs_for_text_tools.append(text)
            for cap in match_patterns(text, ERROR_LINE_PATTERNS):
                _merge_tagged(errors_m, cap, role, "error")
            for cap in match_patterns(text, FOLLOWUP_LINE_PATTERNS):
                _merge_tagged(followups_m, cap, role, "followup")
            continue

        if rl in assistantish:
            for cap in match_patterns(text, DECISION_LINE_PATTERNS):
                _merge_tagged(decisions_m, cap, role, "decision")
            for cap in match_patterns(text, LEARNING_LINE_PATTERNS):
                _merge_tagged(learnings_m, cap, role, "learning")
            for cap in match_patterns(text, ERROR_LINE_PATTERNS):
                _merge_tagged(errors_m, cap, role, "error")
            for cap in match_patterns(text, FOLLOWUP_LINE_PATTERNS):
                _merge_tagged(followups_m, cap, role, "followup")
        elif rl == "user":
            for cap in match_patterns(text, DECISION_LINE_PATTERNS):
                _merge_tagged(decisions_m, cap, role, "decision")
            for cap in match_patterns(text, ERROR_LINE_PATTERNS):
                _merge_tagged(errors_m, cap, role, "error")
            for cap in match_patterns(text, FOLLOWUP_LINE_PATTERNS):
                _merge_tagged(followups_m, cap, role, "followup")
            for cap in match_patterns(text, LEARNING_LINE_PATTERNS):
                _merge_tagged(learnings_m, cap, role, "learning")
        elif rl == "system":
            for cap in match_patterns(text, ERROR_LINE_PATTERNS):
                _merge_tagged(errors_m, cap, role, "error")
            for cap in match_patterns(text, FOLLOWUP_LINE_PATTERNS):
                _merge_tagged(followups_m, cap, role, "followup")
        else:
            for cap in match_patterns(text, ERROR_LINE_PATTERNS):
                _merge_tagged(errors_m, cap, role, "error")
            for cap in match_patterns(text, FOLLOWUP_LINE_PATTERNS):
                _merge_tagged(followups_m, cap, role, "followup")

        blobs_for_text_tools.append(text)

    combined = "\n\n".join(blobs_for_text_tools)
    textual_tools = extract_tool_signals(combined)

    merged_tools: Counter[str] = Counter(structured_tools)
    merged_tools.update(textual_tools)
    session_flags = _session_flags(
        decisions_m,
        errors_m,
        followups_m,
        merged_tools,
        len(segments),
    )

    return ConversationDigest(
        source=source_display,
        generated_at_utc=datetime.now(timezone.utc).isoformat(),
        segments=segments,
        decisions=_ordered_tagged(decisions_m, 120),
        learnings=_ordered_tagged(learnings_m, 120),
        errors=_ordered_tagged(errors_m, 120),
        followups=_ordered_tagged(followups_m, 120),
        session_flags=session_flags,
        tool_structured=structured_tools,
        tool_textual=textual_tools,
    )


def _fmt_tagged_line(entry: TaggedEntry) -> str:
    if entry.flags:
        fl = ", ".join(entry.flags)
        return f"- {entry.text} `[{fl}]`"
    return f"- {entry.text}"


def render_markdown(d: ConversationDigest) -> str:
    lines = [
        "# Conversation extract",
        "",
        f"- **Source**: `{d.source}`",
        f"- **Generated (UTC)**: {d.generated_at_utc}",
        f"- **Segments indexed**: {len(d.segments)}",
        "",
        "## Session flags",
    ]

    if d.session_flags:
        for fl in d.session_flags:
            lines.append(f"- `{fl}`")
    else:
        lines.append("- *(no session-level flags)*")

    lines.extend(["", "## Decisions"])

    if d.decisions:
        for item in d.decisions:
            lines.append(_fmt_tagged_line(item))
    else:
        lines.append("- *(no explicit decision lines detected)*")

    lines.extend(["", "## Errors & failures"])
    if d.errors:
        for item in d.errors:
            lines.append(_fmt_tagged_line(item))
    else:
        lines.append("- *(no explicit error cues detected)*")

    lines.extend(["", "## Follow-ups"])
    if d.followups:
        for item in d.followups:
            lines.append(_fmt_tagged_line(item))
    else:
        lines.append("- *(no follow-up cues detected)*")

    lines.extend(["", "## Learnings & takeaways"])
    if d.learnings:
        for item in d.learnings:
            lines.append(_fmt_tagged_line(item))
    else:
        lines.append("- *(no explicit learning cues detected)*")

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

    lines.extend(["", "---", "*OpenClaw `scripts/conversation_extractor.py` — session distill → `memory/`.*"])
    return "\n".join(lines) + "\n"


def digest_to_dict(d: ConversationDigest) -> dict[str, Any]:
    return {
        "source": d.source,
        "generated_at_utc": d.generated_at_utc,
        "session_flags": list(d.session_flags),
        "counts": {
            "segments": len(d.segments),
            "decisions": len(d.decisions),
            "learnings": len(d.learnings),
            "errors": len(d.errors),
            "followups": len(d.followups),
            "tool_names_distinct": len(d.all_tools()),
        },
        "decisions": [e.to_dict() for e in d.decisions],
        "learnings": [e.to_dict() for e in d.learnings],
        "errors": [e.to_dict() for e in d.errors],
        "followups": [e.to_dict() for e in d.followups],
        "tools_ranked": d.all_tools().most_common(),
        "tools_structured": dict(d.tool_structured),
        "tools_from_text_heuristic": dict(d.tool_textual),
    }


def render_summary_text(d: ConversationDigest, *, top_tools: int = 8) -> str:
    """Plain-text summary for CLI ``summarize`` (no file writes)."""

    lines = [
        f"Source: {d.source}",
        f"Segments: {len(d.segments)}",
        f"Session flags: {', '.join(d.session_flags) or '(none)'}",
        "",
        f"Decisions ({len(d.decisions)}):",
    ]
    for e in d.decisions[:12]:
        suf = f" [{', '.join(e.flags)}]" if e.flags else ""
        lines.append(f"  - {e.text}{suf}")
    if len(d.decisions) > 12:
        lines.append(f"  … +{len(d.decisions) - 12} more")

    lines.extend(["", f"Errors ({len(d.errors)}):"])
    for e in d.errors[:10]:
        suf = f" [{', '.join(e.flags)}]" if e.flags else ""
        lines.append(f"  - {e.text}{suf}")
    if len(d.errors) > 10:
        lines.append(f"  … +{len(d.errors) - 10} more")

    lines.extend(["", f"Follow-ups ({len(d.followups)}):"])
    for e in d.followups[:10]:
        suf = f" [{', '.join(e.flags)}]" if e.flags else ""
        lines.append(f"  - {e.text}{suf}")
    if len(d.followups) > 10:
        lines.append(f"  … +{len(d.followups) - 10} more")

    merged = d.all_tools()
    lines.extend(["", f"Tools ({len(merged)} distinct):"])
    for name, ct in merged.most_common(top_tools):
        lines.append(f"  - {name}: {ct}")
    if len(merged) > top_tools:
        lines.append(f"  … +{len(merged) - top_tools} more tool names")

    return "\n".join(lines) + "\n"


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
    digest = build_digest_from_file(session_path, workspace_root)

    stem = session_path.stem
    parent = session_path.parent.name
    if parent and parent not in {".", ""}:
        stem = f"{parent}__{session_path.stem}"

    return write_digest(digest, memory_dir, stem)


def build_digest_from_file(session_path: Path, workspace_root: Path) -> ConversationDigest:
    """Parse transcript path and return a digest (relative ``source`` when under workspace)."""

    segments = parse_session_log(session_path.resolve())
    digest = analyze_segments(segments, session_path.resolve().as_posix())

    relative = session_path.resolve().as_posix()
    workspace_posix = workspace_root.resolve().as_posix()
    if relative.startswith(workspace_posix):
        short = Path(relative[len(workspace_posix) :].lstrip("/")).as_posix()
        digest.source = short

    return digest


def _stdin_to_temp_path(memory_dir: Path) -> Path:
    import tempfile

    memory_dir.mkdir(parents=True, exist_ok=True)
    payload = sys.stdin.read()
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".txt", prefix="stdin_session_", dir=memory_dir)
    try:
        path = Path(tmp.name)
        path.write_text(payload, encoding="utf-8")
    finally:
        tmp.close()
    return path


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Extract decisions, errors, tools, and follow-ups from OpenClaw transcripts.",
    )
    subs = p.add_subparsers(dest="command", required=True)

    p_ext = subs.add_parser("extract", help="Write markdown + JSON under memory/ (default).")
    p_ext.add_argument(
        "session_log",
        type=Path,
        nargs="?",
        help="Path to session transcript (.json, .log, or text). Omit with --stdin.",
    )
    p_ext.add_argument(
        "--stdin",
        action="store_true",
        help="Read transcript JSON/text from stdin (temp file under memory/).",
    )
    p_ext.add_argument(
        "--memory-dir",
        type=Path,
        default=None,
        help="Destination directory (default: <repo>/memory).",
    )
    p_ext.add_argument(
        "--workspace-root",
        type=Path,
        default=None,
        help="Workspace root for relative paths in output (defaults to repo root).",
    )

    p_sum = subs.add_parser("summarize", help="Print a short stdout summary (no markdown files).")
    p_sum.add_argument(
        "session_log",
        type=Path,
        nargs="?",
        help="Path to transcript. Omit with --stdin.",
    )
    p_sum.add_argument("--stdin", action="store_true", help="Read transcript from stdin.")
    p_sum.add_argument(
        "--workspace-root",
        type=Path,
        default=None,
        help="Workspace root for relative source label in JSON (defaults to repo root).",
    )
    p_sum.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON (counts, flags, top tools) instead of plain text.",
    )
    p_sum.add_argument(
        "--top-tools",
        type=int,
        default=8,
        metavar="N",
        help="Max tool names in text summary (default: 8).",
    )

    return p


def _argv_with_legacy_extract(argv: list[str]) -> list[str]:
    """If the first token is an existing file, assume ``extract`` subcommand."""

    if not argv:
        return argv
    head = argv[0]
    if head in ("extract", "summarize", "-h", "--help"):
        return argv
    if head.startswith("-"):
        return argv
    cand = Path(head).expanduser()
    if cand.is_file():
        return ["extract", *argv]
    return argv


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    argv = _argv_with_legacy_extract(argv)

    args = build_arg_parser().parse_args(argv)
    ws = (args.workspace_root or repo_root()).resolve()

    if args.command == "extract":
        memory_dir = (args.memory_dir or ws / "memory").resolve()
        if args.stdin:
            path = _stdin_to_temp_path(memory_dir)
            try:
                digest = build_digest_from_file(path, ws)
                digest.source = "<stdin>"
                md_path, json_path = write_digest(digest, memory_dir, "stdin_session")
            finally:
                try:
                    path.unlink(missing_ok=True)  # type: ignore[arg-type]
                except OSError:
                    pass
            print(f"Wrote {md_path.as_posix()}")
            print(f"Wrote {json_path.as_posix()}")
            return 0

        if not args.session_log:
            sys.stderr.write("error: session_log path required for extract unless --stdin is set.\n")
            return 2

        sp = args.session_log.resolve()
        if not sp.exists():
            sys.stderr.write(f"error: file not found: {sp}\n")
            return 2

        md_path, json_path = run_extraction(sp, memory_dir, ws)
        print(f"Wrote {md_path.as_posix()}")
        print(f"Wrote {json_path.as_posix()}")
        return 0

    if args.command == "summarize":
        memory_dir = (ws / "memory").resolve()
        if args.stdin:
            path = _stdin_to_temp_path(memory_dir)
            try:
                digest = build_digest_from_file(path, ws)
                digest.source = "<stdin>"
            finally:
                try:
                    path.unlink(missing_ok=True)  # type: ignore[arg-type]
                except OSError:
                    pass
        else:
            if not args.session_log:
                sys.stderr.write("error: session_log path required for summarize unless --stdin is set.\n")
                return 2
            sp = args.session_log.resolve()
            if not sp.exists():
                sys.stderr.write(f"error: file not found: {sp}\n")
                return 2
            digest = build_digest_from_file(sp, ws)

        if args.json:
            payload = {
                "source": digest.source,
                "session_flags": digest.session_flags,
                "counts": digest_to_dict(digest)["counts"],
                "decisions": [e.to_dict() for e in digest.decisions[:30]],
                "errors": [e.to_dict() for e in digest.errors[:30]],
                "followups": [e.to_dict() for e in digest.followups[:30]],
                "tools_top": digest.all_tools().most_common(args.top_tools),
            }
            sys.stdout.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
        else:
            sys.stdout.write(render_summary_text(digest, top_tools=args.top_tools))
        return 0

    sys.stderr.write(f"error: unknown command {args.command!r}\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
