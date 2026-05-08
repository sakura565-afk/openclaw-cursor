"""Deep analysis of agent conversation logs for pattern learning and improvement signals."""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

# Align with proactive_watcher-style error signals; extend for recovery wording.
ERROR_SIGNAL_RE = re.compile(
    r"\b(error|exception|traceback|failed|failure|timeout|timed out|syntaxerror|"
    r"valueerror|keyerror|runtimeerror|typeerror|importerror|exit code [1-9]|"
    r"command failed|non-zero)\b",
    re.IGNORECASE,
)
RECOVERY_SIGNAL_RE = re.compile(
    r"\b(retry|try again|alternative|workaround|instead|fixed|corrected|"
    r"adjusted|rerun|re-run|patch|fallback)\b",
    re.IGNORECASE,
)
TOOL_NAME_RE = re.compile(
    r"(?:tool(?:_|\s*)?(?:name|call)|function)\s*[:=]\s*['\"]?([A-Za-z0-9_.-]+)",
    re.IGNORECASE,
)
ROLE_HEADER_RE = re.compile(
    r"^\s*(?:#{1,3}\s*)?(?P<role>user|human|assistant|agent|tool|system)\s*[:：]\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _safe_json_loads(line: str) -> Any | None:
    line = line.strip()
    if not line:
        return None
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return None


@dataclass
class Turn:
    """One step in a conversation (user, assistant, tool, or system)."""

    role: str
    content: str
    tool_names: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def has_error_signal(self) -> bool:
        return bool(ERROR_SIGNAL_RE.search(self.content))

    def has_recovery_signal(self) -> bool:
        return bool(RECOVERY_SIGNAL_RE.search(self.content))


@dataclass
class ParsedSession:
    """Structured transcript from a single log file or embedded session."""

    source_path: str
    session_id: str | None
    turns: list[Turn]
    raw_outcome_hint: str | None = None
    extras: dict[str, Any] = field(default_factory=dict)

    @property
    def tool_sequence(self) -> list[str]:
        seq: list[str] = []
        for turn in self.turns:
            seq.extend(turn.tool_names)
            for match in TOOL_NAME_RE.finditer(turn.content):
                seq.append(match.group(1))
        return seq

    def outcome_label(self) -> str:
        if self.raw_outcome_hint:
            h = self.raw_outcome_hint.lower()
            if any(x in h for x in ("success", "completed", "done", "passed")):
                return "success"
            if any(x in h for x in ("fail", "error", "abort", "timeout")):
                return "failure"
        # Heuristic from last turns
        tail = "\n".join(t.content for t in self.turns[-4:])
        if ERROR_SIGNAL_RE.search(tail) and not RECOVERY_SIGNAL_RE.search(tail):
            return "failure"
        if self.turns and not ERROR_SIGNAL_RE.search(self.turns[-1].content):
            return "success"
        return "unknown"


def _extract_tool_names_from_obj(obj: dict[str, Any]) -> tuple[str, ...]:
    names: list[str] = []
    tc = obj.get("tool_calls") or obj.get("toolCalls")
    if isinstance(tc, list):
        for item in tc:
            if not isinstance(item, dict):
                continue
            fn = item.get("function") if isinstance(item.get("function"), dict) else None
            if isinstance(fn, dict) and fn.get("name"):
                names.append(str(fn["name"]))
            elif item.get("name"):
                names.append(str(item["name"]))
    typ = str(obj.get("type") or "").lower()
    if typ in ("tool", "tool_result", "function"):
        for key in ("tool_name", "toolName", "name"):
            v = obj.get(key)
            if isinstance(v, str) and v.strip():
                names.append(v.strip())
                break
    return tuple(names)


def _turn_from_mapping(obj: dict[str, Any]) -> Turn | None:
    role = obj.get("role") or obj.get("type") or obj.get("sender") or obj.get("author")
    if role is None:
        return None
    content = obj.get("content") or obj.get("text") or obj.get("message") or ""
    if isinstance(content, list):
        # Anthropic-style blocks
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("text"):
                parts.append(str(block["text"]))
            elif isinstance(block, str):
                parts.append(block)
        content = "\n".join(parts)
    elif not isinstance(content, str):
        content = json.dumps(content, ensure_ascii=False)
    tools = _extract_tool_names_from_obj(obj)
    meta = {k: v for k, v in obj.items() if k not in ("role", "content", "text", "message")}
    return Turn(role=str(role).lower(), content=content, tool_names=tools, metadata=meta)


def _iter_turn_objects(payload: Any) -> Iterable[dict[str, Any]]:
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                yield item
        return
    if isinstance(payload, dict):
        for key in ("messages", "conversation", "turns", "history", "dialog"):
            inner = payload.get(key)
            if isinstance(inner, list):
                for item in inner:
                    if isinstance(item, dict):
                        yield item
                return
        yield payload


def parse_json_document(text: str) -> list[Turn]:
    turns: list[Turn] = []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return turns

    if isinstance(data, dict) and isinstance(data.get("sessions"), list):
        # Multi-session document: flatten first session only here; caller splits files
        for session in data["sessions"]:
            if isinstance(session, dict):
                turns.extend(_turns_from_payload(session))
        return turns

    return _turns_from_payload(data)


def _turns_from_payload(data: Any) -> list[Turn]:
    turns: list[Turn] = []
    for obj in _iter_turn_objects(data):
        turn = _turn_from_mapping(obj)
        if turn:
            turns.append(turn)
    return turns


def parse_jsonl_document(text: str) -> list[Turn]:
    turns: list[Turn] = []
    for line in text.splitlines():
        obj = _safe_json_loads(line)
        if isinstance(obj, dict):
            t = _turn_from_mapping(obj)
            if t:
                turns.append(t)
            elif "messages" in obj:
                turns.extend(_turns_from_payload(obj))
    return turns


def parse_markdown_transcript(text: str) -> list[Turn]:
    """Parse simple ## User / ## Assistant style exports."""

    turns: list[Turn] = []
    matches = list(ROLE_HEADER_RE.finditer(text))
    if not matches:
        # Single-role blob
        if text.strip():
            turns.append(Turn(role="unknown", content=text.strip()))
        return turns

    for i, m in enumerate(matches):
        role = m.group("role").lower()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        chunk = text[start:end].strip()
        tools = tuple(
            x.group(1) for x in TOOL_NAME_RE.finditer(chunk) if x.group(1)
        )
        turns.append(Turn(role=role, content=chunk, tool_names=tools))
    return turns


def extract_outcome_hint_from_json(text: str) -> str | None:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    for key in ("status", "outcome", "result"):
        v = data.get(key)
        if isinstance(v, str):
            return v
    meta = data.get("metadata")
    if isinstance(meta, dict):
        for key in ("success", "status"):
            if key in meta:
                return str(meta[key])
    return None


def parse_log_file(path: Path) -> list[ParsedSession]:
    """Parse one log file into one or more ParsedSession objects."""

    text = _read_text(path)
    rel = path.as_posix()
    sessions: list[ParsedSession] = []

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = None

    if isinstance(data, list):
        turns = _turns_from_payload(data)
        if turns:
            return [
                ParsedSession(
                    source_path=rel,
                    session_id=None,
                    turns=turns,
                    raw_outcome_hint=None,
                    extras={"format": "json_array"},
                )
            ]

    if isinstance(data, dict) and isinstance(data.get("sessions"), list):
        for i, sess in enumerate(data["sessions"]):
            if not isinstance(sess, dict):
                continue
            turns = _turns_from_payload(sess)
            sid = sess.get("id") or sess.get("session_id") or str(i)
            hint = None
            for key in ("status", "outcome"):
                if isinstance(sess.get(key), str):
                    hint = sess[key]
            sessions.append(
                ParsedSession(
                    source_path=rel,
                    session_id=str(sid),
                    turns=turns,
                    raw_outcome_hint=hint,
                    extras={},
                )
            )
        return sessions or _single_session(path, text, data)

    if isinstance(data, dict):
        turns = parse_json_document(text)
        if turns:
            hint = extract_outcome_hint_from_json(text)
            return [
                ParsedSession(
                    source_path=rel,
                    session_id=data.get("session_id") or data.get("id"),
                    turns=turns,
                    raw_outcome_hint=hint,
                    extras={"format": "json"},
                )
            ]

    # JSONL
    jl_turns = parse_jsonl_document(text)
    if jl_turns:
        hint = None
        first = _safe_json_loads(text.splitlines()[0]) if text.splitlines() else None
        if isinstance(first, dict):
            hint = first.get("run_status") or first.get("status")
        return [
            ParsedSession(
                source_path=rel,
                session_id=None,
                turns=jl_turns,
                raw_outcome_hint=str(hint) if hint else None,
                extras={"format": "jsonl"},
            )
        ]

    # Markdown / plain text
    md_turns = parse_markdown_transcript(text)
    return [
        ParsedSession(
            source_path=rel,
            session_id=None,
            turns=md_turns,
            raw_outcome_hint=None,
            extras={"format": "markdown"},
        )
    ]


def _single_session(path: Path, text: str, data: Any) -> list[ParsedSession]:
    rel = path.as_posix()
    turns = parse_json_document(text)
    hint = extract_outcome_hint_from_json(text) if isinstance(data, dict) else None
    return [
        ParsedSession(
            source_path=rel,
            session_id=str(data.get("session_id") or data.get("id"))
            if isinstance(data, dict)
            else None,
            turns=turns,
            raw_outcome_hint=hint,
            extras={"format": "json"},
        )
    ]


def bigrams(sequence: list[str]) -> list[tuple[str, str]]:
    return list(zip(sequence, sequence[1:])) if len(sequence) >= 2 else []


def recovery_events(session: ParsedSession) -> list[dict[str, Any]]:
    """Identify error-to-recovery stretches."""

    events: list[dict[str, Any]] = []
    for i, turn in enumerate(session.turns):
        if not turn.has_error_signal():
            continue
        following = session.turns[i + 1 : i + 4]
        strategy = "unresolved"
        detail: dict[str, Any] = {"turn_index": i, "role": turn.role}
        if not following:
            events.append({"type": "error", "strategy": strategy, **detail})
            continue
        next_turn = following[0]
        if next_turn.has_recovery_signal():
            strategy = "explicit_recovery_language"
        elif next_turn.tool_names:
            strategy = "follow_up_tool"
        elif ERROR_SIGNAL_RE.search(next_turn.content):
            strategy = "chained_error"
        else:
            strategy = "continuation"
        detail["strategy"] = strategy
        detail["next_role"] = next_turn.role
        events.append({"type": "error_recovery", **detail})
    return events


def decision_patterns(session: ParsedSession) -> list[str]:
    """Abstract role transition labels for aggregate statistics."""

    patterns: list[str] = []
    roles = [t.role for t in session.turns]
    for i in range(len(roles) - 1):
        a, b = roles[i], roles[i + 1]
        patterns.append(f"{a}->{b}")
    tools = session.tool_sequence
    for (x, y) in bigrams(tools):
        patterns.append(f"tool:{x}->{y}")
    return patterns


def instruction_snippets(session: ParsedSession, max_chars: int = 240) -> list[str]:
    """First substantive user instructions (for outcome correlation)."""

    out: list[str] = []
    for turn in session.turns:
        if turn.role in ("user", "human") and turn.content.strip():
            text = " ".join(turn.content.split())
            if len(text) > max_chars:
                text = text[:max_chars] + "…"
            out.append(text)
            if len(out) >= 3:
                break
    return out


@dataclass
class AnalysisReport:
    """Aggregated analysis suitable for JSON serialization."""

    generated_at: str
    session_count: int
    outcome_counts: dict[str, int]
    tool_bigrams: dict[str, int]
    decision_pattern_counts: dict[str, int]
    recovery_strategy_counts: dict[str, int]
    instruction_success: list[dict[str, Any]]
    instruction_failure: list[dict[str, Any]]
    recommendations: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "session_count": self.session_count,
            "outcome_counts": self.outcome_counts,
            "tool_bigrams": self.tool_bigrams,
            "decision_pattern_counts": self.decision_pattern_counts,
            "recovery_strategy_counts": self.recovery_strategy_counts,
            "instruction_success_correlation": self.instruction_success,
            "instruction_failure_correlation": self.instruction_failure,
            "recommendations": self.recommendations,
        }


