#!/usr/bin/env python3
"""Extract session transcript patterns: tool sequences, prompts that drove tooling, error recovery."""

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

# Heuristics for pattern mining (tool success, prompts, error → recovery)
_FAILURE_SIGNAL = re.compile(
    r"\b(?:error|failed|failure|exception|traceback|cannot\s+find|could\s+not|"
    r"command\s+not\s+found|exit\s+code\s*[1-9]|ECONNREFUSED|404|500)\b",
    re.IGNORECASE,
)
_RECOVERY_SIGNAL = re.compile(
    r"\b(?:fixed|resolved|worked|retry|retried|succeeded|success|workaround|"
    r"solution|passing|green|recovered|corrected)\b",
    re.IGNORECASE,
)
PATTERNS_SCHEMA_VERSION = 1


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
class ConversationDigest:
    """Structured output for markdown + JSON."""

    source: str
    generated_at_utc: str
    segments: list[tuple[int, str | None, str]]
    decisions: list[str]
    learnings: list[str]
    tool_structured: Counter[str] = field(default_factory=Counter)
    tool_textual: Counter[str] = field(default_factory=Counter)

    def all_tools(self) -> Counter[str]:
        merged: Counter[str] = Counter(self.tool_structured)
        merged.update(self.tool_textual)
        return merged


def analyze_segments(segments: list[tuple[int, str | None, str]], source_display: str) -> ConversationDigest:
    decisions_acc: list[str] = []
    learnings_acc: list[str] = []
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

        if rl in {"", "assistant", "agent"} or rl is None:
            decisions_acc.extend(match_patterns(text, DECISION_LINE_PATTERNS))
            learnings_acc.extend(match_patterns(text, LEARNING_LINE_PATTERNS))
        elif rl == "user":
            # users sometimes phrase decisions explicitly
            decisions_acc.extend(match_patterns(text, DECISION_LINE_PATTERNS))

        blobs_for_text_tools.append(text)

    combined = "\n\n".join(blobs_for_text_tools)
    textual_tools = extract_tool_signals(combined)

    uniq = lambda xs: list(dict.fromkeys([x for x in xs if x]))  # noqa: E731

    return ConversationDigest(
        source=source_display,
        generated_at_utc=datetime.now(timezone.utc).isoformat(),
        segments=segments,
        decisions=uniq(decisions_acc)[:120],
        learnings=uniq(learnings_acc)[:120],
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
            "tool_names_distinct": len(d.all_tools()),
        },
        "decisions": d.decisions,
        "learnings": d.learnings,
        "tools_ranked": d.all_tools().most_common(),
        "tools_structured": dict(d.tool_structured),
        "tools_from_text_heuristic": dict(d.tool_textual),
    }


def _norm_tool_token(text: str) -> str:
    tn = normalize_ws(text.replace("[tool:", "").replace("]", "").strip())
    return tn.split("(", 1)[0].strip() if tn else ""


def _first_assistant_blob_after(segments: list[tuple[int, str | None, str]], start_idx: int) -> str | None:
    """Concatenate assistant / agent / tool_output text until the next user turn."""

    parts: list[str] = []
    for turn, role, text in segments[start_idx:]:
        rl = (role or "").lower()
        if rl == "user":
            break
        if rl in {"assistant", "agent", "tool_output", "system", ""} or rl is None:
            if text.strip():
                parts.append(text.strip())
    return "\n".join(parts) if parts else None


def _user_prompt_led_to_tools(segments: list[tuple[int, str | None, str]], user_idx: int) -> bool:
    for turn, role, text in segments[user_idx + 1 :]:
        rl = (role or "").lower()
        if rl == "user":
            return False
        if rl == "tool":
            return True
        if rl in {"assistant", "agent", "tool_output", ""} or role is None:
            if extract_tool_signals(text):
                return True
    return False


def _truncate_snippet(s: str, limit: int = 220) -> str:
    s = normalize_ws(s)
    if len(s) <= limit:
        return s
    return s[: limit - 3] + "..."


