from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


ROLE_PREFIX_RE = re.compile(r"^\s*(system|user|assistant|tool)\s*[:|-]\s*(.*)$", re.IGNORECASE)
XML_BLOCK_RE = re.compile(r"<(system|user|assistant|tool|user_query)>(.*?)</\1>", re.IGNORECASE | re.DOTALL)
TOOL_NAME_RE = re.compile(
    r'(?:recipient_name"\s*:\s*"functions\.([A-Za-z0-9_]+)"|to=functions\.([A-Za-z0-9_]+)|tool[_\s-]*name"\s*:\s*"([A-Za-z0-9_./-]+)")'
)
TIMESTAMP_RE = re.compile(r"<timestamp>(.*?)</timestamp>", re.IGNORECASE | re.DOTALL)


@dataclass
class Message:
    role: str
    content: str
    line_start: int
    line_end: int

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ToolCall:
    tool_name: str
    line_number: int
    raw: str

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ConversationSummary:
    conversation_id: str
    source: Optional[str]
    extracted_at: str
    metadata: Dict[str, Any]
    messages: List[Dict[str, Any]]
    user_requests: List[str]
    agent_responses: List[str]
    tool_calls: List[Dict[str, Any]]
    key_decisions: List[str]
    outcomes: List[str]
    summary: str

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ConversationExtractor:
    """Extracts structured conversation artifacts from OpenClaw session transcripts."""

    CATEGORY_KEYWORDS: Dict[str, Tuple[str, ...]] = {
        "coding": (
            "code",
            "implement",
            "bug",
            "fix",
            "test",
            "refactor",
            "function",
            "class",
            "api",
            "python",
            "javascript",
        ),
        "analysis": ("analyze", "investigate", "diagnose", "reason", "compare", "evaluate"),
        "creative": ("story", "poem", "creative", "brainstorm", "narrative", "design concept"),
        "planning": ("plan", "roadmap", "milestone", "scope", "approach"),
        "ops": ("deploy", "infrastructure", "docker", "kubernetes", "ci", "pipeline"),
    }

    DECISION_HINTS = ("decide", "chosen", "choose", "will", "should", "approach", "strategy")
    OUTCOME_HINTS = ("completed", "done", "resolved", "fixed", "failed", "error", "success", "merged")

    def extract_from_file(self, path: Path | str) -> ConversationSummary:
        source = Path(path)
        text = source.read_text(encoding="utf-8")
        return self.extract_from_text(text, source=str(source))

    def extract_from_text(self, text: str, *, source: Optional[str] = None) -> ConversationSummary:
        messages = self._extract_messages(text)
        tool_calls = self._extract_tool_calls(text)
        user_requests = self._extract_user_requests(messages, text)
        agent_responses = [m.content for m in messages if m.role == "assistant"]
        key_decisions = self._extract_sentences(agent_responses, self.DECISION_HINTS)
        outcomes = self._extract_sentences(agent_responses, self.OUTCOME_HINTS)
        conversation_type = self._categorize_conversation(messages, user_requests, agent_responses)
        ts_start, ts_end = self._extract_time_bounds(text)

        metadata = {
            "conversation_type": conversation_type,
            "message_count": len(messages),
            "role_counts": dict(Counter(m.role for m in messages)),
            "tool_call_count": len(tool_calls),
            "timestamp_start": ts_start,
            "timestamp_end": ts_end,
            "primary_user_request": user_requests[0] if user_requests else "",
        }

        conversation_id = self._conversation_id(text, source)
        summary = self._build_summary(metadata, user_requests, key_decisions, outcomes)
        return ConversationSummary(
            conversation_id=conversation_id,
            source=source,
            extracted_at=datetime.now(timezone.utc).isoformat(),
            metadata=metadata,
            messages=[m.as_dict() for m in messages],
            user_requests=user_requests,
            agent_responses=agent_responses,
            tool_calls=[tc.as_dict() for tc in tool_calls],
            key_decisions=key_decisions,
            outcomes=outcomes,
            summary=summary,
        )

    def _extract_messages(self, text: str) -> List[Message]:
        messages: List[Message] = []
        lines = text.splitlines()

        for idx, line in enumerate(lines, start=1):
            match = ROLE_PREFIX_RE.match(line)
            if match:
                role = match.group(1).lower()
                content = match.group(2).strip()
                messages.append(Message(role=role, content=content, line_start=idx, line_end=idx))

        for match in XML_BLOCK_RE.finditer(text):
            role = match.group(1).lower()
            if role == "user_query":
                role = "user"
            content = self._normalize_whitespace(match.group(2))
            if not content:
                continue
            line_start = text[: match.start()].count("\n") + 1
            line_end = text[: match.end()].count("\n") + 1
            messages.append(Message(role=role, content=content, line_start=line_start, line_end=line_end))

        messages.sort(key=lambda m: (m.line_start, m.line_end))
        deduped: List[Message] = []
        seen = set()
        for msg in messages:
            key = (msg.role, msg.content, msg.line_start, msg.line_end)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(msg)
        return deduped

    def _extract_user_requests(self, messages: List[Message], text: str) -> List[str]:
        requests = [m.content for m in messages if m.role == "user" and m.content]
        if requests:
            return requests
        # Fallback for partial XML snippets missing user role extraction.
        xml_requests = [
            self._normalize_whitespace(m.group(1))
            for m in re.finditer(r"<user_query>(.*?)</user_query>", text, re.IGNORECASE | re.DOTALL)
        ]
        return [item for item in xml_requests if item]

    def _extract_tool_calls(self, text: str) -> List[ToolCall]:
        tool_calls: List[ToolCall] = []
        for idx, line in enumerate(text.splitlines(), start=1):
            for match in TOOL_NAME_RE.finditer(line):
                tool_name = next((group for group in match.groups() if group), "")
                if not tool_name:
                    continue
                tool_calls.append(ToolCall(tool_name=tool_name, line_number=idx, raw=line.strip()))
        return tool_calls

    def _extract_sentences(self, blocks: List[str], hints: Tuple[str, ...]) -> List[str]:
        results: List[str] = []
        for block in blocks:
            for sentence in re.split(r"(?<=[.!?])\s+", block):
                lowered = sentence.lower()
                if any(hint in lowered for hint in hints):
                    normalized = self._normalize_whitespace(sentence)
                    if normalized:
                        results.append(normalized)
        unique: List[str] = []
        seen = set()
        for item in results:
            if item in seen:
                continue
            seen.add(item)
            unique.append(item)
        return unique[:12]

    def _categorize_conversation(
        self, messages: List[Message], user_requests: List[str], agent_responses: List[str]
    ) -> str:
        corpus = " ".join([m.content for m in messages] + user_requests + agent_responses).lower()
        if not corpus.strip():
            return "unknown"
        scores: Dict[str, int] = {}
        for category, keywords in self.CATEGORY_KEYWORDS.items():
            scores[category] = sum(corpus.count(keyword) for keyword in keywords)
        best = max(scores.items(), key=lambda kv: kv[1])
        return best[0] if best[1] > 0 else "general"

    def _extract_time_bounds(self, text: str) -> Tuple[Optional[str], Optional[str]]:
        timestamps = [self._normalize_whitespace(m.group(1)) for m in TIMESTAMP_RE.finditer(text)]
        if not timestamps:
            return None, None
        return timestamps[0], timestamps[-1]

    def _conversation_id(self, text: str, source: Optional[str]) -> str:
        hasher = hashlib.sha256()
        hasher.update((source or "").encode("utf-8"))
        hasher.update(text.encode("utf-8"))
        return hasher.hexdigest()[:16]

    def _build_summary(
        self, metadata: Dict[str, Any], user_requests: List[str], key_decisions: List[str], outcomes: List[str]
    ) -> str:
        user_goal = user_requests[0] if user_requests else "No explicit user request detected."
        decision_text = key_decisions[0] if key_decisions else "No explicit decision sentence found."
        outcome_text = outcomes[0] if outcomes else "No explicit outcome sentence found."
        return (
            f"Type: {metadata['conversation_type']}. "
            f"Goal: {user_goal} "
            f"Decision: {decision_text} "
            f"Outcome: {outcome_text}"
        )

    @staticmethod
    def _normalize_whitespace(text: str) -> str:
        return re.sub(r"\s+", " ", text).strip()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extract structured summaries from OpenClaw conversations")
    parser.add_argument("transcript", help="Path to transcript text file")
    parser.add_argument("--output", help="Optional output path for JSON payload")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    extractor = ConversationExtractor()
    summary = extractor.extract_from_file(args.transcript).as_dict()
    payload = json.dumps(summary, indent=2, sort_keys=True)
    if args.output:
        Path(args.output).write_text(payload, encoding="utf-8")
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
