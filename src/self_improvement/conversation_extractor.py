"""Conversation extraction and analysis utilities.

This module provides a lightweight, dependency-free way to:

- extract key information from a conversation history,
- identify action items, decisions, and commitments,
- track topics/themes across conversations,
- generate Markdown summaries with important points highlighted,
- store extracted data in structured formats (JSON + Markdown), and
- filter by date range, topic, or participant.

The extractor is intentionally heuristic and rule-based so it can run in any
environment without model dependencies. It is designed to integrate with the
repository's existing ``memory/`` folder conventions by writing to
``memory/conversations/`` by default.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


_ACTION_ITEM_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\baction item\b[:\-\s]*(?P<body>.+)$", re.IGNORECASE),
    re.compile(r"^\s*(?:[-*+]\s*)?TODO\b[:\-\s]*(?P<body>.+)$", re.IGNORECASE),
    re.compile(r"\bwe\s+should\b[:\-\s]*(?P<body>.+)$", re.IGNORECASE),
    re.compile(r"\bwe\s+need\s+to\b[:\-\s]*(?P<body>.+)$", re.IGNORECASE),
    re.compile(r"\blet'?s\b[:\-\s]*(?P<body>.+)$", re.IGNORECASE),
)

_DECISION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bdecision\b[:\-\s]*(?P<body>.+)$", re.IGNORECASE),
    re.compile(r"\bwe\s+decided\b[:\-\s]*(?P<body>.+)$", re.IGNORECASE),
    re.compile(r"\bagreed\b[:\-\s]*(?P<body>.+)$", re.IGNORECASE),
)

_COMMITMENT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bI(?:'| a)m going to\b[:\-\s]*(?P<body>.+)$", re.IGNORECASE),
    re.compile(r"\bI(?:'| wi)ll\b[:\-\s]*(?P<body>.+)$", re.IGNORECASE),
    re.compile(r"\bI\s+commit(?:\s+to)?\b[:\-\s]*(?P<body>.+)$", re.IGNORECASE),
)

_TOPIC_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_\-]{2,}", re.IGNORECASE)
_HASHTAG_RE = re.compile(r"(?:^|\s)#(?P<tag>[a-z0-9_\-]{2,})\b", re.IGNORECASE)

_DEFAULT_STOPWORDS: frozenset[str] = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "this",
        "that",
        "from",
        "into",
        "onto",
        "over",
        "under",
        "about",
        "there",
        "their",
        "they",
        "them",
        "then",
        "than",
        "when",
        "where",
        "what",
        "which",
        "while",
        "will",
        "would",
        "could",
        "should",
        "need",
        "needs",
        "want",
        "wants",
        "lets",
        "let",
        "it's",
        "its",
        "we",
        "you",
        "your",
        "yours",
        "our",
        "ours",
        "i",
        "me",
        "my",
        "mine",
        "a",
        "an",
        "to",
        "of",
        "in",
        "on",
        "as",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "it",
        "at",
        "or",
        "not",
        "no",
        "yes",
        "ok",
        "okay",
        "thanks",
        "thank",
        "please",
    }
)


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp."""

    return datetime.now(timezone.utc)


def _ensure_tz(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return _ensure_tz(parsed)


def _normalize_topic(topic: str) -> str:
    cleaned = topic.strip().lower()
    cleaned = re.sub(r"[^a-z0-9_\-]+", "-", cleaned)
    cleaned = cleaned.strip("-")
    return cleaned


def _truncate(text: str, limit: int = 200) -> str:
    cleaned = re.sub(r"\s+", " ", text.strip())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(0, limit - 1)].rstrip() + "…"


@dataclass(frozen=True)
class ConversationMessage:
    """A normalized conversation message.

    The extractor accepts these messages directly, or can normalize common
    dict-shaped message payloads via ``ConversationExtractor.normalize_messages``.
    """

    timestamp: datetime
    participant: str
    content: str
    message_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["timestamp"] = _ensure_tz(self.timestamp).isoformat()
        return payload