def extract_patterns(
    segments: list[tuple[int, str | None, str]],
    source: str,
) -> dict[str, Any]:
    """Mine tool orderings, user prompts tied to tooling, and error→recovery spans."""

    generated = datetime.now(timezone.utc).isoformat()
    tool_sequences_raw: list[tuple[tuple[str, ...], bool]] = []
    i = 0
    n = len(segments)
    while i < n:
        turn, role, text = segments[i]
        rl = (role or "").lower()
        if rl == "tool":
            chain: list[str] = []
            j = i
            while j < n:
                t2, r2, tx2 = segments[j]
                r2l = (r2 or "").lower()
                if r2l == "tool_output":
                    j += 1
                    continue
                if r2l != "tool":
                    break
                name = _norm_tool_token(tx2)
                if name:
                    chain.append(name)
                j += 1
            if len(chain) >= 2:
                blob = _first_assistant_blob_after(segments, j)
                likely_ok = True
                if blob:
                    if _FAILURE_SIGNAL.search(blob) and not _RECOVERY_SIGNAL.search(blob):
                        likely_ok = False
                tool_sequences_raw.append((tuple(chain), likely_ok))
            i = j
            continue
        i += 1

    prompt_hits: list[str] = []
    seen_prompts: set[str] = set()
    for idx, (turn, role, text) in enumerate(segments):
        rl = (role or "").lower()
        if rl != "user":
            continue
        body = text.strip()
        if len(body) < 24:
            continue
        if not _user_prompt_led_to_tools(segments, idx):
            continue
        key = normalize_ws(body)[:400]
        if key in seen_prompts:
            continue
        seen_prompts.add(key)
        prompt_hits.append(_truncate_snippet(body, 900))

    recovery: list[dict[str, str]] = []
    transcript = "\n\n".join(
        t.strip()
        for _, __, t in segments
        if t.strip()
    )
    pos = 0
    while pos < len(transcript):
        m_err = _FAILURE_SIGNAL.search(transcript, pos)
        if not m_err:
            break
        window = transcript[m_err.start() : m_err.start() + 4500]
        m_rec = _RECOVERY_SIGNAL.search(window)
        if m_rec and m_rec.start() > 0:
            err_snip = _truncate_snippet(window[: m_rec.start()], 280)
            rec_snip = _truncate_snippet(window[m_rec.start() : m_rec.start() + 320], 280)
            recovery.append({"error_context": err_snip, "recovery_context": rec_snip})
        pos = m_err.end()

    seq_entries: list[dict[str, Any]] = []
    for seq, ok in tool_sequences_raw:
        seq_entries.append(
            {
                "sequence": list(seq),
                "count": 1,
                "likely_success": ok,
                "sources": [source],
            }
        )

    prompt_entries = [{"text": p, "count": 1, "sources": [source]} for p in prompt_hits]

    recovery_entries = [
        {
            "error_context": item["error_context"],
            "recovery_context": item["recovery_context"],
            "count": 1,
            "sources": [source],
        }
        for item in recovery
    ]

    return {
        "schema_version": PATTERNS_SCHEMA_VERSION,
        "source": source,
        "generated_at_utc": generated,
        "successful_tool_sequences": seq_entries,
        "prompt_templates": prompt_entries,
        "error_recovery_patterns": recovery_entries,
    }


