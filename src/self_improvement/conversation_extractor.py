"""Extract patterns, insights, and learnings from conversation transcripts.

Supports multiple transcript formats (JSON messages, JSONL, markdown role blocks).
Uses deterministic heuristics so it runs without external LLM calls.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

# --- Regex bundles for analysis ------------------------------------------------

_SUCCESS_PHRASES = re.compile(
    r"(?:"
    r"successfully\s+(?:completed|fixed|resolved)|"
    r"(?:all\s+)?tests?\s+(?:pass|passed|green)|"
    r"(?:issue|bug|problem)\s+(?:is\s+)?(?:fixed|resolved)|"
    r"(?:done|complete)(?:d)?\.?\s*(?:The|$)|"
    r"works\s+(?:now|correctly)|"
    r"(?:patch|fix|change)\s+(?:has\s+been\s+)?(?:landed|applied|merged)|"
    r"(?:resolved|fixed)\s*[\.\!]|"
    r"(?:no\s+errors|build\s+passes|lint\s+clean)"
    r")",
    re.IGNORECASE,
)

_ERROR_PHRASES = re.compile(
    r"(?:"
    r"\b(?:traceback|syntaxerror|typeerror|valueerror|attributeerror|keyerror)\b|"
    r"(?:command\s+)?failed|exit\s+code\s*[1-9]|"
    r"\berror\s*[:|\[]|exception\s*:|"
    r"(?:does\s+not\s+work|still\s+broken|not\s+fixed)|"
    r"(?:sorry|my\s+mistake|incorrect|i\s+was\s+wrong)|"
    r"(?:unable\s+to|failed\s+to)\s+(?:connect|parse|read|write)|"
    r"(?:timeout|connection\s+refused|permission\s+denied)"
    r")",
    re.IGNORECASE,
)

_TOOL_SIGNAL = re.compile(
    r"(?:^|\n)\s*(?:tool\s*(?:use|call)|calling\s+tool|function\s*:|"
    r"\[?(?:bash|run_terminal_cmd|grep|read_file|write|edit)\]?)",
    re.IGNORECASE,
)

_STRUCTURE_ASSISTANT = re.compile(
    r"(?:^|\n)(?:#{1,3}\s+\S|(?:\d+\.\s+)|[-*]\s+\*\*|"
    r"(?:step\s*\d|first,|second,|finally,))",
    re.IGNORECASE | re.MULTILINE,
)

_THANKS_USER = re.compile(
    r"\b(?:thanks|thank\s+you|that\s+worked|perfect|great|helpful|exactly)\b",
    re.IGNORECASE,
)

_CODE_FENCE = re.compile(r"```[\w]*\n.*?```", re.DOTALL)

_STOPWORDS = frozenset(
    """
    a an the and or but if then else for to of in on at by from with as is was were
    be been being have has had do does did will would could should can may might must
    this that these those it its we you they i me my your our their what which who how
    when where why not no yes just like so very too also only into about over out up down
    please let make sure need want get got going way some any all each every both few more
    most such same than then there here after before once again now still even ever never
    """.split()
)


@dataclass
class TranscriptMessage:
    """One normalized message from a transcript."""

    role: str
    content: str
    index: int
    source_span: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        d = {"role": self.role, "content": self.content, "index": self.index}
        if self.source_span:
            d["source_span"] = self.source_span
        return d


@dataclass
class NamedInsight:
    """A single extracted insight with optional evidence snippets."""

    title: str
    detail: str
    category: str
    confidence: str  # "high" | "medium" | "low"
    evidence: List[str] = field(default_factory=list)
    message_indices: List[int] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "detail": self.detail,
            "category": self.category,
            "confidence": self.confidence,
            "evidence": self.evidence,
            "message_indices": self.message_indices,
        }


@dataclass
class ExtractionReport:
    """Full result of parsing and analysis."""

    format_detected: str
    parse_warnings: List[str]
    messages: List[TranscriptMessage]
    statistics: Dict[str, Any]
    successful_approaches: List[NamedInsight]
    error_patterns: List[NamedInsight]
    valuable_interactions: List[NamedInsight]
    recurring_themes: List[str]
    recommendations: List[str]
    raw_meta: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "format_detected": self.format_detected,
            "parse_warnings": self.parse_warnings,
            "messages": [m.as_dict() for m in self.messages],
            "statistics": self.statistics,
            "successful_approaches": [x.as_dict() for x in self.successful_approaches],
            "error_patterns": [x.as_dict() for x in self.error_patterns],
            "valuable_interactions": [x.as_dict() for x in self.valuable_interactions],
            "recurring_themes": self.recurring_themes,
            "recommendations": self.recommendations,
            "meta": self.raw_meta,
        }


def _normalize_role(role: str) -> str:
    r = role.strip().lower()
    if r in ("human", "client"):
        return "user"
    if r in ("ai", "bot", "agent", "model"):
        return "assistant"
    if r in ("user", "assistant", "system", "tool"):
        return r
    return r or "unknown"


def _strip_code_fences_for_words(text: str) -> str:
    return _CODE_FENCE.sub(" ", text)


def _word_tokens(text: str) -> List[str]:
    t = _strip_code_fences_for_words(text)
    return re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", t)


def _snippet(text: str, max_len: int = 220) -> str:
    one = re.sub(r"\s+", " ", text.strip())
    if len(one) <= max_len:
        return one
    return one[: max_len - 3] + "..."


# --- Parsers ------------------------------------------------------------------


def _try_parse_json_messages(raw: str) -> Optional[Tuple[str, List[Dict[str, str]], List[str]]]:
    warnings: List[str] = []
    stripped = raw.strip()
    if not stripped:
        return None

    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        return None

    messages_src: Any = None
    fmt = "json"

    if isinstance(data, list):
        messages_src = data
        fmt = "json_array"
    elif isinstance(data, dict):
        for key in ("messages", "conversation", "history", "turns"):
            if key in data and isinstance(data[key], list):
                messages_src = data[key]
                fmt = f"json_object[{key}]"
                break
        if messages_src is None:
            warnings.append("JSON object had no recognized messages array key.")
            return None

    if not isinstance(messages_src, list):
        return None

    out: List[Dict[str, str]] = []
    for i, item in enumerate(messages_src):
        if not isinstance(item, dict):
            warnings.append(f"Skipping non-object message at index {i}.")
            continue
        role = item.get("role") or item.get("speaker") or item.get("type") or "unknown"
        content = item.get("content")
        if content is None:
            parts = item.get("content_parts") or item.get("parts")
            if isinstance(parts, list):
                content = "\n".join(str(p) for p in parts)
            else:
                content = ""
        if not isinstance(content, str):
            content = str(content)
        out.append({"role": str(role), "content": content})

    if not out:
        warnings.append("JSON parsed but produced zero messages.")
        return None

    return fmt, out, warnings


def _try_parse_jsonl(raw: str) -> Optional[Tuple[str, List[Dict[str, str]], List[str]]]:
    warnings: List[str] = []
    lines = [ln for ln in raw.splitlines() if ln.strip()]
    if len(lines) < 2:
        return None

    parsed_rows: List[Dict[str, Any]] = []
    for ln in lines:
        try:
            parsed_rows.append(json.loads(ln))
        except json.JSONDecodeError:
            return None

    if not all(isinstance(x, dict) for x in parsed_rows):
        return None

    all_have_role = all("role" in x or "speaker" in x for x in parsed_rows)
    if not all_have_role:
        return None

    out: List[Dict[str, str]] = []
    for item in parsed_rows:
        role = item.get("role") or item.get("speaker") or "unknown"
        content = item.get("content", "")
        if not isinstance(content, str):
            content = str(content)
        out.append({"role": str(role), "content": content})

    return "jsonl", out, warnings


_MD_ROLE_LINE = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?P<role>user|human|assistant|ai|system|tool)\s*[:：]\s*$",
    re.IGNORECASE,
)


def _parse_markdown_blocks(raw: str) -> Tuple[str, List[Dict[str, str]], List[str]]:
    warnings: List[str] = []
    text = raw.replace("\r\n", "\n")
    lines = text.split("\n")
    messages: List[Dict[str, str]] = []
    current_role: Optional[str] = None
    buf: List[str] = []

    def flush() -> None:
        nonlocal current_role, buf
        if current_role is None:
            return
        body = "\n".join(buf).strip()
        if body:
            messages.append({"role": current_role, "content": body})
        buf = []

    for line in lines:
        m = _MD_ROLE_LINE.match(line.strip())
        if m:
            if current_role is None and buf:
                pre = "\n".join(buf).strip()
                if pre:
                    messages.append({"role": "system", "content": pre})
                buf = []
            flush()
            current_role = _normalize_role(m.group("role"))
            continue
        if current_role is None:
            buf.append(line)
        else:
            buf.append(line)

    flush()

    if not messages and text.strip():
        # Fallback: split on double newlines with inline **Role:**
        alt = re.split(
            r"(?im)^(?:#{0,6}\s*)?\*{0,2}(user|assistant|system|tool)\*{0,2}\s*[:：]\s*$",
            text,
        )
        if len(alt) > 1:
            messages = []
            it = iter(alt)
            lead = next(it, "")
            if lead.strip():
                messages.append({"role": "system", "content": lead.strip()})
            for chunk in zip(it, it):
                role_name, body = chunk
                messages.append({"role": _normalize_role(role_name), "content": body.strip()})

    if not messages:
        warnings.append("Markdown parser found no role-delimited blocks; trying plain split.")

    fmt = "markdown_roles"
    return fmt, messages, warnings


def _parse_plain_fallback(raw: str) -> Tuple[str, List[Dict[str, str]], List[str]]:
    """Last resort: treat entire file as one assistant message with context note."""
    warnings = ["Used fallback: single-block transcript (no structured roles detected)."]
    text = raw.strip()
    if not text:
        return "empty", [], ["Empty input."]
    return "plain_single", [{"role": "unknown", "content": text}], warnings


def parse_transcript(raw: str) -> Tuple[str, List[TranscriptMessage], List[str]]:
    """Detect format and return (format_name, messages, warnings)."""

    warnings: List[str] = []

    trial = _try_parse_json_messages(raw)
    if trial:
        fmt, objs, w = trial
        warnings.extend(w)
        msgs = [
            TranscriptMessage(role=_normalize_role(o["role"]), content=o["content"], index=i)
            for i, o in enumerate(objs)
        ]
        return fmt, msgs, warnings

    trial = _try_parse_jsonl(raw)
    if trial:
        fmt, objs, w = trial
        warnings.extend(w)
        msgs = [
            TranscriptMessage(role=_normalize_role(o["role"]), content=o["content"], index=i)
            for i, o in enumerate(objs)
        ]
        return fmt, msgs, warnings

    fmt, objs, w = _parse_markdown_blocks(raw)
    warnings.extend(w)
    if objs:
        msgs = [
            TranscriptMessage(role=_normalize_role(o["role"]), content=o["content"], index=i)
            for i, o in enumerate(objs)
        ]
        return fmt, msgs, warnings

    fmt, objs, w = _parse_plain_fallback(raw)
    warnings.extend(w)
    msgs = [
        TranscriptMessage(role=_normalize_role(o["role"]), content=o["content"], index=i)
        for i, o in enumerate(objs)
    ]
    return fmt, msgs, warnings


# --- Analysis -----------------------------------------------------------------


def _count_roles(messages: Sequence[TranscriptMessage]) -> Dict[str, int]:
    c: Counter[str] = Counter()
    for m in messages:
        c[m.role] += 1
    return dict(c)


def _pair_turns(messages: Sequence[TranscriptMessage]) -> List[Tuple[TranscriptMessage, Optional[TranscriptMessage]]]:
    """Pair each user message with the next assistant response, skipping system/tool lines."""

    pairs: List[Tuple[TranscriptMessage, Optional[TranscriptMessage]]] = []
    i = 0
    n = len(messages)
    while i < n:
        if messages[i].role != "user":
            i += 1
            continue
        user_msg = messages[i]
        j = i + 1
        while j < n and messages[j].role in ("system", "tool"):
            j += 1
        if j < n and messages[j].role == "assistant":
            pairs.append((user_msg, messages[j]))
            i = j + 1
        else:
            pairs.append((user_msg, None))
            i += 1
    return pairs


def analyze_messages(messages: Sequence[TranscriptMessage]) -> ExtractionReport:
    """Run heuristics over normalized messages."""

    warnings: List[str] = []
    if not messages:
        return ExtractionReport(
            format_detected="none",
            parse_warnings=["No messages to analyze."],
            messages=[],
            statistics={},
            successful_approaches=[],
            error_patterns=[],
            valuable_interactions=[],
            recurring_themes=[],
            recommendations=[],
        )

    role_counts = _count_roles(messages)
    user_text = "\n".join(m.content for m in messages if m.role == "user")
    assistant_text = "\n".join(m.content for m in messages if m.role == "assistant")

    success_hits = len(_SUCCESS_PHRASES.findall(assistant_text))
    error_hits_a = len(_ERROR_PHRASES.findall(assistant_text))
    error_hits_u = len(_ERROR_PHRASES.findall(user_text))
    tool_refs = len(_TOOL_SIGNAL.findall(assistant_text))

    pairs = _pair_turns(messages)
    successful: List[NamedInsight] = []
    errors: List[NamedInsight] = []
    valuable: List[NamedInsight] = []

    for user_msg, asst_msg in pairs:
        if not asst_msg:
            continue
        combined = user_msg.content + "\n" + asst_msg.content
        idxs = [user_msg.index, asst_msg.index]

        if _ERROR_PHRASES.search(combined):
            ev = []
            for m in _ERROR_PHRASES.finditer(asst_msg.content):
                ev.append(_snippet(m.group(0), 120))
                if len(ev) >= 2:
                    break
            errors.append(
                NamedInsight(
                    title="Failure or correction signal in turn",
                    detail="Assistant or user surface errors, stack traces, apologies, or failure wording.",
                    category="error_pattern",
                    confidence="medium" if ev else "low",
                    evidence=ev or [_snippet(asst_msg.content, 160)],
                    message_indices=idxs,
                )
            )

        if _SUCCESS_PHRASES.search(asst_msg.content) and not _ERROR_PHRASES.search(
            asst_msg.content[-400:]
        ):
            ev = []
            for m in _SUCCESS_PHRASES.finditer(asst_msg.content):
                ev.append(_snippet(m.group(0), 120))
                if len(ev) >= 2:
                    break
            successful.append(
                NamedInsight(
                    title="Resolution or confirmation language",
                    detail="Assistant closed the loop with explicit success or completion language.",
                    category="successful_approach",
                    confidence="high" if ev else "medium",
                    evidence=ev or [_snippet(asst_msg.content, 160)],
                    message_indices=idxs,
                )
            )
        elif _SUCCESS_PHRASES.search(asst_msg.content):
            successful.append(
                NamedInsight(
                    title="Mixed outcome turn",
                    detail="Success language appears alongside residual error mentions — verify end state.",
                    category="successful_approach",
                    confidence="low",
                    evidence=[_snippet(asst_msg.content, 200)],
                    message_indices=idxs,
                )
            )

        struct_score = len(_STRUCTURE_ASSISTANT.findall(asst_msg.content))
        if struct_score >= 2 or (len(asst_msg.content) > 800 and struct_score >= 1):
            valuable.append(
                NamedInsight(
                    title="Structured or substantive assistant reply",
                    detail="Numbered steps, headings, or long-form explanation — good candidate to reuse as a pattern.",
                    category="valuable_interaction",
                    confidence="medium",
                    evidence=[_snippet(asst_msg.content, 240)],
                    message_indices=idxs,
                )
            )

        if _THANKS_USER.search(user_msg.content) and len(asst_msg.content) > 200:
            valuable.append(
                NamedInsight(
                    title="Positive user feedback on detailed help",
                    detail="User expressed satisfaction after a substantive assistant response.",
                    category="valuable_interaction",
                    confidence="medium",
                    evidence=[_snippet(user_msg.content, 120), _snippet(asst_msg.content, 120)],
                    message_indices=idxs,
                )
            )

        if _TOOL_SIGNAL.search(asst_msg.content) and _SUCCESS_PHRASES.search(asst_msg.content):
            valuable.append(
                NamedInsight(
                    title="Tool-assisted resolution",
                    detail="Assistant referenced tools or commands and reported success — reproducible workflow.",
                    category="successful_approach",
                    confidence="high",
                    evidence=[_snippet(asst_msg.content, 260)],
                    message_indices=idxs,
                )
            )

    # Deduplicate insights by (category, title, first index)
    def dedupe(items: List[NamedInsight]) -> List[NamedInsight]:
        seen: set = set()
        out: List[NamedInsight] = []
        for it in items:
            key = (it.category, it.title, tuple(it.message_indices[:1]))
            if key in seen:
                continue
            seen.add(key)
            out.append(it)
        return out

    successful = dedupe(successful)
    errors = dedupe(errors)
    valuable = dedupe(valuable)

    # Recurring themes from user vocabulary
    user_words = [w.lower() for w in _word_tokens(user_text)]
    thematic = [w for w in user_words if w not in _STOPWORDS and len(w) > 3]
    top_pairs = Counter(thematic).most_common(12)
    recurring_themes = [f"{w} ({c})" for w, c in top_pairs if c >= 2][:8]
    if not recurring_themes and top_pairs:
        recurring_themes = [f"{w} ({c})" for w, c in top_pairs[:5]]

    recommendations: List[str] = []
    if error_hits_a + error_hits_u > success_hits + 2:
        recommendations.append(
            "Error signals outweigh explicit success markers — review failing turns and add guardrails or tests."
        )
    if tool_refs == 0 and len(messages) > 4:
        recommendations.append(
            "No strong tool-use markers detected — if this was a coding session, consider capturing commands explicitly."
        )
    if len(pairs) > 6 and success_hits < 2:
        recommendations.append(
            "Long thread with few explicit resolutions — summarize decisions and add a closing verification step."
        )
    if not recommendations:
        recommendations.append(
            "No critical gaps flagged — archive standout assistant replies as reusable snippets or skills."
        )

    stats: Dict[str, Any] = {
        "message_count": len(messages),
        "role_counts": role_counts,
        "user_assistant_pairs": len(pairs),
        "success_markers_assistant": success_hits,
        "error_markers_assistant": error_hits_a,
        "error_markers_user": error_hits_u,
        "tool_like_markers": tool_refs,
        "estimated_user_words": len(user_words),
        "estimated_assistant_words": len(_word_tokens(assistant_text)),
    }

    return ExtractionReport(
        format_detected="",
        parse_warnings=warnings,
        messages=list(messages),
        statistics=stats,
        successful_approaches=successful,
        error_patterns=errors,
        valuable_interactions=valuable,
        recurring_themes=recurring_themes,
        recommendations=recommendations,
    )


def format_markdown_report(report: ExtractionReport) -> str:
    """Render a structured Markdown report for humans."""

    lines: List[str] = []
    lines.append("# Conversation extraction report")
    lines.append("")
    lines.append(f"- **Detected format:** `{report.format_detected}`")
    if report.parse_warnings:
        lines.append("- **Parse notes:**")
        for w in report.parse_warnings:
            lines.append(f"  - {w}")
    lines.append("")

    st = report.statistics
    lines.append("## Summary statistics")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("| --- | --- |")
    lines.append(f"| Messages | {st.get('message_count', 0)} |")
    rc = st.get("role_counts") or {}
    lines.append(f"| Roles | `{json.dumps(rc, sort_keys=True)}` |")
    lines.append(f"| User→assistant pairs (heuristic) | {st.get('user_assistant_pairs', 0)} |")
    lines.append(f"| Success markers (assistant) | {st.get('success_markers_assistant', 0)} |")
    lines.append(f"| Error markers (assistant / user) | {st.get('error_markers_assistant', 0)} / {st.get('error_markers_user', 0)} |")
    lines.append(f"| Tool-like markers | {st.get('tool_like_markers', 0)} |")
    lines.append("")

    lines.append("## Successful approaches")
    lines.append("")
    if not report.successful_approaches:
        lines.append("*No high-confidence success patterns isolated.*")
    else:
        for i, ins in enumerate(report.successful_approaches, 1):
            lines.append(f"### {i}. {ins.title} ({ins.confidence})")
            lines.append("")
            lines.append(ins.detail)
            if ins.message_indices:
                lines.append(f"*Messages:* {ins.message_indices}")
            if ins.evidence:
                lines.append("")
                for ev in ins.evidence:
                    lines.append(f"> {ev}")
            lines.append("")

    lines.append("## Error patterns")
    lines.append("")
    if not report.error_patterns:
        lines.append("*No strong error-pattern signals in paired turns.*")
    else:
        for i, ins in enumerate(report.error_patterns, 1):
            lines.append(f"### {i}. {ins.title} ({ins.confidence})")
            lines.append("")
            lines.append(ins.detail)
            if ins.message_indices:
                lines.append(f"*Messages:* {ins.message_indices}")
            if ins.evidence:
                lines.append("")
                for ev in ins.evidence:
                    lines.append(f"> {ev}")
            lines.append("")

    lines.append("## Valuable interactions")
    lines.append("")
    if not report.valuable_interactions:
        lines.append("*No extra valuable-interaction tags beyond basics.*")
    else:
        for i, ins in enumerate(report.valuable_interactions, 1):
            lines.append(f"### {i}. {ins.title} ({ins.confidence})")
            lines.append("")
            lines.append(ins.detail)
            if ins.message_indices:
                lines.append(f"*Messages:* {ins.message_indices}")
            if ins.evidence:
                lines.append("")
                for ev in ins.evidence:
                    lines.append(f"> {ev}")
            lines.append("")

    lines.append("## Recurring themes (user vocabulary)")
    lines.append("")
    if report.recurring_themes:
        for t in report.recurring_themes:
            lines.append(f"- {t}")
    else:
        lines.append("*Insufficient repeated tokens.*")
    lines.append("")

    lines.append("## Recommendations")
    lines.append("")
    for rec in report.recommendations:
        lines.append(f"- {rec}")
    lines.append("")

    return "\n".join(lines)


class ConversationExtractor:
    """High-level API: parse a transcript file or string and return a report."""

    def extract(self, text: str) -> ExtractionReport:
        fmt, messages, p_warnings = parse_transcript(text)
        report = analyze_messages(messages)
        report.format_detected = fmt
        report.parse_warnings = p_warnings + report.parse_warnings
        report.raw_meta = {"original_length": len(text)}
        return report

    def extract_path(self, path: Path) -> ExtractionReport:
        raw = path.read_text(encoding="utf-8", errors="replace")
        report = self.extract(raw)
        report.raw_meta["source_path"] = str(path)
        return report


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Extract patterns and learnings from conversation transcripts (self-improvement agent)."
    )
    p.add_argument(
        "input",
        nargs="?",
        default="-",
        help="Transcript file path, or '-' for stdin",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON instead of Markdown",
    )
    p.add_argument(
        "--no-embed-messages",
        action="store_true",
        help="JSON output: omit full message list (statistics and insights only)",
    )
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    src = args.input
    if src == "-":
        raw = sys.stdin.read()
    else:
        raw = Path(src).read_text(encoding="utf-8", errors="replace")

    extractor = ConversationExtractor()
    report = extractor.extract(raw)
    if src != "-":
        report.raw_meta["source_path"] = src

    if args.json:
        d = report.as_dict()
        if args.no_embed_messages:
            d.pop("messages", None)
        print(json.dumps(d, indent=2, ensure_ascii=False))
    else:
        print(format_markdown_report(report), end="")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