@dataclass(frozen=True)
class ExtractedItem:
    """A single extracted action item / decision / commitment."""

    kind: str
    text: str
    participant: str | None = None
    timestamp: str | None = None
    message_id: str | None = None
    confidence: float = 0.6
    topics: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ConversationAnalytics:
    """Derived analytics for a conversation slice."""

    message_count: int = 0
    participants: dict[str, int] = field(default_factory=dict)
    topic_distribution: dict[str, int] = field(default_factory=dict)
    avg_message_chars: float = 0.0
    first_timestamp: str | None = None
    last_timestamp: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ConversationExtraction:
    """Structured extraction output for a conversation."""

    generated_at: str
    window: dict[str, Any]
    topics: list[str]
    themes: list[str]
    highlights: list[str]
    action_items: list[ExtractedItem]
    decisions: list[ExtractedItem]
    commitments: list[ExtractedItem]
    analytics: ConversationAnalytics
    messages: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "window": self.window,
            "topics": self.topics,
            "themes": self.themes,
            "highlights": self.highlights,
            "action_items": [item.to_dict() for item in self.action_items],
            "decisions": [item.to_dict() for item in self.decisions],
            "commitments": [item.to_dict() for item in self.commitments],
            "analytics": self.analytics.to_dict(),
            "messages": self.messages,
        }


@dataclass
class ConversationFilter:
    """Filter constraints for selecting messages or stored extractions."""

    start: datetime | None = None
    end: datetime | None = None
    participants: set[str] | None = None
    topics: set[str] | None = None

    def matches_message(self, message: ConversationMessage, *, message_topics: Sequence[str] | None = None) -> bool:
        if self.start is not None and _ensure_tz(message.timestamp) < _ensure_tz(self.start):
            return False
        if self.end is not None and _ensure_tz(message.timestamp) > _ensure_tz(self.end):
            return False
        if self.participants is not None and message.participant not in self.participants:
            return False
        if self.topics is not None:
            topics = set(message_topics or ())
            if not topics.intersection(self.topics):
                return False
        return True


