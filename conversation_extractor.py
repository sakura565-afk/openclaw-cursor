#!/usr/bin/env python3
"""
Extract decisions, corrections, learnings, and reusable snippets from conversation transcripts.

Supports plain text logs (role-prefixed lines, turn markers), JSON session exports
(OpenAI-style messages, nested conversation/history keys), and markdown-heavy transcripts.
Outputs JSON and markdown suitable for memory stores.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

# ---------------------------------------------------------------------------
# Parsing: turn markers and role-prefixed lines (multiple transcript styles)
# ---------------------------------------------------------------------------

TURN_PATTERN = re.compile(r"(?i)\b(?:turn|step|message)\s*[:#\-]?\s*(\d+)\b")

ROLE_LINE = re.compile(
    r"^\s*(?P<role>user|human|assistant|agent|tool|system|model)\s*[:|>\-]+\s*(?P<body>.+)$",
    re.IGNORECASE,
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


def _read_path(path: Path, encoding: str) -> str:
    return path.read_text(encoding=encoding, errors="replace")


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
        if isinstance(inner, dict) and not txt.strip():
            try:
                snippet = json.dumps(inner, ensure_ascii=False)[:800]
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


def _segments_from_messages(messages: list[Any]) -> list[tuple[int, str | None, str]]:
    segments: list[tuple[int, str | None, str]] = []
    for i, raw in enumerate(messages):
        if not isinstance(raw, dict):
            continue
        turn = _infer_turn(raw, i)
        role = raw.get("role") or raw.get("speaker") or raw.get("from")
        rl = role.lower().strip() if isinstance(role, str) else ""
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
            if len(s) >= 120:
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
            for key in STRUCT_TOOL_KEYS:
                if key in node:
                    for tn in _tool_names_from_calls(node[key]):
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


def parse_json_transcript(raw: str) -> list[tuple[int, str | None, str]]:
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


def parse_plain_transcript(raw: str) -> list[tuple[int, str | None, str]]:
    segments: list[tuple[int, str | None, str]] = []
    current_turn = 1
    for line_number, line in enumerate(raw.splitlines(), start=1):
        m_turn = TURN_PATTERN.search(line)
        if m_turn:
            current_turn = int(m_turn.group(1))
        else:
            current_turn = max(current_turn, line_number)
        m_role = ROLE_LINE.match(line)
        if m_role:
            rl = m_role.group("role").lower()
            if rl == "human":
                rl = "user"
            segments.append((current_turn, rl, m_role.group("body").strip()))
        else:
            stripped = line.strip()
            if stripped:
                segments.append((current_turn, None, stripped))
    return segments


def parse_transcript_bytes(content: str, suffix: str) -> list[tuple[int, str | None, str]]:
    suf = suffix.lower()
    if suf == ".json":
        segs = parse_json_transcript(content)
        if segs:
            return segs
    return parse_plain_transcript(content)


def parse_transcript_path(path: Path, encoding: str = "utf-8") -> list[tuple[int, str | None, str]]:
    if not path.exists():
        return []
    raw = _read_path(path, encoding)
    segs = parse_transcript_bytes(raw, path.suffix)
    if path.suffix.lower() == ".json" and not segs:
        return parse_plain_transcript(raw)
    return segs


def normalize_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


# ---------------------------------------------------------------------------
# Pattern sets: decisions, learnings, corrections
# ---------------------------------------------------------------------------

DECISION_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"^\s*(?:decision|resolution|resolved|outcome)\s*[:\-—]\s*(.+)",
        r"^\s*\*{0,2}(?:decision|resolution)\*{0,2}\s*[:\-—]\s*(.+)",
        r"(?:we(?:'ve)?\s+(?:decided|agreed|chose)|let'?s\s+go\s+with|final(?:ly)?\s*:\s*)(.+)",
        r"(?:\bapproved\b|\bfinalize[ds]?\b|\bchosen\b\s+(?:approach|option|path))\s*[:\-]?\s*(.+)",
        r"^\s*(?:TL;DR|TDLR|takeaway)s?\s*[:\-—]\s*(.+)",
        r"\b(?:concluded|conclusion)\s+(?:that\s+)?(.{10,})",
        r"\bdecision\s*[:\-—]\s*(.+)",
        r"^\s*(?:agreed|locked\s+in|sign-?off)\s*[:\-—]\s*(.+)",
    )
)

LEARNING_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"^\s*(?:learning|lesson|takeaway|insight|key\s+learning)\s*[:\-—]\s*(.+)",
        r"^\s*\*{0,2}(?:important|remember|note)\*{0,2}\s*[:\-—]\s*(.+)",
        r"(?:核心价值|关键点|经验教训|结论是|需要注意的是)\s*[：:]\s*(.+)",
        r"\b(?:going\s+forward|best\s+practice|rule\s+of\s+thumb)\s*[:\-—]?\s*(.{15,})",
    )
)

CORRECTION_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"^\s*(?:correction|erratum|fix(?:ed)?|update)\s*[:\-—]\s*(.+)",
        r"\b(?:actually|instead|rather\s+than|not\s+\w+\s+but)\b[^.]{0,12}\b(wrong|incorrect|mistake|error)\b[.:]?\s*(.{10,})",
        r"\b(?:that\s+was\s+wrong|my\s+mistake|I\s+(?:was\s+)?wrong)\b[.:]?\s*(.{8,})",
        r"\b(?:replace|use)\s+`[^`]+`\s+(?:with|by)\s+`[^`]+`",
        r"\b(?:revert|undo)\s+(?:the\s+)?(?:change|commit|approach)\b[^.]{0,80}\.",
        r"(?:修正|更正|不对|应该是)[：:]\s*(.+)",
        r"\b(?:scratch\s+that|ignore\s+(?:the\s+)?(?:above|previous))\b[.:]?\s*(.{8,})",
    )
)


def _capture_from_match(m: re.Match[str]) -> str:
    if m.lastindex:
        parts: list[str] = []
        for i in range(1, m.lastindex + 1):
            g = m.group(i)
            if g and str(g).strip():
                parts.append(str(g).strip())
        if parts:
            return normalize_ws(" ".join(parts))
    return normalize_ws(m.group(0))


def _match_pattern_lines(
    text: str,
    patterns: tuple[re.Pattern[str], ...],
    category: str,
    turn: int,
    role: str | None,
) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for line in text.splitlines():
        line_st = line.strip()
        if not line_st:
            continue
        for pat in patterns:
            m = pat.search(line_st)
            if m:
                cap = _capture_from_match(m)
                if len(cap) > 500:
                    cap = cap[:497] + "..."
                hits.append(
                    {
                        "category": category,
                        "text": cap,
                        "turn": turn,
                        "role": role,
                        "pattern": pat.pattern[:120],
                    }
                )
                break
    compact = normalize_ws(text.replace("\n", " "))
    for pat in patterns:
        if pat.pattern.startswith("^"):
            continue
        m = pat.search(compact)
        if m:
            cap = _capture_from_match(m)
            if len(cap) > 400:
                cap = cap[:397] + "..."
            hits.append(
                {
                    "category": category,
                    "text": cap,
                    "turn": turn,
                    "role": role,
                    "pattern": pat.pattern[:120],
                    "context": "inline",
                }
            )
            break
    return hits


FENCED_CODE = re.compile(
    r"```\s*([^\n`]*)\n(.*?)```",
    re.DOTALL | re.IGNORECASE,
)

SHELL_LINE = re.compile(
    r"^\s*(?:\$|>%?)\s*(.+)$",
)
INLINE_SHELLISH = re.compile(
    r"\b(?:git|npm|pnpm|yarn|pip|pip3|docker|kubectl|curl|wget|ssh|cd|export|source)\s+[^\n`]{3,200}",
    re.IGNORECASE,
)

CONFIG_FENCE_LANG = frozenset(
    {
        "yaml",
        "yml",
        "json",
        "toml",
        "ini",
        "cfg",
        "conf",
        "config",
        "env",
        "properties",
        "xml",
        "nginx",
        "dockerfile",
    }
)


def _snippet_fingerprint(content: str, kind: str) -> str:
    h = hashlib.sha256(f"{kind}\n{content}".encode("utf-8", errors="replace")).hexdigest()[:16]
    return h


def _extract_snippets_from_text(
    text: str,
    turn: int,
    role: str | None,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen_fp: set[str] = set()

    def add(kind: str, content: str, language: str | None = None, meta: dict[str, Any] | None = None) -> None:
        c = content.strip()
        if len(c) < 2:
            return
        fp = _snippet_fingerprint(c, kind)
        if fp in seen_fp:
            return
        seen_fp.add(fp)
        row: dict[str, Any] = {
            "kind": kind,
            "content": c if len(c) <= 8000 else c[:7997] + "\n... [truncated]",
            "turn": turn,
            "role": role,
            "fingerprint": fp,
        }
        if language:
            row["language_or_hint"] = language
        if meta:
            row.update(meta)
        out.append(row)

    for m in FENCED_CODE.finditer(text):
        lang = (m.group(1) or "").strip().lower() or None
        body = m.group(2) or ""
        lang_base = lang.split()[0] if lang else ""
        if lang_base in CONFIG_FENCE_LANG or (lang_base in {"", "text", "plaintext"} and _looks_like_config(body)):
            add("config", body, lang or "text", {"detected_as": "fenced_block"})
        else:
            add("code", body, lang or "text", {"detected_as": "fenced_block"})

    for line in text.splitlines():
        sm = SHELL_LINE.match(line)
        if sm:
            cmd = sm.group(1).strip()
            if len(cmd) >= 3:
                add("command", cmd, "shell", {"detected_as": "prompt_line"})
        else:
            for im in INLINE_SHELLISH.finditer(line):
                frag = im.group(0).strip()
                if len(frag) >= 5:
                    add("command", frag, "shell", {"detected_as": "inline_token"})

    return out


def _extract_fenced_snippets_globally(
    segments: list[tuple[int, str | None, str]],
) -> list[dict[str, Any]]:
    """Find fenced code blocks that span multiple plain-text segments (no role on each line)."""
    chunks: list[str] = []
    ranges: list[tuple[int, int, int, str | None]] = []
    pos = 0
    sep = "\n\n"
    first = True
    for turn, role, text in segments:
        if not text.strip():
            continue
        if not first:
            pos += len(sep)
        first = False
        start = pos
        chunks.append(text)
        pos += len(text)
        ranges.append((start, pos, turn, role))
    if not chunks:
        return []
    blob = sep.join(chunks)

    def locate(off: int) -> tuple[int, str | None]:
        for s, e, t, r in ranges:
            if s <= off < e:
                return t, r
        return ranges[-1][2], ranges[-1][3]

    out: list[dict[str, Any]] = []
    for m in FENCED_CODE.finditer(blob):
        lang = (m.group(1) or "").strip().lower() or None
        body = m.group(2) or ""
        turn, role = locate(m.start())
        wrapped = f"```{lang or ''}\n{body}\n```"
        out.extend(_extract_snippets_from_text(wrapped, turn, role))
    return out


def _looks_like_config(body: str) -> bool:
    if re.search(r"^[\w\-]+\s*:\s*\S+", body, re.MULTILINE):
        return True
    if re.search(r"^\s*[\[{]", body) and re.search(r"^\s*[\]}]", body, re.MULTILINE):
        return True
    if "=" in body and re.search(r"^\s*\w+\s*=", body, re.MULTILINE):
        return True
    return False


@dataclass
class ConversationExtractor:
    """
    Loads a transcript from a path, raw string, or pre-parsed segment list, then runs extraction.
    Segments are (turn, role, text) tuples; role may be None for undifferentiated lines.
    """

    segments: list[tuple[int, str | None, str]] = field(default_factory=list)
    source_label: str = field(default="inline")
    encoding: str = field(default="utf-8")
    parsed_at_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @classmethod
    def from_path(cls, path: str | Path, encoding: str = "utf-8") -> ConversationExtractor:
        p = Path(path)
        segs = parse_transcript_path(p, encoding=encoding)
        return cls(segments=segs, source_label=p.resolve().as_posix(), encoding=encoding)

    @classmethod
    def from_string(cls, content: str, format_hint: str = ".txt") -> ConversationExtractor:
        segs = parse_transcript_bytes(content, format_hint)
        return cls(segments=segs, source_label="string")

    @classmethod
    def from_messages(cls, messages: list[dict[str, Any]]) -> ConversationExtractor:
        segs = _segments_from_messages(messages)
        return cls(segments=segs, source_label="messages:list")

    def _iter_assistant_user_text(self) -> Iterable[tuple[int, str | None, str]]:
        for turn, role, text in self.segments:
            rl = (role or "").lower()
            if rl == "tool":
                continue
            yield turn, role, text

    def extract_decisions(self) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        for turn, role, text in self._iter_assistant_user_text():
            rl = (role or "").lower()
            if rl in {"user", "assistant", "agent", "system", ""} or role is None:
                found.extend(_match_pattern_lines(text, DECISION_PATTERNS, "decision", turn, role))
        return _dedupe_records(found, key_fields=("text", "turn"))

    def extract_corrections(self) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        for turn, role, text in self.segments:
            rl = (role or "").lower()
            if rl in {"user", "assistant", "agent", "system", "tool_output", ""} or role is None:
                found.extend(_match_pattern_lines(text, CORRECTION_PATTERNS, "correction", turn, role))
        return _dedupe_records(found, key_fields=("text", "turn"))

    def extract_snippets(self) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        for turn, role, text in self.segments:
            if not text.strip():
                continue
            found.extend(_extract_snippets_from_text(text, turn, role))
        found.extend(_extract_fenced_snippets_globally(self.segments))
        return _dedupe_records(found, key_fields=("fingerprint",))

    def extract_learnings(self) -> list[dict[str, Any]]:
        """Structured learnings; included in ``generate_summary`` and ``to_document``."""
        found: list[dict[str, Any]] = []
        for turn, role, text in self._iter_assistant_user_text():
            rl = (role or "").lower()
            if rl in {"user", "assistant", "agent", "system", ""} or role is None:
                found.extend(_match_pattern_lines(text, LEARNING_PATTERNS, "learning", turn, role))
        return _dedupe_records(found, key_fields=("text", "turn"))

    def _render_summary_markdown(
        self,
        decisions: list[dict[str, Any]],
        corrections: list[dict[str, Any]],
        learnings: list[dict[str, Any]],
        snippets: list[dict[str, Any]],
    ) -> str:
        code_n = sum(1 for s in snippets if s["kind"] == "code")
        cmd_n = sum(1 for s in snippets if s["kind"] == "command")
        cfg_n = sum(1 for s in snippets if s["kind"] == "config")

        lines = [
            "# Conversation extraction summary",
            "",
            f"- **Source**: `{self.source_label}`",
            f"- **Parsed at (UTC)**: {self.parsed_at_utc}",
            f"- **Segment count**: {len(self.segments)}",
            f"- **Decisions**: {len(decisions)} | **Corrections**: {len(corrections)} | **Learnings**: {len(learnings)}",
            f"- **Snippets**: code={code_n}, commands={cmd_n}, configs={cfg_n}",
            "",
            "## Decisions",
        ]
        if decisions:
            for d in decisions[:80]:
                lines.append(f"- (turn {d['turn']}) {d['text']}")
        else:
            lines.append("- *(none detected from cue patterns)*")

        lines.extend(["", "## Corrections"])
        if corrections:
            for c in corrections[:80]:
                lines.append(f"- (turn {c['turn']}) {c['text']}")
        else:
            lines.append("- *(none detected from cue patterns)*")

        lines.extend(["", "## Learnings"])
        if learnings:
            for L in learnings[:80]:
                lines.append(f"- (turn {L['turn']}) {L['text']}")
        else:
            lines.append("- *(none detected from cue patterns)*")

        lines.extend(["", "## Notable snippets (sample)"])
        for s in snippets[:25]:
            preview = s["content"].splitlines()[0][:160] if s["content"] else ""
            lines.append(f"- **{s['kind']}** (turn {s['turn']}): `{preview}`")

        lines.append("")
        lines.append("---")
        lines.append("*Generated by `conversation_extractor.py` for durable memory storage.*")
        return "\n".join(lines) + "\n"

    def generate_summary(self) -> str:
        return self._render_summary_markdown(
            self.extract_decisions(),
            self.extract_corrections(),
            self.extract_learnings(),
            self.extract_snippets(),
        )

    def to_document(self) -> dict[str, Any]:
        """Single JSON-serializable document for memory pipelines."""
        decisions = self.extract_decisions()
        corrections = self.extract_corrections()
        learnings = self.extract_learnings()
        snippets = self.extract_snippets()
        summary = self._render_summary_markdown(decisions, corrections, learnings, snippets)
        return {
            "schema": "conversation_extractor.v1",
            "source": self.source_label,
            "parsed_at_utc": self.parsed_at_utc,
            "counts": {
                "segments": len(self.segments),
                "decisions": len(decisions),
                "corrections": len(corrections),
                "learnings": len(learnings),
                "snippets": len(snippets),
            },
            "decisions": decisions,
            "corrections": corrections,
            "learnings": learnings,
            "snippets": snippets,
            "summary_markdown": summary,
        }

    def export_json_markdown(
        self,
        json_path: str | Path,
        markdown_path: str | Path,
        *,
        indent: int = 2,
    ) -> tuple[Path, Path]:
        doc = self.to_document()
        jp = Path(json_path)
        mp = Path(markdown_path)
        jp.parent.mkdir(parents=True, exist_ok=True)
        mp.parent.mkdir(parents=True, exist_ok=True)
        payload = dict(doc)
        payload.pop("summary_markdown", None)
        jp.write_text(json.dumps(payload, indent=indent, ensure_ascii=False) + "\n", encoding="utf-8")
        mp.write_text(doc["summary_markdown"], encoding="utf-8")
        return jp, mp


def _dedupe_records(items: list[dict[str, Any]], key_fields: tuple[str, ...]) -> list[dict[str, Any]]:
    seen: set[tuple[Any, ...]] = set()
    out: list[dict[str, Any]] = []
    for it in items:
        key = tuple(it.get(k) for k in key_fields)
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


def _default_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")


def run_cli(session_path: Path, memory_dir: Path, workspace_root: Path | None = None) -> tuple[Path, Path]:
    ws = (workspace_root or Path.cwd()).resolve()
    memory_dir = memory_dir.resolve()
    ext = ConversationExtractor.from_path(session_path)
    rel = session_path.resolve().as_posix()
    if rel.startswith(ws.as_posix()):
        ext.source_label = Path(rel[len(ws.as_posix()) :].lstrip("/")).as_posix()

    stem = re.sub(r"[^\w\-_.]+", "_", session_path.stem).strip("_") or "session"
    parent = session_path.parent.name
    if parent and parent not in {".", ""}:
        stem = f"{parent}__{stem}"
    tag = _default_stamp()
    memory_dir.mkdir(parents=True, exist_ok=True)
    json_path = memory_dir / f"conversation_extract_{stem}_{tag}.json"
    md_path = memory_dir / f"conversation_extract_{stem}_{tag}.md"
    return ext.export_json_markdown(json_path, md_path)


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Extract decisions, corrections, learnings, and snippets from transcripts.",
    )
    p.add_argument(
        "session_log",
        type=Path,
        nargs="?",
        help="Transcript path (.json, .log, .txt, .md).",
    )
    p.add_argument(
        "--stdin",
        action="store_true",
        help="Read transcript from stdin (format hint via --format-hint).",
    )
    p.add_argument(
        "--format-hint",
        default=".txt",
        help="When using stdin, suffix hint for parser (default: .txt).",
    )
    p.add_argument(
        "--memory-dir",
        type=Path,
        default=None,
        help="Output directory (default: <cwd>/memory).",
    )
    p.add_argument(
        "--workspace-root",
        type=Path,
        default=None,
        help="Strip this prefix from source label in outputs.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    memory_dir = (args.memory_dir or Path.cwd() / "memory").resolve()
    ws = (args.workspace_root or Path.cwd()).resolve()

    if args.stdin:
        import tempfile

        memory_dir.mkdir(parents=True, exist_ok=True)
        payload = sys.stdin.read()
        tmp = tempfile.NamedTemporaryFile(
            delete=False,
            suffix=Path(args.format_hint).suffix or ".txt",
            prefix="stdin_session_",
            dir=str(memory_dir),
        )
        try:
            path = Path(tmp.name)
            path.write_text(payload, encoding="utf-8")
        finally:
            tmp.close()
        jp, mp = run_cli(path, memory_dir, ws)
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        print(f"Wrote {jp.as_posix()}")
        print(f"Wrote {mp.as_posix()}")
        return 0

    if not args.session_log:
        sys.stderr.write("error: session_log path required unless --stdin is set.\n")
        return 2
    sp = args.session_log.resolve()
    if not sp.exists():
        sys.stderr.write(f"error: file not found: {sp}\n")
        return 2
    jp, mp = run_cli(sp, memory_dir, ws)
    print(f"Wrote {jp.as_posix()}")
    print(f"Wrote {mp.as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