def _tokenize_instruction(text: str) -> set[str]:
    return {w.lower() for w in re.findall(r"[A-Za-z][A-Za-z0-9_+-]{2,}", text)}


def analyze_sessions(sessions: list[ParsedSession]) -> AnalysisReport:
    """Correlate outcomes, tools, recovery strategies, and emit recommendations."""

    outcome_counts: Counter[str] = Counter()
    tool_bigrams: Counter[str] = Counter()
    decision_pattern_counts: Counter[str] = Counter()
    recovery_strategy_counts: Counter[str] = Counter()

    success_instr_tokens: Counter[str] = Counter()
    failure_instr_tokens: Counter[str] = Counter()

    for session in sessions:
        outcome_counts[session.outcome_label()] += 1
        for a, b in bigrams(session.tool_sequence):
            tool_bigrams[f"{a}|{b}"] += 1
        for p in decision_patterns(session):
            decision_pattern_counts[p] += 1
        for ev in recovery_events(session):
            if ev.get("type") == "error_recovery" and "strategy" in ev:
                recovery_strategy_counts[str(ev["strategy"])] += 1

        tokens: set[str] = set()
        for phrase in instruction_snippets(session):
            tokens |= _tokenize_instruction(phrase)
        if session.outcome_label() == "success":
            success_instr_tokens.update(tokens)
        elif session.outcome_label() == "failure":
            failure_instr_tokens.update(tokens)

    # Tokens more common in success vs failure (simple log-odds style ranking)
    correlation_success: list[dict[str, Any]] = []
    for tok, sc in success_instr_tokens.most_common(80):
        fc = failure_instr_tokens.get(tok, 0)
        if sc >= 2 and sc > fc:
            correlation_success.append(
                {"token": tok, "success_count": sc, "failure_count": fc}
            )

    correlation_failure: list[dict[str, Any]] = []
    for tok, fc in failure_instr_tokens.most_common(80):
        sc = success_instr_tokens.get(tok, 0)
        if fc >= 2 and fc > sc:
            correlation_failure.append(
                {"token": tok, "failure_count": fc, "success_count": sc}
            )

    recommendations: list[str] = []
    top_recovery = recovery_strategy_counts.most_common(3)
    if top_recovery:
        recommendations.append(
            "Most frequent error-recovery strategies: "
            + ", ".join(f"{k} ({v})" for k, v in top_recovery)
        )
    if outcome_counts.get("failure", 0) > outcome_counts.get("success", 0):
        recommendations.append(
            "Failures outnumber successes in parsed logs; review recent sessions for recurring tool or environment errors."
        )
    top_tools = tool_bigrams.most_common(5)
    if top_tools:
        recommendations.append(
            "Common tool chains: " + ", ".join(f"{k} ({v})" for k, v in top_tools)
        )
    if correlation_success[:5]:
        recommendations.append(
            "Instruction tokens more often present in successful sessions: "
            + ", ".join(x["token"] for x in correlation_success[:10])
        )
    if correlation_failure[:5]:
        recommendations.append(
            "Instruction tokens more often present in failed sessions: "
            + ", ".join(x["token"] for x in correlation_failure[:10])
        )

    return AnalysisReport(
        generated_at=_utc_now_iso(),
        session_count=len(sessions),
        outcome_counts=dict(outcome_counts),
        tool_bigrams=dict(tool_bigrams),
        decision_pattern_counts=dict(decision_pattern_counts),
        recovery_strategy_counts=dict(recovery_strategy_counts),
        instruction_success=correlation_success[:40],
        instruction_failure=correlation_failure[:40],
        recommendations=recommendations,
    )


