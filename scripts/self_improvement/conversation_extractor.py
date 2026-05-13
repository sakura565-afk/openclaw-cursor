#!/usr/bin/env python3
"""Parse session transcripts, extract decisions/context/topics, write structured memory artifacts."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

# Repo root on sys.path so `python scripts/self_improvement/conversation_extractor.py` works.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.optimize_context import HEADING_PATTERN, TURN_PATTERN, read_text, repo_root

# -----------------------------------------------------------------------------
# Line patterns: decisions, learnings, context, actions (regex heuristics)
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
        r"\b(?:the\s+)?(?:key\s+)?(?:insight|realization)\s+(?:is|was)\s+(.{8,})",
    )
)

CONTEXT_LINE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"^\s*(?:context|background|constraint|assumption|risk|blocker)\s*[:\-—]\s*(.+)",
        r"^\s*\*{0,2}(?:context|constraint)\*{0,2}\s*[:\-—]\s*(.+)",
        r"\b(?:worth\s+noting|keep\s+in\s+mind|important\s+to\s+know)\s*[:\-]?\s*(.{12,})",
        r"\b(?:root\s+cause|underlying\s+issue)\s*[:\-—]?\s*(.{10,})",
    )
)

ACTION_LINE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"^\s*(?:action|next\s+step|follow[-\s]?up|todo|FIXME|TODO)\s*[:\-—]\s*(.+)",
        r"\b(?:we\s+should|need\s+to|must\s+|will\s+)(.{8,120})\b",
    )
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

# Lightweight NLP: English stopwords for topic scoring (not linguistic POS tagging).
_STOPWORDS = frozenset(
    """
    a an the and or but if then else for to of in on at by from with as is was were be been being
    it its this that these those we you they he she i me my your our their what which who whom
    when where why how all any both each few more most other some such no not only own same so
    than too very can could should would will just also into about over after before again further
    once here there do does did doing done have has had having get got getting make made making
    use used using may might must shall need needs needed let like going go went gone
    """.split()
)

WORD_TOKEN = re.compile(r"[a-z][a-z0-9_-]{2,}", re.I)
CAMEL_TOKEN = re.compile(r"\b[A-Z][a-z]+(?:[A-Z][a-z]+)+\b")
SNAKE_TOKEN = re.compile(r"\b[a-z][a-z0-9_]*_[a-z][a-z0-9_]+\b")

FRONTMATTER_BOUND = re.compile(r"^---\s*\n.*?\n---\s*\n", re.DOTALL | re.MULTILINE)
ROLE_PREFIX_MD = re.compile(
    r"^\s*\*{0,2}(?P<role>user|human|assistant|agent|tool|system)\*{0,2}\s*[:|\\-]+\s*(?P<body>.+)$",
    re.IGNORECASE,
)
ROLE_PREFIX = re.compile(
    r"^\s*(?P<role>user|human|assistant|agent|tool|system)\s*[:|\\-]+\s*(?P<body>.+)$",
    re.IGNORECASE,
)


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

        seen = set()
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


def parse_jsonl_session(path: Path) -> list[tuple[int, str | None, str]]:
    """One JSON object per line (streaming exports)."""
    lines = read_text(path).splitlines()
    merged: list[tuple[int, str | None, str]] = []
    turn_base = 0
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        inner = _unpack_session_json(row) or _fallback_json_segments(row)
        if not inner:
            continue
        max_t = max((t for t, _, _ in inner), default=0)
        for t, r, txt in inner:
            merged.append((t + turn_base, r, txt))
        turn_base += max(max_t, 1)
    return merged


def strip_markdown_frontmatter(text: str) -> str:
    return FRONTMATTER_BOUND.sub("", text, count=1).strip()


def extract_markdown_headings(text: str) -> list[str]:
    return [normalize_ws(m.group(1)) for m in HEADING_PATTERN.finditer(text)]


def parse_text_session(path: Path, *, is_markdown: bool) -> list[tuple[int, str | None, str]]:
    raw = read_text(path)
    if is_markdown:
        raw = strip_markdown_frontmatter(raw)

    segments: list[tuple[int, str | None, str]] = []
    current_turn = 1
    prefix = ROLE_PREFIX_MD if is_markdown else ROLE_PREFIX

    for line_number, line in enumerate(raw.splitlines(), start=1):
        m_turn = TURN_PATTERN.search(line)
        if m_turn:
            current_turn = int(m_turn.group(1))
        else:
            current_turn = max(current_turn, line_number)

        m_role = prefix.match(line)
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
    if suf == ".jsonl":
        segs = parse_jsonl_session(path)
        return segs if segs else parse_text_session(path, is_markdown=False)

    if suf == ".json":
        segs = parse_json_session(path)
        if segs:
            return segs
        return parse_text_session(path, is_markdown=False)

    is_md = suf in {".md", ".markdown"} or path.name.lower().endswith(".md")
    return parse_text_session(path, is_markdown=is_md)


def match_patterns(text: str, patterns: tuple[re.Pattern[str], ...]) -> list[str]:
    hits: list[str] = []
    for ln in text.splitlines():
        line = ln.strip()
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


def _token_words(text: str) -> list[str]:
    lowered = text.lower()
    words = [m.group(0).lower() for m in WORD_TOKEN.finditer(lowered)]
    return [w for w in words if w not in _STOPWORDS and not w.isdigit()]


def extract_topic_counters(text: str) -> tuple[Counter[str], Counter[str]]:
    """Unigram and bigram content-word counters (lightweight NLP)."""
    words = _token_words(text)
    uni: Counter[str] = Counter(words)
    bi: Counter[str] = Counter()
    for a, b in zip(words, words[1:]):
        if a == b:
            continue
        bi[f"{a} {b}"] += 1
    for m in CAMEL_TOKEN.finditer(text):
        uni[m.group(0)] += 2
    for m in SNAKE_TOKEN.finditer(text):
        w = m.group(0).lower()
        if w not in _STOPWORDS:
            uni[w] += 2
    return uni, bi


def extract_recurring_signals(
    uni: Counter[str], bi: Counter[str], *, min_uni: int = 3, min_bi: int = 2
) -> tuple[list[tuple[str, int]], list[tuple[str, int]]]:
    top_u = [(w, c) for w, c in uni.most_common(80) if c >= min_uni]
    top_b = [(p, c) for p, c in bi.most_common(60) if c >= min_bi]
    return top_u[:40], top_b[:30]


@dataclass
class ConversationDigest:
    """Structured extraction from one transcript."""

    source: str
    generated_at_utc: str
    segments: list[tuple[int, str | None, str]]
    decisions: list[str]
    learnings: list[str]
    context_snippets: list[str]
    action_items: list[str]
    markdown_headings: list[str]
    topic_terms: list[tuple[str, int]]
    topic_phrases: list[tuple[str, int]]
    tool_structured: Counter[str] = field(default_factory=Counter)
    tool_textual: Counter[str] = field(default_factory=Counter)

    def all_tools(self) -> Counter[str]:
        merged: Counter[str] = Counter(self.tool_structured)
        merged.update(self.tool_textual)
        return merged


def _uniq(xs: Iterable[str], cap: int) -> list[str]:
    return list(dict.fromkeys([x for x in xs if x]))[:cap]


def analyze_segments(
    segments: list[tuple[int, str | None, str]],
    source_display: str,
    *,
    raw_markdown: str | None = None,
) -> ConversationDigest:
    decisions_acc: list[str] = []
    learnings_acc: list[str] = []
    context_acc: list[str] = []
    action_acc: list[str] = []
    structured_tools: Counter[str] = Counter()
    blobs_for_nlp: list[str] = []

    for turn, role, text in segments:
        rl = (role or "").lower()
        if rl == "tool":
            tn = normalize_ws(text.replace("[tool:", "").replace("]", "").strip())
            if tn:
                structured_tools[tn.split("(", 1)[0].strip()] += 1
            continue

        if rl == "tool_output":
            blobs_for_nlp.append(text)
            continue

        if rl in {"", "assistant", "agent"} or rl is None:
            decisions_acc.extend(match_patterns(text, DECISION_LINE_PATTERNS))
            learnings_acc.extend(match_patterns(text, LEARNING_LINE_PATTERNS))
            context_acc.extend(match_patterns(text, CONTEXT_LINE_PATTERNS))
            action_acc.extend(match_patterns(text, ACTION_LINE_PATTERNS))
        elif rl == "user":
            decisions_acc.extend(match_patterns(text, DECISION_LINE_PATTERNS))
            context_acc.extend(match_patterns(text, CONTEXT_LINE_PATTERNS))
            action_acc.extend(match_patterns(text, ACTION_LINE_PATTERNS))

        blobs_for_nlp.append(text)

    combined = "\n\n".join(blobs_for_nlp)
    if raw_markdown:
        combined = combined + "\n\n" + raw_markdown

    textual_tools = extract_tool_signals(combined)
    uni, bi = extract_topic_counters(combined)
    top_u, top_b = extract_recurring_signals(uni, bi)

    headings: list[str] = []
    if raw_markdown:
        headings = extract_markdown_headings(raw_markdown)

    return ConversationDigest(
        source=source_display,
        generated_at_utc=datetime.now(timezone.utc).isoformat(),
        segments=segments,
        decisions=_uniq(decisions_acc, 120),
        learnings=_uniq(learnings_acc, 120),
        context_snippets=_uniq(context_acc, 80),
        action_items=_uniq(action_acc, 80),
        markdown_headings=_uniq(headings, 60),
        topic_terms=top_u,
        topic_phrases=top_b,
        tool_structured=structured_tools,
        tool_textual=textual_tools,
    )


def render_markdown(d: ConversationDigest) -> str:
    lines = [
        "# Conversation extract (self-improvement)",
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

    lines.extend(["", "## Learnings and takeaways"])
    if d.learnings:
        for item in d.learnings:
            lines.append(f"- {item}")
    else:
        lines.append("- *(no explicit learning cues detected)*")

    lines.extend(["", "## Context and constraints"])
    if d.context_snippets:
        for item in d.context_snippets:
            lines.append(f"- {item}")
    else:
        lines.append("- *(no context/blocker pattern matches)*")

    lines.extend(["", "## Actions and follow-ups"])
    if d.action_items:
        for item in d.action_items:
            lines.append(f"- {item}")
    else:
        lines.append("- *(no action-style lines detected)*")

    lines.extend(["", "## Recurring topics (NLP heuristics)"])
    if d.topic_terms or d.topic_phrases:
        lines.append("### High-signal terms")
        for w, c in d.topic_terms[:25]:
            lines.append(f"- `{w}` — {c}")
        lines.append("")
        lines.append("### Recurring phrases (bigrams)")
        for p, c in d.topic_phrases[:20]:
            lines.append(f"- {p} — {c}")
    else:
        lines.append("- *(insufficient lexical repetition for topic scoring)*")

    if d.markdown_headings:
        lines.extend(["", "## Markdown structure (headings)"])
        for h in d.markdown_headings[:40]:
            lines.append(f"- {h}")

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
            "*Generated by scripts/self_improvement/conversation_extractor.py*",
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
            "context_snippets": len(d.context_snippets),
            "action_items": len(d.action_items),
            "markdown_headings": len(d.markdown_headings),
            "tool_names_distinct": len(d.all_tools()),
        },
        "decisions": d.decisions,
        "learnings": d.learnings,
        "context_snippets": d.context_snippets,
        "action_items": d.action_items,
        "markdown_headings": d.markdown_headings,
        "topic_terms": d.topic_terms,
        "topic_phrases": d.topic_phrases,
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


def _raw_for_headings(path: Path) -> str | None:
    suf = path.suffix.lower()
    if suf not in {".md", ".markdown"} and not path.name.lower().endswith(".md"):
        return None
    return strip_markdown_frontmatter(read_text(path))


def run_extraction(session_path: Path, memory_dir: Path, workspace_root: Path) -> tuple[Path, Path]:
    segments = parse_session_log(session_path.resolve())
    raw_md = _raw_for_headings(session_path)
    digest = analyze_segments(segments, session_path.resolve().as_posix(), raw_markdown=raw_md)

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


def aggregate_topic_maps(digests: list[ConversationDigest]) -> dict[str, Any]:
    """Merge topic signals across transcripts to surface cross-session themes."""
    global_uni: Counter[str] = Counter()
    global_bi: Counter[str] = Counter()
    learnings: Counter[str] = Counter()
    decisions: Counter[str] = Counter()

    for d in digests:
        for w, c in d.topic_terms:
            global_uni[w] += c
        for p, c in d.topic_phrases:
            global_bi[p] += c
        for x in d.learnings:
            learnings[x] += 1
        for x in d.decisions:
            decisions[x] += 1

    return {
        "sources": [d.source for d in digests],
        "cross_session_topic_terms": global_uni.most_common(50),
        "cross_session_topic_phrases": global_bi.most_common(40),
        "recurring_learnings": learnings.most_common(30),
        "recurring_decisions": decisions.most_common(30),
    }


def render_aggregate_markdown(meta: dict[str, Any], generated_at: str) -> str:
    lines = [
        "# Conversation aggregate (cross-session)",
        "",
        f"- **Generated (UTC)**: {generated_at}",
        f"- **Transcripts merged**: {len(meta.get('sources', []))}",
        "",
        "## Recurring topic terms",
    ]
    for w, c in meta.get("cross_session_topic_terms", []):
        lines.append(f"- `{w}` — score {c}")
    if not meta.get("cross_session_topic_terms"):
        lines.append("- *(none)*")

    lines.extend(["", "## Recurring phrases (bigrams)"])
    for p, c in meta.get("cross_session_topic_phrases", []):
        lines.append(f"- {p} — score {c}")

    lines.extend(["", "## Recurring learnings (exact-line frequency)"])
    for t, c in meta.get("recurring_learnings", []):
        lines.append(f"- ({c}x) {t}")

    lines.extend(["", "## Recurring decisions (exact-line frequency)"])
    for t, c in meta.get("recurring_decisions", []):
        lines.append(f"- ({c}x) {t}")

    lines.extend(["", "## Sources"])
    for s in meta.get("sources", []):
        lines.append(f"- `{s}`")

    lines.extend(["", "---", "*Aggregate from scripts/self_improvement/conversation_extractor.py*"])
    return "\n".join(lines) + "\n"


def write_aggregate(
    meta: dict[str, Any],
    memory_dir: Path,
    tag: str,
) -> tuple[Path, Path]:
    memory_dir.mkdir(parents=True, exist_ok=True)
    gen = datetime.now(timezone.utc).isoformat()
    md_path = memory_dir / f"conversation_aggregate_{tag}.md"
    js_path = memory_dir / f"conversation_aggregate_{tag}.json"
    payload = {"generated_at_utc": gen, **meta}
    md_path.write_text(render_aggregate_markdown(meta, gen), encoding="utf-8")
    js_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return md_path, js_path


_KNOWN_TRANSCRIPT_SUFFIXES = frozenset({".json", ".jsonl", ".md", ".markdown", ".log", ".txt"})


def iter_transcript_files(root: Path, glob_pat: str) -> list[Path]:
    """Glob transcripts. Pattern ``*`` means any file with a known transcript suffix in ``root``."""
    if glob_pat.strip() == "*":
        paths = [p for p in root.iterdir() if p.is_file() and p.suffix.lower() in _KNOWN_TRANSCRIPT_SUFFIXES]
    else:
        paths = [p for p in root.glob(glob_pat) if p.is_file()]
    return sorted(paths, key=lambda p: p.name.lower())


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Extract decisions, topics, and learnings from session transcripts into memory/"
    )
    p.add_argument(
        "session_log",
        type=Path,
        nargs="?",
        help="Path to session transcript (.json, .jsonl, .md, .log, or text). Omit with --stdin or --input-dir.",
    )
    p.add_argument(
        "--stdin",
        action="store_true",
        help="Read transcript JSON/text from stdin (temp file under memory/).",
    )
    p.add_argument(
        "--input-dir",
        type=Path,
        default=None,
        help="Directory of transcripts; combine with --glob (writes per-file + aggregate).",
    )
    p.add_argument(
        "--glob",
        dest="glob_pat",
        default="*",
        help='Glob under --input-dir (default: "*" = known suffixes .json/.jsonl/.md/.log/.txt).',
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

        md_path, json_path = run_extraction(path, memory_dir, ws)
        try:
            path.unlink(missing_ok=True)  # type: ignore[arg-type]
        except OSError:
            pass

        print(f"Wrote {md_path.as_posix()}")
        print(f"Wrote {json_path.as_posix()}")
        return 0

    if args.input_dir:
        root = args.input_dir.resolve()
        if not root.is_dir():
            sys.stderr.write(f"error: not a directory: {root}\n")
            return 2
        files = iter_transcript_files(root, args.glob_pat)
        if not files:
            sys.stderr.write(f"error: no files matched {args.glob_pat!r} under {root}\n")
            return 2
        full_digests: list[ConversationDigest] = []
        for fp in files:
            md_path, json_path = run_extraction(fp, memory_dir, ws)
            print(f"Wrote {md_path.as_posix()}")
            print(f"Wrote {json_path.as_posix()}")
            segs = parse_session_log(fp)
            raw_md = _raw_for_headings(fp)
            d = analyze_segments(segs, fp.as_posix(), raw_markdown=raw_md)
            rel = fp.resolve().as_posix()
            ws_pos = ws.resolve().as_posix()
            if rel.startswith(ws_pos):
                d.source = Path(rel[len(ws_pos) :].lstrip("/")).as_posix()
            full_digests.append(d)
        agg = aggregate_topic_maps(full_digests)
        tag = utc_stamp()
        am, aj = write_aggregate(agg, memory_dir, tag)
        print(f"Wrote {am.as_posix()}")
        print(f"Wrote {aj.as_posix()}")
        return 0

    if not args.session_log:
        sys.stderr.write("error: session_log path required unless --stdin or --input-dir is set.\n")
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
