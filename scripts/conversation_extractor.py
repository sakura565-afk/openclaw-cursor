#!/usr/bin/env python3
"""Extract meaningful exchanges from OpenClaw / Cursor-style session transcripts.

Reads JSON or line-oriented logs (including under ``~/.openclaw/logs`` or the repo
``.openclaw/logs``), builds structured Q&A / decision / correction records with
metadata, deduplicates near-duplicates, and writes ``data/extracted_conversations.json``
plus optional Markdown export.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable

from scripts.optimize_context import TURN_PATTERN, read_text, repo_root

# -----------------------------------------------------------------------------
# Patterns: decisions / learnings / corrections (English + light multilingual)
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

CORRECTION_HINT_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\b(?:actually|correction|corrected|I\s+meant|sorry,)\b",
        r"\b(?:not\s+\S+\s+but\s+rather|instead\s+of\s+\S+,)\b",
        r"\b(?:that\s+was\s+wrong|my\s+mistake|misunderstood)\b",
        r"(?:不对|更正|纠正一下|我说错了|应该是)",
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

STOPWORDS = frozenset(
    """
    a an the and or but if then else for to of in on at by from with as is are was were
    be been being it its this that these those we you i me my our your they them their
    not no yes so do does did will would could should can may might just also into out up
    about over under than then there here when what which who how why all any each some
    such very more most other another one two first last new like get got use used using
    """.split()
)

DEFAULT_LOG_SUFFIXES = (".json", ".log", ".txt", ".md")
MAX_TEXT_FIELD = 50_000
SCHEMA_VERSION = 1


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")


def normalize_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _truncate(s: str, limit: int = MAX_TEXT_FIELD) -> str:
    if len(s) <= limit:
        return s
    return s[: max(0, limit - 3)] + "..."


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


def _parse_json_data(data: Any) -> list[tuple[int, str | None, str]]:
    structured = _unpack_session_json(data)
    if structured:
        return structured
    return _fallback_json_segments(data)


def parse_json_session(path: Path) -> list[tuple[int, str | None, str]]:
    raw = read_text(path)
    if not raw.strip():
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return _parse_json_data(data)


def parse_text_session(path: Path) -> list[tuple[int, str | None, str]]:
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


def load_json_raw(path: Path) -> Any | None:
    raw = read_text(path)
    if not raw.strip():
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def envelope_session_metadata(path: Path, data: Any | None) -> dict[str, Any]:
    """Best-effort session-level metadata from JSON envelope or filesystem."""

    st = path.stat()
    mtime = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat()
    meta: dict[str, Any] = {
        "source_path": path.resolve().as_posix(),
        "file_mtime_utc": mtime,
        "session_id": path.stem,
    }

    if isinstance(data, dict):
        for key in (
            "session_id",
            "id",
            "conversation_id",
            "chat_id",
            "slug",
            "sessionId",
            "conversationId",
        ):
            val = data.get(key)
            if isinstance(val, str) and val.strip():
                meta["session_id"] = val.strip()
                break
        for ts_key in ("started_at", "created_at", "updated_at", "timestamp", "time", "date"):
            val = data.get(ts_key)
            if isinstance(val, str) and val.strip():
                meta["session_timestamp"] = val.strip()
                break
            if isinstance(val, (int, float)):
                try:
                    meta["session_timestamp"] = datetime.fromtimestamp(
                        float(val), tz=timezone.utc
                    ).isoformat()
                except (OSError, OverflowError, ValueError):
                    pass
                break
        title = data.get("title") or data.get("name") or data.get("summary")
        if isinstance(title, str) and title.strip():
            meta["title"] = title.strip()[:500]

    return meta


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


def infer_topics(*texts: str, max_topics: int = 12) -> list[str]:
    bag: Counter[str] = Counter()
    token_re = re.compile(r"[A-Za-z_][\w\-]{2,}|[\u4e00-\u9fff]{2,}")
    for blob in texts:
        if not blob:
            continue
        for m in token_re.finditer(blob):
            tok = m.group(0).lower()
            if tok in STOPWORDS or tok.isdigit():
                continue
            if len(tok) < 3:
                continue
            bag[tok] += 1
    return [w for w, _ in bag.most_common(max_topics)]


def _role_bucket(role: str | None) -> str:
    rl = (role or "").lower()
    if rl in {"human"}:
        return "user"
    if rl in {"agent"}:
        return "assistant"
    if rl in {"user", "assistant", "system", "tool", "tool_output"}:
        return rl
    return "unknown"


def segments_to_messages_ordered(
    segments: list[tuple[int, str | None, str]],
) -> list[tuple[int, str, str]]:
    """Flatten to ordered (turn, effective_role, text) with unknown role preserved."""

    out: list[tuple[int, str, str]] = []
    for turn, role, text in segments:
        if not text.strip():
            continue
        bucket = _role_bucket(role)
        if bucket == "tool":
            out.append((turn, "tool", text.strip()))
        elif bucket == "tool_output":
            out.append((turn, "tool_output", text.strip()))
        else:
            out.append((turn, bucket, text.strip()))
    return out


def build_exchanges_from_messages(
    messages: list[tuple[int, str, str]],
    session_meta: dict[str, Any],
) -> list[dict[str, Any]]:
    """Pair user prompts with following assistant replies; attach decisions/corrections."""

    exchanges: list[dict[str, Any]] = []
    pending_user: list[str] = []
    pending_turns: list[int] = []
    tool_buffer: list[str] = []

    structured_tool_names: list[str] = []
    for _turn, role, txt in messages:
        if role == "tool":
            structured_tool_names.append(txt.split("(", 1)[0].strip())
    structured_tool_names = list(dict.fromkeys(structured_tool_names))[:40]

    def flush_pair(answer: str, answer_turn: int) -> None:
        nonlocal pending_user, pending_turns, tool_buffer
        if not pending_user and not answer.strip():
            tool_buffer.clear()
            return
        q = "\n\n".join(pending_user).strip()
        a = answer.strip()
        if not q and not a:
            tool_buffer.clear()
            pending_user = []
            pending_turns = []
            return

        participants = ["user", "assistant"] if q and a else []
        if q and not a:
            participants = ["user"]
        if a and not q:
            participants = ["assistant"]

        turns_for_range = list(pending_turns)
        if answer.strip():
            turns_for_range.append(answer_turn)
        turn_lo = min(turns_for_range) if turns_for_range else None
        turn_hi = max(turns_for_range) if turns_for_range else None

        blob = "\n\n".join([*pending_user, answer]).strip()
        tools = list(extract_tool_signals(blob).keys())[:40]

        decisions = match_patterns(blob, DECISION_LINE_PATTERNS)
        learnings = match_patterns(blob, LEARNING_LINE_PATTERNS)
        correction = any(p.search(q) for p in CORRECTION_HINT_PATTERNS) if q else False

        ex_type = "qa_pair"
        if correction:
            ex_type = "correction"
        elif decisions and not q:
            ex_type = "decision"
        elif learnings and not q:
            ex_type = "learning"

        topics = infer_topics(q, a, *(decisions[:3] + learnings[:3]))

        rec: dict[str, Any] = {
            "exchange_type": ex_type,
            "session_id": session_meta.get("session_id", ""),
            "source_path": session_meta.get("source_path", ""),
            "timestamp": session_meta.get("session_timestamp") or session_meta.get("file_mtime_utc"),
            "turn_range": [turn_lo, turn_hi] if turn_lo is not None and turn_hi is not None else None,
            "participants": participants,
            "topics": topics,
            "question": _truncate(q) if q else None,
            "answer": _truncate(a) if a else None,
            "decisions_detected": decisions[:20],
            "learnings_detected": learnings[:20],
            "tools_mentioned": list(dict.fromkeys([*structured_tool_names, *tools]))[:48],
            "tool_trace": _truncate("\n".join(tool_buffer), 8000) if tool_buffer else None,
            "context_summary": _truncate(normalize_ws(blob[:1200]), 2000),
        }
        exchanges.append(rec)
        pending_user = []
        pending_turns = []
        tool_buffer.clear()

    last_turn = 0
    for turn, role, text in messages:
        last_turn = max(last_turn, turn)
        if role == "tool":
            tool_buffer.append(f"[tool] {text}")
            continue
        if role == "tool_output":
            tool_buffer.append(f"[tool_output] {_truncate(text, 4000)}")
            continue
        if role == "user":
            if pending_user:
                pending_user.append(text)
                pending_turns.append(turn)
            else:
                pending_user = [text]
                pending_turns = [turn]
            continue
        if role == "system":
            continue
        if role == "assistant":
            flush_pair(text, turn)
            continue
        # Narrative blocks without a clear role (common in plain logs)
        if role == "unknown":
            flush_pair(text, turn)
            continue

    if pending_user:
        flush_pair("", last_turn or 0)

    return exchanges


def stable_exchange_hash(rec: dict[str, Any]) -> str:
    key = "|".join(
        [
            str(rec.get("session_id", "")),
            str(rec.get("exchange_type", "")),
            normalize_ws(str(rec.get("question") or ""))[:600],
            normalize_ws(str(rec.get("answer") or ""))[:600],
        ]
    )
    return hashlib.sha256(key.encode("utf-8", errors="replace")).hexdigest()[:20]


def similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def dedupe_exchanges(exchanges: list[dict[str, Any]], threshold: float) -> list[dict[str, Any]]:
    if threshold <= 0:
        return exchanges

    def fingerprint(rec: dict[str, Any]) -> str:
        parts = [
            normalize_ws(str(rec.get("question") or "")),
            normalize_ws(str(rec.get("answer") or "")),
            " ".join(rec.get("decisions_detected") or []),
            " ".join(rec.get("learnings_detected") or []),
        ]
        return normalize_ws("\n".join(parts))[:8000]

    kept: list[dict[str, Any]] = []
    fps: list[str] = []
    for rec in exchanges:
        fp = fingerprint(rec)
        dup = False
        for i, prev in enumerate(fps):
            if similarity(fp, prev) >= threshold:
                dup = True
                prev_rec = kept[i]
                if len(fp) > len(prev):
                    kept[i] = rec
                    fps[i] = fp
                elif len(fp) == len(prev):
                    pt = set(prev_rec.get("topics") or [])
                    pt.update(rec.get("topics") or [])
                    prev_rec["topics"] = sorted(pt)[:20]
                break
        if not dup:
            kept.append(rec)
            fps.append(fp)
    return kept


def filter_exchanges(
    exchanges: list[dict[str, Any]],
    topic_substr: str | None,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    topic_lower = topic_substr.lower() if topic_substr else None

    for rec in exchanges:
        if topic_lower:
            hay = " ".join(rec.get("topics") or []).lower()
            hay += " " + normalize_ws(
                (rec.get("question") or "") + " " + (rec.get("answer") or "")
            ).lower()
            if topic_lower not in hay:
                continue

        out.append(rec)
    return out


def _parse_ts(raw: Any) -> datetime | None:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    if not isinstance(raw, str) or not raw.strip():
        return None
    s = raw.strip()
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(s[:10], fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def parse_cli_datetime(s: str) -> datetime:
    dt = _parse_ts(s)
    if dt is None:
        raise argparse.ArgumentTypeError(f"not a valid ISO date/datetime: {s!r}")
    return dt


def default_log_roots(workspace: Path) -> list[Path]:
    roots: list[Path] = []
    env_ws = os.environ.get("OPENCLAW_WORKSPACE", "").strip()
    if env_ws:
        roots.append(Path(env_ws).expanduser() / "logs")
        roots.append(Path(env_ws).expanduser() / ".openclaw" / "logs")
    home = Path.home()
    roots.append(home / ".openclaw" / "logs")
    roots.append(workspace / ".openclaw" / "logs")
    roots.append(workspace / ".openclaw")
    return roots


def discover_session_files(
    roots: Iterable[Path],
    suffixes: tuple[str, ...] = DEFAULT_LOG_SUFFIXES,
    since: datetime | None = None,
    until: datetime | None = None,
) -> list[Path]:
    files: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        root = root.expanduser().resolve()
        if not root.exists():
            continue
        if root.is_file():
            candidates = [root]
        else:
            candidates = []
            for suf in suffixes:
                candidates.extend(root.rglob(f"*{suf}"))
        for p in candidates:
            if not p.is_file():
                continue
            key = p.resolve().as_posix()
            if key in seen:
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            mtime = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)
            if since and mtime < since:
                continue
            if until and mtime > until:
                continue
            seen.add(key)
            files.append(p)
    files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
    return files


def load_session(path: Path, workspace: Path) -> tuple[list[tuple[int, str | None, str]], dict[str, Any]]:
    data: Any | None = None
    if path.suffix.lower() == ".json":
        data = load_json_raw(path)
        meta = envelope_session_metadata(path, data)
        segments = _parse_json_data(data) if data is not None else []
        if not segments:
            segments = parse_text_session(path)
    else:
        meta = envelope_session_metadata(path, None)
        segments = parse_session_log(path)

    rel = path.resolve().as_posix()
    wposix = workspace.resolve().as_posix()
    if rel.startswith(wposix):
        meta["source_path"] = Path(rel[len(wposix) :].lstrip("/")).as_posix()

    return segments, meta


def build_document(
    paths: list[Path],
    workspace: Path,
    topic: str | None,
    since: datetime | None,
    until: datetime | None,
    dedupe_threshold: float,
) -> dict[str, Any]:
    all_exchanges: list[dict[str, Any]] = []
    session_index: dict[str, dict[str, Any]] = {}
    files_used = 0

    for path in paths:
        try:
            st = path.stat()
        except OSError:
            continue
        mtime = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)
        if since and mtime < since:
            continue
        if until and mtime > until:
            continue

        files_used += 1
        segments, meta = load_session(path, workspace)
        messages = segments_to_messages_ordered(segments)
        sid = str(meta.get("session_id") or path.stem)
        if sid not in session_index:
            session_index[sid] = {
                "session_id": sid,
                "source_path": meta.get("source_path", path.as_posix()),
                "title": meta.get("title"),
                "session_timestamp": meta.get("session_timestamp"),
                "file_mtime_utc": meta.get("file_mtime_utc"),
                "exchange_count": 0,
            }

        ex_list = build_exchanges_from_messages(messages, meta)
        all_exchanges.extend(ex_list)
        session_index[sid]["exchange_count"] += len(ex_list)

    all_exchanges = dedupe_exchanges(all_exchanges, dedupe_threshold)
    all_exchanges = filter_exchanges(all_exchanges, topic)

    for rec in all_exchanges:
        rec["exchange_id"] = stable_exchange_hash(rec)

    doc = {
        "artifact": "extracted_conversations",
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": utc_now_iso(),
        "sources_scanned": files_used,
        "exchange_count": len(all_exchanges),
        "sessions_index": list(session_index.values()),
        "exchanges": all_exchanges,
    }
    return doc


def reindex_sessions(doc: dict[str, Any]) -> None:
    idx: dict[str, dict[str, Any]] = {}
    for ex in doc.get("exchanges") or []:
        if not isinstance(ex, dict):
            continue
        sid = str(ex.get("session_id") or "unknown")
        if sid not in idx:
            idx[sid] = {
                "session_id": sid,
                "source_path": ex.get("source_path", ""),
                "title": None,
                "session_timestamp": None,
                "file_mtime_utc": None,
                "exchange_count": 0,
            }
        idx[sid]["exchange_count"] += 1
    doc["sessions_index"] = list(idx.values())


def render_aggregate_markdown(doc: dict[str, Any]) -> str:
    lines = [
        "# Extracted conversations",
        "",
        f"- **Generated (UTC)**: {doc.get('generated_at_utc', '')}",
        f"- **Sessions scanned**: {doc.get('sources_scanned', 0)}",
        f"- **Exchanges**: {doc.get('exchange_count', 0)}",
        "",
        "## Sessions",
    ]
    for s in doc.get("sessions_index") or []:
        sid = s.get("session_id", "")
        lines.append(f"- **{sid}** — {s.get('exchange_count', 0)} exchanges — `{s.get('source_path', '')}`")
    lines.extend(["", "## Exchanges"])
    for i, ex in enumerate(doc.get("exchanges") or [], start=1):
        lines.append(f"### {i}. [{ex.get('exchange_type', '?')}] {ex.get('exchange_id', '')}")
        lines.append("")
        lines.append(f"- **Session**: `{ex.get('session_id', '')}`")
        lines.append(f"- **When**: {ex.get('timestamp', '')}")
        if ex.get("topics"):
            lines.append(f"- **Topics**: {', '.join(ex['topics'])}")
        if ex.get("participants"):
            lines.append(f"- **Participants**: {', '.join(ex['participants'])}")
        if ex.get("question"):
            lines.extend(["", "**Question / prompt**", "", "```", ex["question"], "```"])
        if ex.get("answer"):
            lines.extend(["", "**Answer / response**", "", "```", ex["answer"], "```"])
        if ex.get("tool_trace"):
            lines.extend(["", "<details><summary>Tool trace</summary>", "", "```", ex["tool_trace"], "```", "</details>"])
        dd = ex.get("decisions_detected") or []
        if dd:
            lines.extend(["", "**Decisions detected**"])
            for d in dd[:12]:
                lines.append(f"- {d}")
        ll = ex.get("learnings_detected") or []
        if ll:
            lines.extend(["", "**Learnings detected**"])
            for d in ll[:12]:
                lines.append(f"- {d}")
        if ex.get("tools_mentioned"):
            lines.append("")
            lines.append("**Tools**: " + ", ".join(f"`{t}`" for t in ex["tools_mentioned"][:24]))
        lines.append("")
    lines.append("---")
    lines.append("*Produced by `scripts/conversation_extractor.py` — suitable for review or training data curation.*")
    lines.append("")
    return "\n".join(lines)


@dataclass
class ConversationDigest:
    """Per-session digest for optional legacy memory-style export."""

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
            decisions_acc.extend(match_patterns(text, DECISION_LINE_PATTERNS))

        blobs_for_text_tools.append(text)

    combined = "\n\n".join(blobs_for_text_tools)
    textual_tools = extract_tool_signals(combined)

    def uniq(xs: list[str]) -> list[str]:
        return list(dict.fromkeys([x for x in xs if x]))

    return ConversationDigest(
        source=source_display,
        generated_at_utc=utc_now_iso(),
        segments=segments,
        decisions=uniq(decisions_acc)[:120],
        learnings=uniq(learnings_acc)[:120],
        tool_structured=structured_tools,
        tool_textual=textual_tools,
    )


def render_digest_markdown(d: ConversationDigest) -> str:
    lines = [
        "# Conversation extract (digest)",
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
    else:
        lines.append("- *(no tool mentions parsed)*")
    lines.extend(["", "---", "*Legacy digest from `conversation_extractor.py`.*"])
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


def write_legacy_digest(
    digest: ConversationDigest,
    memory_dir: Path,
    stem: str,
) -> tuple[Path, Path]:
    memory_dir.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^\w\-_.]+", "_", stem).strip("_") or "session"
    tag = utc_stamp()
    md_path = memory_dir / f"conversation_extract_{safe}_{tag}.md"
    js_path = memory_dir / f"conversation_extract_{safe}_{tag}.json"
    md_path.write_text(render_digest_markdown(digest), encoding="utf-8")
    js_path.write_text(json.dumps(digest_to_dict(digest), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return md_path, js_path


def merge_with_existing_json(path: Path, new_doc: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return new_doc
    try:
        prev = json.loads(read_text(path))
    except json.JSONDecodeError:
        return new_doc
    if not isinstance(prev, dict) or "exchanges" not in prev:
        return new_doc
    old_ex = prev.get("exchanges")
    if not isinstance(old_ex, list):
        return new_doc
    merged_ex = list(old_ex) + list(new_doc.get("exchanges") or [])
    new_doc["exchanges"] = merged_ex
    new_doc["exchange_count"] = len(merged_ex)
    new_doc["generated_at_utc"] = utc_now_iso()
    reindex_sessions(new_doc)
    return new_doc


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Extract structured Q&A / decisions / corrections from OpenClaw session logs.",
    )
    p.add_argument(
        "sessions",
        nargs="*",
        type=Path,
        help="Explicit transcript paths (.json, .log, .txt). Used together with --bulk unless omitted.",
    )
    p.add_argument(
        "--bulk",
        action="store_true",
        help="Scan default and custom log directories for transcript files.",
    )
    p.add_argument(
        "--logs-dir",
        dest="logs_dirs",
        action="append",
        default=None,
        help="Extra directory to scan (repeatable). Used with --bulk or alone implies bulk.",
    )
    p.add_argument(
        "--since",
        type=parse_cli_datetime,
        default=None,
        help="Only include sessions whose transcript file mtime is on/after this UTC instant (ISO).",
    )
    p.add_argument(
        "--until",
        type=parse_cli_datetime,
        default=None,
        help="Only include sessions whose transcript file mtime is on/before this UTC instant (ISO).",
    )
    p.add_argument(
        "--topic",
        type=str,
        default=None,
        help="Case-insensitive substring filter on inferred topics and exchange text.",
    )
    p.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help=f"JSON output path (default: <repo>/data/extracted_conversations.json).",
    )
    p.add_argument(
        "--markdown",
        type=Path,
        default=None,
        help="Also write a human-readable Markdown export to this path.",
    )
    p.add_argument(
        "--merge",
        action="store_true",
        help="Merge new exchanges into existing --output JSON before deduplication.",
    )
    p.add_argument(
        "--dedupe-threshold",
        type=float,
        default=0.88,
        help="SequenceMatcher ratio for near-duplicate removal (0 disables). Default: 0.88",
    )
    p.add_argument(
        "--stdin",
        action="store_true",
        help="Read one transcript from stdin (JSON or text); combines with output flags.",
    )
    p.add_argument(
        "--workspace-root",
        type=Path,
        default=None,
        help="Repository/workspace root for relative paths (default: repo root).",
    )
    p.add_argument(
        "--legacy-memory-dir",
        type=Path,
        default=None,
        help="If set, also write per-session digest .md/.json under this directory (legacy shape).",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    ws = (args.workspace_root or repo_root()).resolve()
    out_path = (args.output or ws / "data" / "extracted_conversations.json").resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    logs_dirs = [Path(p) for p in (args.logs_dirs or [])]
    implied_bulk = bool(logs_dirs) and not args.sessions and not args.stdin

    paths: list[Path] = []
    if args.stdin:
        import tempfile

        payload = sys.stdin.read()
        tmp = tempfile.NamedTemporaryFile(
            delete=False, suffix=".txt", prefix="stdin_session_", dir=out_path.parent
        )
        try:
            tpath = Path(tmp.name)
            tpath.write_text(payload, encoding="utf-8")
        finally:
            tmp.close()
        paths.append(tpath)

    for s in args.sessions:
        paths.append(s.expanduser().resolve())

    if args.bulk or implied_bulk:
        if args.bulk:
            roots = default_log_roots(ws) + logs_dirs
        else:
            roots = logs_dirs
        discovered = discover_session_files(roots, since=args.since, until=args.until)
        for d in discovered:
            if d.resolve() not in {p.resolve() for p in paths}:
                paths.append(d)

    if not paths:
        sys.stderr.write(
            "error: no transcripts. Pass session paths, use --bulk, --logs-dir, or --stdin.\n",
        )
        return 2

    for p in paths:
        if not p.exists():
            sys.stderr.write(f"error: file not found: {p}\n")
            return 2

    doc = build_document(
        paths,
        ws,
        topic=args.topic,
        since=args.since,
        until=args.until,
        dedupe_threshold=args.dedupe_threshold,
    )

    if args.merge:
        doc = merge_with_existing_json(out_path, doc)
        doc["exchanges"] = dedupe_exchanges(doc["exchanges"], args.dedupe_threshold)
        doc["exchange_count"] = len(doc["exchanges"])
        for rec in doc["exchanges"]:
            rec["exchange_id"] = stable_exchange_hash(rec)
        reindex_sessions(doc)

    out_path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {out_path.as_posix()} ({doc['exchange_count']} exchanges)")

    if args.markdown:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(render_aggregate_markdown(doc), encoding="utf-8")
        print(f"Wrote {args.markdown.resolve().as_posix()}")

    if args.legacy_memory_dir:
        mem = args.legacy_memory_dir.resolve()
        for path in paths:
            if path.name.startswith("stdin_session_"):
                continue
            segments, _meta = load_session(path, ws)
            digest = analyze_segments(segments, path.as_posix())
            stem = path.stem
            parent = path.parent.name
            if parent and parent not in {".", ""}:
                stem = f"{parent}__{path.stem}"
            md_p, js_p = write_legacy_digest(digest, mem, stem)
            print(f"Legacy digest: {md_p.as_posix()} | {js_p.as_posix()}")

    for p in paths:
        if p.name.startswith("stdin_session_"):
            try:
                p.unlink(missing_ok=True)  # type: ignore[arg-type]
            except OSError:
                pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
