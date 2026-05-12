#!/usr/bin/env python3
"""Extract structured conversations from OpenClaw session transcripts.

Writes one JSON file per session under ``.learnings/conversations/`` (configurable),
maintains ``index.md``, deduplicates by content fingerprint, and supports CLI search.
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
from typing import Any, Iterable, Literal

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.optimize_context import TURN_PATTERN, read_text, repo_root

# -----------------------------------------------------------------------------
# Patterns: decisions / learnings / outcomes / boilerplate
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

SUCCESS_HINTS = re.compile(
    r"\b(?:success|succeeded|completed|done|fixed|resolved|works?|passed|"
    r"implemented|shipped|merged|no\s+errors?)\b",
    re.I,
)
FAILURE_HINTS = re.compile(
    r"\b(?:fail(?:ed|ure)?|error|exception|timeout|traceback|cannot|can't|unable|"
    r"blocked|crash(?:ed)?)\b",
    re.I,
)

BOILERPLATE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^\s*you\s+are\s+(?:a|an)\s+helpful", re.I),
    re.compile(r"^\s*system\s*:\s*you\s+are", re.I),
    re.compile(r"^\s*\[?\s*context\s+(?:window|length)", re.I),
    re.compile(r"^\s*<\s*thinking\s*>", re.I),
    re.compile(r"^\s*session\s+(?:started|initialized)\b", re.I),
)

TOOL_REGEXES: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:invoke|calling|called)\s+(?:tool\s+)?[`\"]?([\w\-./:]+)[`\"]?", re.I),
    re.compile(r"`(?:functions?\.)?([\w\-]+)", re.I),
    re.compile(r"\"(?:tool|toolName|name|function)\"\s*:\s*\"([^\"]+)\""),
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

INTENT_KEYWORDS: list[tuple[str, list[str]]] = [
    ("debugging", ["error", "traceback", "exception", "bug", "fix", "fail"]),
    ("implementation", ["implement", "code", "patch", "refactor", "add", "create file"]),
    ("planning", ["plan", "design", "approach", "architecture", "roadmap"]),
    ("question", ["what is", "how do", "why does", "explain", "?"]),
    ("review", ["review", "lgtm", "nit", "feedback", "pr"]),
    ("operations", ["deploy", "ci", "pipeline", "cron", "server"]),
]

POSITIVE_WORDS = frozenset(
    "great thanks excellent perfect works worked solved awesome appreciate good nice".split()
)
NEGATIVE_WORDS = frozenset(
    "bad wrong stuck broken error failed sorry unfortunately cannot can't ugly".split()
)

TurnKind = Literal["user", "agent", "tool_invocation", "tool_output", "system_event", "unknown"]

DEFAULT_OUTPUT_SUBDIR = Path(".learnings/conversations")
SESSION_JSON_SUFFIX = ".session.json"
DEDUP_INDEX_NAME = "dedup_index.json"
MAX_TEXT_STORE = 12000


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")


def normalize_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def safe_slug(s: str, max_len: int = 64) -> str:
    t = re.sub(r"[^\w\-_.]+", "_", s).strip("_") or "session"
    return t[:max_len]


def content_fingerprint(raw_transcript: str) -> str:
    return hashlib.sha256(raw_transcript.encode("utf-8", errors="replace")).hexdigest()


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
    segments: list[tuple[int, str | None, str]] = []
    current_turn = 1
    role_prefix = re.compile(
        r"^\s*(?P<role>user|human|assistant|agent|tool|system)\s*[:|\\-]+\s*(?P<body>.+)$",
        re.IGNORECASE,
    )

    for line_number, line in enumerate(read_text(path).splitlines(), start=1):
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
        elif line.rstrip("\n\r").strip():
            segments.append((current_turn, None, line.rstrip("\n\r").strip()))

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


def session_json_timestamp(path: Path) -> str | None:
    raw = read_text(path)
    if not raw.strip():
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    for key in ("started_at", "created_at", "timestamp", "session_start", "time"):
        val = data.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


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


def is_boilerplate(text: str) -> bool:
    t = text.strip()
    if len(t) < 8:
        return True
    low = t.lower()
    for pat in BOILERPLATE_PATTERNS:
        if pat.search(low):
            return True
    return False


def classify_kind(role: str | None, text: str) -> TurnKind:
    rl = (role or "").lower()
    if rl == "user":
        return "user"
    if rl in {"assistant", "agent"}:
        return "agent"
    if rl == "system":
        return "system_event"
    if rl == "tool":
        if len(text) < 200 and "\n" not in text and not text.strip().startswith("{"):
            return "tool_invocation"
        return "tool_output"
    if rl == "tool_output":
        return "tool_output"
    if rl in {"", "unknown"} or rl is None:
        low = text.lower()
        if any(low.startswith(p) for p in ("system:", "[system]", "notice:", "warning:")):
            return "system_event"
    return "unknown"


def clip_text(text: str, limit: int = MAX_TEXT_STORE) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 20] + "\n… [truncated] …\n" + text[-200:]


def segments_to_turns(segments: list[tuple[int, str | None, str]]) -> list[dict[str, Any]]:
    turns: list[dict[str, Any]] = []
    for seq, (turn_idx, role, text) in enumerate(segments):
        kind = classify_kind(role, text)
        meaningful = not is_boilerplate(text) and kind not in ("tool_invocation",)
        entry = {
            "seq": seq,
            "turn_index": turn_idx,
            "kind": kind,
            "role_raw": role,
            "text": clip_text(text),
            "meaningful": meaningful,
            "skip_in_narrative": kind in ("tool_invocation",) or is_boilerplate(text),
        }
        turns.append(entry)
    return turns


def _tools_from_turns_slice(turns: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for t in turns:
        if t["kind"] == "tool_invocation":
            n = normalize_ws(str(t["text"]).replace("[tool:", "").replace("]", "").strip())
            base = n.split("(", 1)[0].strip()
            if base and base not in seen:
                seen.add(base)
                names.append(base)
    return names


def _sentiment_hint(text: str) -> str:
    words = re.findall(r"[A-Za-z]+", text.lower())
    if not words:
        return "neutral"
    pos = sum(1 for w in words if w in POSITIVE_WORDS)
    neg = sum(1 for w in words if w in NEGATIVE_WORDS)
    if pos > neg + 1:
        return "positive"
    if neg > pos + 1:
        return "negative"
    return "neutral"


def _infer_intent(blob: str) -> str:
    low = blob.lower()
    best = "general"
    best_score = 0
    for label, keys in INTENT_KEYWORDS:
        score = sum(1 for k in keys if k in low)
        if score > best_score:
            best_score = score
            best = label
    return best


def _infer_outcome(blob: str) -> Literal["success", "failure", "mixed", "unknown"]:
    s_ok = bool(SUCCESS_HINTS.search(blob))
    f_ok = bool(FAILURE_HINTS.search(blob))
    if s_ok and f_ok:
        return "mixed"
    if s_ok:
        return "success"
    if f_ok:
        return "failure"
    return "unknown"


def build_conversation_units(turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Split into units at each meaningful user message (OpenClaw-style threads)."""

    units: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []

    for t in turns:
        if t["kind"] == "user" and t.get("meaningful") and current:
            units.append(current)
            current = [t]
        else:
            current.append(t)

    if current:
        units.append(current)

    if not units and turns:
        units = [turns]

    out: list[dict[str, Any]] = []
    for i, block in enumerate(units, start=1):
        narr_parts: list[str] = []
        for t in block:
            if t.get("skip_in_narrative"):
                continue
            if t["kind"] in ("user", "agent", "unknown", "system_event", "tool_output"):
                narr_parts.append(str(t["text"]))
        narrative = normalize_ws("\n".join(narr_parts))

        user_texts = [str(t["text"]) for t in block if t["kind"] == "user" and not t.get("skip_in_narrative")]
        agent_texts = [
            str(t["text"]) for t in block if t["kind"] == "agent" and not t.get("skip_in_narrative")
        ]

        topic_src = user_texts[0] if user_texts else (narrative[:200] if narrative else f"segment-{i}")
        topic = normalize_ws(topic_src)[:160]

        blob = "\n".join(user_texts + agent_texts)
        tools = _tools_from_turns_slice(block)

        out.append(
            {
                "id": f"conv-{i}",
                "topic": topic,
                "intent": _infer_intent(blob),
                "outcome": _infer_outcome(blob),
                "sentiment_hints": {
                    "user": _sentiment_hint("\n".join(user_texts)) if user_texts else "neutral",
                    "agent": _sentiment_hint("\n".join(agent_texts)) if agent_texts else "neutral",
                },
                "tool_dependencies": tools,
                "summary": narrative[:500] + ("…" if len(narrative) > 500 else ""),
                "turn_seq_range": [block[0]["seq"], block[-1]["seq"]],
                "user_message_count": len(user_texts),
                "agent_message_count": len(agent_texts),
            }
        )
    return out


