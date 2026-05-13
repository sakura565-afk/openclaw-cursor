#!/usr/bin/env python3
"""Extract decisions, learnings, and tool-usage highlights from OpenClaw session transcripts."""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from scripts.optimize_context import TURN_PATTERN, read_text, repo_root

logger = logging.getLogger(__name__)

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

USER_PREFERENCE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"^\s*(?:preference|my\s+preference)\s*[:\-—]\s*(.+)",
        r"(?:\bI\s+prefer\b|\balways\s+use\b|\bnever\s+use\b|\bI\s+want\b|\bI\s+need\b)\s+(.{8,})",
        r"(?:\bgoing\s+forward\b|\bfrom\s+now\s+on\b),?\s+(.{8,})",
        r"(?:请|希望|务必|不要)\s*(.{4,})",  # Chinese steering
    )
)

CORRECTION_LINE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"^\s*(?:correction|fix|fixed)\s*[:\-—]\s*(.+)",
        r"(?:\bno,?\s+that'?s\s+wrong\b|\bthat'?s\s+incorrect\b|\bactually,?\b)\s*[,:]?\s*(.{6,})",
        r"(?:\bI\s+meant\b|\bnot\s+\S+\s*,?\s*but\b)\s+(.{6,})",
        r"(?:\bwrong\b|\bincorrect\b)\s*[.!]?\s*(.{6,})",
        r"(?:不对|错了|应该是)\s*[：:,]?\s*(.{4,})",
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


def _openclaw_argv() -> list[str]:
    """Resolve argv prefix: full path to `openclaw` or `npx openclaw`."""

    exe = os.environ.get("OPENCLAW_EXECUTABLE", "").strip()
    if exe:
        return [exe]
    return ["npx", "openclaw"]


