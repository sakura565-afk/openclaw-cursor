#!/usr/bin/env python3
"""Extract decisions, learnings, and tool-usage highlights from OpenClaw session transcripts."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Any, Iterable, Literal

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

_FAILURE_STAT_FALSE_POS = re.compile(
    r"(?i)['\"]failed['\"]\s*:\s*(0|false|null)\b|"
    r"\bstats?\s*=\s*\{[^}]*['\"]failed['\"]\s*:\s*0"
)


def _scrub_failure_false_positives(text: str) -> str:
    """Remove benign `failed: 0` JSON/log fragments so heuristics stay precise."""

    return _FAILURE_STAT_FALSE_POS.sub("", text)


FAILURE_SIGNALS = re.compile(
    r"(?i)(\b(traceback|exception|errno|syntaxerror|typeerror|valueerror|"
    r"attributeerror|referenceerror|failed|failure|fatal|critical)\b|"
    r"\berror\s*[:#]|exit\s*code\s*[1-9]|timed?\s*out|does\s+not\s+work|"
    r"doesn't\s+work|\b404\b|\b500\b)"
)

SUCCESS_SIGNALS = re.compile(
    r"(?i)(\b(resolved|fixed\s+(?:it|the)|works\s+now|all\s+(?:tests?\s+)?pass|"
    r"tests?\s+pass(?:ed|ing)?|green\b|succeeded|completed\s+successfully|"
    r"patch\s+applied|deployed\s+ok)\b)"
)

USER_CORRECTION_SIGNALS = re.compile(
    r"(?i)(\b(actually|not\s+quite|that'?s\s+wrong|incorrect|instead\s+of|"
    r"should\s+be|correction:|rewriting|disagree|misunderstood|"
    r"i\s+meant|no,\s+|prefer\s+)\b)"
)

IMPROVEMENT_SIGNALS = re.compile(
    r"(?i)(\b(should\s+have|next\s+time|avoid\s+|better\s+to|"
    r"improvement:|could\s+have|would\s+be\s+nicer)\b)"
)

DEFAULT_TRANSCRIPT_SUFFIXES = frozenset({".json", ".log", ".txt", ".md"})
MAX_TRANSCRIPT_BYTES = 5 * 1024 * 1024

CategoryName = Literal["success", "failure", "learning_moment", "improvement_opportunity"]

ALL_CATEGORIES: tuple[CategoryName, ...] = (
    "success",
    "failure",
    "learning_moment",
    "improvement_opportunity",
)


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")


def default_openclaw_workspace() -> Path:
    return Path.home() / ".openclaw" / "workspace"


def default_insights_path() -> Path:
    return repo_root() / "scripts" / "conversation_insights.md"


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


# -----------------------------------------------------------------------------
# Self-improvement: categorized exchanges → scripts/conversation_insights.md
# -----------------------------------------------------------------------------


def exchange_tags_for_text(role: str | None, text: str) -> list[str]:
    tags: list[str] = []
    rl = (role or "").lower()
    scrubbed = _scrub_failure_false_positives(text)
    if FAILURE_SIGNALS.search(scrubbed) or (
        rl == "tool_output"
        and (
            re.search(r"(?i)\b(error|stderr|non-zero)\b", scrubbed)
            or re.search(r"(?i)\bfailed\b", scrubbed)
        )
    ):
        tags.append("error_fix")
    if rl == "tool" or extract_tool_signals(text):
        tags.append("tool_usage_pattern")
    if match_patterns(text, DECISION_LINE_PATTERNS):
        tags.append("decision_point")
    if rl == "user" and USER_CORRECTION_SIGNALS.search(text):
        tags.append("user_correction")
    if tags:
        return list(dict.fromkeys(tags))
    # weak defaults for substantive assistant/user prose
    if len(text) >= 120 and rl in {"user", "assistant", "agent", ""}:
        if IMPROVEMENT_SIGNALS.search(text):
            tags.append("improvement_note")
        elif match_patterns(text, LEARNING_LINE_PATTERNS):
            tags.append("learning_cue")
    return list(dict.fromkeys(tags))


def classify_exchange(category_text: str, roles_involved: set[str]) -> CategoryName:
    """Pick a primary self-improvement category for a merged exchange."""

    scrubbed = _scrub_failure_false_positives(category_text)
    fail = bool(FAILURE_SIGNALS.search(scrubbed))
    ok = bool(SUCCESS_SIGNALS.search(category_text))
    learn = bool(match_patterns(category_text, LEARNING_LINE_PATTERNS)) or bool(
        re.search(r"(?i)\b(lesson\s+learned|takeaway|TIL|insight)\b", category_text)
    )
    improve = bool(IMPROVEMENT_SIGNALS.search(category_text)) or bool(
        USER_CORRECTION_SIGNALS.search(category_text)
    )
    user_spoke = "user" in roles_involved

    if fail and ok:
        return "learning_moment"
    if fail:
        return "failure"
    if ok:
        return "success"
    if learn:
        return "learning_moment"
    if improve or (user_spoke and USER_CORRECTION_SIGNALS.search(category_text)):
        return "improvement_opportunity"
    if match_patterns(category_text, DECISION_LINE_PATTERNS):
        return "learning_moment"
    return "learning_moment"


@dataclass
class ExtractedExchange:
    source_path: str
    turn: int
    category: CategoryName
    tags: list[str]
    summary: str
    excerpt: str


def _snippet(text: str, limit: int = 320) -> str:
    one = normalize_ws(text)
    if len(one) <= limit:
        return one
    return one[: limit - 3] + "..."


def exchanges_from_segments(
    segments: list[tuple[int, str | None, str]],
    source_display: str,
) -> list[ExtractedExchange]:
    """Turn parsed segments into categorized exchanges (pair-aware)."""

    out: list[ExtractedExchange] = []
    if not segments:
        return out

    prev: tuple[int, str | None, str] | None = None

    for i, (turn, role, text) in enumerate(segments):
        if not text.strip():
            prev = (turn, role, text)
            continue

        rl = (role or "").lower()
        if rl == "user" and i + 1 < len(segments):
            _nt, next_role, next_txt = segments[i + 1]
            nrl = (next_role or "").lower()
            if next_txt.strip() and nrl in {"assistant", "agent", "tool", "tool_output", ""}:
                prev = (turn, role, text)
                continue

        tags = exchange_tags_for_text(role, text)
        roles_single = {r for r in {rl} if r}

        merged_text = text
        merged_roles = set(roles_single)
        if prev:
            pt, pr, ptxt = prev
            prl = (pr or "").lower()
            if ptxt.strip():
                # User → assistant/tool pairs capture fuller context
                if prl == "user" and rl in {"assistant", "agent", "tool", "", "tool_output"}:
                    merged_text = f"[user]: {normalize_ws(ptxt)}\n[reply]: {normalize_ws(text)}"
                    merged_roles.add("user")
                    merged_roles.update({rl} if rl else set())
                    tags = list(
                        dict.fromkeys(exchange_tags_for_text(pr, ptxt) + exchange_tags_for_text(role, text))
                    )
                elif prl in {"assistant", "agent"} and rl == "user":
                    merged_text = f"[assistant]: {normalize_ws(ptxt)}\n[user]: {normalize_ws(text)}"
                    merged_roles.update({"assistant", "user"})
                    tags = list(
                        dict.fromkeys(exchange_tags_for_text(pr, ptxt) + exchange_tags_for_text(role, text))
                    )

        if not tags:
            prev = (turn, role, text)
            continue

        cat = classify_exchange(merged_text, merged_roles)
        summary_bits = [t.replace("_", " ") for t in tags[:3]]
        summary = ", ".join(summary_bits) if summary_bits else "exchange"

        out.append(
            ExtractedExchange(
                source_path=source_display,
                turn=turn,
                category=cat,
                tags=tags[:12],
                summary=summary,
                excerpt=_snippet(merged_text, 400),
            )
        )
        prev = (turn, role, text)

    return out


def parse_date_range(spec: str | None) -> tuple[datetime | None, datetime | None]:
    """Inclusive UTC date range. Accepted: `YYYY-MM-DD:YYYY-MM-DD` or `YYYY-MM-DD..YYYY-MM-DD`."""

    if not spec or not spec.strip():
        return None, None
    s = spec.strip()
    sep = None
    if ".." in s:
        sep = ".."
    elif ":" in s:
        sep = ":"
    if sep is None:
        raise ValueError("date range must look like START:END or START..END (YYYY-MM-DD)")
    a, b = s.split(sep, 1)
    d0 = datetime.strptime(a.strip(), "%Y-%m-%d").replace(tzinfo=timezone.utc)
    d1 = datetime.strptime(b.strip(), "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end = datetime.combine(d1.date(), time(23, 59, 59, tzinfo=timezone.utc))
    return d0, end


def discover_transcript_files(
    roots: Iterable[Path],
    *,
    session_substring: str | None,
    date_start: datetime | None,
    date_end: datetime | None,
    suffixes: frozenset[str] = DEFAULT_TRANSCRIPT_SUFFIXES,
) -> list[Path]:
    files: list[Path] = []
    seen: set[Path] = set()
    needle = session_substring.lower().strip() if session_substring else None

    for root in roots:
        root = root.expanduser().resolve()
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix.lower() not in suffixes:
                continue
            try:
                st = path.stat()
            except OSError:
                continue
            if st.st_size > MAX_TRANSCRIPT_BYTES:
                continue
            mtime = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)
            if date_start is not None and mtime < date_start:
                continue
            if date_end is not None and mtime > date_end:
                continue
            if needle and needle not in path.as_posix().lower():
                continue
            rp = path.resolve()
            if rp in seen:
                continue
            seen.add(rp)
            files.append(path)

    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return files


def render_insights_document(
    exchanges: list[ExtractedExchange],
    *,
    roots_scanned: list[str],
    files_scanned: int,
    filters_note: str,
) -> str:
    generated = datetime.now(timezone.utc).isoformat()
    by_cat: dict[CategoryName, list[ExtractedExchange]] = {c: [] for c in ALL_CATEGORIES}
    for ex in exchanges:
        by_cat[ex.category].append(ex)

    counts = {c: len(by_cat[c]) for c in ALL_CATEGORIES}

    lines = [
        "# Conversation insights",
        "",
        "Structured exchanges mined for self-improvement (error fixes, tool patterns,"
        " decisions, user corrections).",
        "",
        f"- **Generated (UTC)**: {generated}",
        f"- **Transcript files scanned**: {files_scanned}",
        f"- **Roots**: {', '.join(f'`{r}`' for r in roots_scanned) if roots_scanned else '`none`'}",
        f"- **Filters**: {filters_note}",
        "",
        "## Summary",
        "",
    ]
    for c in ALL_CATEGORIES:
        lines.append(f"- **{c.replace('_', ' ')}**: {counts[c]}")
    lines.append("")

    for cat in ALL_CATEGORIES:
        bucket = by_cat[cat]
        title = cat.replace("_", " ").title()
        lines.extend([f"## {title}", ""])
        if not bucket:
            lines.append("*No items in this category for the current filters.*")
            lines.append("")
            continue
        for ex in bucket:
            lines.append(f"### `{ex.source_path}` — turn {ex.turn}")
            lines.append("")
            lines.append(f"- **Category**: `{ex.category}`")
            lines.append(f"- **Tags**: {', '.join(f'`{t}`' for t in ex.tags) if ex.tags else '`none`'}")
            lines.append(f"- **Summary**: {ex.summary}")
            lines.append("")
            lines.append("```text")
            lines.append(ex.excerpt)
            lines.append("```")
            lines.append("")

    lines.extend(
        [
            "---",
            "",
            "*Produced by `scripts/conversation_extractor.py`. Re-run after sessions to refresh.*",
            "",
        ]
    )
    return "\n".join(lines)


def run_batch_insights(
    *,
    roots: list[Path],
    explicit_files: list[Path] | None,
    session_filter: str | None,
    date_range: str | None,
    categories_allow: frozenset[CategoryName] | None,
    output_path: Path,
    workspace_root: Path,
) -> tuple[int, Path]:
    """Scan transcripts and write aggregated markdown."""

    d0, d1 = None, None
    if date_range:
        d0, d1 = parse_date_range(date_range)

    if explicit_files:
        paths: list[Path] = []
        needle = session_filter.lower().strip() if session_filter else None
        for raw in explicit_files:
            p = raw.expanduser().resolve()
            if not p.is_file():
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            if st.st_size > MAX_TRANSCRIPT_BYTES:
                continue
            mtime = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)
            if d0 is not None and mtime < d0:
                continue
            if d1 is not None and mtime > d1:
                continue
            if needle and needle not in p.as_posix().lower():
                continue
            paths.append(p)
    else:
        paths = discover_transcript_files(
            roots,
            session_substring=session_filter,
            date_start=d0,
            date_end=d1,
        )

    filters_note_parts: list[str] = []
    if session_filter:
        filters_note_parts.append(f"session path contains `{session_filter}`")
    if date_range:
        filters_note_parts.append(f"date range `{date_range}` (mtime UTC)")
    if categories_allow:
        filters_note_parts.append(
            "categories " + ", ".join(f"`{c}`" for c in sorted(categories_allow))
        )
    filters_note = "; ".join(filters_note_parts) if filters_note_parts else "none"

    all_exchanges: list[ExtractedExchange] = []
    ws_posix = workspace_root.resolve().as_posix()

    for path in paths:
        segments = parse_session_log(path)
        display = path.resolve().as_posix()
        if display.startswith(ws_posix):
            display = Path(display[len(ws_posix) :].lstrip("/")).as_posix()
        elif display.startswith(str(Path.home())):
            display = str(Path("~") / Path(display).relative_to(Path.home()))

        for ex in exchanges_from_segments(segments, display):
            if categories_allow is not None and ex.category not in categories_allow:
                continue
            all_exchanges.append(ex)

    # Stable sort: category order, then source, turn
    cat_order = {c: i for i, c in enumerate(ALL_CATEGORIES)}
    all_exchanges.sort(key=lambda e: (cat_order[e.category], e.source_path, e.turn))

    if explicit_files:
        scan_roots = sorted({p.parent.resolve().as_posix() for p in paths})
        if not scan_roots:
            scan_roots = [
                raw.expanduser().resolve().parent.as_posix()
                for raw in (explicit_files or [])
                if raw.expanduser().resolve().is_file()
            ]
    else:
        scan_roots = [r.expanduser().resolve().as_posix() for r in roots]

    doc = render_insights_document(
        all_exchanges,
        roots_scanned=scan_roots,
        files_scanned=len(paths),
        filters_note=filters_note,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(doc, encoding="utf-8")
    return len(paths), output_path


def parse_category_filter(raw: str | None) -> frozenset[CategoryName] | None:
    if not raw or not raw.strip():
        return None
    parts = [p.strip().lower().replace(" ", "_") for p in re.split(r"[,;]+", raw) if p.strip()]
    allowed = set(ALL_CATEGORIES)
    out: list[CategoryName] = []
    for p in parts:
        if p not in allowed:
            raise ValueError(
                f"unknown category `{p}`; expected one of: {', '.join(ALL_CATEGORIES)}"
            )
        out.append(p)  # type: ignore[arg-type]
    return frozenset(out)


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Extract OpenClaw conversation highlights and self-improvement insights "
            "from session transcripts."
        )
    )
    p.add_argument(
        "session_log",
        type=Path,
        nargs="?",
        help="Optional path to one transcript (.json, .log, or text). If omitted, scans default roots.",
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
        help="Destination for per-session timestamped extracts (default: <repo>/memory).",
    )
    p.add_argument(
        "--workspace-root",
        type=Path,
        default=None,
        help="Workspace root for relative paths in output (defaults to repo root).",
    )
    p.add_argument(
        "--session",
        type=str,
        default=None,
        metavar="SUBSTRING",
        help="Only include transcript files whose path contains this substring (case-insensitive).",
    )
    p.add_argument(
        "--date-range",
        type=str,
        default=None,
        metavar="START:END",
        help="Filter transcripts by file mtime (UTC), inclusive: YYYY-MM-DD:YYYY-MM-DD or YYYY-MM-DD..YYYY-MM-DD.",
    )
    p.add_argument(
        "--category",
        type=str,
        default=None,
        metavar="LIST",
        help=(
            "Comma-separated categories to include in the aggregated insights report: "
            "success, failure, learning_moment, improvement_opportunity."
        ),
    )
    p.add_argument(
        "--transcript-root",
        type=Path,
        action="append",
        default=None,
        help=(
            "Extra directory to scan for transcripts (repeatable). "
            "Defaults include ~/.openclaw/workspace/memory and .../sessions."
        ),
    )
    p.add_argument(
        "--output",
        type=Path,
        default=None,
        help=f"Aggregated insights markdown (default: {default_insights_path().as_posix()}).",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    ws = (args.workspace_root or repo_root()).resolve()
    memory_dir = (args.memory_dir or ws / "memory").resolve()
    insights_out = (args.output or default_insights_path()).resolve()

    try:
        cats = parse_category_filter(args.category)
        if args.date_range:
            parse_date_range(args.date_range)
    except ValueError as e:
        sys.stderr.write(f"error: {e}\n")
        return 2

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

    if args.session_log:
        sp = args.session_log.resolve()
        if not sp.is_file():
            sys.stderr.write(f"error: not an existing file: {sp}\n")
            return 2

        n, outp = run_batch_insights(
            roots=[],
            explicit_files=[sp],
            session_filter=args.session,
            date_range=args.date_range,
            categories_allow=cats,
            output_path=insights_out,
            workspace_root=ws,
        )
        print(f"Scanned {n} transcript file(s); wrote {outp.as_posix()}")
        return 0

    ocw = default_openclaw_workspace()
    roots: list[Path] = [ocw / "memory", ocw / "sessions"]
    if args.transcript_root:
        roots.extend(args.transcript_root)

    n, outp = run_batch_insights(
        roots=roots,
        explicit_files=None,
        session_filter=args.session,
        date_range=args.date_range,
        categories_allow=cats,
        output_path=insights_out,
        workspace_root=ws,
    )
    print(f"Scanned {n} transcript file(s); wrote {outp.as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
