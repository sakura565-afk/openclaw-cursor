#!/usr/bin/env python3
"""Extract decisions, errors, tools, and follow-ups from OpenClaw session transcripts."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
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
        r"(?:Traceback \(most recent call last\)).+",
        r"(?:Exception|Error)\s*:\s*(.+)",
        r"^\s*(?:error|fatal|failure|failed|exception)\s*[:\-—]\s*(.+)",
        r"\b(?:ECONNREFUSED|ETIMEDOUT|ENOENT|EACCES|403\s+Forbidden|500\s+Internal)\b",
        r"\b(?:stack\s+trace|stderr|non-zero\s+exit|exit\s+code\s+[1-9]\d*)\b",
        r"(?:失败|错误|异常)\s*[：:]\s*(.+)",
    )
)

FOLLOWUP_LINE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"^\s*(?:TODO|FIXME|WIP|TBD)\b[#:\s]\s*(.+)",
        r"(?:follow[-\s]?up|next\s+steps?|open\s+questions?|action\s+items?)\s*[:\-—]\s*(.+)",
        r"(?:still\s+need\s+to|remains?\s+to|blocked\s+on|pending|outstanding)\s*[:\-—]?\s*(.+)",
        r"(?:let'?s\s+(?:revisit|circle\s+back)|need\s+to\s+confirm|for\s+later)\s*[:\-—]?\s*(.+)",
        r"(?:待办|后续|跟进|待确认)\s*[：:]\s*(.+)",
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


def normalize_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def match_pattern_hits(
    text: str, patterns: tuple[re.Pattern[str], ...], role: str | None
) -> list[tuple[str, str | None]]:
    """Return (snippet, role) for each pattern hit (line-oriented + compact fallback)."""

    hits: list[tuple[str, str | None]] = []
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
                hits.append((cap, role))
                break

    compact = normalize_ws(text.replace("\n", " "))
    for pat in patterns:
        if pat.pattern.startswith("^"):
            continue
        m = pat.search(compact)
        if m:
            cap = (m.group(1) if m.lastindex else m.group(0)).strip()
            short = cap if len(cap) <= 240 else cap[:237] + "..."
            hits.append((short, role))
    return hits


URGENT_TERMS = re.compile(
    r"\b(?:urgent|asap|critical|blocking|p0|sev-?0|production\s+down)\b",
    re.I,
)
TODO_INLINE = re.compile(r"\b(?:todo|fixme)\b", re.I)


@dataclass(frozen=True)
class FlaggedItem:
    text: str
    flags: tuple[str, ...] = ()

    def has_flags(self) -> bool:
        return bool(self.flags)

    def to_dict(self) -> dict[str, Any]:
        return {"text": self.text, "flags": list(self.flags)}


def build_flagged_items(pairs: Iterable[tuple[str, str | None]], *, category: str, limit: int = 120) -> list[FlaggedItem]:
    pair_list = [(t.strip(), r) for t, r in pairs if t and str(t).strip()]
    norm_counts: Counter[str] = Counter()
    for txt, _ in pair_list:
        norm_counts[normalize_ws(txt.lower())] += 1

    roles_by_text: dict[str, set[str]] = defaultdict(set)
    for txt, r in pair_list:
        roles_by_text[txt].add((r or "segment").lower())

    seen: set[str] = set()
    out: list[FlaggedItem] = []
    for txt, _r in pair_list:
        if txt in seen:
            continue
        seen.add(txt)
        roles_m = roles_by_text[txt]
        flags: set[str] = set()
        if norm_counts[normalize_ws(txt.lower())] > 1:
            flags.add("duplicate_in_transcript")
        if len(txt) < 28:
            flags.add("short")
        if "tool_output" in roles_m:
            flags.add("from_tool_stream")
        if "user" in roles_m:
            flags.add("user_turn")
        if category in ("error", "followup") and URGENT_TERMS.search(txt):
            flags.add("urgent_language")
        if category == "followup" and TODO_INLINE.search(txt):
            flags.add("todo_marker")
        if category == "error" and len(txt) > 800:
            flags.add("long_snippet")
        if category != "tool" and re.fullmatch(r"[\w./\\-]+", txt) and "/" in txt and len(txt) < 72:
            flags.add("possible_path_fragment")

        out.append(FlaggedItem(text=txt, flags=tuple(sorted(flags))))
        if len(out) >= limit:
            break
    return out


def flag_tool_counts(merged: Counter[str]) -> dict[str, tuple[str, ...]]:
    meta: dict[str, tuple[str, ...]] = {}
    for name, ct in merged.items():
        fl: list[str] = []
        if ct == 1:
            fl.append("single_mention")
        if len(name) > 56:
            fl.append("long_identifier")
        if fl:
            meta[name] = tuple(fl)
    return meta


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
    decisions: list[FlaggedItem] = field(default_factory=list)
    errors: list[FlaggedItem] = field(default_factory=list)
    followups: list[FlaggedItem] = field(default_factory=list)
    learnings: list[FlaggedItem] = field(default_factory=list)
    tool_structured: Counter[str] = field(default_factory=Counter)
    tool_textual: Counter[str] = field(default_factory=Counter)
    tool_flags: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def all_tools(self) -> Counter[str]:
        merged: Counter[str] = Counter(self.tool_structured)
        merged.update(self.tool_textual)
        return merged

    def narrative_items(self) -> list[tuple[str, FlaggedItem]]:
        out: list[tuple[str, FlaggedItem]] = []
        for label, xs in (
            ("decision", self.decisions),
            ("error", self.errors),
            ("followup", self.followups),
            ("learning", self.learnings),
        ):
            for it in xs:
                out.append((label, it))
        return out

    def flagged_narrative(self) -> list[tuple[str, FlaggedItem]]:
        return [(lbl, it) for lbl, it in self.narrative_items() if it.has_flags()]


def analyze_segments(segments: list[tuple[int, str | None, str]], source_display: str) -> ConversationDigest:
    decisions_raw: list[tuple[str, str | None]] = []
    learnings_raw: list[tuple[str, str | None]] = []
    errors_raw: list[tuple[str, str | None]] = []
    follow_raw: list[tuple[str, str | None]] = []
    structured_tools: Counter[str] = Counter()
    blobs_for_text_tools: list[str] = []

    for _turn, role, text in segments:
        rl = (role or "").lower()
        if rl == "tool":
            tn = normalize_ws(text.replace("[tool:", "").replace("]", "").strip())
            if tn:
                structured_tools[tn.split("(", 1)[0].strip()] += 1
            continue

        hit_role: str | None = rl or None

        if rl == "tool_output":
            errors_raw.extend(match_pattern_hits(text, ERROR_LINE_PATTERNS, hit_role))
            follow_raw.extend(match_pattern_hits(text, FOLLOWUP_LINE_PATTERNS, hit_role))
            blobs_for_text_tools.append(text)
            continue

        if rl in {"", "assistant", "agent"}:
            decisions_raw.extend(match_pattern_hits(text, DECISION_LINE_PATTERNS, hit_role))
            learnings_raw.extend(match_pattern_hits(text, LEARNING_LINE_PATTERNS, hit_role))
            errors_raw.extend(match_pattern_hits(text, ERROR_LINE_PATTERNS, hit_role))
            follow_raw.extend(match_pattern_hits(text, FOLLOWUP_LINE_PATTERNS, hit_role))
        elif rl == "user":
            decisions_raw.extend(match_pattern_hits(text, DECISION_LINE_PATTERNS, hit_role))
            errors_raw.extend(match_pattern_hits(text, ERROR_LINE_PATTERNS, hit_role))
            follow_raw.extend(match_pattern_hits(text, FOLLOWUP_LINE_PATTERNS, hit_role))
        else:
            errors_raw.extend(match_pattern_hits(text, ERROR_LINE_PATTERNS, hit_role))
            follow_raw.extend(match_pattern_hits(text, FOLLOWUP_LINE_PATTERNS, hit_role))

        blobs_for_text_tools.append(text)

    combined = "\n\n".join(blobs_for_text_tools)
    textual_tools = extract_tool_signals(combined)
    merged_preview = Counter(structured_tools)
    merged_preview.update(textual_tools)

    return ConversationDigest(
        source=source_display,
        generated_at_utc=datetime.now(timezone.utc).isoformat(),
        segments=segments,
        decisions=build_flagged_items(decisions_raw, category="decision"),
        errors=build_flagged_items(errors_raw, category="error"),
        followups=build_flagged_items(follow_raw, category="followup"),
        learnings=build_flagged_items(learnings_raw, category="learning"),
        tool_structured=structured_tools,
        tool_textual=textual_tools,
        tool_flags=flag_tool_counts(merged_preview),
    )


def _fmt_flagged_item(item: FlaggedItem) -> str:
    if item.flags:
        return f"- {item.text}  — *flags*: {', '.join(item.flags)}"
    return f"- {item.text}"


def render_markdown(d: ConversationDigest) -> str:
    lines = [
        "# Conversation extract",
        "",
        f"- **Source**: `{d.source}`",
        f"- **Generated (UTC)**: {d.generated_at_utc}",
        f"- **Segments indexed**: {len(d.segments)}",
        "",
        "## Decisions",
    ]

    if d.decisions:
        for item in d.decisions:
            lines.append(_fmt_flagged_item(item))
    else:
        lines.append("- *(no explicit decision lines detected)*")

    lines.extend(["", "## Errors & failures"])
    if d.errors:
        for item in d.errors:
            lines.append(_fmt_flagged_item(item))
    else:
        lines.append("- *(no error signals detected)*")

    lines.extend(["", "## Follow-ups"])
    if d.followups:
        for item in d.followups:
            lines.append(_fmt_flagged_item(item))
    else:
        lines.append("- *(no follow-up cues detected)*")

    lines.extend(["", "## Learnings & takeaways"])
    if d.learnings:
        for item in d.learnings:
            lines.append(_fmt_flagged_item(item))
    else:
        lines.append("- *(no explicit learning cues detected)*")

    lines.extend(["", "## Tool usage"])

    merged = d.all_tools()
    if merged:
        for name, ct in merged.most_common(40):
            tflags = d.tool_flags.get(name)
            extra = f"  — *flags*: {', '.join(tflags)}" if tflags else ""
            lines.append(f"- `{name}` — {ct} mentions{extra}")
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

    flagged = d.flagged_narrative()
    lines.extend(["", "## Flagged for review"])
    if flagged:
        for label, item in flagged:
            lines.append(f"- **{label}**: {item.text}  — `{', '.join(item.flags)}`")
    else:
        lines.append("- *(no heuristic flags on extracted items)*")

    lines.extend(["", "---", "*OpenClaw conversation_extractor.py — distill session value into `memory/`.*"])
    return "\n".join(lines) + "\n"


def digest_to_dict(d: ConversationDigest) -> dict[str, Any]:
    merged = d.all_tools()
    return {
        "source": d.source,
        "generated_at_utc": d.generated_at_utc,
        "counts": {
            "segments": len(d.segments),
            "decisions": len(d.decisions),
            "errors": len(d.errors),
            "followups": len(d.followups),
            "learnings": len(d.learnings),
            "flagged_narrative_items": len(d.flagged_narrative()),
            "tool_names_distinct": len(merged),
        },
        "decisions": [x.to_dict() for x in d.decisions],
        "errors": [x.to_dict() for x in d.errors],
        "followups": [x.to_dict() for x in d.followups],
        "learnings": [x.to_dict() for x in d.learnings],
        "tools_ranked": merged.most_common(),
        "tools_structured": dict(d.tool_structured),
        "tools_from_text_heuristic": dict(d.tool_textual),
        "tool_item_flags": {k: list(v) for k, v in d.tool_flags.items()},
    }


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


def stem_for_outputs(session_path: Path) -> str:
    stem = session_path.stem
    parent = session_path.parent.name
    if parent and parent not in {".", ""}:
        stem = f"{parent}__{session_path.stem}"
    return stem


def apply_workspace_relative_source(digest: ConversationDigest, session_path: Path, workspace_root: Path) -> None:
    relative = session_path.resolve().as_posix()
    workspace_posix = workspace_root.resolve().as_posix()
    if relative.startswith(workspace_posix):
        digest.source = Path(relative[len(workspace_posix) :].lstrip("/")).as_posix()


def build_digest(session_path: Path, workspace_root: Path) -> ConversationDigest:
    segments = parse_session_log(session_path.resolve())
    digest = analyze_segments(segments, session_path.resolve().as_posix())
    apply_workspace_relative_source(digest, session_path, workspace_root)
    return digest


def run_extraction(session_path: Path, memory_dir: Path, workspace_root: Path) -> tuple[Path, Path]:
    digest = build_digest(session_path, workspace_root)
    return write_digest(digest, memory_dir, stem_for_outputs(session_path))


def render_summary_text(d: ConversationDigest) -> str:
    merged = d.all_tools()
    top_tools = ", ".join(n for n, _ in merged.most_common(8)) or "—"
    lines = [
        f"Source: {d.source}",
        f"Segments: {len(d.segments)}",
        f"Decisions: {len(d.decisions)} ({sum(1 for x in d.decisions if x.has_flags())} flagged)",
        f"Errors: {len(d.errors)} ({sum(1 for x in d.errors if x.has_flags())} flagged)",
        f"Follow-ups: {len(d.followups)} ({sum(1 for x in d.followups if x.has_flags())} flagged)",
        f"Learnings: {len(d.learnings)} ({sum(1 for x in d.learnings if x.has_flags())} flagged)",
        f"Distinct tools: {len(merged)}; top: {top_tools}",
    ]
    return "\n".join(lines) + "\n"


def render_summary_json(d: ConversationDigest) -> str:
    merged = d.all_tools()
    payload = {
        "source": d.source,
        "segments": len(d.segments),
        "decisions": {"count": len(d.decisions), "flagged": sum(1 for x in d.decisions if x.has_flags())},
        "errors": {"count": len(d.errors), "flagged": sum(1 for x in d.errors if x.has_flags())},
        "followups": {"count": len(d.followups), "flagged": sum(1 for x in d.followups if x.has_flags())},
        "learnings": {"count": len(d.learnings), "flagged": sum(1 for x in d.learnings if x.has_flags())},
        "tools": {"distinct": len(merged), "top": merged.most_common(12)},
    }
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def _stdin_to_temp_transcript(memory_dir: Path) -> Path:
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
    sub = p.add_subparsers(dest="command", required=True)

    ex = sub.add_parser("extract", help="Write markdown + JSON digest under memory/.")
    ex.add_argument(
        "session_log",
        type=Path,
        nargs="?",
        help="Path to session transcript (.json, .log, or text). Omit with --stdin.",
    )
    ex.add_argument(
        "--stdin",
        action="store_true",
        help="Read transcript JSON/text from stdin (temp file under memory/).",
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

    sm = sub.add_parser("summarize", help="Print counts and highlights to stdout (no files written).")
    sm.add_argument(
        "session_log",
        type=Path,
        nargs="?",
        help="Path to session transcript. Omit with --stdin.",
    )
    sm.add_argument(
        "--stdin",
        action="store_true",
        help="Read transcript from stdin.",
    )
    sm.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of plain text.",
    )
    sm.add_argument(
        "--workspace-root",
        type=Path,
        default=None,
        help="Workspace root for relative paths in summary output.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    ws = (getattr(args, "workspace_root", None) or repo_root()).resolve()

    if args.command == "summarize":
        memory_dir = ws / "memory"
        temp_path: Path | None = None
        if args.stdin:
            temp_path = _stdin_to_temp_transcript(memory_dir)
            sp = temp_path
        else:
            if not args.session_log:
                sys.stderr.write("error: summarize requires session_log path or --stdin.\n")
                return 2
            sp = args.session_log.resolve()
            if not sp.exists():
                sys.stderr.write(f"error: file not found: {sp}\n")
                return 2

        digest = build_digest(sp, ws)
        sys.stdout.write(render_summary_json(digest) if args.json else render_summary_text(digest))
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)  # type: ignore[arg-type]
            except OSError:
                pass
        return 0

    if args.command == "extract":
        memory_dir = (args.memory_dir or ws / "memory").resolve()
        temp_path: Path | None = None
        if args.stdin:
            temp_path = _stdin_to_temp_transcript(memory_dir)
            session_path = temp_path
        else:
            if not args.session_log:
                sys.stderr.write("error: extract requires session_log path or --stdin.\n")
                return 2
            session_path = args.session_log.resolve()
            if not session_path.exists():
                sys.stderr.write(f"error: file not found: {session_path}\n")
                return 2

        md_path, json_path = run_extraction(session_path, memory_dir, ws)
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)  # type: ignore[arg-type]
            except OSError:
                pass

        print(f"Wrote {md_path.as_posix()}")
        print(f"Wrote {json_path.as_posix()}")
        return 0

    sys.stderr.write("error: unknown command.\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
