#!/usr/bin/env python3
"""Extract meaningful exchanges, Q&A, decisions, errors, and tool usage from OpenClaw session transcripts."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

try:
    from scripts.optimize_context import TURN_PATTERN, read_text, repo_root
except ImportError:  # pragma: no cover - `python scripts/conversation_extractor.py` from repo root
    from optimize_context import TURN_PATTERN, read_text, repo_root  # type: ignore[no-redef]

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

ERROR_LINE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"(?i)(traceback\s*\(most recent call last\)|\btraceback\b)",
        r"(?i)\b(exception|error|failed|failure|fatal|critical)\b\s*[:\-]\s*(.{20,500})",
        r"(?i)^(error|warning)\s*[:\-]\s*(.{10,500})",
        r"(?i)\b(exit\s*code\s*[1-9]\d*|timed?\s*out|timeout)\b",
        r"(?i)\b(connection\s+refused|econnrefused|errno\s+\d+)\b",
        r"(?i)\[\s*ERROR\s*\]",
    )
)

TRIVIAL_USER_LINE: re.Pattern[str] = re.compile(
    r"^\s*(?:"
    r"hi+|hello|hey|thanks?|thank\s+you|thx|ty|ok(?:ay)?|sure|yep|yeah|nope|nope|"
    r"yes|no|got\s+it|sounds\s+good|lgtm|👍|🙏"
    r")[\s!.,?]*$",
    re.IGNORECASE,
)

GREETING_OPEN: re.Pattern[str] = re.compile(
    r"^\s*(?:hi|hello|hey|good\s+(?:morning|afternoon|evening))\b",
    re.IGNORECASE,
)

SCHEMA_VERSION = 1
EXTRACTION_SCHEMA_VERSION = 2
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

MAX_SESSION_BYTES = 20 * 1024 * 1024
_SENTIMENT_NEG = re.compile(
    r"(?i)\b(error|errors|failed|failure|traceback|exception|blocked|broken|wrong|"
    r"cannot|can't|unable|timeout|fatal|critical|regression)\b",
)
_SENTIMENT_POS = re.compile(
    r"(?i)\b(fixed|resolved|success|works|passed|verified|great|perfect|"
    r"completed|shipped|merged|solution|helpful)\b",
)

DEFAULT_SESSIONS_SUBDIR = Path(".openclaw") / "sessions"


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")


def normalize_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


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


def _read_session_raw(path: Path) -> str:
    try:
        st = path.stat()
    except OSError:
        return ""
    if st.st_size > MAX_SESSION_BYTES:
        return ""
    return read_text(path)


def parse_json_session(path: Path) -> list[tuple[int, str | None, str]]:
    raw = _read_session_raw(path)
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
    segments: list[tuple[int, str | None, str]] = []
    current_turn = 1
    ROLE_PREFIX = re.compile(
        r"^\s*(?P<role>user|human|assistant|agent|tool|system)\s*[:|\\-]+\s*(?P<body>.+)$",
        re.IGNORECASE,
    )

    for line_number, line in enumerate(_read_session_raw(path).splitlines(), start=1):
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
        elif line.strip():
            segments.append((current_turn, None, line.strip()))

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


# --- Session path resolution -------------------------------------------------


def _env_path(name: str) -> Path | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    p = Path(raw).expanduser()
    return p if p.is_dir() else None


def iter_sessions_roots(workspace: Path | None = None) -> list[Path]:
    """Ordered search roots for ``.openclaw/sessions`` (OpenClaw + repo-local)."""

    roots: list[Path] = []
    for ep in (_env_path("OPENCLAW_SESSIONS_DIR"), _env_path("OPENCLAW_HOME")):
        if ep is not None:
            cand = ep / "sessions" if ep.name != "sessions" else ep
            if cand.is_dir():
                roots.append(cand.resolve())

    home_sessions = (Path.home() / ".openclaw" / "sessions").resolve()
    if home_sessions.is_dir():
        roots.append(home_sessions)

    ws = (workspace or repo_root()).resolve()
    local = (ws / DEFAULT_SESSIONS_SUBDIR).resolve()
    if local.is_dir():
        roots.append(local)

    return list(dict.fromkeys(roots))


def resolve_session_path(session_id: str, workspace: Path | None = None) -> Path | None:
    """Return the primary session JSON file for ``session_id``."""

    sid = session_id.strip()
    if not sid:
        return None

    candidates: list[str] = []
    if sid.endswith(".json"):
        candidates.append(sid)
        candidates.append(sid[: -len(".json")])
    else:
        candidates.append(sid)

    tried: set[Path] = set()
    for root in iter_sessions_roots(workspace):
        for base in candidates:
            for path in (
                root / base / "session.json",
                root / base / "messages.json",
                root / f"{base}.json",
            ):
                rp = path.resolve()
                if rp in tried:
                    continue
                tried.add(rp)
                if rp.is_file():
                    return rp

    for root in iter_sessions_roots(workspace):
        for path in root.glob(f"**/{sid}.json"):
            if path.is_file() and path.resolve() not in tried:
                return path.resolve()
    return None


def _session_id_from_path(session_file: Path, roots: Sequence[Path]) -> str:
    for r in roots:
        try:
            rel = session_file.resolve().relative_to(r.resolve())
            if rel.parts:
                return rel.parts[0] if len(rel.parts) > 1 else rel.stem
        except ValueError:
            continue
    return session_file.parent.name or session_file.stem


def iter_recent_session_files(
    *,
    days: float,
    workspace: Path | None = None,
) -> Iterator[tuple[Path, str]]:
    """Yield ``(path, session_id)`` for session JSON files modified within ``days``."""

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    roots = iter_sessions_roots(workspace)
    seen: set[Path] = set()

    for root in roots:
        candidates: list[Path] = []
        try:
            for child in root.iterdir():
                if child.is_dir():
                    for name in ("session.json", "messages.json"):
                        candidates.append(child / name)
                elif child.is_file() and child.suffix.lower() == ".json":
                    candidates.append(child)
        except OSError:
            continue
        for path in candidates:
            if not path.is_file():
                continue
            try:
                st = path.stat()
            except OSError:
                continue
            if st.st_size > MAX_SESSION_BYTES:
                continue
            mtime = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)
            if mtime < cutoff:
                continue
            rp = path.resolve()
            if rp in seen:
                continue
            seen.add(rp)
            sid = _session_id_from_path(path, roots)
            yield path, sid


# --- Trivial / sentiment / substance ----------------------------------------


def is_trivial_user_message(text: str) -> bool:
    compact = normalize_ws(text)
    if not compact:
        return True
    if len(compact) <= 80 and TRIVIAL_USER_LINE.match(compact):
        return True
    if len(compact) < 18 and "`" not in compact and "/" not in compact and not any(c.isdigit() for c in compact):
        if not re.search(r"[?]{2,}", compact):
            return True
    if len(compact) < 40 and GREETING_OPEN.match(compact) and "?" not in compact and "`" not in compact:
        return True
    return False


def classify_sentiment(text: str) -> str:
    has_neg = bool(_SENTIMENT_NEG.search(text))
    has_pos = bool(_SENTIMENT_POS.search(text))
    if has_neg and has_pos:
        return "mixed"
    if has_neg:
        return "negative"
    if has_pos:
        return "positive"
    return "neutral"


def substance_quality_score(question: str, answer: str) -> tuple[float, str]:
    combined = f"{question}\n{answer}"
    score = 0.25
    if len(normalize_ws(question)) >= 40:
        score += 0.15
    if len(normalize_ws(answer)) >= 120:
        score += 0.2
    if re.search(r"[`./\\]|```|\b(def|class|import|function|api|http)\b", combined, re.I):
        score += 0.2
    if "?" in question:
        score += 0.1
    if re.search(r"\b(error|exception|traceback|fix|debug)\b", combined, re.I):
        score += 0.1
    score = min(1.0, score)
    if score >= 0.72:
        tier = "high"
    elif score >= 0.45:
        tier = "medium"
    else:
        tier = "low"
    return score, tier


def exchange_is_substantive(question: str, answer: str) -> bool:
    q, a = normalize_ws(question), normalize_ws(answer)
    if len(q) < 12 and len(a) < 80:
        return False
    if is_trivial_user_message(q) and len(a) < 160:
        return False
    score, _ = substance_quality_score(q, a)
    return score >= 0.38 or ("?" in q and len(a) > 60)


# --- Structured records -----------------------------------------------------


@dataclass(frozen=True)
class QAPair:
    index: int
    turn_start: int
    turn_end: int
    question: str
    answer: str
    sentiment: str
    quality_score: float
    quality_tier: str
    substantive: bool


@dataclass(frozen=True)
class ErrorRecord:
    turn: int
    role: str | None
    excerpt: str
    severity: str


@dataclass(frozen=True)
class ToolUsageEntry:
    turn: int
    tool_name: str
    source: str


@dataclass(frozen=True)
class EnrichedExchange:
    """One user→assistant exchange with classification metadata."""

    index: int
    turn_start: int
    turn_end: int
    question: str
    answer: str
    sentiment: str
    quality_score: float
    quality_tier: str
    substantive: bool
    decisions: tuple[str, ...] = ()


def _normalize_segment_role(role: str | None) -> str:
    if not role:
        return "unknown"
    r = role.lower().strip()
    if r in {"human"}:
        return "user"
    return r


def build_tool_usage_log(segments: list[tuple[int, str | None, str]]) -> list[ToolUsageEntry]:
    rows: list[ToolUsageEntry] = []
    structured_keys: set[tuple[int, str]] = set()
    for turn, role, text in segments:
        rl = _normalize_segment_role(role)
        if rl == "tool":
            name = normalize_ws(text.replace("[tool:", "").replace("]", "")).split("(", 1)[0].strip()
            if name:
                rows.append(ToolUsageEntry(turn=turn, tool_name=name, source="structured"))
                structured_keys.add((turn, name))

    for turn, role, text in segments:
        rl = _normalize_segment_role(role)
        if rl == "tool":
            continue
        for name, _ in extract_tool_signals(text).items():
            key = (turn, name)
            if key in structured_keys:
                continue
            rows.append(ToolUsageEntry(turn=turn, tool_name=name, source="text_heuristic"))

    best: dict[tuple[int, str], ToolUsageEntry] = {}
    for row in rows:
        key = (row.turn, row.tool_name)
        cur = best.get(key)
        if cur is None or (cur.source != "structured" and row.source == "structured"):
            best[key] = row
    return sorted(best.values(), key=lambda r: (r.turn, r.tool_name))


def extract_error_records(segments: list[tuple[int, str | None, str]]) -> list[ErrorRecord]:
    out: list[ErrorRecord] = []
    for turn, role, text in segments:
        rl = _normalize_segment_role(role)
        if rl in {"tool"}:
            continue
        for line in text.splitlines():
            line_st = line.strip()
            if len(line_st) < 8:
                continue
            for pat in ERROR_LINE_PATTERNS:
                if pat.search(line_st):
                    excerpt = line_st if len(line_st) <= 400 else line_st[:397] + "..."
                    sev = "error" if re.search(r"(traceback|exception|fatal|econnrefused)", line_st, re.I) else "warning"
                    out.append(ErrorRecord(turn=turn, role=role, excerpt=excerpt, severity=sev))
                    break
    return out


def build_enriched_exchanges(segments: list[tuple[int, str | None, str]]) -> list[EnrichedExchange]:
    buffer_q: list[tuple[int, str]] = []
    buffer_a: list[tuple[int, str]] = []
    pending_prefix: list[tuple[int, str]] = []

    def flush(out: list[EnrichedExchange], idx_holder: list[int]) -> None:
        if not buffer_a:
            buffer_q.clear()
            return
        turns = [t for t, _ in buffer_q] + [t for t, _ in buffer_a]
        if not turns:
            buffer_q.clear()
            buffer_a.clear()
            return
        q_text = "\n\n".join(x for _, x in buffer_q).strip()
        a_text = "\n\n".join(x for _, x in buffer_a).strip()
        buffer_q.clear()
        buffer_a.clear()
        if not a_text:
            return
        if is_trivial_user_message(q_text) and len(normalize_ws(a_text)) < 100:
            return
        if not exchange_is_substantive(q_text or "(context)", a_text):
            return
        sentiment = classify_sentiment(f"{q_text}\n{a_text}")
        score, tier = substance_quality_score(q_text or "(context)", a_text)
        sub = exchange_is_substantive(q_text or "(context)", a_text)
        decs = tuple(match_patterns(f"{q_text}\n{a_text}", DECISION_LINE_PATTERNS)[:5])
        idx_holder[0] += 1
        out.append(
            EnrichedExchange(
                index=idx_holder[0],
                turn_start=min(turns),
                turn_end=max(turns),
                question=q_text or "(prior context / implicit)",
                answer=a_text,
                sentiment=sentiment,
                quality_score=round(score, 3),
                quality_tier=tier,
                substantive=sub,
                decisions=decs,
            ),
        )

    exchanges: list[EnrichedExchange] = []
    idx = [0]

    for turn, role, text in segments:
        rl = _normalize_segment_role(role)
        if rl == "user":
            if buffer_a and buffer_q:
                flush(exchanges, idx)
            elif buffer_a and not buffer_q:
                pending_prefix.extend(buffer_a)
                buffer_a.clear()
            if is_trivial_user_message(text):
                continue
            buffer_q.append((turn, text))
        elif rl in {"assistant", "agent", "unknown"} or role is None:
            if not text.strip():
                continue
            if buffer_q and pending_prefix and not buffer_a:
                buffer_a.extend(pending_prefix)
                pending_prefix.clear()
            buffer_a.append((turn, text))
        elif rl == "tool_output":
            if text.strip():
                chunk = (turn, f"[tool output]\n{text.strip()}")
                if buffer_q and pending_prefix and not buffer_a:
                    buffer_a.extend(pending_prefix)
                    pending_prefix.clear()
                buffer_a.append(chunk)
        elif rl == "system":
            continue

    if buffer_q or buffer_a:
        flush(exchanges, idx)

    return exchanges


def exchanges_to_qa_pairs(exchanges: Sequence[EnrichedExchange]) -> list[QAPair]:
    return [
        QAPair(
            index=e.index,
            turn_start=e.turn_start,
            turn_end=e.turn_end,
            question=e.question,
            answer=e.answer,
            sentiment=e.sentiment,
            quality_score=e.quality_score,
            quality_tier=e.quality_tier,
            substantive=e.substantive,
        )
        for e in exchanges
    ]


@dataclass
class ConversationDigest:
    """Structured output for markdown + JSON (memory pipelines and CLI)."""

    source: str
    generated_at_utc: str
    segments: list[tuple[int, str | None, str]]
    decisions: list[str]
    learnings: list[str]
    patterns: list[str]
    tool_structured: Counter[str] = field(default_factory=Counter)
    tool_textual: Counter[str] = field(default_factory=Counter)
    exchanges: list[EnrichedExchange] = field(default_factory=list)
    qa_pairs: list[QAPair] = field(default_factory=list)
    errors: list[ErrorRecord] = field(default_factory=list)
    tool_usage_log: list[ToolUsageEntry] = field(default_factory=list)
    key_learnings: list[str] = field(default_factory=list)

    def all_tools(self) -> Counter[str]:
        merged: Counter[str] = Counter(self.tool_structured)
        merged.update(self.tool_textual)
        return merged


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

    exchanges = build_enriched_exchanges(segments)
    qa = exchanges_to_qa_pairs(exchanges)
    errs = extract_error_records(segments)
    tool_log = build_tool_usage_log(segments)

    key_learnings = _dedupe_preserve_order(
        learnings_acc + patterns_acc[:40],
        max_items=60,
    )

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
        exchanges=exchanges,
        qa_pairs=qa,
        errors=errs,
        tool_usage_log=tool_log,
        key_learnings=key_learnings,
    )


def render_markdown(d: ConversationDigest) -> str:
    lines = [
        "# Conversation knowledge extract",
        "",
        f"- **Source**: `{d.source}`",
        f"- **Generated (UTC)**: {d.generated_at_utc}",
        f"- **Segments indexed**: {len(d.segments)}",
        "",
        "## Key learnings",
    ]
    if d.key_learnings:
        for item in d.key_learnings[:25]:
            lines.append(f"- {item}")
    else:
        lines.append("- *(no consolidated learnings)*")

    lines.extend(["", "## Q&A pairs (substantive)"])
    if d.qa_pairs:
        for qa in d.qa_pairs:
            lines.append(f"### Exchange {qa.index} (turns {qa.turn_start}–{qa.turn_end})")
            lines.append(f"- **Sentiment**: `{qa.sentiment}`  ·  **Quality**: {qa.quality_tier} ({qa.quality_score:.2f})")
            lines.append("")
            lines.append("**Q:**")
            lines.append("")
            lines.append(qa.question[:8000])
            lines.append("")
            lines.append("**A:**")
            lines.append("")
            lines.append(qa.answer[:12000])
            lines.append("")
    else:
        lines.append("- *(no substantive Q&A pairs detected)*")

    lines.extend(["", "## Key decisions"])
    if d.decisions:
        for item in d.decisions:
            lines.append(f"- {item}")
    else:
        lines.append("- *(no explicit decision lines detected)*")

    lines.extend(["", "## Decisions log (from exchanges)"])
    row_n = 0
    for ex in d.exchanges:
        for dec in ex.decisions:
            row_n += 1
            lines.append(f"{row_n}. [turn {ex.turn_start}] {dec}")
    if row_n == 0:
        lines.append("- *(none)*")

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

    lines.extend(["", "## Error log"])
    if d.errors:
        for er in d.errors:
            lines.append(f"- **[{er.severity}]** turn {er.turn} ({er.role or 'unknown'}): `{er.excerpt}`")
    else:
        lines.append("- *(no error signatures detected)*")

    lines.extend(["", "## Tool usage log"])
    if d.tool_usage_log:
        for tu in d.tool_usage_log[:200]:
            lines.append(f"- turn {tu.turn}: `{tu.tool_name}` ({tu.source})")
        if len(d.tool_usage_log) > 200:
            lines.append(f"- … and {len(d.tool_usage_log) - 200} more")
    else:
        lines.append("- *(no tool usage rows)*")

    lines.extend(["", "## Tool mentions (ranked)"])
    merged = d.all_tools()
    if merged:
        for name, ct in merged.most_common(40):
            lines.append(f"- `{name}` — {ct} mentions")
    else:
        lines.append("- *(no tool mentions parsed)*")

    lines.extend(["", "---", "*OpenClaw conversation_extractor — session distillation.*"])
    return "\n".join(lines) + "\n"


def _dataclass_list(items: Sequence[Any]) -> list[dict[str, Any]]:
    return [asdict(x) if hasattr(x, "__dataclass_fields__") else dict(x) for x in items]


def digest_to_dict(d: ConversationDigest) -> dict[str, Any]:
    ranked = d.all_tools().most_common()
    return {
        "artifact_type": ARTIFACT_TYPE,
        "schema_version": SCHEMA_VERSION,
        "extraction_schema_version": EXTRACTION_SCHEMA_VERSION,
        "source": d.source,
        "generated_at_utc": d.generated_at_utc,
        "counts": {
            "segments": len(d.segments),
            "key_decisions": len(d.decisions),
            "lessons_learned": len(d.learnings),
            "reusable_patterns": len(d.patterns),
            "tool_names_distinct": len(d.all_tools()),
            "qa_pairs": len(d.qa_pairs),
            "errors": len(d.errors),
            "tool_usage_rows": len(d.tool_usage_log),
            "substantive_exchanges": sum(1 for e in d.exchanges if e.substantive),
        },
        "key_decisions": d.decisions,
        "lessons_learned": d.learnings,
        "reusable_patterns": d.patterns,
        "qa_pairs": _dataclass_list(d.qa_pairs),
        "exchanges": [asdict(e) for e in d.exchanges],
        "errors": _dataclass_list(d.errors),
        "tool_usage_log": _dataclass_list(d.tool_usage_log),
        "key_learnings": d.key_learnings,
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
    if d.qa_pairs:
        tags.append("qa")
    if d.errors:
        tags.append("errors")
    return tags


def _summary_one_liners(d: ConversationDigest) -> list[str]:
    out: list[str] = []
    if d.qa_pairs:
        out.append(f"Substantive Q&A exchanges: {len(d.qa_pairs)}")
    if d.decisions:
        out.append(f"Decisions captured: {len(d.decisions)}")
    if d.learnings:
        out.append(f"Learnings captured: {len(d.learnings)}")
    if d.patterns:
        out.append(f"Reusable patterns: {len(d.patterns)}")
    if d.errors:
        out.append(f"Error-like lines: {len(d.errors)}")
    top = d.all_tools().most_common(3)
    if top:
        names = ", ".join(f"{n} ({c})" for n, c in top)
        out.append(f"Top tools: {names}")
    return out


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
    parent = session_path.parent.name
    if parent and parent not in {".", ""}:
        stem = f"{parent}__{session_path.stem}"

    relative = session_path.resolve().as_posix()
    workspace_posix = workspace_root.resolve().as_posix()
    if relative.startswith(workspace_posix):
        short = Path(relative[len(workspace_posix) :].lstrip("/")).as_posix()
        digest.source = short

    return write_digest(digest, memory_dir, stem)


def write_summary_outputs(
    digest: ConversationDigest,
    *,
    output_md: Path,
    write_json_sidecar: bool = True,
) -> tuple[Path, Path | None]:
    output_md = output_md.expanduser().resolve()
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_md.write_text(render_markdown(digest), encoding="utf-8")
    json_path: Path | None = None
    if write_json_sidecar:
        json_path = output_md.with_suffix(".json")
        json_path.write_text(json.dumps(digest_to_dict(digest), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return output_md, json_path


def cmd_extract(args: argparse.Namespace, workspace: Path) -> int:
    path = resolve_session_path(args.session_id, workspace)
    if path is None:
        sys.stderr.write(
            f"error: no session file found for id {args.session_id!r} under .openclaw/sessions "
            "(try OPENCLAW_SESSIONS_DIR).\n",
        )
        return 2

    segments = parse_session_log(path)
    if not segments:
        sys.stderr.write(f"error: no parseable segments in {path}\n")
        return 2

    rel = path.as_posix()
    try:
        rel = path.resolve().relative_to(workspace.resolve()).as_posix()
    except ValueError:
        pass

    digest = analyze_segments(segments, rel)
    safe = re.sub(r"[^\w\-_.]+", "_", args.session_id).strip("_") or "session"
    out = Path(args.output) if args.output else (workspace / "summaries" / f"{safe}_summary.md")
    md_path, js_path = write_summary_outputs(digest, output_md=out, write_json_sidecar=not args.no_json)
    print(md_path.as_posix())
    if js_path:
        print(js_path.as_posix())
    return 0


def cmd_batch(args: argparse.Namespace, workspace: Path) -> int:
    raw = args.output_dir
    out_dir = Path(raw).expanduser()
    if not out_dir.is_absolute():
        out_dir = (workspace / out_dir).resolve()
    else:
        out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    for path, sid in iter_recent_session_files(days=args.days, workspace=workspace):
        segments = parse_session_log(path)
        if not segments:
            continue
        digest = analyze_segments(segments, path.as_posix())
        safe = re.sub(r"[^\w\-_.]+", "_", sid).strip("_") or path.stem
        md_path, _ = write_summary_outputs(
            digest,
            output_md=out_dir / f"{safe}_summary.md",
            write_json_sidecar=not args.no_json,
        )
        print(md_path.as_posix())
        count += 1
    if count == 0:
        sys.stderr.write(f"warning: no sessions found in the last {args.days} days.\n")
    return 0


def _build_legacy_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Parse session logs (JSON or text), extract knowledge, write markdown + JSON into memory/.",
    )
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
    return p


def _main_legacy(argv: list[str]) -> int:
    args = _build_legacy_arg_parser().parse_args(argv)
    ws = (args.workspace_root or repo_root()).resolve()
    memory_dir = (args.memory_dir or ws / "memory").resolve()

    if args.stdin:
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
        sys.stderr.write("error: session_log path required unless --stdin is set.\n")
        return 2

    sp = args.session_log.resolve()
    if not sp.exists():
        sys.stderr.write(f"error: file not found: {sp}\n")
        return 2

    md_path, json_path = run_extraction(sp, memory_dir, ws)
    print(f"Wrote {md_path.as_posix()}")
    print(f"Wrote {json_path.as_posix()}")
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    root = repo_root()
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument(
        "--workspace-root",
        type=Path,
        default=None,
        help="Repository / workspace root (default: auto-detected from this file).",
    )

    p = argparse.ArgumentParser(
        description="Extract Q&A, decisions, errors, and tool usage from OpenClaw session JSON.",
        parents=[parent],
    )
    sub = p.add_subparsers(dest="command", required=False)

    ex = sub.add_parser(
        "extract",
        parents=[parent],
        help="Extract one session by id from .openclaw/sessions/.",
    )
    ex.add_argument("session_id", help="Session folder name or stem (e.g. abc123 or abc123.json).")
    ex.add_argument(
        "--output",
        "-o",
        type=str,
        default=None,
        help="Markdown output path (default: <workspace>/summaries/<session_id>_summary.md).",
    )
    ex.add_argument(
        "--no-json",
        action="store_true",
        help="Do not write a sidecar .json next to the markdown file.",
    )
    ex.set_defaults(run=lambda a: cmd_extract(a, (a.workspace_root or root).resolve()))

    ba = sub.add_parser(
        "batch",
        parents=[parent],
        help="Batch-extract recently modified sessions.",
    )
    ba.add_argument(
        "--days",
        type=float,
        default=7.0,
        help="Only include session files modified within this many days (default: 7).",
    )
    ba.add_argument(
        "--output-dir",
        "-o",
        type=str,
        default="summaries/",
        help="Directory for summary markdown (and JSON) files (relative paths resolve under workspace).",
    )
    ba.add_argument(
        "--no-json",
        action="store_true",
        help="Do not write sidecar .json files.",
    )
    ba.set_defaults(run=lambda a: cmd_batch(a, (a.workspace_root or root).resolve()))

    return p


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        sys.stderr.write("error: specify a subcommand (extract, batch) or a session file path.\n")
        return 2

    first = argv[0]
    if first not in ("extract", "batch", "-h", "--help") and not first.startswith("-"):
        candidate = Path(first).expanduser()
        if candidate.is_file():
            return _main_legacy(argv)

    parser = build_arg_parser()
    args = parser.parse_args(argv)
    ws = (args.workspace_root or repo_root()).resolve()
    args.workspace_root = ws

    if getattr(args, "run", None):
        return int(args.run(args))

    sys.stderr.write("error: specify extract, batch, or a direct session file path.\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
