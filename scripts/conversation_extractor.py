#!/usr/bin/env python3
"""
Extract meaningful structure from OpenClaw agent conversation transcripts.

Reads session exports (JSON or line-oriented logs), classifies roles and tool
traffic, distills decisions / learnings / errors / outcomes, deduplicates noisy
repetition, and writes compact JSON (optionally Markdown) summaries.

Typical OpenClaw paths live under ``~/.openclaw/workspace``; override with
``OPENCLAW_WORKSPACE`` or ``--workspace``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from scripts.optimize_context import TURN_PATTERN, read_text, repo_root

# ---------------------------------------------------------------------------
# Environment & workspace defaults
# ---------------------------------------------------------------------------

OPENCLAW_WORKSPACE_ENV = "OPENCLAW_WORKSPACE"

# Relative glob patterns scanned when ``--batch`` is given with no file list.
DEFAULT_OPENCLAW_TRANSCRIPT_GLOBS: tuple[str, ...] = (
    "sessions/**/*.json",
    "memory/**/*.json",
    "memory/**/*.log",
    "logs/**/*.json",
    "logs/**/*.log",
)

# ---------------------------------------------------------------------------
# Regex: decisions, learnings, errors, outcomes (English + light multilingual)
# ---------------------------------------------------------------------------

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

# Failures surfaced in prose, stack traces, or tool stderr echoes.
ERROR_LINE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"(?i)^\s*(?:error|exception|traceback|fatal)\s*[:\-]\s*(.+)",
        r"(?i)\b(?:command failed|exit code\s+[1-9]\d*|non-zero exit|timed?\s*out)\b[:\s]*(.*)",
        r"(?i)\b(?:AttributeError|TypeError|ValueError|KeyError|FileNotFoundError|OSError|RuntimeError)\b\s*[:\-]?\s*(.*)",
        r"(?i)\bHTTP\s*(?:4\d\d|5\d\d)\b[^\n]{0,200}",
        r"(?i)\b(?:ECONNREFUSED|ENOTFOUND|ETIMEDOUT)\b[^\n]{0,120}",
    )
)

OUTCOME_LINE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"^\s*(?:outcome|result|completed|done|fixed|merged|shipped)\s*[:\-—]\s*(.+)",
        r"(?i)\b(?:successfully\s+(?:built|deployed|merged|fixed|completed)|all\s+tests\s+pass|CI\s+passed)\b[^\n]{0,200}",
        r"(?i)\b(?:PR\s+(?:opened|merged)|patch\s+landed)\b[^\n]{0,200}",
    )
)

# Inline tool/function references in free-form assistant text.
TOOL_REGEXES: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:invoke|calling|called)\s+(?:tool\s+)?[`\"]?([\w\-./:]+)[`\"]?", re.I),
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

_PREVIEW_LIMIT = 400
_TOOL_RESULT_CAP = 12_000


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")


def openclaw_workspace() -> Path:
    """Resolve the OpenClaw workspace root (session + memory artifacts)."""

    raw = os.environ.get(OPENCLAW_WORKSPACE_ENV, "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return (Path.home() / ".openclaw" / "workspace").expanduser().resolve()


def normalize_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def content_fingerprint(text: str, max_len: int = 2800) -> str:
    """
    Stable fingerprint for deduplication: normalized whitespace, capped length
    so near-duplicate blobs collapse while long unique tails remain distinct.
    """

    n = normalize_ws(text)
    if len(n) > max_len:
        half = max_len // 2
        return n[:half] + "::" + n[-half:]
    return n


def dedupe_preserve_order(items: Iterable[str], max_keep: int = 200) -> list[str]:
    """Drop repeated / near-duplicate lines using fingerprints; preserve first-seen order."""

    seen: set[str] = set()
    out: list[str] = []
    for raw in items:
        s = raw.strip()
        if not s:
            continue
        fp = content_fingerprint(s)
        if fp in seen:
            continue
        seen.add(fp)
        out.append(s)
        if len(out) >= max_keep:
            break
    return out


def preview_text(text: str, limit: int = _PREVIEW_LIMIT) -> str:
    one = normalize_ws(text)
    if len(one) <= limit:
        return one
    return one[: max(0, limit - 3)] + "..."


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
            # Caller should lift tool results before flattening when possible.
            inner = piece.get("content") or piece.get("output") or piece.get("text")
            t, subt = _flatten_content_piece(inner)
            tools.extend(subt)
            return t, tools

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


def _extract_list_tool_events(
    turn: int,
    content_list: list[Any],
    segments: list[tuple[int, str | None, str]],
) -> list[Any]:
    """
    Pull explicit tool_use / tool_result entries from an Anthropic-style content
    array so they become first-class segments instead of being merged into prose.
    """

    filtered: list[Any] = []
    for piece in content_list:
        if not isinstance(piece, dict):
            filtered.append(piece)
            continue
        typ = str(piece.get("type") or "").lower()
        if typ in ("tool_use", "tool-use", "toolcall", "function", "tool_invocation"):
            name = _stringify_toolish_dict(piece)
            if name:
                segments.append((turn, "tool", name))
            continue
        if typ in ("tool_result", "tool-result"):
            body, _ = _flatten_content_piece(piece.get("content") or piece.get("output") or piece.get("text"))
            body = body.strip()
            if body:
                segments.append((turn, "tool_result", body[:_TOOL_RESULT_CAP]))
            continue
        filtered.append(piece)
    return filtered


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

        c = raw.get("content") or raw.get("text") or raw.get("body")
        if isinstance(c, list):
            c = _extract_list_tool_events(turn, c, segments)

        text = ""
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
    role_prefix = re.compile(
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

        m_role = role_prefix.match(line)
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
                if len(cap) > 200:
                    cap = cap[:197] + "..."
                hits.append(cap)
                break

    compact = normalize_ws(text.replace("\n", " "))
    for pat in patterns:
        if pat.pattern.startswith("^"):
            continue
        m = pat.search(compact)
        if m:
            cap = (m.group(1) if m.lastindex else m.group(0)).strip()
            short = cap if len(cap) <= 280 else cap[:277] + "..."
            hits.append(short)
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


def _role_bucket(role: str | None) -> str:
    if not role:
        return "unknown"
    rl = role.lower()
    if rl in {"human"}:
        return "user"
    if rl in {"user"}:
        return "user"
    if rl in {"assistant", "agent"}:
        return "assistant"
    if rl == "tool":
        return "tool_call"
    if rl == "tool_result":
        return "tool_result"
    if rl == "tool_output":
        return "tool_output"
    if rl == "system":
        return "system"
    return rl


def build_taxonomy_summary(
    segments: list[tuple[int, str | None, str]],
    max_message_samples: int = 80,
) -> dict[str, Any]:
    """
    Build compact structured views: per-role message previews, tool calls,
    and tool results (truncated). Long transcripts stay bounded.
    """

    counts: Counter[str] = Counter()
    tool_calls: list[dict[str, Any]] = []
    tool_results: list[dict[str, Any]] = []
    message_roll: list[dict[str, Any]] = []

    for turn, role, text in segments:
        bucket = _role_bucket(role)
        counts[bucket] += 1
        pv = preview_text(text)

        if bucket == "tool_call":
            tool_calls.append({"turn": turn, "name": text.split("(", 1)[0].strip()})
            if len(message_roll) < max_message_samples:
                message_roll.append({"turn": turn, "kind": "tool_call", "name": text, "preview": pv})
            continue

        if bucket == "tool_result":
            tool_results.append({"turn": turn, "preview": pv})
            if len(message_roll) < max_message_samples:
                message_roll.append({"turn": turn, "kind": "tool_result", "preview": pv})
            continue

        if bucket in {"user", "assistant", "system", "tool_output", "unknown"}:
            if len(message_roll) < max_message_samples:
                message_roll.append({"turn": turn, "kind": bucket, "preview": pv})

    # Dedupe tool call names while preserving frequency in a separate counter.
    call_counter: Counter[str] = Counter(t["name"] for t in tool_calls if t.get("name"))
    deduped_calls = [{"name": n, "count": c} for n, c in call_counter.most_common(60)]

    deduped_results = dedupe_preserve_order([t["preview"] for t in tool_results], max_keep=40)

    return {
        "message_counts_by_kind": dict(counts),
        "tool_calls_ranked": deduped_calls,
        "tool_result_previews": deduped_results,
        "message_roll": message_roll,
    }


@dataclass
class ConversationDigest:
    """Structured in-memory representation of one transcript."""

    source: str
    generated_at_utc: str
    openclaw_workspace: str
    segments: list[tuple[int, str | None, str]]
    decisions: list[str]
    learnings: list[str]
    errors: list[str]
    outcomes: list[str]
    tool_structured: Counter[str] = field(default_factory=Counter)
    tool_textual: Counter[str] = field(default_factory=Counter)

    def all_tools(self) -> Counter[str]:
        merged: Counter[str] = Counter(self.tool_structured)
        merged.update(self.tool_textual)
        return merged


def analyze_segments(
    segments: list[tuple[int, str | None, str]],
    source_display: str,
    oc_ws: Path,
) -> ConversationDigest:
    decisions_acc: list[str] = []
    learnings_acc: list[str] = []
    errors_acc: list[str] = []
    outcomes_acc: list[str] = []
    structured_tools: Counter[str] = Counter()
    blobs_for_text_tools: list[str] = []

    for turn, role, text in segments:
        rl = (role or "").lower()
        if rl == "tool":
            tn = normalize_ws(text.replace("[tool:", "").replace("]", "").strip())
            if tn:
                structured_tools[tn.split("(", 1)[0].strip()] += 1
            continue

        if rl == "tool_result":
            errors_acc.extend(match_patterns(text, ERROR_LINE_PATTERNS))
            outcomes_acc.extend(match_patterns(text, OUTCOME_LINE_PATTERNS))
            blobs_for_text_tools.append(text)
            continue

        if rl == "tool_output":
            errors_acc.extend(match_patterns(text, ERROR_LINE_PATTERNS))
            blobs_for_text_tools.append(text)
            continue

        if rl in {"", "assistant", "agent"} or role is None:
            decisions_acc.extend(match_patterns(text, DECISION_LINE_PATTERNS))
            learnings_acc.extend(match_patterns(text, LEARNING_LINE_PATTERNS))
            errors_acc.extend(match_patterns(text, ERROR_LINE_PATTERNS))
            outcomes_acc.extend(match_patterns(text, OUTCOME_LINE_PATTERNS))
        elif rl == "user":
            decisions_acc.extend(match_patterns(text, DECISION_LINE_PATTERNS))
            errors_acc.extend(match_patterns(text, ERROR_LINE_PATTERNS))

        blobs_for_text_tools.append(text)

    combined = "\n\n".join(blobs_for_text_tools)
    textual_tools = extract_tool_signals(combined)

    return ConversationDigest(
        source=source_display,
        generated_at_utc=datetime.now(timezone.utc).isoformat(),
        openclaw_workspace=str(oc_ws.resolve()),
        segments=segments,
        decisions=dedupe_preserve_order(decisions_acc, max_keep=120),
        learnings=dedupe_preserve_order(learnings_acc, max_keep=120),
        errors=dedupe_preserve_order(errors_acc, max_keep=120),
        outcomes=dedupe_preserve_order(outcomes_acc, max_keep=120),
        tool_structured=structured_tools,
        tool_textual=textual_tools,
    )


def digest_to_dict(d: ConversationDigest) -> dict[str, Any]:
    taxonomy = build_taxonomy_summary(d.segments)
    return {
        "source": d.source,
        "generated_at_utc": d.generated_at_utc,
        "openclaw_workspace": d.openclaw_workspace,
        "stats": {
            "segments": len(d.segments),
            "decisions": len(d.decisions),
            "learnings": len(d.learnings),
            "errors": len(d.errors),
            "outcomes": len(d.outcomes),
            "tool_names_distinct": len(d.all_tools()),
        },
        "taxonomy": taxonomy,
        "decisions": d.decisions,
        "learnings": d.learnings,
        "errors": d.errors,
        "key_outcomes": d.outcomes,
        "tools_ranked": d.all_tools().most_common(),
        "tools_structured": dict(d.tool_structured),
        "tools_from_text_heuristic": dict(d.tool_textual),
    }


def render_markdown(d: ConversationDigest) -> str:
    lines = [
        "# Conversation extract",
        "",
        f"- **Source**: `{d.source}`",
        f"- **Generated (UTC)**: {d.generated_at_utc}",
        f"- **OpenClaw workspace**: `{d.openclaw_workspace}`",
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
        lines.append("- *(no strong error cues detected)*")

    lines.extend(["", "## Key outcomes"])
    if d.outcomes:
        for item in d.outcomes:
            lines.append(f"- {item}")
    else:
        lines.append("- *(no explicit outcome cues detected)*")

    lines.extend(["", "## Tool usage"])

    merged = d.all_tools()
    if merged:
        for name, ct in merged.most_common(40):
            lines.append(f"- `{name}` — {ct} mentions")
    else:
        lines.append("- *(no tool mentions parsed)*")

    lines.extend(
        [
            "",
            "---",
            "*OpenClaw conversation_extractor.py — distill session transcripts into JSON/Markdown.*",
        ]
    )
    return "\n".join(lines) + "\n"


def write_digest(
    digest: ConversationDigest,
    memory_dir: Path,
    stem: str,
    stamp: str | None = None,
) -> tuple[Path, Path]:
    memory_dir.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^\w\-_.]+", "_", stem).strip("_") or "session"
    tag = stamp or utc_stamp()

    md_path = memory_dir / f"conversation_extract_{safe}_{tag}.md"
    js_path = memory_dir / f"conversation_extract_{safe}_{tag}.json"

    md_path.write_text(render_markdown(digest), encoding="utf-8")
    js_path.write_text(json.dumps(digest_to_dict(digest), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    return md_path, js_path


def write_json_only(path: Path, digest: ConversationDigest) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(digest_to_dict(digest), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def relative_display(path: Path, workspace_root: Path) -> str:
    try:
        return path.resolve().relative_to(workspace_root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def run_extraction(
    session_path: Path,
    memory_dir: Path,
    workspace_root: Path,
    oc_ws: Path,
) -> tuple[Path, Path]:
    segments = parse_session_log(session_path.resolve())
    digest = analyze_segments(segments, session_path.resolve().as_posix(), oc_ws)

    stem = session_path.stem
    parent = session_path.parent.name
    if parent and parent not in {".", ""}:
        stem = f"{parent}__{session_path.stem}"

    digest.source = relative_display(session_path, workspace_root)

    return write_digest(digest, memory_dir, stem)


def discover_transcripts(ws: Path, globs: tuple[str, ...]) -> list[Path]:
    """Collect transcript-like files under the OpenClaw workspace."""

    files: list[Path] = []
    seen: set[Path] = set()
    if not ws.is_dir():
        return files
    for pattern in globs:
        for path in ws.glob(pattern):
            if not path.is_file():
                continue
            rp = path.resolve()
            if rp in seen:
                continue
            seen.add(rp)
            files.append(path)
    files.sort(key=lambda p: p.stat().st_mtime_ns if p.exists() else 0, reverse=True)
    return files


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Extract decisions, learnings, errors, and tool traffic from OpenClaw "
            "session transcripts; write JSON (and optionally Markdown)."
        )
    )
    p.add_argument(
        "session_log",
        type=Path,
        nargs="?",
        default=None,
        help="Legacy positional path to a transcript (.json, .log, or text).",
    )
    p.add_argument(
        "--input",
        "-i",
        type=Path,
        default=None,
        help="Transcript file to process (preferred over positional path).",
    )
    p.add_argument(
        "--output",
        "-o",
        type=Path,
        default=None,
        help=(
            "Destination: a .json file for single-file mode, or a directory for "
            "legacy memory output / batch JSON writes."
        ),
    )
    p.add_argument(
        "--batch",
        nargs="*",
        metavar="PATH",
        default=None,
        help=(
            "Process multiple transcripts. With no paths, scan OPENCLAW_WORKSPACE "
            "(see --workspace) using built-in glob patterns."
        ),
    )
    p.add_argument(
        "--workspace",
        type=Path,
        default=None,
        help=f"OpenClaw workspace root (default: env {OPENCLAW_WORKSPACE_ENV} or ~/.openclaw/workspace).",
    )
    p.add_argument(
        "--workspace-root",
        type=Path,
        default=None,
        help="Repository / display root for shortening source paths in output (defaults to repo root).",
    )
    p.add_argument(
        "--memory-dir",
        type=Path,
        default=None,
        help="Legacy: directory for timestamped .md + .json extracts (default: <repo>/memory).",
    )
    p.add_argument(
        "--json-only",
        action="store_true",
        help="Never write Markdown (overrides --also-markdown for JSON export modes).",
    )
    p.add_argument(
        "--also-markdown",
        action="store_true",
        help="Alongside a .json destination, also write a sibling .md summary.",
    )
    p.add_argument(
        "--stdin",
        action="store_true",
        help="Read transcript JSON/text from stdin (spooled to a temp file under memory/).",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    ws_root = (args.workspace_root or repo_root()).resolve()
    memory_dir = (args.memory_dir or ws_root / "memory").resolve()
    oc_ws = (args.workspace or openclaw_workspace()).resolve()

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

        if args.output and args.output.suffix.lower() == ".json":
            segments = parse_session_log(path.resolve())
            digest = analyze_segments(segments, "<stdin>", oc_ws)
            digest.source = "<stdin>"
            out = write_json_only(args.output.resolve(), digest)
            if args.also_markdown and not args.json_only:
                out.with_suffix(".md").write_text(render_markdown(digest), encoding="utf-8")
            print(out.as_posix())
        else:
            md_path, json_path = run_extraction(path, memory_dir, ws_root, oc_ws)
            if args.output:
                # Treat as directory override for legacy pair output
                memory_dir = args.output.resolve()
                memory_dir.mkdir(parents=True, exist_ok=True)
                md_path, json_path = run_extraction(path, memory_dir, ws_root, oc_ws)
            print(md_path.as_posix())
            print(json_path.as_posix())
        try:
            path.unlink(missing_ok=True)  # type: ignore[arg-type]
        except OSError:
            pass
        return 0

    # --- batch mode ---
    if args.batch is not None:
        paths = [Path(p).resolve() for p in args.batch if str(p)]
        if not paths:
            paths = discover_transcripts(oc_ws, DEFAULT_OPENCLAW_TRANSCRIPT_GLOBS)

        if not paths:
            sys.stderr.write(
                f"error: no transcript files found under {oc_ws} (tried globs: {DEFAULT_OPENCLAW_TRANSCRIPT_GLOBS}).\n"
            )
            return 2

        out_dir = args.output
        if out_dir is None:
            out_dir = memory_dir
        out_dir = out_dir.resolve()
        out_dir.mkdir(parents=True, exist_ok=True)

        written: list[str] = []
        for sp in paths:
            if not sp.exists():
                sys.stderr.write(f"warning: skip missing file: {sp}\n")
                continue
            segments = parse_session_log(sp)
            digest = analyze_segments(segments, sp.as_posix(), oc_ws)
            digest.source = relative_display(sp, ws_root)
            stem_key = f"{sp.parent.name}__{sp.stem}" if sp.parent.name not in {"", "."} else sp.stem
            safe = re.sub(r"[^\w\-_.]+", "_", stem_key).strip("_") or "session"
            json_path = out_dir / f"{safe}_conversation_summary.json"
            write_json_only(json_path, digest)
            written.append(json_path.as_posix())
            if args.also_markdown and not args.json_only:
                json_path.with_suffix(".md").write_text(render_markdown(digest), encoding="utf-8")

        for line in written:
            print(line)
        return 0

    # --- single file ---
    sp_arg = args.input or args.session_log
    if not sp_arg:
        sys.stderr.write("error: provide --input, a positional transcript path, or --batch / --stdin.\n")
        return 2

    sp = sp_arg.resolve()
    if not sp.exists():
        sys.stderr.write(f"error: file not found: {sp}\n")
        return 2

    if args.output and args.output.suffix.lower() == ".json":
        segments = parse_session_log(sp)
        digest = analyze_segments(segments, sp.as_posix(), oc_ws)
        digest.source = relative_display(sp, ws_root)
        out = write_json_only(args.output.resolve(), digest)
        if args.also_markdown and not args.json_only:
            out.with_suffix(".md").write_text(render_markdown(digest), encoding="utf-8")
        print(out.as_posix())
        return 0

    target_memory = memory_dir
    if args.output:
        target_memory = args.output.resolve()
        target_memory.mkdir(parents=True, exist_ok=True)

    md_path, json_path = run_extraction(sp, target_memory, ws_root, oc_ws)
    print(md_path.as_posix())
    print(json_path.as_posix())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