class ConversationExtractor:
    """Heuristic extractor for conversation history."""

    def __init__(
        self,
        *,
        repo_root: Path | str | None = None,
        memory_dir: Path | str | None = None,
        stopwords: set[str] | None = None,
    ) -> None:
        self.repo_root = Path(repo_root or Path.cwd()).resolve()
        self.memory_dir = Path(memory_dir or self.repo_root / "memory").resolve()
        self.stopwords = frozenset(stopwords) if stopwords is not None else _DEFAULT_STOPWORDS

    @property
    def conversation_dir(self) -> Path:
        return self.memory_dir / "conversations"

    def normalize_messages(self, raw: Iterable[ConversationMessage | Mapping[str, Any]]) -> list[ConversationMessage]:
        """Normalize messages from dataclasses or common dict-like shapes.

        Supported mapping keys:
        - timestamp: ISO 8601 str, datetime, or UNIX seconds
        - participant: str (or "role", "author")
        - content: str (or "text", "message")
        - id: message identifier (or "message_id")
        - metadata: optional dict
        """

        messages: list[ConversationMessage] = []
        for entry in raw:
            if isinstance(entry, ConversationMessage):
                messages.append(entry)
                continue
            if not isinstance(entry, Mapping):
                raise TypeError(f"Unsupported message type: {type(entry)!r}")

            timestamp_raw = entry.get("timestamp") or entry.get("time") or entry.get("created_at")
            timestamp = None
            if isinstance(timestamp_raw, datetime):
                timestamp = _ensure_tz(timestamp_raw)
            elif isinstance(timestamp_raw, (int, float)):
                timestamp = datetime.fromtimestamp(float(timestamp_raw), tz=timezone.utc)
            elif isinstance(timestamp_raw, str):
                timestamp = _parse_datetime(timestamp_raw)
            if timestamp is None:
                timestamp = utc_now()

            participant = str(entry.get("participant") or entry.get("role") or entry.get("author") or "unknown")
            content = str(entry.get("content") or entry.get("text") or entry.get("message") or "")
            message_id = entry.get("message_id") or entry.get("id")
            message_id_str = str(message_id) if message_id is not None else None
            metadata = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
            messages.append(
                ConversationMessage(
                    timestamp=timestamp,
                    participant=participant,
                    content=content,
                    message_id=message_id_str,
                    metadata=dict(metadata),
                )
            )
        messages.sort(key=lambda item: _ensure_tz(item.timestamp))
        return messages

    def filter_messages(self, messages: Sequence[ConversationMessage], constraints: ConversationFilter) -> list[ConversationMessage]:
        """Filter normalized messages using the provided constraints."""

        filtered: list[ConversationMessage] = []
        for message in messages:
            message_topics = self.extract_topics(message.content)
            if constraints.matches_message(message, message_topics=message_topics):
                filtered.append(message)
        return filtered

    def extract_topics(self, text: str, *, top_k: int = 8) -> list[str]:
        """Extract topics from free text using hashtags + frequency heuristics."""

        hashtags = [_normalize_topic(match.group("tag")) for match in _HASHTAG_RE.finditer(text)]
        tokens = [token.lower() for token in _TOPIC_TOKEN_RE.findall(text)]
        counts: dict[str, int] = {}
        for token in tokens:
            normalized = _normalize_topic(token)
            if not normalized or normalized in self.stopwords:
                continue
            if normalized.isdigit():
                continue
            counts[normalized] = counts.get(normalized, 0) + 1

        ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        topics: list[str] = []
        for tag in hashtags:
            if tag and tag not in topics:
                topics.append(tag)
        for token, _count in ordered:
            if token not in topics:
                topics.append(token)
            if len(topics) >= top_k:
                break
        return topics[:top_k]

    def extract_items(
        self,
        message: ConversationMessage,
        *,
        topics: Sequence[str] | None = None,
    ) -> tuple[list[ExtractedItem], list[ExtractedItem], list[ExtractedItem]]:
        """Extract action items, decisions, and commitments from a single message."""

        text = message.content.strip()
        if not text:
            return [], [], []

        extracted_topics = list(topics or self.extract_topics(text))
        timestamp = _ensure_tz(message.timestamp).replace(microsecond=0).isoformat()

        def build(kind: str, body: str, confidence: float) -> ExtractedItem:
            cleaned = _truncate(body, 240)
            return ExtractedItem(
                kind=kind,
                text=cleaned,
                participant=message.participant,
                timestamp=timestamp,
                message_id=message.message_id,
                confidence=confidence,
                topics=extracted_topics[:],
            )

        actions: list[ExtractedItem] = []
        decisions: list[ExtractedItem] = []
        commitments: list[ExtractedItem] = []

        for pattern in _ACTION_ITEM_PATTERNS:
            match = pattern.search(text)
            if match:
                actions.append(build("action_item", match.group("body"), confidence=0.7))

        for pattern in _DECISION_PATTERNS:
            match = pattern.search(text)
            if match:
                decisions.append(build("decision", match.group("body"), confidence=0.65))

        for pattern in _COMMITMENT_PATTERNS:
            match = pattern.search(text)
            if match:
                commitments.append(build("commitment", match.group("body"), confidence=0.7))

        return actions, decisions, commitments

    def summarize(self, messages: Sequence[ConversationMessage], *, max_highlights: int = 8) -> list[str]:
        """Generate highlight bullets (short, high-signal lines)."""

        highlights: list[str] = []
        for message in messages:
            text = message.content.strip()
            if not text:
                continue
            score = 0
            lower = text.lower()
            if any(token in lower for token in ("decision", "we decided", "agreed")):
                score += 2
            if any(token in lower for token in ("action item", "todo", "we should", "we need to")):
                score += 2
            if any(token in lower for token in ("i will", "i'll", "i am going to", "commit")):
                score += 2
            if "?" in text:
                score += 1
            if len(text) >= 180:
                score += 1

            if score >= 2:
                highlights.append(f"**{message.participant}**: {_truncate(text, 220)}")
            if len(highlights) >= max_highlights:
                break

        if not highlights:
            for message in messages[-max_highlights:]:
                text = message.content.strip()
                if text:
                    highlights.append(f"**{message.participant}**: {_truncate(text, 220)}")
        return highlights[:max_highlights]

    def analyze(self, messages: Sequence[ConversationMessage]) -> ConversationAnalytics:
        """Compute analytics for the provided messages."""

        analytics = ConversationAnalytics()
        analytics.message_count = len(messages)
        if not messages:
            return analytics

        total_chars = 0
        participants: dict[str, int] = {}
        topic_distribution: dict[str, int] = {}
        first = _ensure_tz(messages[0].timestamp).replace(microsecond=0)
        last = _ensure_tz(messages[-1].timestamp).replace(microsecond=0)

        for message in messages:
            participants[message.participant] = participants.get(message.participant, 0) + 1
            total_chars += len(message.content or "")
            for topic in self.extract_topics(message.content):
                topic_distribution[topic] = topic_distribution.get(topic, 0) + 1

        analytics.participants = dict(sorted(participants.items(), key=lambda item: (-item[1], item[0])))
        analytics.topic_distribution = dict(
            sorted(topic_distribution.items(), key=lambda item: (-item[1], item[0]))
        )
        analytics.avg_message_chars = round(total_chars / max(1, len(messages)), 2)
        analytics.first_timestamp = first.isoformat()
        analytics.last_timestamp = last.isoformat()
        return analytics

    def extract(
        self,
        raw_messages: Iterable[ConversationMessage | Mapping[str, Any]],
        *,
        constraints: ConversationFilter | None = None,
        include_messages: bool = False,
    ) -> ConversationExtraction:
        """Extract key info from a conversation history."""

        messages = self.normalize_messages(raw_messages)
        if constraints is not None:
            messages = self.filter_messages(messages, constraints)

        topics_seen: dict[str, int] = {}
        actions: list[ExtractedItem] = []
        decisions: list[ExtractedItem] = []
        commitments: list[ExtractedItem] = []

        for message in messages:
            message_topics = self.extract_topics(message.content)
            for topic in message_topics:
                topics_seen[topic] = topics_seen.get(topic, 0) + 1
            found_actions, found_decisions, found_commitments = self.extract_items(
                message, topics=message_topics
            )
            actions.extend(found_actions)
            decisions.extend(found_decisions)
            commitments.extend(found_commitments)

        ordered_topics = [topic for topic, _ in sorted(topics_seen.items(), key=lambda item: (-item[1], item[0]))]
        themes = ordered_topics[:5]
        analytics = self.analyze(messages)

        window: dict[str, Any] = {
            "message_count": len(messages),
            "participants": sorted({m.participant for m in messages}),
            "start": analytics.first_timestamp,
            "end": analytics.last_timestamp,
        }
        if constraints is not None:
            window["filter"] = {
                "start": _ensure_tz(constraints.start).isoformat() if constraints.start else None,
                "end": _ensure_tz(constraints.end).isoformat() if constraints.end else None,
                "participants": sorted(constraints.participants) if constraints.participants else None,
                "topics": sorted(constraints.topics) if constraints.topics else None,
            }

        extraction = ConversationExtraction(
            generated_at=utc_now().replace(microsecond=0).isoformat(),
            window=window,
            topics=ordered_topics[:20],
            themes=themes,
            highlights=self.summarize(messages),
            action_items=actions,
            decisions=decisions,
            commitments=commitments,
            analytics=analytics,
            messages=[m.to_dict() for m in messages] if include_messages else [],
        )
        return extraction

    def render_markdown(self, extraction: ConversationExtraction) -> str:
        """Render a Markdown report for a conversation extraction."""

        generated = extraction.generated_at
        window = extraction.window
        participants = ", ".join(window.get("participants") or []) or "unknown"

        def render_items(title: str, items: Sequence[ExtractedItem]) -> list[str]:
            lines = [f"## {title}", ""]
            if not items:
                lines.append("- None detected.")
                lines.append("")
                return lines
            for item in items:
                who = f" ({item.participant})" if item.participant else ""
                at = f" — {item.timestamp}" if item.timestamp else ""
                lines.append(f"- **{item.text}**{who}{at}")
                if item.topics:
                    lines.append(f"  - topics: {', '.join(item.topics[:6])}")
            lines.append("")
            return lines

        lines: list[str] = [
            "# Conversation Summary",
            "",
            f"- Generated: {generated}",
            f"- Participants: {participants}",
            f"- Messages: {window.get('message_count', 0)}",
            "",
        ]

        if extraction.themes:
            lines.extend(["## Themes", ""])
            lines.append("- " + ", ".join(f"`{topic}`" for topic in extraction.themes))
            lines.append("")

        lines.extend(["## Highlights", ""])
        if extraction.highlights:
            lines.extend([f"- {item}" for item in extraction.highlights])
        else:
            lines.append("- No highlights available.")
        lines.append("")

        lines.extend(render_items("Action Items", extraction.action_items))
        lines.extend(render_items("Decisions", extraction.decisions))
        lines.extend(render_items("Commitments", extraction.commitments))

        lines.extend(["## Analytics", ""])
        lines.append(f"- **Message count**: {extraction.analytics.message_count}")
        if extraction.analytics.first_timestamp and extraction.analytics.last_timestamp:
            lines.append(f"- **Window**: {extraction.analytics.first_timestamp} → {extraction.analytics.last_timestamp}")
        if extraction.analytics.participants:
            lines.append("- **Messages by participant**:")
            for name, count in extraction.analytics.participants.items():
                lines.append(f"  - {name}: {count}")
        if extraction.analytics.topic_distribution:
            lines.append("- **Topic distribution (top 12)**:")
            for topic, count in list(extraction.analytics.topic_distribution.items())[:12]:
                lines.append(f"  - `{topic}`: {count}")
        lines.append("")

        return "\n".join(lines) + "\n"

    def _atomic_write_text(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(path)

    def save_extraction(
        self,
        extraction: ConversationExtraction,
        *,
        name: str | None = None,
        write_markdown: bool = True,
        write_json: bool = True,
    ) -> dict[str, str]:
        """Persist an extraction into ``memory/conversations``.

        Returns paths written (relative to repo root when possible).
        """

        self.conversation_dir.mkdir(parents=True, exist_ok=True)
        stamp = utc_now().strftime("%Y%m%d_%H%M%S")
        safe_name = _normalize_topic(name or "conversation")
        base = f"{stamp}_{safe_name}" if safe_name else stamp

        written: dict[str, str] = {}
        if write_json:
            json_path = self.conversation_dir / f"{base}.json"
            payload = extraction.to_dict()
            self._atomic_write_text(json_path, json.dumps(payload, indent=2, sort_keys=True) + "\n")
            written["json"] = str(json_path.relative_to(self.repo_root) if self.repo_root in json_path.parents else json_path)

        if write_markdown:
            md_path = self.conversation_dir / f"{base}.md"
            self._atomic_write_text(md_path, self.render_markdown(extraction))
            written["markdown"] = str(md_path.relative_to(self.repo_root) if self.repo_root in md_path.parents else md_path)

        return written

    def load_extractions(self) -> list[dict[str, Any]]:
        """Load stored extraction JSON payloads from ``memory/conversations``."""

        if not self.conversation_dir.exists():
            return []
        payloads: list[dict[str, Any]] = []
        for path in sorted(self.conversation_dir.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                data["_path"] = str(path)
                payloads.append(data)
        return payloads

    def filter_extractions(self, payloads: Sequence[Mapping[str, Any]], constraints: ConversationFilter) -> list[dict[str, Any]]:
        """Filter stored extraction payloads by window and topics/participants."""

        filtered: list[dict[str, Any]] = []
        for payload in payloads:
            window = payload.get("window") if isinstance(payload.get("window"), dict) else {}
            start = _parse_datetime(str(window.get("start"))) if window.get("start") else None
            end = _parse_datetime(str(window.get("end"))) if window.get("end") else None
            if constraints.start is not None and start is not None and start < _ensure_tz(constraints.start):
                continue
            if constraints.end is not None and end is not None and end > _ensure_tz(constraints.end):
                continue

            if constraints.participants is not None:
                participants = set(window.get("participants") or [])
                if not participants.intersection(constraints.participants):
                    continue

            if constraints.topics is not None:
                topics = set(payload.get("topics") or [])
                if not topics.intersection(constraints.topics):
                    continue

            filtered.append(dict(payload))
        return filtered


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Conversation extraction and analysis.")
    parser.add_argument(
        "--input",
        type=Path,
        default=None,
        help="Optional JSON file containing an array of messages to analyze.",
    )
    parser.add_argument("--name", default="conversation", help="Name prefix for saved outputs.")
    parser.add_argument("--start", default=None, help="ISO timestamp for filtering window start.")
    parser.add_argument("--end", default=None, help="ISO timestamp for filtering window end.")
    parser.add_argument("--participant", action="append", default=[], help="Participant filter (repeatable).")
    parser.add_argument("--topic", action="append", default=[], help="Topic filter (repeatable).")
    parser.add_argument("--include-messages", action="store_true", help="Embed normalized messages in JSON output.")
    parser.add_argument("--no-markdown", action="store_true", help="Skip writing Markdown output.")
    parser.add_argument("--no-json", action="store_true", help="Skip writing JSON output.")
    return parser.parse_args(list(argv) if argv is not None else None)


def _load_input_messages(path: Path) -> list[Mapping[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return [item for item in data if isinstance(item, Mapping)]
    if isinstance(data, dict) and isinstance(data.get("messages"), list):
        return [item for item in data["messages"] if isinstance(item, Mapping)]
    raise ValueError("Input JSON must be a list of message objects or an object with a 'messages' list.")


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint for ad-hoc extraction runs."""

    args = _parse_args(argv)
    extractor = ConversationExtractor()

    constraints = ConversationFilter(
        start=_parse_datetime(args.start) if args.start else None,
        end=_parse_datetime(args.end) if args.end else None,
        participants=set(args.participant) if args.participant else None,
        topics={_normalize_topic(topic) for topic in args.topic} if args.topic else None,
    )

    messages: list[Mapping[str, Any]] = []
    if args.input is not None:
        messages = _load_input_messages(args.input)

    extraction = extractor.extract(
        messages,
        constraints=constraints if any((constraints.start, constraints.end, constraints.participants, constraints.topics)) else None,
        include_messages=bool(args.include_messages),
    )
    written = extractor.save_extraction(
        extraction,
        name=str(args.name),
        write_markdown=not args.no_markdown,
        write_json=not args.no_json,
    )
    print(json.dumps({"written": written, "analytics": extraction.analytics.to_dict()}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

