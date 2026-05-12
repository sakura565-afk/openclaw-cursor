#!/usr/bin/env python3
"""Extract high-value signals from OpenClaw session transcripts into `.learnings/conversation_extracts.md`.

Scans the workspace for JSON/text logs that look like chat sessions, classifies lines into
decisions, tools, errors, corrections, and insights, and merges everything into one Markdown
file. Re-runs are idempotent: unchanged transcripts keep their existing section (matched by
content hash in HTML comment markers).

Run from the repository root::

    python3 scripts/conversation_extractor.py
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
from typing import Any

# -----------------------------------------------------------------------------
# Repo / IO helpers (stdlib-only; safe for `python3 scripts/conversation_extractor.py`)
# -----------------------------------------------------------------------------

TURN_PATTERN = re.compile(r"(?i)\b(?:turn|step|message)\s*[:#-]?\s*(\d+)\b")

MARKER_PREFIX = "conversation_extractor:"

SKIP_DIR_PARTS = frozenset(
    {
        ".git",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        "dist",
        "build",
        ".mypy_cache",
        ".pytest_cache",
        ".tox",
    }
)

MAX_TRANSCRIPT_BYTES = 4 * 1024 * 1024
SNIFF_BYTES = 96 * 1024

# -----------------------------------------------------------------------------
# Patterns: decisions / learnings / errors / corrections
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

ERROR_LINE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"(?:^|\s)(?:traceback|exception|error)\s*(?:\(|:|,|\s)",
        r"\b(?:fatal|critical)\s+error\b",
        r"\b(?:command\s+)?failed\b",
        r"\bexit\s+code\s*[:\s]+\d+",
        r"\b(?:errno|status\s+code)\s*[:\s]+\d+",
        r"`[^`]*(?:error|exception|traceback)[^`]*`",
        r"(?:stderr|stdout)\s*:\s*(.{8,})",
        r"\bModuleNotFoundError\b",
        r"\bAttributeError\b",
        r"\bTypeError\b",
        r"\bValueError\b",
        r"\bKeyError\b",
        r"\bJSONDecodeError\b",
        r"\bConnection(?:Error|RefusedError)\b",
        r"\b404\b|\b500\b",
    )
)

CORRECTION_LINE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\b(?:correction|correcting)\s*[:\-—]?\s*(.+)",
        r"\b(?:actually|I\s+meant|to\s+clarify)\b[,:]?\s*(.{8,})",
        r"\b(?:that\s+was\s+wrong|I\s+was\s+wrong|my\s+mistake)\b[.:]?\s*(.{0,200})",
        r"\b(?:fixed|updated)\s+(?:by|to|the)\b[:\s]+(.{8,})",
        r"\b(?:retract|retracting|override)\b[:\s]+(.{8,})",
    )
)

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

DISCOVERY_GLOBS: tuple[str, ...] = (
    "memory/**/*.json",
    "logs/**/*.json",
    ".openclaw/**/sessions/**/*.json",
    ".openclaw/**/workspace/**/sessions/**/*.json",
)

SESSION_FILENAMES = frozenset(
    {
        "session.json",
        "transcript.json",
        "conversation.json",
        "messages.json",
    }
)


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def read_text(path: Path, limit: int | None = None) -> str:
    try:
        raw = path.read_bytes()
    except OSError:
        return ""
    if limit is not None and len(raw) > limit:
        raw = raw[:limit]
    return raw.decode("utf-8", errors="replace")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    try:
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(chunk)
    except OSError:
        return ""
    return h.hexdigest()


def make_marker(rel_posix: str, sha256_hex: str) -> str:
    payload = json.dumps({"v": 1, "rel": rel_posix, "sha": sha256_hex}, separators=(",", ":"))
    return f"<!-- {MARKER_PREFIX}{payload} -->"


def parse_marker_line(line: str) -> tuple[str, str] | None:
    s = line.strip()
    token = f"<!-- {MARKER_PREFIX}"
    if not s.startswith(token) or not s.endswith("-->"):
        return None
    inner = s[len(token) : -len("-->")].strip()
    try:
        obj = json.loads(inner)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    rel = obj.get("rel")
    sha = obj.get("sha")
    if isinstance(rel, str) and isinstance(sha, str) and len(sha) == 64:
        return rel, sha
    return None


def _infer_turn(blob: dict[str, Any], index: int) -> int:
    for key in ("turn", "step", "message_index", "index", "message"):
        raw = blob.get(key)
        if isinstance(raw, int) and raw > 0:
            return raw
        if isinstance(raw, str) and raw.isdigit():
            return max(int(raw), 1)
    return index + 1


def _stringify_toolish_dict(d: dict[str, Any]) -> str | None:
    fn = d.get("function")
    if isinstance(fn, dict) and isinstance(fn.get("name"), str):
        return fn["name"]
    for key in ("name", "tool", "toolName", "tool_name", "id"):
        raw = d.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return None


def _flatten_content_piece(piece: Any) -> tuple[str, list[str]]:
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

        seen: set[str] = set()
        for tn in blob_tools:
            tn = tn.strip()
            if not tn or tn in seen:
                continue
            seen.add(tn)
            segments.append((turn, "tool", tn))

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


def parse_json_session(raw: str) -> list[tuple[int, str | None, str]]:
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


def parse_text_session(text: str) -> list[tuple[int, str | None, str]]:
    segments: list[tuple[int, str | None, str]] = []
    current_turn = 1
    role_prefix = re.compile(
        r"^\s*(?P<role>user|human|assistant|agent|tool|system)\s*[:|\\-]+\s*(?P<body>.+)$",
        re.IGNORECASE,
    )

    for line_number, line in enumerate(text.splitlines(), start=1):
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


def parse_session_file(path: Path) -> list[tuple[int, str | None, str]]:
    suf = path.suffix.lower()
    if suf == ".json":
        raw = read_text(path, MAX_TRANSCRIPT_BYTES)
        segs = parse_json_session(raw)
        if segs:
            return segs
        return parse_text_session(raw)
    return parse_text_session(read_text(path, MAX_TRANSCRIPT_BYTES))


def json_looks_like_transcript(raw: str) -> bool:
    if not raw.strip():
        return False
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return False
    if isinstance(data, dict):
        for key in ("messages", "conversation", "transcript", "history"):
            inner = data.get(key)
            if isinstance(inner, list) and inner and isinstance(inner[0], dict):
                if any(k in inner[0] for k in ("role", "content", "speaker", "tool_calls", "toolCalls")):
                    return True
        sessions = data.get("sessions")
        if isinstance(sessions, list) and sessions and isinstance(sessions[0], dict):
            if "messages" in sessions[0]:
                return True
    if isinstance(data, list) and data and isinstance(data[0], dict):
        if any(k in data[0] for k in ("role", "content", "speaker", "turn")):
            return True
    return False


def text_sniff_looks_like_transcript(sample: str) -> bool:
    if TURN_PATTERN.search(sample):
        return True
    role_hit = re.search(
        r"(?mi)^\s*(?:user|human|assistant|agent|tool|system)\s*[:|\\-]+\s*\S+",
        sample,
    )
    if role_hit:
        return True
    return False


def looks_like_transcript(path: Path) -> bool:
    try:
        st = path.stat()
    except OSError:
        return False
    if not path.is_file() or st.st_size == 0 or st.st_size > MAX_TRANSCRIPT_BYTES:
        return False

    suf = path.suffix.lower()
    if suf == ".json":
        return json_looks_like_transcript(read_text(path, min(SNIFF_BYTES, st.st_size)))

    if suf in {".log", ".txt", ".md"}:
        return text_sniff_looks_like_transcript(read_text(path, min(SNIFF_BYTES, st.st_size)))

    return False


def _skip_path(path: Path) -> bool:
    return any(p in SKIP_DIR_PARTS for p in path.parts)


def discover_transcripts(root: Path) -> list[Path]:
    found: set[Path] = set()

    for pattern in DISCOVERY_GLOBS:
        for p in root.glob(pattern):
            if p.is_file() and not _skip_path(p.relative_to(root)) and looks_like_transcript(p):
                found.add(p.resolve())

    for name in SESSION_FILENAMES:
        for p in root.rglob(name):
            try:
                rp = p.resolve()
            except OSError:
                continue
            if not p.is_file() or _skip_path(p):
                continue
            if looks_like_transcript(p):
                found.add(rp)

    return sorted(found)


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


def uniq(xs: list[str], cap: int = 80) -> list[str]:
    return list(dict.fromkeys([x for x in xs if x]))[:cap]


@dataclass
class ConversationDigest:
    source: str
    generated_at_utc: str
    content_sha256: str
    segments: list[tuple[int, str | None, str]]
    decisions: list[str]
    learnings: list[str]
    errors: list[str]
    corrections: list[str]
    tool_structured: Counter[str] = field(default_factory=Counter)
    tool_textual: Counter[str] = field(default_factory=Counter)

    def all_tools(self) -> Counter[str]:
        merged: Counter[str] = Counter(self.tool_structured)
        merged.update(self.tool_textual)
        return merged


def analyze_segments(
    segments: list[tuple[int, str | None, str]],
    source_display: str,
    content_sha256: str,
) -> ConversationDigest:
    decisions_acc: list[str] = []
    learnings_acc: list[str] = []
    errors_acc: list[str] = []
    corrections_acc: list[str] = []
    structured_tools: Counter[str] = Counter()
    blobs_for_text: list[str] = []

    for turn, role, text in segments:
        rl = (role or "").lower()
        if rl == "tool":
            tn = normalize_ws(text.replace("[tool:", "").replace("]", "").strip())
            if tn:
                structured_tools[tn.split("(", 1)[0].strip()] += 1
            continue

        if rl == "tool_output":
            blobs_for_text.append(text)
            errors_acc.extend(match_patterns(text, ERROR_LINE_PATTERNS))
            continue

        blobs_for_text.append(text)
        errors_acc.extend(match_patterns(text, ERROR_LINE_PATTERNS))
        corrections_acc.extend(match_patterns(text, CORRECTION_LINE_PATTERNS))

        if rl in {"", "assistant", "agent"} or role is None:
            decisions_acc.extend(match_patterns(text, DECISION_LINE_PATTERNS))
            learnings_acc.extend(match_patterns(text, LEARNING_LINE_PATTERNS))
        elif rl == "user":
            decisions_acc.extend(match_patterns(text, DECISION_LINE_PATTERNS))
            learnings_acc.extend(match_patterns(text, LEARNING_LINE_PATTERNS))
        elif rl == "system":
            learnings_acc.extend(match_patterns(text, LEARNING_LINE_PATTERNS))

    combined = "\n\n".join(blobs_for_text)
    textual_tools = extract_tool_signals(combined)

    return ConversationDigest(
        source=source_display,
        generated_at_utc=datetime.now(timezone.utc).isoformat(),
        content_sha256=content_sha256,
        segments=segments,
        decisions=uniq(decisions_acc),
        learnings=uniq(learnings_acc),
        errors=uniq(errors_acc),
        corrections=uniq(corrections_acc),
        tool_structured=structured_tools,
        tool_textual=textual_tools,
    )


def _md_list(title: str, items: list[str], empty: str) -> list[str]:
    lines = [f"### {title}", ""]
    if items:
        for item in items:
            lines.append(f"- {item}")
    else:
        lines.append(empty)
    lines.append("")
    return lines


def render_section_block(rel_posix: str, digest: ConversationDigest) -> str:
    marker = make_marker(rel_posix, digest.content_sha256)
    lines = [
        marker,
        "",
        f"## `{rel_posix}`",
        "",
        f"- **Content SHA-256**: `{digest.content_sha256[:16]}…`",
        f"- **Segment rows**: {len(digest.segments)}",
        f"- **Analyzed (UTC)**: {digest.generated_at_utc}",
        "",
    ]
    lines.extend(_md_list("Decisions", digest.decisions, "- *(none detected)*"))
    lines.extend(_md_list("Insights & learnings", digest.learnings, "- *(none detected)*"))
    merged_tools = digest.all_tools()
    tool_lines = [f"`{name}` — {ct}×" for name, ct in merged_tools.most_common(35)]
    lines.extend(_md_list("Tools used", tool_lines, "- *(none detected)*"))
    lines.extend(_md_list("Errors & failures", digest.errors, "- *(none detected)*"))
    lines.extend(_md_list("Corrections", digest.corrections, "- *(none detected)*"))
    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def parse_existing_blocks(md: str) -> dict[str, tuple[str, str]]:
    """Map relative path -> (sha256, full block text including marker)."""

    out: dict[str, tuple[str, str]] = {}
    lines = md.splitlines(keepends=True)
    i = 0
    while i < len(lines):
        parsed = parse_marker_line(lines[i])
        if not parsed:
            i += 1
            continue
        rel, sha = parsed
        start = i
        i += 1
        while i < len(lines) and parse_marker_line(lines[i]) is None:
            i += 1
        block = "".join(lines[start:i])
        out[rel] = (sha, block)
    return out


def build_output_document(
    root: Path,
    paths: list[Path],
    existing: dict[str, tuple[str, str]],
) -> tuple[str, int, int]:
    reused = 0
    fresh = 0
    blocks: list[str] = []

    for path in paths:
        try:
            rel = path.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            rel = path.as_posix()

        sha = file_sha256(path)
        if not sha:
            continue

        prev = existing.get(rel)
        if prev and prev[0] == sha:
            blocks.append(prev[1].rstrip() + "\n\n")
            reused += 1
            continue

        segments = parse_session_file(path)
        digest = analyze_segments(segments, rel, sha)
        blocks.append(render_section_block(rel, digest))
        fresh += 1

    header = "\n".join(
        [
            "# Conversation extracts",
            "",
            "Structured highlights from OpenClaw-style session transcripts found under this workspace "
            "(JSON chat exports and line-oriented logs). Regenerate with `python3 scripts/conversation_extractor.py`.",
            "",
            f"- **Last run (UTC)**: {utc_now_iso()}",
            f"- **Transcripts scanned**: {len(paths)}",
            f"- **Sections reused (unchanged)**: {reused}",
            f"- **Sections rebuilt**: {fresh}",
            "",
            "---",
            "",
        ]
    )
    body = "".join(blocks)
    return header + body, reused, fresh


def write_output(path: Path, text: str, dry_run: bool) -> None:
    if dry_run:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Extract decisions, tools, errors, corrections, and insights from OpenClaw transcripts.",
    )
    p.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Workspace root to scan (default: repository root containing scripts/).",
    )
    p.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="Optional explicit transcript files; when set, only these are processed.",
    )
    p.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output markdown path (default: <root>/.learnings/conversation_extracts.md).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print counts only; do not write the markdown file.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    root = (args.root or repo_root()).resolve()
    out_path = (args.output or (root / ".learnings" / "conversation_extracts.md")).resolve()

    if args.paths:
        explicit = [p.resolve() for p in args.paths if p.exists()]
        paths = [p for p in explicit if looks_like_transcript(p)]
    else:
        paths = discover_transcripts(root)

    existing_md = ""
    if out_path.exists() and not args.dry_run:
        existing_md = read_text(out_path)
    elif out_path.exists():
        existing_md = read_text(out_path)

    existing_blocks = parse_existing_blocks(existing_md)
    doc, reused, fresh = build_output_document(root, paths, existing_blocks)

    if args.dry_run:
        print(f"Would write: {out_path.as_posix()}")
        print(f"Transcripts: {len(paths)}  reused_sections={reused}  rebuilt_sections={fresh}")
        return 0

    write_output(out_path, doc, dry_run=False)
    print(f"Wrote {out_path.as_posix()}")
    print(f"transcripts={len(paths)} reused={reused} rebuilt={fresh}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
