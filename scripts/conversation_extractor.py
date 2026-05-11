#!/usr/bin/env python3
"""Extract reusable agent-improvement patterns from session transcripts.

Writes tagged records under ``.learnings/`` (JSONL + state for incremental runs),
with optional markdown/JSON digests under ``memory/``.
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
from typing import Any, Iterable

from scripts.optimize_context import TURN_PATTERN, read_text, repo_root

SCHEMA_VERSION = 1
PATTERNS_FILE = "patterns.jsonl"
STATE_FILE = "conversation_extractor_state.json"
PATTERN_INDEX_FILE = "pattern_id_index.json"

# Primary ``type`` field values (also duplicated in ``tags`` for queries).
PATTERN_TYPES = frozenset(
    {
        "error_fix",
        "optimization",
        "workflow",
        "decision",
        "solution",
        "learning",
        "tooling",
        "context",
    }
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

# User pushback / corrections (prefer whole line as body).
CORRECTION_LINE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"(?:actually|not\s+quite|that'?s\s+wrong|incorrect|i\s+meant|correction)\s*[:\-—]?\s*(.+)",
        r"(?:no,?\s+(?:that|this|it)|don'?t\s+do\s+that|undo\s+that|revert)\b[.!]?\s*(.*)",
        r"(?:should\s+be|use\s+\S+\s+instead\s+of|not\s+\S+\s+but)\s+(.+)",
        r"(?:you\s+(?:misunderstood|missed|ignored))\b[:\-]?\s*(.+)",
    )
)

SOLUTION_LINE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"(?:that\s+worked|works\s+now|problem\s+solved|all\s+set|we'?re\s+good)\b[.!]?\s*(.*)",
        r"(?:tests?\s+pass|CI\s+is\s+green|build\s+is\s+green|merged|shipped)\b[.!]?\s*(.*)",
        r"(?:successfully|fixed\s+the\s+issue|resolved\s+the\s+(?:issue|bug))\b[.!]?\s*(.*)",
    )
)

WORKFLOW_LINE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"(?:always\s+(?:start|begin|do|run)|first\s+step|workflow|playbook|rubric|checklist)\b\s*[:\-—]?\s*(.+)",
        r"(?:before\s+you\s+|when\s+debugging|when\s+implementing)\b[,:]?\s*(.+)",
    )
)

OPTIMIZATION_LINE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"(?:cache|memoiz|batch\s+requests?|paralleliz|reduce\s+tokens|latency|throughput)\b[^.!?]{0,120}",
        r"(?:O\([^)]+\)|time\s+complexity|memory\s+bloat|hot\s+path)\b[^.!?]{0,120}",
    )
)

CONTEXT_LINE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"(?:constraint|must\s+not|never\s+|always\s+enforce|hard\s+requirement)\b\s*[:\-—]?\s*(.+)",
        r"(?:security|PII|secret|credential)\b[^.!?]{0,160}",
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


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _normalize_key(text: str) -> str:
    return " ".join(text.strip().lower().split())


def pattern_stable_id(source: str, pattern_type: str, summary: str) -> str:
    payload = json.dumps(
        {"source": source, "type": pattern_type, "summary": _normalize_key(summary)},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def learnings_dir(workspace: Path, override: Path | None) -> Path:
    base = (override or (workspace / ".learnings")).resolve()
    return base


def _patterns_path(learnings: Path) -> Path:
    return learnings / PATTERNS_FILE


def _state_path(learnings: Path) -> Path:
    return learnings / STATE_FILE


def _id_index_path(learnings: Path) -> Path:
    return learnings / PATTERN_INDEX_FILE


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def load_pattern_id_index(learnings: Path) -> set[str]:
    raw = _load_json(_id_index_path(learnings), None)
    if isinstance(raw, dict) and isinstance(raw.get("ids"), list):
        return {str(x) for x in raw["ids"] if isinstance(x, str)}
    return set()


def save_pattern_id_index(learnings: Path, ids: set[str]) -> None:
    _atomic_write_json(_id_index_path(learnings), {"schema_version": SCHEMA_VERSION, "ids": sorted(ids)})


def merge_pattern_ids(learnings: Path, new_ids: Iterable[str]) -> set[str]:
    cur = load_pattern_id_index(learnings)
    cur.update(new_ids)
    save_pattern_id_index(learnings, cur)
    return cur


def load_extractor_state(learnings: Path) -> dict[str, Any]:
    doc = _load_json(_state_path(learnings), {})
    if not isinstance(doc, dict):
        return {"schema_version": SCHEMA_VERSION, "sources": {}}
    doc.setdefault("schema_version", SCHEMA_VERSION)
    doc.setdefault("sources", {})
    if not isinstance(doc["sources"], dict):
        doc["sources"] = {}
    return doc


def save_extractor_state(learnings: Path, state: dict[str, Any]) -> None:
    _atomic_write_json(_state_path(learnings), state)


def append_patterns_jsonl(learnings: Path, records: list[dict[str, Any]]) -> int:
    if not records:
        return 0
    path = _patterns_path(learnings)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return len(records)


def iter_patterns_jsonl(learnings: Path) -> Iterable[dict[str, Any]]:
    path = _patterns_path(learnings)
    if not path.exists():
        return
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                yield obj


def _truncate(s: str, limit: int) -> str:
    s = s.strip()
    if len(s) <= limit:
        return s
    return s[: limit - 3] + "..."


def _classify_line(
    line: str,
    role: str | None,
) -> list[tuple[str, str, list[str]]]:
    """Return list of (type, summary, extra_tags) for one line."""

    rl = (role or "").lower()
    hits: list[tuple[str, str, list[str]]] = []

    def add(pat_type: str, summary: str, tags: list[str]) -> None:
        summary = _truncate(summary, 400)
        if len(summary) < 8:
            return
        hits.append((pat_type, summary, tags))

    if rl == "user":
        for pat in CORRECTION_LINE_PATTERNS:
            m = pat.search(line)
            if m:
                cap = (m.group(1) if m.lastindex else m.group(0)).strip() or line.strip()
                add("error_fix", cap, ["user_correction"])
                break

    for pat in SOLUTION_LINE_PATTERNS:
        m = pat.search(line)
        if m:
            cap = (m.group(1) if m.lastindex else m.group(0)).strip() or line.strip()
            add("solution", cap, ["success_signal"])
            break

    for pat in DECISION_LINE_PATTERNS:
        m = pat.search(line)
        if m:
            cap = (m.group(1) if m.lastindex else m.group(0)).strip()
            add("decision", cap, [])
            break

    for pat in LEARNING_LINE_PATTERNS:
        m = pat.search(line)
        if m:
            cap = (m.group(1) if m.lastindex else m.group(0)).strip()
            add("learning", cap, [])
            break

    for pat in WORKFLOW_LINE_PATTERNS:
        m = pat.search(line)
        if m:
            cap = (m.group(1) if m.lastindex else m.group(0)).strip() or line.strip()
            add("workflow", cap, [])
            break

    for pat in OPTIMIZATION_LINE_PATTERNS:
        m = pat.search(line)
        if m:
            cap = m.group(0).strip()
            add("optimization", cap, [])
            break

    for pat in CONTEXT_LINE_PATTERNS:
        m = pat.search(line)
        if m:
            cap = (m.group(1) if m.lastindex else m.group(0)).strip() or line.strip()
            add("context", cap, [])
            break

    return hits


def extract_patterns_from_segments(
    segments: list[tuple[int, str | None, str]],
    source_display: str,
    *,
    max_tooling: int = 24,
) -> list[dict[str, Any]]:
    """Build persisted pattern dicts aimed at future agent performance."""

    out: list[dict[str, Any]] = []
    extracted_at = datetime.now(timezone.utc).isoformat()
    seen_tool: set[str] = set()

    for turn, role, text in segments:
        rl = (role or "").lower()
        if rl == "tool":
            name = normalize_ws(text.replace("[tool:", "").replace("]", "").strip()).split("(", 1)[0].strip()
            if name and name not in seen_tool and len(seen_tool) < max_tooling:
                seen_tool.add(name)
                summary = f"Transcript shows tool invocation: `{name}`"
                pid = pattern_stable_id(source_display, "tooling", summary)
                tags = ["tooling", "tool_invocation", name.lower().replace(" ", "_")[:48]]
                out.append(
                    {
                        "id": pid,
                        "type": "tooling",
                        "summary": summary,
                        "body": "",
                        "turn": turn,
                        "role": "tool",
                        "tags": tags,
                        "source": source_display,
                        "extracted_at_utc": extracted_at,
                    }
                )
            continue

        if rl == "tool_output":
            continue

        for raw_line in text.splitlines():
            line = raw_line.strip()
            if len(line) < 12:
                continue
            for ptype, summary, extra_tags in _classify_line(line, role):
                tags = list(dict.fromkeys([ptype, *extra_tags]))
                pid = pattern_stable_id(source_display, ptype, summary)
                out.append(
                    {
                        "id": pid,
                        "type": ptype,
                        "summary": summary,
                        "body": _truncate(line, 800),
                        "turn": turn,
                        "role": rl or None,
                        "tags": tags,
                        "source": source_display,
                        "extracted_at_utc": extracted_at,
                    }
                )

    return out


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

    lines.extend(
        [
            "",
            "---",
            "*OpenClaw conversation_extractor.py — patterns in `.learnings/`; optional digest in `memory/`.*",
        ]
    )
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


def _relative_source(session_path: Path, workspace_root: Path) -> str:
    absolute = session_path.resolve().as_posix()
    root_posix = workspace_root.resolve().as_posix()
    if absolute.startswith(root_posix):
        return Path(absolute[len(root_posix) :].lstrip("/")).as_posix()
    return absolute


def run_extraction(
    session_path: Path,
    memory_dir: Path,
    workspace_root: Path,
) -> tuple[Path, Path]:
    segments = parse_session_log(session_path.resolve())

    digest = analyze_segments(segments, session_path.resolve().as_posix())

    stem = session_path.stem
    parent = session_path.parent.name
    if parent and parent not in {".", ""}:
        stem = f"{parent}__{session_path.stem}"

    digest.source = _relative_source(session_path, workspace_root)

    return write_digest(digest, memory_dir, stem)


def extract_and_store_patterns(
    session_path: Path,
    learnings: Path,
    workspace_root: Path,
    *,
    incremental: bool = True,
) -> tuple[bool, int, list[dict[str, Any]]]:
    """Parse transcript, append new patterns to JSONL, update incremental state.

    Returns ``(skipped_incremental, new_count, new_records)``.
    """

    resolved = session_path.resolve()
    if not resolved.exists():
        raise FileNotFoundError(resolved)

    segments = parse_session_log(resolved)
    source_display = _relative_source(resolved, workspace_root)
    patterns = extract_patterns_from_segments(segments, source_display)

    state = load_extractor_state(learnings)
    sources = state.setdefault("sources", {})
    if not isinstance(sources, dict):
        sources = {}
        state["sources"] = sources

    source_key = resolved.as_posix()
    content_hash = _sha256_file(resolved)
    if incremental and isinstance(sources.get(source_key), dict):
        prev = sources[source_key].get("sha256")
        if isinstance(prev, str) and prev == content_hash:
            return True, 0, []

    existing_ids = load_pattern_id_index(learnings)
    new_recs = [p for p in patterns if p["id"] not in existing_ids]
    append_patterns_jsonl(learnings, new_recs)
    if new_recs:
        merge_pattern_ids(learnings, (p["id"] for p in new_recs))

    sources[source_key] = {
        "sha256": content_hash,
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "relative_source": source_display,
    }
    save_extractor_state(learnings, state)
    return False, len(new_recs), new_recs


def query_patterns(
    learnings: Path,
    *,
    keywords: list[str],
    tags: list[str],
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Filter persisted patterns: any keyword hit (if given) AND any tag/type (if given)."""

    kw = [k.strip().lower() for k in keywords if k and k.strip()]
    tg = [t.strip().lower() for t in tags if t and t.strip()]
    out: list[dict[str, Any]] = []
    for rec in iter_patterns_jsonl(learnings):
        if not isinstance(rec, dict):
            continue
        tags_list = rec.get("tags") if isinstance(rec.get("tags"), list) else []
        blob = " ".join(
            [
                str(rec.get("summary", "")),
                str(rec.get("body", "")),
                str(rec.get("source", "")),
                " ".join(str(x) for x in tags_list),
            ]
        ).lower()
        ptype = str(rec.get("type", "")).lower()
        rec_tags = [str(x).lower() for x in rec.get("tags") or []] if isinstance(rec.get("tags"), list) else []

        if kw and not any(k in blob for k in kw):
            continue
        if tg:
            if not any(t == ptype or t in rec_tags for t in tg):
                continue
        out.append(rec)
        if len(out) >= limit:
            break
    return out


