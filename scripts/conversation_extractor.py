#!/usr/bin/env python3
"""Extract decisions, errors, tools, follow-ups, and learnings from OpenClaw session transcripts.

CLI: ``python -m scripts.conversation_extractor extract …`` and ``… summarize …``.
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
        r"^\s*(?:error|exception|traceback|failure|failed|fatal)\s*[:\-—]\s*(.+)",
        r"\b(?:AttributeError|TypeError|ValueError|KeyError|RuntimeError|OSError|"
        r"ImportError|ModuleNotFoundError|SyntaxError|AssertionError)\b\s*[:\-]?\s*(.+)",
        r"(\b(?:HTTP\s*\d{3}|ECONNREFUSED|ENOENT|EACCES|timeout|timed\s+out)\b[^\n]{0,120})",
        r"(\b(?:could\s+not|unable\s+to|failed\s+to|does\s+not\s+exist|not\s+found)\b[^\n]{10,200})",
    )
)

FOLLOWUP_LINE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"^\s*(?:follow[-\s]?up|next\s+steps?|action\s+items?|pending|todo|to\s*[-\s]?do)\s*[:\-—]\s*(.+)",
        r"(\b(?:we\s+should|need\s+to|still\s+need|remains\s+to)\b[^\n]{8,200})",
        r"(\b(?:let\s+me\s+know|circle\s+back|revisit|after\s+you)\b[^\n]{8,200})",
        r"(?:后续|待办|下一步)\s*[：:]\s*(.+)",
    )
)

# Lines that warrant a human flag (inline markers, blockers)
FLAG_INLINE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("todo_marker", re.compile(r"\b(?:TODO|FIXME|HACK|XXX)\b\s*[:\-]?\s*([^\n]{0,160})", re.I)),
    ("blocked_signal", re.compile(r"\b(?:blocked|stuck|cannot\s+proceed|unable\s+to\s+proceed)\b[^\n]{0,200}", re.I)),
    ("ambiguous_signal", re.compile(r"\b(?:not\s+sure|unclear|ambiguous|TBD|to\s+be\s+determined)\b[^\n]{0,200}", re.I)),
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


def collect_inline_flags(turn: int, text: str) -> list[dict[str, Any]]:
    """Surface TODO/blocker/ambiguity markers as structured flags."""

    out: list[dict[str, Any]] = []
    if len(text.strip()) < 8:
        return out
    for code, pat in FLAG_INLINE_PATTERNS:
        for m in pat.finditer(text):
            cap = (m.group(1) if m.lastindex else m.group(0)).strip()
            if len(cap) > 220:
                cap = cap[:217] + "..."
            if cap:
                out.append({"turn": turn, "code": code, "detail": cap})
    return out


def _dedupe_flags(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[Any, ...]] = set()
    uniq: list[dict[str, Any]] = []
    for r in rows:
        key = (r.get("turn"), r.get("code"), r.get("detail"))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(r)
    return uniq


def normalize_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _finalize_flags(
    segments: list[tuple[int, str | None, str]],
    decisions: list[str],
    errors: list[str],
    followups: list[str],
    inline_flags: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    flags = list(inline_flags)
    if not segments:
        flags.append(
            {
                "turn": None,
                "code": "empty_transcript",
                "detail": "No segments parsed from input; check file format.",
            }
        )
    if errors and not decisions:
        flags.append(
            {
                "turn": None,
                "code": "errors_without_decision",
                "detail": "Error-like lines detected but no explicit decision markers; review session outcome.",
            }
        )
    if followups and not decisions:
        flags.append(
            {
                "turn": None,
                "code": "followups_without_decision",
                "detail": "Follow-up cues present without a captured decision; confirm ownership and deadlines.",
            }
        )

    first_word_keys: list[str] = []
    for d in decisions:
        if not d:
            continue
        w = normalize_ws(d).lower().split()[:5]
        if w:
            first_word_keys.append(" ".join(w))
    dup_counts = Counter(first_word_keys)
    for key, n in dup_counts.items():
        if n >= 2 and key:
            flags.append(
                {
                    "turn": None,
                    "code": "possible_duplicate_decisions",
                    "detail": f"Similar decision phrasing appears {n} times: {key[:120]}",
                }
            )

    return _dedupe_flags(flags)


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
    """Structured output for markdown + JSON."""

    source: str
    generated_at_utc: str
    segments: list[tuple[int, str | None, str]]
    decisions: list[str]
    learnings: list[str]
    errors: list[str]
    followups: list[str]
    flags: list[dict[str, Any]]
    tool_structured: Counter[str] = field(default_factory=Counter)
    tool_textual: Counter[str] = field(default_factory=Counter)

    def all_tools(self) -> Counter[str]:
        merged: Counter[str] = Counter(self.tool_structured)
        merged.update(self.tool_textual)
        return merged


def analyze_segments(segments: list[tuple[int, str | None, str]], source_display: str) -> ConversationDigest:
    decisions_acc: list[str] = []
    learnings_acc: list[str] = []
    errors_acc: list[str] = []
    followups_acc: list[str] = []
    structured_tools: Counter[str] = Counter()
    blobs_for_text_tools: list[str] = []
    inline_flags: list[dict[str, Any]] = []

    for turn, role, text in segments:
        rl = (role or "").lower()
        if rl == "tool":
            tn = normalize_ws(text.replace("[tool:", "").replace("]", "").strip())
            if tn:
                structured_tools[tn.split("(", 1)[0].strip()] += 1
            continue

        if rl == "tool_output":
            blobs_for_text_tools.append(text)
            errors_acc.extend(match_patterns(text, ERROR_LINE_PATTERNS))
            followups_acc.extend(match_patterns(text, FOLLOWUP_LINE_PATTERNS))
            inline_flags.extend(collect_inline_flags(turn, text))
            continue

        if rl in {"", "assistant", "agent"} or rl is None:
            decisions_acc.extend(match_patterns(text, DECISION_LINE_PATTERNS))
            learnings_acc.extend(match_patterns(text, LEARNING_LINE_PATTERNS))
            errors_acc.extend(match_patterns(text, ERROR_LINE_PATTERNS))
            followups_acc.extend(match_patterns(text, FOLLOWUP_LINE_PATTERNS))
        elif rl == "user":
            decisions_acc.extend(match_patterns(text, DECISION_LINE_PATTERNS))
            errors_acc.extend(match_patterns(text, ERROR_LINE_PATTERNS))
            followups_acc.extend(match_patterns(text, FOLLOWUP_LINE_PATTERNS))

        blobs_for_text_tools.append(text)
        inline_flags.extend(collect_inline_flags(turn, text))

    combined = "\n\n".join(blobs_for_text_tools)
    textual_tools = extract_tool_signals(combined)

    uniq = lambda xs: list(dict.fromkeys([x for x in xs if x]))  # noqa: E731

    decisions_u = uniq(decisions_acc)[:120]
    learnings_u = uniq(learnings_acc)[:120]
    errors_u = uniq(errors_acc)[:120]
    followups_u = uniq(followups_acc)[:120]
    flags = _finalize_flags(segments, decisions_u, errors_u, followups_u, _dedupe_flags(inline_flags))

    return ConversationDigest(
        source=source_display,
        generated_at_utc=datetime.now(timezone.utc).isoformat(),
        segments=segments,
        decisions=decisions_u,
        learnings=learnings_u,
        errors=errors_u,
        followups=followups_u,
        flags=flags,
        tool_structured=structured_tools,
        tool_textual=textual_tools,
    )


def render_markdown(d: ConversationDigest) -> str:
    lines = [
        f"# Conversation 精华 extract",
        "",
        f"- **Source**: `{d.source}`",
        f"- **Generated (UTC)**: {d.generated_at_utc}",
        f"- **Segments indexed**: {len(d.segments)}",
        "",
        "## Decisions",
    ]

    if d.decisions:
        for item in d.decisions:
            lines.append(f"- {item}")
    else:
        lines.append("- *(no explicit decision lines detected)*")

    lines.extend(["", "## Learnings & takeaways"])
    if d.learnings:
        for item in d.learnings:
            lines.append(f"- {item}")
    else:
        lines.append("- *(no explicit learning cues detected)*")

    lines.extend(["", "## Errors & failures"])
    if d.errors:
        for item in d.errors:
            lines.append(f"- {item}")
    else:
        lines.append("- *(no error-shaped lines detected)*")

    lines.extend(["", "## Follow-ups"])
    if d.followups:
        for item in d.followups:
            lines.append(f"- {item}")
    else:
        lines.append("- *(no follow-up phrasing detected)*")

    lines.extend(["", "## Flags"])
    if d.flags:
        for fl in d.flags:
            turn = fl.get("turn")
            code = fl.get("code", "?")
            detail = fl.get("detail", "")
            turn_s = f"turn {turn}" if turn is not None else "session"
            lines.append(f"- **{code}** ({turn_s}): {detail}")
    else:
        lines.append("- *(no heuristic flags)*")

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

    lines.extend(["", "---", "*OpenClaw conversation_extractor.py — distill session value into `memory/`.*"])
    return "\n".join(lines) + "\n"


def digest_to_dict(d: ConversationDigest) -> dict[str, Any]:
    return {
        "source": d.source,
        "generated_at_utc": d.generated_at_utc,
        "counts": {
            "segments": len(d.segments),
            "decisions": len(d.decisions),
            "learnings": len(d.learnings),
            "errors": len(d.errors),
            "followups": len(d.followups),
            "flags": len(d.flags),
            "tool_names_distinct": len(d.all_tools()),
        },
        "decisions": d.decisions,
        "learnings": d.learnings,
        "errors": d.errors,
        "followups": d.followups,
        "flags": d.flags,
        "tools_ranked": d.all_tools().most_common(),
        "tools_structured": dict(d.tool_structured),
        "tools_from_text_heuristic": dict(d.tool_textual),
    }


def _as_str_list(val: Any) -> list[str]:
    if not isinstance(val, list):
        return []
    out: list[str] = []
    for x in val:
        if isinstance(x, str) and x.strip():
            out.append(x.strip())
    return out


def _summary_headline_from_lists(
    segments_n: int,
    decisions: list[str],
    errors: list[str],
    followups: list[str],
    tools_distinct: int,
    flags: list[dict[str, Any]],
) -> str:
    return (
        f"{len(decisions)} decisions, {len(errors)} error cues, {len(followups)} follow-ups, "
        f"{tools_distinct} tools (distinct), {len(flags)} flags — {segments_n} segments"
    )


def summary_dict(d: ConversationDigest) -> dict[str, Any]:
    """Compact payload for ``summarize --format json``."""

    merged = d.all_tools()
    counts = digest_to_dict(d)["counts"]
    return {
        "source": d.source,
        "counts": counts,
        "headline": _summary_headline_from_lists(
            len(d.segments),
            d.decisions,
            d.errors,
            d.followups,
            len(merged),
            d.flags,
        ),
        "decisions_top": d.decisions[:5],
        "learnings_top": d.learnings[:5],
        "errors_top": d.errors[:5],
        "followups_top": d.followups[:5],
        "tools_top": [{"name": k, "count": v} for k, v in merged.most_common(8)],
        "flags": d.flags[:25],
    }


def format_summary(d: ConversationDigest, fmt: str) -> str:
    """Short stdout-oriented summary (markdown or JSON)."""

    kind = fmt.lower().strip()
    if kind == "json":
        return json.dumps(summary_dict(d), indent=2, ensure_ascii=False) + "\n"

    hl = _summary_headline_from_lists(
        len(d.segments),
        d.decisions,
        d.errors,
        d.followups,
        len(d.all_tools()),
        d.flags,
    )
    lines = [
        "# Transcript summary",
        "",
        f"- **Source**: `{d.source}`",
        f"- **{hl}**",
        "",
        "## Flags",
    ]
    if d.flags:
        for fl in d.flags[:25]:
            turn = fl.get("turn")
            code = fl.get("code", "?")
            detail = fl.get("detail", "")
            turn_s = f"turn {turn}" if turn is not None else "session"
            lines.append(f"- **{code}** ({turn_s}): {detail}")
    else:
        lines.append("- *(none)*")

    def block(title: str, items: list[str]) -> None:
        lines.extend(["", f"## {title}"])
        if items:
            for it in items[:8]:
                lines.append(f"- {it}")
        else:
            lines.append("- *(none)*")

    block("Decisions", d.decisions)
    block("Errors", d.errors)
    block("Follow-ups", d.followups)
    block("Learnings", d.learnings)

    lines.extend(["", "## Tools (top)"])
    merged = d.all_tools()
    if merged:
        for name, ct in merged.most_common(8):
            lines.append(f"- `{name}` — {ct}")
    else:
        lines.append("- *(none)*")

    return "\n".join(lines) + "\n"


def format_summary_from_export(data: dict[str, Any], fmt: str) -> str:
    """Summarize a prior ``conversation_extract_*.json`` (including legacy shapes)."""

    counts = data.get("counts") if isinstance(data.get("counts"), dict) else {}
    decisions = _as_str_list(data.get("decisions"))
    learnings = _as_str_list(data.get("learnings"))
    errors = _as_str_list(data.get("errors"))
    followups = _as_str_list(data.get("followups"))
    flags_raw = data.get("flags")
    flags: list[dict[str, Any]] = flags_raw if isinstance(flags_raw, list) else []
    source = str(data.get("source", ""))

    ranked = data.get("tools_ranked")
    tools_distinct = int(counts.get("tool_names_distinct", 0))
    if isinstance(ranked, list) and ranked:
        tools_distinct = max(tools_distinct, len(ranked))
    elif isinstance(ranked, list):
        tools_distinct = max(tools_distinct, 0)

    segments_n = int(counts.get("segments", 0))
    headline = _summary_headline_from_lists(segments_n, decisions, errors, followups, tools_distinct, flags)

    kind = fmt.lower().strip()
    if kind == "json":
        tools_top: list[dict[str, Any]] = []
        if isinstance(ranked, list):
            for pair in ranked[:8]:
                if isinstance(pair, (list, tuple)) and len(pair) == 2:
                    tools_top.append({"name": str(pair[0]), "count": int(pair[1])})
        payload = {
            "source": source,
            "counts": counts,
            "headline": headline,
            "decisions_top": decisions[:5],
            "learnings_top": learnings[:5],
            "errors_top": errors[:5],
            "followups_top": followups[:5],
            "tools_top": tools_top,
            "flags": flags[:25],
        }
        return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"

    lines = [
        "# Transcript summary (from export JSON)",
        "",
        f"- **Source**: `{source}`",
        f"- **{headline}**",
        "",
        "## Flags",
    ]
    if flags:
        for fl in flags[:25]:
            if not isinstance(fl, dict):
                continue
            turn = fl.get("turn")
            code = fl.get("code", "?")
            detail = fl.get("detail", "")
            turn_s = f"turn {turn}" if turn is not None else "session"
            lines.append(f"- **{code}** ({turn_s}): {detail}")
    else:
        lines.append("- *(none)*")

    def block(title: str, items: list[str]) -> None:
        lines.extend(["", f"## {title}"])
        if items:
            for it in items[:8]:
                lines.append(f"- {it}")
        else:
            lines.append("- *(none)*")

    block("Decisions", decisions)
    block("Errors", errors)
    block("Follow-ups", followups)
    block("Learnings", learnings)

    lines.extend(["", "## Tools (from export)"])
    if isinstance(ranked, list) and ranked:
        for pair in ranked[:8]:
            if isinstance(pair, (list, tuple)) and len(pair) == 2:
                lines.append(f"- `{pair[0]}` — {pair[1]}")
    else:
        lines.append("- *(none listed)*")

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


def _apply_workspace_relative_source(digest: ConversationDigest, session_path: Path, workspace_root: Path) -> None:
    relative = session_path.resolve().as_posix()
    workspace_posix = workspace_root.resolve().as_posix()
    if relative.startswith(workspace_posix):
        short = Path(relative[len(workspace_posix) :].lstrip("/")).as_posix()
        digest.source = short


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="conversation_extractor",
        description="Extract decisions, errors, tools, and follow-ups from OpenClaw transcripts.",
    )
    sub = p.add_subparsers(dest="command", required=True)

    ex = sub.add_parser("extract", help="Write markdown + JSON digest into memory/.")
    ex.add_argument(
        "session_log",
        type=Path,
        nargs="?",
        help="Path to session transcript (.json, .log, or text). Omit with --stdin.",
    )
    ex.add_argument(
        "--stdin",
        action="store_true",
        help="Read transcript JSON/text from stdin (written to a temp file under memory/).",
    )
    ex.add_argument(
        "--memory-dir",
        type=Path,
        default=None,
        help="Destination directory (default: <repo>/memory).",
    )
    ex.add_argument(
        "--workspace-root",
        type=Path,
        default=None,
        help="Workspace root for relative paths in output (defaults to repo root).",
    )

    sm = sub.add_parser("summarize", help="Print a short summary to stdout (no files written).")
    sm.add_argument(
        "--from-json",
        type=Path,
        default=None,
        dest="digest_json",
        help="Use a prior conversation_extract_*.json from extract instead of a raw transcript.",
    )
    sm.add_argument(
        "session_log",
        type=Path,
        nargs="?",
        help="Transcript path when --from-json is not set.",
    )
    sm.add_argument(
        "--format",
        choices=("md", "json"),
        default="md",
        help="Output format (default: md).",
    )
    sm.add_argument(
        "--workspace-root",
        type=Path,
        default=None,
        help="Workspace root for relative source label when summarizing a transcript file.",
    )
    return p


def _cmd_extract(args: argparse.Namespace, ws: Path, memory_dir: Path) -> int:
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
        sys.stderr.write("error: extract requires session_log path unless --stdin is set.\n")
        return 2

    sp = args.session_log.resolve()
    if not sp.exists():
        sys.stderr.write(f"error: file not found: {sp}\n")
        return 2

    md_path, json_path = run_extraction(sp, memory_dir, ws)
    print(f"Wrote {md_path.as_posix()}")
    print(f"Wrote {json_path.as_posix()}")
    return 0


def _cmd_summarize(args: argparse.Namespace, ws: Path) -> int:
    fmt = args.format
    if args.digest_json:
        jp = args.digest_json.resolve()
        if not jp.exists():
            sys.stderr.write(f"error: JSON file not found: {jp}\n")
            return 2
        raw = read_text(jp)
        if not raw.strip():
            sys.stderr.write("error: empty JSON file.\n")
            return 2
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            sys.stderr.write(f"error: invalid JSON: {e}\n")
            return 2
        if not isinstance(data, dict):
            sys.stderr.write("error: JSON root must be an object.\n")
            return 2
        sys.stdout.write(format_summary_from_export(data, fmt))
        return 0

    if args.session_log:
        sp = args.session_log.resolve()
        if not sp.exists():
            sys.stderr.write(f"error: file not found: {sp}\n")
            return 2
        segments = parse_session_log(sp)
        digest = analyze_segments(segments, sp.as_posix())
        _apply_workspace_relative_source(digest, sp, ws)
        sys.stdout.write(format_summary(digest, fmt))
        return 0

    sys.stderr.write("error: summarize requires --from-json or a session transcript path.\n")
    return 2


def main(argv: list[str] | None = None) -> int:
    argv_list = list(sys.argv[1:] if argv is None else argv)
    if (
        argv_list
        and argv_list[0] not in ("extract", "summarize", "-h", "--help")
        and not argv_list[0].startswith("-")
    ):
        p0 = Path(argv_list[0])
        if p0.is_file():
            argv_list = ["extract", *argv_list]

    args = build_arg_parser().parse_args(argv_list)
    ws = (args.workspace_root or repo_root()).resolve()

    if args.command == "extract":
        memory_dir = (args.memory_dir or ws / "memory").resolve()
        return _cmd_extract(args, ws, memory_dir)
    if args.command == "summarize":
        return _cmd_summarize(args, ws)

    sys.stderr.write("error: unknown command.\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