def collect_session_artifacts(turns: list[dict[str, Any]], segments: list[tuple[int, str | None, str]]) -> dict[str, Any]:
    structured_tools: Counter[str] = Counter()
    blobs: list[str] = []
    for turn_idx, role, text in segments:
        rl = (role or "").lower()
        if rl == "tool":
            tn = normalize_ws(text.replace("[tool:", "").replace("]", "").strip())
            if tn:
                structured_tools[tn.split("(", 1)[0].strip()] += 1
            continue
        blobs.append(text)

    combined = "\n\n".join(blobs)
    textual = extract_tool_signals(combined)

    decisions: list[str] = []
    learnings: list[str] = []
    for t in turns:
        if t["kind"] not in ("user", "agent", "unknown"):
            continue
        body = str(t["text"])
        if t["kind"] == "user":
            decisions.extend(match_patterns(body, DECISION_LINE_PATTERNS))
        else:
            decisions.extend(match_patterns(body, DECISION_LINE_PATTERNS))
            learnings.extend(match_patterns(body, LEARNING_LINE_PATTERNS))

    uniq = lambda xs: list(dict.fromkeys([x for x in xs if x]))  # noqa: E731

    merged = Counter(structured_tools)
    merged.update(textual)

    return {
        "decisions": uniq(decisions)[:80],
        "learnings": uniq(learnings)[:80],
        "tools_ranked": merged.most_common(),
        "tools_structured": dict(structured_tools),
        "tools_from_text_heuristic": dict(textual),
    }