def _prepend_extract_command(argv: list[str]) -> list[str]:
    if not argv:
        return argv
    if argv[0] in {"extract", "query", "-h", "--help"}:
        return argv
    if argv[0] in {"--stdin"}:
        return ["extract", *argv]
    if argv[0].startswith("-"):
        return ["extract", *argv]
    return ["extract", *argv]


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Extract tagged, reusable patterns from agent transcripts into .learnings/",
    )
    sub = p.add_subparsers(dest="command", required=True)

    ex = sub.add_parser("extract", help="Parse a transcript and append patterns (optional memory digest).")
    ex.add_argument(
        "session_log",
        type=Path,
        nargs="?",
        help="Session transcript (.json, .log, or text). Required unless --stdin.",
    )
    ex.add_argument(
        "--stdin",
        action="store_true",
        help="Read transcript from stdin (temp file; not incremental).",
    )
    ex.add_argument(
        "--learnings-dir",
        type=Path,
        default=None,
        help="Directory for patterns.jsonl and state (default: <workspace>/.learnings).",
    )
    ex.add_argument(
        "--no-incremental",
        action="store_true",
        help="Reprocess even when transcript bytes unchanged.",
    )
    ex.add_argument(
        "--memory-digest",
        action="store_true",
        help="Also write markdown + JSON digest under memory/ (legacy summary).",
    )
    ex.add_argument(
        "--memory-dir",
        type=Path,
        default=None,
        help="Destination for digest when --memory-digest is set (default: <repo>/memory).",
    )
    ex.add_argument(
        "--workspace-root",
        type=Path,
        default=None,
        help="Workspace root for relative paths (default: repository root).",
    )

    qu = sub.add_parser("query", help="Search persisted patterns by keyword and/or tag.")
    qu.add_argument(
        "-k",
        "--keyword",
        action="append",
        default=[],
        help="Substring match on summary/body/source/tags (repeatable; OR logic).",
    )
    qu.add_argument(
        "-t",
        "--tag",
        action="append",
        default=[],
        help="Match pattern type or any tag (repeatable; OR logic among values).",
    )
    qu.add_argument(
        "--limit",
        type=int,
        default=80,
        help="Maximum rows to print (default: 80).",
    )
    qu.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON array instead of human-readable lines.",
    )
    qu.add_argument(
        "--learnings-dir",
        type=Path,
        default=None,
        help="Directory containing patterns.jsonl (default: <workspace>/.learnings).",
    )
    qu.add_argument(
        "--workspace-root",
        type=Path,
        default=None,
        help="Workspace root when resolving default learnings dir.",
    )

    return p