def effective_behaviors(
    sessions: list[ParsedSession],
    *,
    min_support: int = 2,
) -> list[dict[str, Any]]:
    """Mine tool chains that appear disproportionately in successful sessions."""

    success_tools: Counter[str] = Counter()
    failure_tools: Counter[str] = Counter()
    for session in sessions:
        seq = session.tool_sequence
        label = session.outcome_label()
        for i in range(len(seq)):
            for j in range(i + 2, min(i + 4, len(seq) + 1)):
                chain = "->".join(seq[i:j])
                if label == "success":
                    success_tools[chain] += 1
                elif label == "failure":
                    failure_tools[chain] += 1

    behaviors: list[dict[str, Any]] = []
    for chain, sc in success_tools.items():
        if sc < min_support:
            continue
        fc = failure_tools.get(chain, 0)
        if sc >= fc + min_support or (fc == 0 and sc >= min_support):
            behaviors.append(
                {
                    "pattern": chain,
                    "success_support": sc,
                    "failure_support": fc,
                }
            )
    behaviors.sort(key=lambda x: (-x["success_support"], x["failure_support"]))
    return behaviors[:50]


def discover_log_files(
    logs_dir: Path,
    *,
    extensions: frozenset[str] | None = None,
    recursive: bool = False,
) -> list[Path]:
    """Return sorted candidate log files under logs_dir."""

    if extensions is None:
        extensions = frozenset({".json", ".jsonl", ".md", ".txt", ".log"})
    if not logs_dir.is_dir():
        return []
    files: list[Path] = []
    iterator = logs_dir.rglob("*") if recursive else logs_dir.iterdir()
    for p in sorted(iterator):
        if not p.is_file():
            continue
        if p.suffix.lower() not in extensions:
            continue
        if p.name.startswith(".") and "gitkeep" in p.name.lower():
            continue
        files.append(p)
    return files