def relative_source(path: Path, workspace_root: Path) -> str:
    try:
        return path.resolve().relative_to(workspace_root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def output_filename_for_session(source_display: str, fingerprint: str) -> str:
    slug = safe_slug(Path(source_display).stem + "_" + Path(source_display).parent.name)
    return f"{slug}_{fingerprint[:10]}{SESSION_JSON_SUFFIX}"


# --- Dedup + index ---


def learnings_dir(workspace: Path, override: Path | None) -> Path:
    return (override if override is not None else workspace / DEFAULT_OUTPUT_SUBDIR).resolve()


def load_dedup_index(out_dir: Path) -> dict[str, Any]:
    p = out_dir / DEDUP_INDEX_NAME
    if not p.exists():
        return {"fingerprints": {}, "by_source": {}}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"fingerprints": {}, "by_source": {}}


def save_dedup_index(out_dir: Path, data: dict[str, Any]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / DEDUP_INDEX_NAME).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def iter_session_json_files(out_dir: Path) -> Iterable[Path]:
    if not out_dir.is_dir():
        return
    for p in sorted(out_dir.glob(f"*{SESSION_JSON_SUFFIX}")):
        yield p


def load_session_record(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def remove_files_for_source(out_dir: Path, source_path: str) -> list[str]:
    """Remove prior JSON for the same source path; return fingerprints that were removed."""

    removed_fp: list[str] = []
    for p in list(iter_session_json_files(out_dir)):
        rec = load_session_record(p)
        if rec and rec.get("source_path") == source_path:
            fp = rec.get("content_fingerprint")
            if isinstance(fp, str):
                removed_fp.append(fp)
            try:
                p.unlink()
            except OSError:
                pass
    return removed_fp


def rebuild_conversation_index(out_dir: Path) -> Path:
    rows: list[dict[str, Any]] = []
    for p in iter_session_json_files(out_dir):
        rec = load_session_record(p)
        if not rec:
            continue
        for conv in rec.get("conversations") or []:
            rows.append(
                {
                    "file": p.name,
                    "source": rec.get("source_path", ""),
                    "session_id": rec.get("session_id", ""),
                    "extracted": rec.get("extracted_at_utc", ""),
                    "conv_id": conv.get("id", ""),
                    "topic": conv.get("topic", ""),
                    "outcome": conv.get("outcome", ""),
                    "intent": conv.get("intent", ""),
                    "tools": ", ".join(conv.get("tool_dependencies") or []),
                    "summary": conv.get("summary", ""),
                }
            )

    lines = [
        "# Conversation extraction index",
        "",
        f"_Generated (UTC): {utc_now_iso()}_",
        "",
        "| Session file | Session id | Source | Conversation | Topic | Intent | Outcome | Tools |",
        "|---|---:|---|---|---|---|---|---|",
    ]
    for r in rows:
        def esc(s: str) -> str:
            return str(s).replace("|", "\\|").replace("\n", " ")

        lines.append(
            f"| `{esc(r['file'])}` | {esc(r['session_id'])} | `{esc(r['source'])}` | {esc(r['conv_id'])} | "
            f"{esc(r['topic'])} | {esc(r['intent'])} | {esc(r['outcome'])} | {esc(r['tools'])} |"
        )

    if len(lines) <= 5:
        lines.append("\n_(No session JSON files found yet.)_\n")

    idx = out_dir / "index.md"
    out_dir.mkdir(parents=True, exist_ok=True)
    idx.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return idx


def extract_session_to_learnings(
    session_path: Path,
    workspace_root: Path,
    out_dir: Path,
    *,
    force: bool = False,
) -> tuple[str, Path | None]:
    """Returns status: written|duplicate|empty and optional output path."""

    raw_transcript = read_text(session_path)
    if not raw_transcript.strip():
        return "empty", None

    fp = content_fingerprint(raw_transcript)
    dedup = load_dedup_index(out_dir)
    fingerprints: dict[str, Any] = dedup.setdefault("fingerprints", {})
    by_source: dict[str, Any] = dedup.setdefault("by_source", {})

    source_display = relative_source(session_path, workspace_root)

    if not force and fp in fingerprints:
        return "duplicate", None

    segments = parse_session_log(session_path.resolve())
    if not segments:
        return "empty", None

    turns = segments_to_turns(segments)
    conversations = build_conversation_units(turns)
    artifacts = collect_session_artifacts(turns, segments)

    mtime = datetime.fromtimestamp(session_path.stat().st_mtime, tz=timezone.utc).isoformat()
    embedded_ts = session_json_timestamp(session_path)

    session_id = safe_slug(f"{session_path.parent.name}_{session_path.stem}")[:48]

    out_name = output_filename_for_session(source_display, fp)
    out_path = out_dir / out_name

    record: dict[str, Any] = {
        "schema_version": 1,
        "session_id": session_id,
        "source_path": source_display,
        "source_mtime_utc": mtime,
        "session_timestamp_hint": embedded_ts,
        "content_fingerprint": fp,
        "extracted_at_utc": utc_now_iso(),
        "turns": turns,
        "conversations": conversations,
        "artifacts": artifacts,
    }

    out_dir.mkdir(parents=True, exist_ok=True)

    for old_fp in remove_files_for_source(out_dir, source_display):
        fingerprints.pop(old_fp, None)
    by_source.pop(source_display, None)

    out_path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    fingerprints[fp] = {"file": out_name, "source_path": source_display}
    by_source[source_display] = {"fingerprint": fp, "file": out_name}
    save_dedup_index(out_dir, dedup)

    rebuild_conversation_index(out_dir)
    return "written", out_path


# --- Search ---


def _parse_iso_date(s: str) -> datetime | None:
    s = s.strip()
    if not s:
        return None
    try:
        if len(s) == 10 and s[4] == "-" and s[7] == "-":
            return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def search_records(
    out_dir: Path,
    *,
    topic: str | None = None,
    keyword: str | None = None,
    outcome: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict[str, Any]]:
    df = _parse_iso_date(date_from) if date_from else None
    dt = _parse_iso_date(date_to) if date_to else None
    if date_to and dt:
        dt = dt.replace(hour=23, minute=59, second=59, microsecond=999999)

    matches: list[dict[str, Any]] = []
    topic_l = topic.lower() if topic else None
    kw_l = keyword.lower() if keyword else None
    oc_l = outcome.lower() if outcome else None

    for p in iter_session_json_files(out_dir):
        rec = load_session_record(p)
        if not rec:
            continue
        extracted = rec.get("extracted_at_utc") or ""
        try:
            ex_dt = datetime.fromisoformat(extracted.replace("Z", "+00:00"))
            if ex_dt.tzinfo is None:
                ex_dt = ex_dt.replace(tzinfo=timezone.utc)
            ex_dt = ex_dt.astimezone(timezone.utc)
        except ValueError:
            ex_dt = None

        if df and ex_dt and ex_dt < df:
            continue
        if dt and ex_dt and ex_dt > dt:
            continue

        for conv in rec.get("conversations") or []:
            t = str(conv.get("topic", ""))
            summ = str(conv.get("summary", ""))
            oc = str(conv.get("outcome", "")).lower()

            if topic_l and topic_l not in t.lower():
                continue
            if oc_l and oc != oc_l:
                continue
            if kw_l:
                blob = (t + " " + summ + " " + json.dumps(rec.get("artifacts", {}))).lower()
                if kw_l not in blob:
                    continue

            matches.append(
                {
                    "file": p.name,
                    "source_path": rec.get("source_path"),
                    "session_id": rec.get("session_id"),
                    "extracted_at_utc": extracted,
                    "conversation": conv,
                }
            )

    return matches


# --- Legacy memory digest (optional) ---


@dataclass
class ConversationDigest:
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


def analyze_segments_legacy(segments: list[tuple[int, str | None, str]], source_display: str) -> ConversationDigest:
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


def render_markdown_digest(d: ConversationDigest) -> str:
    lines = [
        "# Conversation extract (legacy digest)",
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
    merged = d.all_tools()
    lines.extend(["", "## Tool usage"])
    if merged:
        for name, ct in merged.most_common(40):
            lines.append(f"- `{name}` — {ct} mentions")
    else:
        lines.append("- *(no tool mentions parsed)*")
    lines.extend(["", "---", "*conversation_extractor.py — legacy digest to `memory/`.*"])
    return "\n".join(lines) + "\n"


def write_legacy_digest(
    digest: ConversationDigest,
    memory_dir: Path,
    stem: str,
) -> tuple[Path, Path]:
    memory_dir.mkdir(parents=True, exist_ok=True)
    safe = safe_slug(stem, 80) or "session"
    tag = utc_stamp()
    md_path = memory_dir / f"conversation_extract_{safe}_{tag}.md"
    js_path = memory_dir / f"conversation_extract_{safe}_{tag}.json"
    md_path.write_text(render_markdown_digest(digest), encoding="utf-8")
    js_path.write_text(
        json.dumps(
            {
                "source": digest.source,
                "generated_at_utc": digest.generated_at_utc,
                "decisions": digest.decisions,
                "learnings": digest.learnings,
                "tools_ranked": digest.all_tools().most_common(),
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return md_path, js_path


def run_legacy_extraction(session_path: Path, memory_dir: Path, workspace_root: Path) -> tuple[Path, Path]:
    segments = parse_session_log(session_path.resolve())
    digest = analyze_segments_legacy(segments, session_path.resolve().as_posix())
    stem = session_path.stem
    parent = session_path.parent.name
    if parent and parent not in {".", ""}:
        stem = f"{parent}__{session_path.stem}"
    digest.source = relative_source(session_path, workspace_root)
    return write_legacy_digest(digest, memory_dir, stem)


# --- argv + argparse ---

LEGACY_FLAGS = frozenset({"--stdin", "--memory-dir", "--workspace-root"})


def normalize_argv(argv: list[str] | None) -> list[str]:
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        return argv
    if argv[0] in ("extract", "search", "index", "-h", "--help"):
        return argv
    if any(a in LEGACY_FLAGS for a in argv):
        return ["extract"] + argv
    if not argv[0].startswith("-") and Path(argv[0]).exists():
        return ["extract"] + argv
    return argv


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Extract OpenClaw session transcripts into .learnings/conversations/ "
        "with search and deduplication.",
    )
    sub = p.add_subparsers(dest="cmd", required=False)

    e = sub.add_parser("extract", help="Parse transcript(s) and write structured JSON + index.")
    e.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="Session files (.json, .log, .txt).",
    )
    e.add_argument(
        "--stdin",
        action="store_true",
        help="Read transcript from stdin (treated as one session; temp file under output dir).",
    )
    e.add_argument(
        "--workspace-root",
        type=Path,
        default=None,
        help="Repo root for relative source paths (default: auto).",
    )
    e.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help=f"Where to write JSON + index (default: <root>/{DEFAULT_OUTPUT_SUBDIR.as_posix()}).",
    )
    e.add_argument(
        "--force",
        action="store_true",
        help="Re-extract even when content fingerprint matches a prior run.",
    )
    e.add_argument(
        "--also-legacy-memory",
        action="store_true",
        help="Also emit timestamped digest files under --memory-dir (previous behavior).",
    )
    e.add_argument(
        "--memory-dir",
        type=Path,
        default=None,
        help="Destination for legacy digest when --also-legacy-memory is set.",
    )

    s = sub.add_parser("search", help="Search extracted conversations on disk.")
    s.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help=f"Directory containing *.session.json (default: <root>/{DEFAULT_OUTPUT_SUBDIR.as_posix()}).",
    )
    s.add_argument("--workspace-root", type=Path, default=None)
    s.add_argument("--topic", type=str, default=None, help="Substring match on conversation topic.")
    s.add_argument("--keyword", type=str, default=None, help="Case-insensitive substring in topic/summary/artifacts.")
    s.add_argument(
        "--outcome",
        type=str,
        choices=("success", "failure", "mixed", "unknown"),
        default=None,
    )
    s.add_argument("--from-date", type=str, default=None, help="ISO date (YYYY-MM-DD) lower bound on extracted_at.")
    s.add_argument("--to-date", type=str, default=None, help="ISO date upper bound on extracted_at.")
    s.add_argument("--json", action="store_true", help="Print JSON lines instead of human text.")

    ix = sub.add_parser("index", help="Rebuild index.md from all *.session.json files.")
    ix.add_argument("--output-dir", type=Path, default=None)
    ix.add_argument("--workspace-root", type=Path, default=None)

    return p


def cmd_extract(args: argparse.Namespace) -> int:
    ws = (args.workspace_root or repo_root()).resolve()
    out_dir = learnings_dir(ws, args.output_dir)

    if getattr(args, "stdin", False):
        import tempfile

        out_dir.mkdir(parents=True, exist_ok=True)
        payload = sys.stdin.read()
        tmp = tempfile.NamedTemporaryFile(
            delete=False, suffix=".txt", prefix="stdin_session_", dir=out_dir
        )
        try:
            path = Path(tmp.name)
            path.write_text(payload, encoding="utf-8")
        finally:
            tmp.close()

        status, outp = extract_session_to_learnings(path, ws, out_dir, force=args.force)
        if args.also_legacy_memory:
            mem = (args.memory_dir or ws / "memory").resolve()
            tw = mem / f"_stdin_replay_{utc_stamp()}.txt"
            tw.write_text(payload, encoding="utf-8")
            md_path, json_path = run_legacy_extraction(tw, mem, ws)
            tw.unlink(missing_ok=True)  # type: ignore[arg-type]
            print(f"legacy: {md_path.as_posix()}")
            print(f"legacy: {json_path.as_posix()}")
        try:
            path.unlink(missing_ok=True)  # type: ignore[arg-type]
        except OSError:
            pass

        if status == "duplicate":
            print("skip: duplicate transcript (same content fingerprint)")
        elif status == "empty":
            print("skip: empty transcript")
        else:
            print(f"wrote {outp.as_posix()}" if outp else "done")
        return 0

    paths = list(args.paths)
    if not paths:
        sys.stderr.write("extract: provide file path(s) or --stdin\n")
        return 2

    rc = 0
    for sp in paths:
        sp = sp.resolve()
        if not sp.exists():
            sys.stderr.write(f"missing file: {sp}\n")
            rc = 1
            continue
        status, outp = extract_session_to_learnings(sp, ws, out_dir, force=args.force)
        if status == "duplicate":
            print(f"{sp}: skip duplicate")
        elif status == "empty":
            print(f"{sp}: skip empty")
        else:
            print(f"{sp}: {outp.as_posix()}" if outp else f"{sp}: done")
        if args.also_legacy_memory:
            mem = (args.memory_dir or ws / "memory").resolve()
            md_path, json_path = run_legacy_extraction(sp, mem, ws)
            print(f"legacy: {md_path.as_posix()}")
            print(f"legacy: {json_path.as_posix()}")
    return rc


def cmd_search(args: argparse.Namespace) -> int:
    ws = (args.workspace_root or repo_root()).resolve()
    out_dir = learnings_dir(ws, args.output_dir)
    hits = search_records(
        out_dir,
        topic=args.topic,
        keyword=args.keyword,
        outcome=args.outcome,
        date_from=args.from_date,
        date_to=args.to_date,
    )
    if args.json:
        for h in hits:
            print(json.dumps(h, ensure_ascii=False))
        return 0
    if not hits:
        print("no matches")
        return 0
    for h in hits:
        c = h["conversation"]
        print("---")
        print(f"file: {h['file']}")
        print(f"source: {h['source_path']}")
        print(f"topic: {c.get('topic')}")
        print(f"outcome: {c.get('outcome')} intent: {c.get('intent')}")
        print(f"summary: {c.get('summary')}")
    return 0


def cmd_index(args: argparse.Namespace) -> int:
    ws = (args.workspace_root or repo_root()).resolve()
    out_dir = learnings_dir(ws, args.output_dir)
    p = rebuild_conversation_index(out_dir)
    print(p.as_posix())
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = normalize_argv(list(argv if argv is not None else sys.argv[1:]))
    parser = build_arg_parser()
    if not argv:
        parser.print_help()
        return 0

    args = parser.parse_args(argv)

    cmd = args.cmd
    if cmd is None:
        parser.print_help()
        return 0

    if cmd == "extract":
        return cmd_extract(args)
    if cmd == "search":
        return cmd_search(args)
    if cmd == "index":
        return cmd_index(args)

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