def main(argv: list[str] | None = None) -> int:
    argv = _prepend_extract_command(list(sys.argv[1:] if argv is None else argv))
    args = build_arg_parser().parse_args(argv)
    ws = (args.workspace_root or repo_root()).resolve()
    learnings = learnings_dir(ws, getattr(args, "learnings_dir", None))

    if args.command == "query":
        rows = query_patterns(
            learnings,
            keywords=list(args.keyword),
            tags=list(args.tag),
            limit=max(1, args.limit),
        )
        if args.json:
            print(json.dumps(rows, indent=2, ensure_ascii=False))
            return 0
        if not rows:
            print("(no matches)")
            return 0
        for rec in rows:
            tid = rec.get("id", "")
            ptype = rec.get("type", "")
            summary = rec.get("summary", "")
            src = rec.get("source", "")
            print(f"[{tid}] {ptype}: {summary}")
            print(f"    source={src}")
        return 0

    # extract
    memory_dir = (args.memory_dir or ws / "memory").resolve()
    incremental = not args.no_incremental

    if args.stdin:
        import tempfile

        memory_dir.mkdir(parents=True, exist_ok=True)
        payload = sys.stdin.read()
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".txt", prefix="stdin_session_", dir=memory_dir)
        path = Path(tmp.name)
        try:
            path.write_text(payload, encoding="utf-8")
        finally:
            tmp.close()

        try:
            skipped, n_new, _ = extract_and_store_patterns(path, learnings, ws, incremental=False)
            print(f"Patterns: appended {n_new} new record(s) to {_patterns_path(learnings).as_posix()}")
            if args.memory_digest:
                md_path, json_path = run_extraction(path, memory_dir, ws)
                print(f"Wrote {md_path.as_posix()}")
                print(f"Wrote {json_path.as_posix()}")
        finally:
            try:
                path.unlink(missing_ok=True)  # type: ignore[arg-type]
            except OSError:
                pass
        return 0

    if not args.session_log:
        sys.stderr.write("error: extract requires session_log or --stdin.\n")
        return 2

    sp = args.session_log.resolve()
    if not sp.exists():
        sys.stderr.write(f"error: file not found: {sp}\n")
        return 2

    try:
        skipped, n_new, _ = extract_and_store_patterns(sp, learnings, ws, incremental=incremental)
    except OSError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 1

    status = "skipped (unchanged transcript; incremental)" if skipped else f"appended {n_new} new pattern(s)"
    print(f"Patterns [{status}]: {_patterns_path(learnings).as_posix()}")

    if args.memory_digest:
        md_path, json_path = run_extraction(sp, memory_dir, ws)
        print(f"Wrote {md_path.as_posix()}")
        print(f"Wrote {json_path.as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