def save_patterns_to_memory(
    patterns: dict[str, Any],
    memory_dir: Path,
    *,
    filename: str = "patterns.json",
    merge: bool = True,
    output_path: Path | None = None,
) -> Path:
    """Write (or merge) aggregated patterns next to other memory artifacts."""

    path = output_path if output_path is not None else (memory_dir / filename)
    path.parent.mkdir(parents=True, exist_ok=True)

    def _dedupe_sources(xs: list[str]) -> list[str]:
        return list(dict.fromkeys([s for s in xs if s]))[:12]

    if merge and path.exists():
        try:
            prior = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            prior = {}

        if isinstance(prior, dict) and prior.get("schema_version") == PATTERNS_SCHEMA_VERSION:
            seq_map: dict[str, dict[str, Any]] = {}
            for row in prior.get("successful_tool_sequences", []):
                if not isinstance(row, dict):
                    continue
                seq = row.get("sequence")
                if isinstance(seq, list) and all(isinstance(x, str) for x in seq):
                    key = json.dumps(seq, ensure_ascii=False)
                    seq_map[key] = dict(row)

            for row in patterns.get("successful_tool_sequences", []):
                if not isinstance(row, dict):
                    continue
                seq = row.get("sequence")
                if not isinstance(seq, list):
                    continue
                key = json.dumps(seq, ensure_ascii=False)
                if key not in seq_map:
                    seq_map[key] = {
                        "sequence": list(seq),
                        "count": 0,
                        "likely_success": row.get("likely_success", True),
                        "sources": [],
                    }
                tgt = seq_map[key]
                tgt["count"] = int(tgt.get("count", 0)) + int(row.get("count", 1))
                tgt["likely_success"] = bool(tgt.get("likely_success", True)) or bool(
                    row.get("likely_success", True)
                )
                tgt["sources"] = _dedupe_sources(
                    list(tgt.get("sources", [])) + list(row.get("sources", []))
                )

            tmpl_map: dict[str, dict[str, Any]] = {}
            for row in prior.get("prompt_templates", []):
                if isinstance(row, dict) and isinstance(row.get("text"), str):
                    tmpl_map[row["text"]] = dict(row)

            for row in patterns.get("prompt_templates", []):
                if not isinstance(row, dict) or not isinstance(row.get("text"), str):
                    continue
                t = row["text"]
                if t not in tmpl_map:
                    tmpl_map[t] = {"text": t, "count": 0, "sources": []}
                tmpl_map[t]["count"] = int(tmpl_map[t].get("count", 0)) + int(row.get("count", 1))
                tmpl_map[t]["sources"] = _dedupe_sources(
                    list(tmpl_map[t].get("sources", [])) + list(row.get("sources", []))
                )

            rec_map: dict[str, dict[str, Any]] = {}
            for row in prior.get("error_recovery_patterns", []):
                if not isinstance(row, dict):
                    continue
                ek = json.dumps(
                    [row.get("error_context", ""), row.get("recovery_context", "")],
                    ensure_ascii=False,
                )
                rec_map[ek] = dict(row)

            for row in patterns.get("error_recovery_patterns", []):
                if not isinstance(row, dict):
                    continue
                ek = json.dumps(
                    [row.get("error_context", ""), row.get("recovery_context", "")],
                    ensure_ascii=False,
                )
                if ek not in rec_map:
                    rec_map[ek] = {
                        "error_context": row.get("error_context", ""),
                        "recovery_context": row.get("recovery_context", ""),
                        "count": 0,
                        "sources": [],
                    }
                tgt = rec_map[ek]
                tgt["count"] = int(tgt.get("count", 0)) + int(row.get("count", 1))
                tgt["sources"] = _dedupe_sources(
                    list(tgt.get("sources", [])) + list(row.get("sources", []))
                )

            out_doc = {
                "schema_version": PATTERNS_SCHEMA_VERSION,
                "updated_at_utc": datetime.now(timezone.utc).isoformat(),
                "last_source": patterns.get("source", ""),
                "successful_tool_sequences": sorted(
                    seq_map.values(),
                    key=lambda r: int(r.get("count", 0)),
                    reverse=True,
                ),
                "prompt_templates": sorted(
                    tmpl_map.values(),
                    key=lambda r: int(r.get("count", 0)),
                    reverse=True,
                ),
                "error_recovery_patterns": sorted(
                    rec_map.values(),
                    key=lambda r: int(r.get("count", 0)),
                    reverse=True,
                ),
            }
        else:
            out_doc = None
    else:
        out_doc = None

    if out_doc is None:
        out_doc = {
            "schema_version": PATTERNS_SCHEMA_VERSION,
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            "last_source": patterns.get("source", ""),
            "successful_tool_sequences": list(patterns.get("successful_tool_sequences", [])),
            "prompt_templates": list(patterns.get("prompt_templates", [])),
            "error_recovery_patterns": list(patterns.get("error_recovery_patterns", [])),
        }

    path.write_text(json.dumps(out_doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


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


def run_extraction(
    session_path: Path,
    memory_dir: Path,
    workspace_root: Path,
    *,
    patterns_json: Path | None = None,
    merge_patterns: bool = True,
) -> tuple[Path, Path, Path]:
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

    md_path, js_path = write_digest(digest, memory_dir, stem)
    pattern_doc = extract_patterns(segments, digest.source)
    if patterns_json is not None:
        pj = patterns_json if patterns_json.is_absolute() else (workspace_root / patterns_json).resolve()
    else:
        pj = (memory_dir / "patterns.json").resolve()
    patterns_path = save_patterns_to_memory(
        pattern_doc,
        pj.parent,
        filename=pj.name,
        merge=merge_patterns,
        output_path=pj,
    )
    return md_path, js_path, patterns_path


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Extract OpenClaw conversation 精华 into memory/")
    p.add_argument(
        "session_log",
        type=Path,
        nargs="?",
        help="Path to session transcript (.json, .log, or text). Omit with --stdin.",
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
    p.add_argument(
        "--patterns-json",
        type=Path,
        default=None,
        help="Where to write patterns.json (default: <memory-dir>/patterns.json).",
    )
    p.add_argument(
        "--no-merge-patterns",
        action="store_true",
        help="Replace patterns.json instead of merging with an existing file.",
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

        md_path, json_path, patterns_path = run_extraction(
            path,
            memory_dir,
            ws,
            patterns_json=args.patterns_json,
            merge_patterns=not args.no_merge_patterns,
        )
        try:
            path.unlink(missing_ok=True)  # type: ignore[arg-type]
        except OSError:
            pass

        print(f"Wrote {md_path.as_posix()}")
        print(f"Wrote {json_path.as_posix()}")
        print(f"Wrote {patterns_path.as_posix()}")
        return 0

    if not args.session_log:
        sys.stderr.write("error: session_log path required unless --stdin is set.\n")
        return 2

    sp = args.session_log.resolve()
    if not sp.exists():
        sys.stderr.write(f"error: file not found: {sp}\n")
        return 2

    md_path, json_path, patterns_path = run_extraction(
        sp,
        memory_dir,
        ws,
        patterns_json=args.patterns_json,
        merge_patterns=not args.no_merge_patterns,
    )
    print(f"Wrote {md_path.as_posix()}")
    print(f"Wrote {json_path.as_posix()}")
    print(f"Wrote {patterns_path.as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
