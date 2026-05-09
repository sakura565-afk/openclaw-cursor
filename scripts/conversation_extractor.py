#!/usr/bin/env python3
"""Extract decisions, errors, tool calls, follow-ups, and learnings from OpenClaw session transcripts.

CLI: ``extract`` writes markdown + JSON under ``memory/``; ``summarize`` prints a short digest to stdout.
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

FOLLOWUP_LINE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"^\s*(?:follow[- ]?ups?|follow\s+up)\s*[:\-—]\s*(.+)",
        r"^\s*(?:TODO|FIXME|ACTION|NEXT\s+STEP)s?\s*[:\-—]?\s*(.+)",
        r"(?:\bnext\s+steps?\b|\bopen\s+questions?\b|\bremaining\s+work\b)\s*[:\-—]?\s*(.{8,})",
        r"(?:we\s+should|still\s+need\s+to|outstanding|pending)\s+(.{10,})",
        r"(?:待办|后续|跟进|下一步)\s*[：:]\s*(.+)",
        r"\b(?:TBD|blocker)\b\s*[:\-—]?\s*(.+)",
    )
)

ERROR_LINE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"^\s*(?:error|exception|traceback|failure)\s*[:\-—]\s*(.+)",
        r"^\s*\*{0,2}(?:ERROR|FAILED)\*{0,2}\s*[:\-—]?\s*(.+)",
    )
)

# Lines that look like failures (stack traces, CLI errors, timeouts)
ERROR_LINE_HINT = re.compile(
    r"(?i)(\b(traceback|exception|error\s*:|assertionerror|keyerror|valueerror|"
    r"typeerror|runtimeerror|oserror|permission\s*denied|command\s+not\s+found|"
    r"exit\s*code\s*[1-9]\d*|timed?\s*out|ECONNREFUSED|404\s+not\s+found|"
    r"failed\b|\[error\])\b|^(Traceback\s*\(most recent call last\):))",
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


def _clip_snippet(s: str, limit: int = 280) -> str:
    s = s.strip()
    if len(s) <= limit:
        return s
    return s[: max(0, limit - 3)] + "..."


def _match_patterns_with_turn(turn: int, text: str, patterns: tuple[re.Pattern[str], ...]) -> list[tuple[int, str]]:
    hits: list[tuple[int, str]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        for pat in patterns:
            m = pat.search(line)
            if m:
                cap = (m.group(1) if m.lastindex else m.group(0)).strip()
                hits.append((turn, _clip_snippet(cap, 200)))
                break
    compact = normalize_ws(text.replace("\n", " "))
    for pat in patterns:
        if pat.pattern.startswith("^"):
            continue
        m = pat.search(compact)
        if m:
            cap = (m.group(1) if m.lastindex else m.group(0)).strip()
            hits.append((turn, _clip_snippet(cap, 240)))
    return hits


def _error_hint_lines(turn: int, text: str) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for line in text.splitlines():
        s = line.strip()
        if not s or len(s) < 8:
            continue
        if ERROR_LINE_HINT.search(s):
            found.append((turn, _clip_snippet(s, 300)))
    return found


def _dedupe_turn_pairs(pairs: list[tuple[int, str]], limit: int) -> tuple[list[str], list[tuple[int, str]]]:
    texts: list[str] = []
    kept: list[tuple[int, str]] = []
    seen: set[str] = set()
    for turn, txt in pairs:
        key = txt.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        texts.append(txt)
        kept.append((turn, txt))
        if len(texts) >= limit:
            break
    return texts, kept


def _build_flagged(
    errors: list[tuple[int, str]],
    decisions: list[tuple[int, str]],
    followups: list[tuple[int, str]],
    learnings: list[tuple[int, str]],
    tools: Counter[str],
) -> list[dict[str, Any]]:
    """Ordered list of notable items for review (errors and blockers first)."""

    flags: list[dict[str, Any]] = []
    for turn, txt in errors[:50]:
        flags.append({"kind": "error", "text": txt, "priority": "high", "turn": turn})
    for turn, txt in decisions[:40]:
        flags.append({"kind": "decision", "text": txt, "priority": "medium", "turn": turn})
    for turn, txt in followups[:50]:
        pri = "high" if re.search(r"(?i)\b(blocker|critical|must|urgent)\b", txt) else "low"
        flags.append({"kind": "followup", "text": txt, "priority": pri, "turn": turn})
    for turn, txt in learnings[:30]:
        pri = "medium" if re.search(r"(?i)\b(important|critical|never|always)\b", txt) else "low"
        flags.append({"kind": "learning", "text": txt, "priority": pri, "turn": turn})
    for name, ct in tools.most_common(15):
        flags.append(
            {
                "kind": "tool_call",
                "text": f"{name} (x{ct})",
                "priority": "low",
                "turn": None,
            }
        )
    return flags


@dataclass
class ConversationDigest:
    """Structured output for markdown + JSON."""

    source: str
    generated_at_utc: str
    segments: list[tuple[int, str | None, str]]
    decisions: list[str]
    learnings: list[str]
    errors: list[str] = field(default_factory=list)
    followups: list[str] = field(default_factory=list)
    flagged: list[dict[str, Any]] = field(default_factory=list)
    tool_structured: Counter[str] = field(default_factory=Counter)
    tool_textual: Counter[str] = field(default_factory=Counter)

    def all_tools(self) -> Counter[str]:
        merged: Counter[str] = Counter(self.tool_structured)
        merged.update(self.tool_textual)
        return merged


def analyze_segments(segments: list[tuple[int, str | None, str]], source_display: str) -> ConversationDigest:
    decisions_pairs: list[tuple[int, str]] = []
    learnings_pairs: list[tuple[int, str]] = []
    followup_pairs: list[tuple[int, str]] = []
    structured_errors: list[tuple[int, str]] = []
    hint_errors: list[tuple[int, str]] = []
    structured_tools: Counter[str] = Counter()
    blobs_for_text_tools: list[str] = []

    text_roles = frozenset(
        {
            "",
            "assistant",
            "agent",
            "user",
            "human",
            "system",
            "tool_output",
        }
    )

    for turn, role, text in segments:
        rl = (role or "").lower()
        if rl == "tool":
            tn = normalize_ws(text.replace("[tool:", "").replace("]", "").strip())
            if tn:
                structured_tools[tn.split("(", 1)[0].strip()] += 1
            continue

        if rl in text_roles:
            if rl in {"assistant", "agent", ""}:
                decisions_pairs.extend(_match_patterns_with_turn(turn, text, DECISION_LINE_PATTERNS))
                learnings_pairs.extend(_match_patterns_with_turn(turn, text, LEARNING_LINE_PATTERNS))
                followup_pairs.extend(_match_patterns_with_turn(turn, text, FOLLOWUP_LINE_PATTERNS))
                structured_errors.extend(_match_patterns_with_turn(turn, text, ERROR_LINE_PATTERNS))
                hint_errors.extend(_error_hint_lines(turn, text))
            elif rl in {"user", "human"}:
                decisions_pairs.extend(_match_patterns_with_turn(turn, text, DECISION_LINE_PATTERNS))
                learnings_pairs.extend(_match_patterns_with_turn(turn, text, LEARNING_LINE_PATTERNS))
                followup_pairs.extend(_match_patterns_with_turn(turn, text, FOLLOWUP_LINE_PATTERNS))
                structured_errors.extend(_match_patterns_with_turn(turn, text, ERROR_LINE_PATTERNS))
                hint_errors.extend(_error_hint_lines(turn, text))
            elif rl in {"system", "tool_output"}:
                structured_errors.extend(_match_patterns_with_turn(turn, text, ERROR_LINE_PATTERNS))
                hint_errors.extend(_error_hint_lines(turn, text))
                followup_pairs.extend(_match_patterns_with_turn(turn, text, FOLLOWUP_LINE_PATTERNS))

        if rl == "tool_output":
            blobs_for_text_tools.append(text)
            continue

        if rl in text_roles and rl != "tool_output":
            blobs_for_text_tools.append(text)

    combined = "\n\n".join(blobs_for_text_tools)
    textual_tools = extract_tool_signals(combined)

    decisions, _ = _dedupe_turn_pairs(decisions_pairs, 120)
    learnings, _ = _dedupe_turn_pairs(learnings_pairs, 120)
    followups, _ = _dedupe_turn_pairs(followup_pairs, 120)

    err_pairs = structured_errors + hint_errors
    errors, _ = _dedupe_turn_pairs(err_pairs, 120)

    _, error_f = _dedupe_turn_pairs(err_pairs, 80)
    _, decision_f = _dedupe_turn_pairs(decisions_pairs, 80)
    _, follow_f = _dedupe_turn_pairs(followup_pairs, 80)
    _, learn_f = _dedupe_turn_pairs(learnings_pairs, 40)
    merged_tools: Counter[str] = Counter(structured_tools)
    merged_tools.update(textual_tools)
    flagged = _build_flagged(error_f, decision_f, follow_f, learn_f, merged_tools)

    return ConversationDigest(
        source=source_display,
        generated_at_utc=datetime.now(timezone.utc).isoformat(),
        segments=segments,
        decisions=decisions,
        learnings=learnings,
        errors=errors,
        followups=followups,
        flagged=flagged,
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

    lines.extend(["", "## Follow-ups & open work"])
    if d.followups:
        for item in d.followups:
            lines.append(f"- {item}")
    else:
        lines.append("- *(no follow-up cues detected)*")

    lines.extend(["", "## Flagged (review queue)"])
    high = [x for x in d.flagged if x.get("priority") == "high"]
    med = [x for x in d.flagged if x.get("priority") == "medium"]
    rest = [x for x in d.flagged if x.get("priority") not in {"high", "medium"}]
    if not d.flagged:
        lines.append("- *(nothing flagged)*")
    else:
        if high:
            lines.append("### High")
            for it in high[:30]:
                turn = it.get("turn")
                tag = f"turn {turn} — " if turn is not None else ""
                lines.append(f"- **{it['kind']}** {tag}{it['text']}")
        if med:
            lines.append("### Medium")
            for it in med[:25]:
                turn = it.get("turn")
                tag = f"turn {turn} — " if turn is not None else ""
                lines.append(f"- **{it['kind']}** {tag}{it['text']}")
        if rest:
            lines.append("### Other")
            for it in rest[:25]:
                turn = it.get("turn")
                tag = f"turn {turn} — " if turn is not None else ""
                lines.append(f"- **{it['kind']}** {tag}{it['text']}")

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
            "flagged": len(d.flagged),
            "tool_names_distinct": len(d.all_tools()),
        },
        "decisions": d.decisions,
        "learnings": d.learnings,
        "errors": d.errors,
        "followups": d.followups,
        "flagged": d.flagged,
        "tools_ranked": d.all_tools().most_common(),
        "tools_structured": dict(d.tool_structured),
        "tools_from_text_heuristic": dict(d.tool_textual),
    }


def session_stem(session_path: Path) -> str:
    stem = session_path.stem
    parent = session_path.parent.name
    if parent and parent not in {".", ""}:
        stem = f"{parent}__{session_path.stem}"
    return stem


def build_digest(session_path: Path, workspace_root: Path) -> ConversationDigest:
    segments = parse_session_log(session_path.resolve())
    digest = analyze_segments(segments, session_path.resolve().as_posix())
    relative = session_path.resolve().as_posix()
    workspace_posix = workspace_root.resolve().as_posix()
    if relative.startswith(workspace_posix):
        digest.source = Path(relative[len(workspace_posix) :].lstrip("/")).as_posix()
    return digest


def render_summary_text(d: ConversationDigest, *, max_items: int = 10) -> str:
    lines = [
        f"Source: {d.source}",
        f"segments={len(d.segments)} decisions={len(d.decisions)} learnings={len(d.learnings)} "
        f"errors={len(d.errors)} followups={len(d.followups)} tools={len(d.all_tools())}",
        "",
        "Flagged (high priority):",
    ]
    high = [x for x in d.flagged if x.get("priority") == "high"][:max_items]
    if high:
        lines.extend(f"  * [{x['kind']}] {x['text']}" for x in high)
    else:
        lines.append("  (none)")

    sections: tuple[tuple[str, list[str]], ...] = (
        ("Decisions", d.decisions),
        ("Errors", d.errors),
        ("Follow-ups", d.followups),
        ("Learnings", d.learnings),
        ("Tool calls (top)", [f"{n} ({c})" for n, c in d.all_tools().most_common(max_items)]),
    )
    for title, items in sections:
        lines.extend(["", f"{title}:"])
        if items:
            lines.extend(f"  - {x}" for x in items[:max_items])
        else:
            lines.append("  (none)")
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
    digest = build_digest(session_path, workspace_root)
    return write_digest(digest, memory_dir, session_stem(session_path))


def _resolve_session_path(args: argparse.Namespace, memory_dir: Path) -> tuple[Path, bool]:
    """Return transcript path and whether it should be deleted (stdin temp file)."""

    if getattr(args, "stdin", False):
        import tempfile

        memory_dir.mkdir(parents=True, exist_ok=True)
        payload = sys.stdin.read()
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".txt", prefix="stdin_session_", dir=memory_dir)
        path = Path(tmp.name)
        try:
            path.write_text(payload, encoding="utf-8")
        finally:
            tmp.close()
        return path, True

    if not args.session_log:
        raise ValueError("session_log path required unless --stdin is set.")

    sp = args.session_log.resolve()
    if not sp.exists():
        raise FileNotFoundError(str(sp))
    return sp, False


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Extract decisions, errors, tool calls, and follow-ups from OpenClaw session transcripts."
    )
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "session_log",
        type=Path,
        nargs="?",
        help="Path to session transcript (.json, .log, or text). Omit with --stdin.",
    )
    common.add_argument(
        "--stdin",
        action="store_true",
        help="Read transcript JSON/text from stdin (temp file under memory/ for extract).",
    )
    common.add_argument(
        "--workspace-root",
        type=Path,
        default=None,
        help="Workspace root for relative paths in output (defaults to repo root).",
    )

    sub = p.add_subparsers(dest="command", required=True)

    ex = sub.add_parser("extract", parents=[common], help="Write markdown + JSON digest under memory/.")
    ex.add_argument(
        "--memory-dir",
        type=Path,
        default=None,
        help="Destination directory (default: <repo>/memory).",
    )

    sm = sub.add_parser("summarize", parents=[common], help="Print a short text or JSON digest to stdout.")
    sm.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Output format (default: text).",
    )
    sm.add_argument(
        "--max-items",
        type=int,
        default=10,
        metavar="N",
        help="Cap list sections in text output (default: 10).",
    )

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    ws = (args.workspace_root or repo_root()).resolve()
    memory_dir = (getattr(args, "memory_dir", None) or ws / "memory").resolve()

    try:
        path, cleanup = _resolve_session_path(args, memory_dir)
    except ValueError as e:
        sys.stderr.write(f"error: {e}\n")
        return 2
    except FileNotFoundError as e:
        sys.stderr.write(f"error: file not found: {e}\n")
        return 2

    try:
        digest = build_digest(path, ws)
        if args.command == "extract":
            md_path, json_path = write_digest(digest, memory_dir, session_stem(path))
            print(f"Wrote {md_path.as_posix()}")
            print(f"Wrote {json_path.as_posix()}")
            return 0
        if args.command == "summarize":
            if args.format == "json":
                sys.stdout.write(json.dumps(digest_to_dict(digest), indent=2, ensure_ascii=False) + "\n")
            else:
                sys.stdout.write(render_summary_text(digest, max_items=args.max_items))
            return 0
    finally:
        if cleanup:
            try:
                path.unlink(missing_ok=True)  # type: ignore[arg-type]
            except OSError:
                pass

    sys.stderr.write("error: unknown command\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
