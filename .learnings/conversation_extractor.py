#!/usr/bin/env python3
"""Extract patterns from OpenClaw session transcripts: tools, prompts, error recovery.

Scans a directory of session exports (.json, .log, .txt, .md), aggregates structured signals,
and writes one JSON summary (CLI: ``--session-dir``, ``--output``).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

# -----------------------------------------------------------------------------
# Minimal I/O (avoid depending on other repo packages when run as a script)
# -----------------------------------------------------------------------------

TURN_PATTERN = re.compile(r"(?i)\b(?:turn|step|message)\s*[:#-]?\s*(\d+)\b")

_SESSION_SUFFIXES = frozenset({".json", ".log", ".txt", ".md"})


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


# -----------------------------------------------------------------------------
# Heuristics: decisions, learnings, success / error / recovery
# -----------------------------------------------------------------------------

DECISION_LINE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"^\s*(?:decision|resolution|resolved|outcome)\s*[:\-—]\s*(.+)",
        r"^\s*\*{0,2}(?:decision|resolution)\*{0,2}\s*[:\-—]\s*(.+)",
        r"(?:we(?:'ve)?\s+(?:decided|agreed|chose)|let'?s\s+go\s+with|final(?:ly)?\s*:\s*)(.+)",
        r"(?:\bapproved\b|\bfinalize[ds]?\b|\bchosen\b\s+(?:approach|option|path))\s*[:\-]?\s*(.+)",
        r"^\s*(?:TL;DR|TDLR|takeaway)s?\s*[:\-—]\s*(.+)",
    )
)

LEARNING_LINE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"^\s*(?:learning|lesson|takeaway|insight|key\s+learning)\s*[:\-—]\s*(.+)",
        r"^\s*\*{0,2}(?:important|remember|note)\*{0,2}\s*[:\-—]\s*(.+)",
    )
)

TOOL_REGEXES: tuple[re.Pattern[str], ...] = (
    re.compile(r'\b(?:invoke|calling|called)\s+(?:tool\s+)?[`"]?([\w\-./:]+)[`"]?', re.I),
    re.compile(r"`(?:functions?\.)?([\w\-]+)", re.I),
    re.compile(r'"(?:tool|toolName|name|function)"\s*:\s*"([^"]+)"'),
    re.compile(r"\[tool\s*:\s*([\w\-./:]+)\]", re.I),
    re.compile(r"\b(?:mcp|MCP)[_\s:]+([\w\-]+)", re.I),
)

SUCCESS_AFTER_USER = re.compile(
    r"\b(?:fixed|resolved|completed|success|passed|works now|all set|shipped|merged|lgtm)\b",
    re.I,
)
ERROR_CUE = re.compile(
    r"\b(?:error|failed|failure|exception|traceback|syntaxerror|timeouterror|"
    r"enoent|eacces|cannot|unable to|command failed|exit code [1-9])\b",
    re.I,
)
RECOVERY_CUE = re.compile(
    r"\b(?:retry|retried|fixed|workaround|instead|switched to|corrected|patched|"
    r"now works|resolved by|reran|re-ran|try again|adjusted|updated)\b",
    re.I,
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


def parse_text_session(raw: str) -> list[tuple[int, str | None, str]]:
    segments: list[tuple[int, str | None, str]] = []
    current_turn = 1
    role_prefix = re.compile(
        r"^\s*(?P<role>user|human|assistant|agent|tool|system)\s*[:|\\-]+\s*(?P<body>.+)$",
        re.IGNORECASE,
    )

    for line_number, line in enumerate(raw.splitlines(), start=1):
        m_turn = TURN_PATTERN.search(line)
        if m_turn:
            current_turn = int(m_turn.group(1))
        else:
            current_turn = max(current_turn, line_number)

        m_role = role_prefix.match(line)
        if m_role:
            rl = m_role.group("role").lower()
            rl = {"human": "user"}.get(rl, rl)
            segments.append((current_turn, rl, m_role.group("body").strip()))
        elif line.strip():
            segments.append((current_turn, None, line.strip()))

    return segments


def parse_session_file(path: Path) -> list[tuple[int, str | None, str]]:
    raw = read_text(path)
    if path.suffix.lower() == ".json":
        segs = parse_json_session(raw)
        if segs:
            return segs
    return parse_text_session(raw)


def normalize_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


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


def _ordered_tool_sequence(segments: list[tuple[int, str | None, str]]) -> list[str]:
    """Preserve transcript order for n-gram analysis."""

    ordered: list[str] = []
    for _turn, role, text in segments:
        if (role or "").lower() == "tool":
            tn = normalize_ws(text.replace("[tool:", "").replace("]", "").strip())
            if tn:
                ordered.append(tn.split("(", 1)[0].strip())
    return ordered


def _ngrams(seq: list[str], n: int) -> Iterable[tuple[str, ...]]:
    if len(seq) < n:
        return
    for i in range(len(seq) - n + 1):
        yield tuple(seq[i : i + n])


def _prompt_templates_from_segments(
    segments: list[tuple[int, str | None, str]],
) -> list[dict[str, Any]]:
    """User turns whose following assistant content suggests success."""

    by_turn: dict[int, list[tuple[str | None, str]]] = {}
    for turn, role, text in segments:
        by_turn.setdefault(turn, []).append((role, text))

    templates: list[dict[str, Any]] = []
    turns_sorted = sorted(by_turn)

    for i, t in enumerate(turns_sorted):
        chunk = by_turn[t]
        user_texts = [tx for rl, tx in chunk if (rl or "").lower() in {"user", "human"}]
        if not user_texts:
            continue
        ut = max(user_texts, key=len)
        if len(normalize_ws(ut)) < 36:
            continue

        following = []
        for t2 in turns_sorted[i + 1 : i + 4]:
            following.extend(tx for _rl, tx in by_turn[t2])

        blob = "\n".join(following)
        if not SUCCESS_AFTER_USER.search(blob):
            continue

        snippet = normalize_ws(ut)
        if len(snippet) > 420:
            snippet = snippet[:417] + "..."
        templates.append({"user_prompt_excerpt": snippet, "followed_by_success_heuristic": True})

    return templates


def _error_recovery_pairs(segments: list[tuple[int, str | None, str]]) -> list[dict[str, str]]:
    flat: list[tuple[int, str | None, str]] = list(segments)
    pairs: list[dict[str, str]] = []

    def clip(s: str, limit: int = 200) -> str:
        s = normalize_ws(s)
        return s if len(s) <= limit else s[: limit - 3] + "..."

    for i, (_t, _r, text) in enumerate(flat):
        m_e = ERROR_CUE.search(text)
        if not m_e:
            continue

        m_r_same = RECOVERY_CUE.search(text, pos=m_e.end())
        if m_r_same:
            err_ctx = clip(text[max(0, m_e.start() - 24) : m_e.end() + 72])
            rec_ctx = clip(text[m_r_same.start() : m_r_same.end() + 96])
            pairs.append({"error_context": err_ctx, "recovery_context": rec_ctx})
            continue

        err_ctx = clip(text[max(0, m_e.start() - 24) : m_e.end() + 72])
        for _t2, _r2, text2 in flat[i + 1 : i + 12]:
            m_r = RECOVERY_CUE.search(text2)
            if m_r:
                rec_ctx = clip(text2[m_r.start() : m_r.end() + 100])
                pairs.append({"error_context": err_ctx, "recovery_context": rec_ctx})
                break

    return pairs


@dataclass
class SessionSummary:
    path: str
    segment_count: int
    tools_structured: dict[str, int]
    tools_from_text: dict[str, int]
    decisions: list[str]
    learnings: list[str]
    tool_bigrams: list[tuple[tuple[str, str], int]]
    tool_trigrams: list[tuple[tuple[str, str, str], int]]
    prompt_templates: list[dict[str, Any]]
    error_recovery_examples: list[dict[str, str]]


@dataclass
class AggregateSummary:
    tool_names: dict[str, int]
    tool_bigrams: dict[str, int]
    tool_trigrams: dict[str, int]
    prompt_templates: list[dict[str, Any]]
    error_recovery_strategies: list[dict[str, Any]]


@dataclass
class ExtractionReport:
    generated_at_utc: str
    session_dir: str
    sessions_scanned: int
    session_files: list[str]
    per_session: list[SessionSummary]
    aggregated: AggregateSummary
    notes: list[str] = field(default_factory=list)


def _digest_session(path: Path, segments: list[tuple[int, str | None, str]]) -> SessionSummary:
    structured_tools: Counter[str] = Counter()
    blobs: list[str] = []

    for turn, role, text in segments:
        rl = (role or "").lower()
        if rl == "tool":
            tn = normalize_ws(text.replace("[tool:", "").replace("]", "").strip())
            if tn:
                structured_tools[tn.split("(", 1)[0].strip()] += 1
            continue
        if rl == "tool_output":
            blobs.append(text)
            continue
        blobs.append(text)

    combined = "\n\n".join(blobs)
    textual = extract_tool_signals(combined)

    decisions: list[str] = []
    learnings: list[str] = []
    for turn, role, text in segments:
        rl = (role or "").lower()
        if rl in {"tool"}:
            continue
        if rl in {"", "assistant", "agent"} or rl is None:
            decisions.extend(match_patterns(text, DECISION_LINE_PATTERNS))
            learnings.extend(match_patterns(text, LEARNING_LINE_PATTERNS))
        elif rl == "user":
            decisions.extend(match_patterns(text, DECISION_LINE_PATTERNS))

    def uniq(xs: list[str]) -> list[str]:
        return list(dict.fromkeys([x for x in xs if x]))[:80]

    seq = _ordered_tool_sequence(segments)
    bi = Counter([" → ".join(p) for p in _ngrams(seq, 2)])
    tri = Counter([" → ".join(p) for p in _ngrams(seq, 3)])

    return SessionSummary(
        path=path.resolve().as_posix(),
        segment_count=len(segments),
        tools_structured=dict(structured_tools),
        tools_from_text=dict(textual),
        decisions=uniq(decisions),
        learnings=uniq(learnings),
        tool_bigrams=[(tuple(k.split(" → ")), v) for k, v in bi.most_common(25)],
        tool_trigrams=[(tuple(k.split(" → ")), v) for k, v in tri.most_common(20)],
        prompt_templates=_prompt_templates_from_segments(segments)[:15],
        error_recovery_examples=_error_recovery_pairs(segments)[:12],
    )


def discover_session_files(root: Path, *, max_files: int = 800) -> list[Path]:
    if not root.is_dir():
        return []

    found: list[Path] = []
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        if p.suffix.lower() not in _SESSION_SUFFIXES:
            continue
        rel = p.relative_to(root)
        if any(part.startswith(".") for part in rel.parts):
            continue
        found.append(p)
        if len(found) >= max_files:
            break
    return found


def _serialize_dataclass(obj: Any) -> Any:
    if hasattr(obj, "__dataclass_fields__"):
        return {k: _serialize_dataclass(v) for k, v in asdict(obj).items()}
    if isinstance(obj, dict):
        return {k: _serialize_dataclass(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_serialize_dataclass(v) for v in obj]
    if isinstance(obj, tuple):
        return list(obj)
    return obj


def run_scan(session_dir: Path) -> ExtractionReport:
    notes: list[str] = []
    files = discover_session_files(session_dir)
    if not files:
        notes.append("No session-like files found (*.json, *.log, *.txt, *.md).")

    per_session: list[SessionSummary] = []
    agg_tools: Counter[str] = Counter()
    agg_bi: Counter[str] = Counter()
    agg_tri: Counter[str] = Counter()
    tmpl_hashes: dict[str, dict[str, Any]] = {}
    recovery_hashes: dict[str, dict[str, Any]] = {}

    for fp in files:
        segs = parse_session_file(fp)
        if not segs:
            notes.append(f"No parseable segments: {fp.name}")
            continue
        s = _digest_session(fp, segs)
        per_session.append(s)

        for name, c in s.tools_structured.items():
            agg_tools[name] += c
        for name, c in s.tools_from_text.items():
            agg_tools[name] += c

        for tup, c in s.tool_bigrams:
            agg_bi[" → ".join(tup)] += c
        for tup, c in s.tool_trigrams:
            agg_tri[" → ".join(tup)] += c

        for item in s.prompt_templates:
            h = hashlib.sha256(item["user_prompt_excerpt"].encode("utf-8", errors="replace")).hexdigest()[:16]
            if h not in tmpl_hashes:
                tmpl_hashes[h] = {
                    "template_excerpt": item["user_prompt_excerpt"],
                    "sessions_matched": 0,
                }
            tmpl_hashes[h]["sessions_matched"] += 1

        for pair in s.error_recovery_examples:
            h = hashlib.sha256(
                (pair["error_context"] + "|" + pair["recovery_context"]).encode("utf-8", errors="replace")
            ).hexdigest()[:16]
            if h not in recovery_hashes:
                recovery_hashes[h] = {
                    "error_context": pair["error_context"],
                    "recovery_context": pair["recovery_context"],
                    "occurrences": 0,
                }
            recovery_hashes[h]["occurrences"] += 1

    agg = AggregateSummary(
        tool_names=dict(agg_tools.most_common(200)),
        tool_bigrams=dict(agg_bi.most_common(60)),
        tool_trigrams=dict(agg_tri.most_common(40)),
        prompt_templates=sorted(
            tmpl_hashes.values(),
            key=lambda x: x["sessions_matched"],
            reverse=True,
        )[:40],
        error_recovery_strategies=sorted(
            recovery_hashes.values(),
            key=lambda x: x["occurrences"],
            reverse=True,
        )[:40],
    )

    return ExtractionReport(
        generated_at_utc=datetime.now(timezone.utc).isoformat(),
        session_dir=session_dir.resolve().as_posix(),
        sessions_scanned=len(per_session),
        session_files=[p.resolve().as_posix() for p in files],
        per_session=per_session,
        aggregated=agg,
        notes=notes,
    )


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Extract tool patterns, prompt templates, and error-recovery signals from OpenClaw sessions.",
    )
    p.add_argument(
        "--session-dir",
        type=Path,
        required=True,
        help="Directory containing session transcripts (.json, .log, .txt, .md).",
    )
    p.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Write structured JSON summary to this path.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    sd = args.session_dir.resolve()
    if not sd.is_dir():
        sys.stderr.write(f"error: not a directory: {sd}\n")
        return 2

    report = run_scan(sd)
    out = args.output.resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = _serialize_dataclass(report)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(out.as_posix())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