def run_openclaw_cli(
    subcommand: Sequence[str],
    *,
    timeout_sec: float = 120.0,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run OpenClaw CLI; stdout/stderr captured as text."""

    cmd = [*_openclaw_argv(), *subcommand]
    logger.debug("Running OpenClaw: %s", " ".join(cmd))
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout_sec,
        cwd=str(cwd) if cwd else None,
        check=False,
    )


def fetch_sessions_list_json(
    *,
    all_agents: bool = False,
    limit: int = 50,
    active_minutes: int | None = None,
    timeout_sec: float = 120.0,
) -> dict[str, Any]:
    """
    Mirror MCP ``sessions_list`` store discovery via ``openclaw sessions --json``.

    Uses the local session store (``sessions.json``); does not require a running Gateway.
    """

    lim = "all" if limit <= 0 else str(limit)
    parts: list[str] = ["sessions"]
    if all_agents:
        parts.append("--all-agents")
    if active_minutes is not None and active_minutes > 0:
        parts.extend(["--active", str(active_minutes)])
    parts.extend(["--limit", lim, "--json"])

    proc = run_openclaw_cli(parts, timeout_sec=timeout_sec)
    if proc.returncode != 0:
        logger.warning(
            "openclaw sessions failed (rc=%s): %s",
            proc.returncode,
            (proc.stderr or proc.stdout or "")[:500],
        )
        return {}
    try:
        return json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        logger.warning("openclaw sessions returned non-JSON stdout")
        return {}


def fetch_sessions_history_json(
    session_key: str,
    *,
    message_limit: int | None = None,
    timeout_sec: float = 180.0,
) -> dict[str, Any]:
    """
    Mirror MCP ``sessions_history`` via Gateway RPC ``chat.history``.

    Requires a reachable Gateway (same as the in-agent tool).
    """

    params: dict[str, Any] = {"sessionKey": session_key}
    if message_limit is not None and message_limit > 0:
        params["limit"] = message_limit

    timeout_ms = max(1000, int(timeout_sec * 1000))
    proc = run_openclaw_cli(
        [
            "gateway",
            "call",
            "chat.history",
            "--json",
            "--params",
            json.dumps(params, separators=(",", ":"), ensure_ascii=False),
            "--timeout",
            str(timeout_ms),
        ],
        timeout_sec=timeout_sec + 15.0,
    )
    if proc.returncode != 0:
        logger.warning(
            "chat.history failed for %s (rc=%s): %s",
            session_key,
            proc.returncode,
            (proc.stderr or proc.stdout or "")[:500],
        )
        return {}
    try:
        return json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        logger.warning("chat.history returned non-JSON for %s", session_key)
        return {}


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
    user_preferences: list[str]
    corrections: list[str]
    tool_structured: Counter[str] = field(default_factory=Counter)
    tool_textual: Counter[str] = field(default_factory=Counter)

    def all_tools(self) -> Counter[str]:
        merged: Counter[str] = Counter(self.tool_structured)
        merged.update(self.tool_textual)
        return merged


def analyze_segments(segments: list[tuple[int, str | None, str]], source_display: str) -> ConversationDigest:
    decisions_acc: list[str] = []
    learnings_acc: list[str] = []
    preferences_acc: list[str] = []
    corrections_acc: list[str] = []
    structured_tools: Counter[str] = Counter()
    blobs_for_text_tools: list[str] = []

    def uniq(xs: list[str]) -> list[str]:
        return list(dict.fromkeys([x for x in xs if x]))

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
            decisions_acc.extend(match_patterns(text, DECISION_LINE_PATTERNS))
            preferences_acc.extend(match_patterns(text, USER_PREFERENCE_PATTERNS))
            corrections_acc.extend(match_patterns(text, CORRECTION_LINE_PATTERNS))

        blobs_for_text_tools.append(text)

    combined = "\n\n".join(blobs_for_text_tools)
    textual_tools = extract_tool_signals(combined)

    return ConversationDigest(
        source=source_display,
        generated_at_utc=datetime.now(timezone.utc).isoformat(),
        segments=segments,
        decisions=uniq(decisions_acc)[:120],
        learnings=uniq(learnings_acc)[:120],
        user_preferences=uniq(preferences_acc)[:120],
        corrections=uniq(corrections_acc)[:120],
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

    lines.extend(["", "## User preferences"])
    if d.user_preferences:
        for item in d.user_preferences:
            lines.append(f"- {item}")
    else:
        lines.append("- *(no explicit preference cues detected)*")

    lines.extend(["", "## Corrections & steering"])
    if d.corrections:
        for item in d.corrections:
            lines.append(f"- {item}")
    else:
        lines.append("- *(no explicit correction cues detected)*")

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
            "user_preferences": len(d.user_preferences),
            "corrections": len(d.corrections),
            "tool_names_distinct": len(d.all_tools()),
        },
        "decisions": d.decisions,
        "learnings": d.learnings,
        "user_preferences": d.user_preferences,
        "corrections": d.corrections,
        "tools_ranked": d.all_tools().most_common(),
        "tools_structured": dict(d.tool_structured),
        "tools_from_text_heuristic": dict(d.tool_textual),
    }


def dated_memory_output_dir(memory_dir: Path, at: datetime | None = None) -> Path:
    """``memory/conversation_summaries/YYYY-MM-DD/`` (UTC date)."""

    dt = at or datetime.now(timezone.utc)
    out = memory_dir / "conversation_summaries" / dt.date().isoformat()
    out.mkdir(parents=True, exist_ok=True)
    return out


def segments_from_chat_history_payload(payload: dict[str, Any]) -> list[tuple[int, str | None, str]]:
    """Turn a ``chat.history`` / ``sessions_history``-style JSON payload into segments."""

    messages = payload.get("messages")
    if not isinstance(messages, list):
        inner = payload.get("result")
        if isinstance(inner, dict):
            messages = inner.get("messages")
    if isinstance(messages, list):
        return _segments_from_messages(messages)
    return []


def extract_from_openclaw_stores(
    memory_dir: Path,
    workspace_root: Path,
    *,
    max_sessions: int = 10,
    history_message_limit: int | None = 400,
    all_agents: bool = False,
    list_limit: int = 50,
) -> list[tuple[Path, Path]]:
    """
    List sessions via CLI (``sessions_list`` parity), fetch each transcript via
    ``chat.history`` (``sessions_history`` parity), and write digests under *memory_dir*.
    """

    written: list[tuple[Path, Path]] = []
    listing = fetch_sessions_list_json(all_agents=all_agents, limit=list_limit)
    rows = listing.get("sessions") if isinstance(listing.get("sessions"), list) else []
    if not rows:
        logger.info("OpenClaw sessions list empty or unavailable; nothing to extract.")
        return written

    count = 0
    for row in rows:
        if count >= max_sessions:
            break
        if not isinstance(row, dict):
            continue
        key = row.get("key")
        if not isinstance(key, str) or not key.strip():
            continue

        hist = fetch_sessions_history_json(key.strip(), message_limit=history_message_limit)
        segments = segments_from_chat_history_payload(hist)
        if not segments:
            logger.debug("Skipping session %s: no messages from chat.history", key)
            continue

        digest = analyze_segments(segments, f"openclaw:{key.strip()}")
        digest.source = f"openclaw:{key.strip()}"
        stem = key.strip().replace(":", "_")
        written.append(write_digest(digest, memory_dir, stem))
        count += 1
        logger.info("Wrote conversation extract for session key %s", key)

    return written


def run_post_reflection_conversation_extract(
    root: Path,
    *,
    max_sessions: int = 5,
    dry_run: bool = False,
    all_agents: bool = False,
) -> list[str]:
    """
    Hook for ``auto_reflection.py``: summarize recent OpenClaw sessions into ``memory/``.
    """

    if dry_run:
        return ["[dry-run] OpenClaw conversation extract skipped."]

    memory_dir = (root / "memory").resolve()
    paths = extract_from_openclaw_stores(
        memory_dir,
        root,
        max_sessions=max_sessions,
        all_agents=all_agents,
    )
    if not paths:
        return ["Conversation extract: no OpenClaw sessions written (empty list or gateway unavailable)."]
    return [f"Conversation extract: wrote {md.as_posix()} (+ JSON)" for md, _ in paths]


def write_digest(
    digest: ConversationDigest,
    memory_dir: Path,
    stem: str,
    *,
    output_at: datetime | None = None,
) -> tuple[Path, Path]:
    memory_dir.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^\w\-_.]+", "_", stem).strip("_") or "session"
    tag = utc_stamp()

    base_dir = dated_memory_output_dir(memory_dir, output_at)
    md_path = base_dir / f"conversation_extract_{safe}_{tag}.md"
    js_path = base_dir / f"conversation_extract_{safe}_{tag}.json"

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
    p = argparse.ArgumentParser(description="Extract OpenClaw conversation 精华 into memory/")
    p.add_argument(
        "session_log",
        type=Path,
        nargs="?",
        help="Path to session transcript (.json, .log, or text). Omit with --stdin or --from-openclaw.",
    )
    p.add_argument(
        "--stdin",
        action="store_true",
        help="Read transcript JSON/text from stdin (written to a temp file under memory/).",
    )
    p.add_argument(
        "--from-openclaw",
        action="store_true",
        help="Use openclaw sessions + gateway chat.history (sessions_list / sessions_history parity).",
    )
    p.add_argument(
        "--openclaw-max-sessions",
        type=int,
        default=10,
        metavar="N",
        help="With --from-openclaw: max sessions to pull history for (default: 10).",
    )
    p.add_argument(
        "--openclaw-all-agents",
        action="store_true",
        help="With --from-openclaw: pass --all-agents to openclaw sessions.",
    )
    p.add_argument(
        "--openclaw-history-limit",
        type=int,
        default=400,
        metavar="N",
        help="With --from-openclaw: chat.history message limit (default: 400; use 0 for no limit).",
    )
    p.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable debug logging.",
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


def configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    if not logging.root.handlers:
        logging.basicConfig(level=level, format="%(levelname)s %(message)s")
    else:
        logging.getLogger().setLevel(level)
    logger.setLevel(level)


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    configure_logging(args.verbose)

    ws = (args.workspace_root or repo_root()).resolve()
    memory_dir = (args.memory_dir or ws / "memory").resolve()

    if args.from_openclaw:
        hl = args.openclaw_history_limit
        history_limit = None if hl <= 0 else hl
        paths = extract_from_openclaw_stores(
            memory_dir,
            ws,
            max_sessions=max(1, args.openclaw_max_sessions),
            history_message_limit=history_limit,
            all_agents=args.openclaw_all_agents,
        )
        for md, js in paths:
            print(f"Wrote {md.as_posix()}")
            print(f"Wrote {js.as_posix()}")
        if not paths:
            logger.warning("No extracts written from OpenClaw.")
            return 1
        return 0

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
        sys.stderr.write("error: session_log path required unless --stdin or --from-openclaw is set.\n")
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
