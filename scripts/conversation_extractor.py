#!/usr/bin/env python3
"""Extract and archive valuable OpenClaw conversations."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence


ANSI = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "blue": "\033[34m",
    "cyan": "\033[36m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "red": "\033[31m",
    "magenta": "\033[35m",
}
ARCHIVE_RELATIVE_DIR = Path(".learnings") / "conversations"
SUPPORTED_SESSION_SUFFIXES = {".json", ".jsonl", ".log", ".txt", ".md"}
EXCLUDED_DIRECTORY_NAMES = {".git", ".learnings", "__pycache__", ".pytest_cache", "node_modules"}
TOPIC_STOPWORDS = {
    "about",
    "after",
    "again",
    "agent",
    "also",
    "assistant",
    "because",
    "before",
    "being",
    "between",
    "build",
    "change",
    "changes",
    "code",
    "content",
    "conversation",
    "correct",
    "could",
    "decision",
    "decisions",
    "during",
    "each",
    "extract",
    "feature",
    "from",
    "have",
    "into",
    "just",
    "learning",
    "learnings",
    "made",
    "memory",
    "message",
    "messages",
    "more",
    "need",
    "next",
    "openclaw",
    "output",
    "python",
    "recent",
    "reply",
    "session",
    "sessions",
    "should",
    "since",
    "some",
    "summary",
    "takeaway",
    "that",
    "there",
    "these",
    "they",
    "this",
    "tool",
    "topic",
    "topics",
    "used",
    "user",
    "valuable",
    "want",
    "were",
    "what",
    "when",
    "with",
    "would",
}
SIGNAL_PATTERNS = {
    "learnings": [
        re.compile(r"\blearn(?:ed|ing)?\b", re.IGNORECASE),
        re.compile(r"\binsight\b", re.IGNORECASE),
        re.compile(r"\bkey takeaway\b", re.IGNORECASE),
        re.compile(r"\bdiscovered\b", re.IGNORECASE),
        re.compile(r"\broot cause\b", re.IGNORECASE),
        re.compile(r"\bunderst(?:and|ood)\b", re.IGNORECASE),
    ],
    "decisions": [
        re.compile(r"\bdecid(?:e|ed|ing)\b", re.IGNORECASE),
        re.compile(r"\bdecision\b", re.IGNORECASE),
        re.compile(r"\bwe will\b", re.IGNORECASE),
        re.compile(r"\bgoing with\b", re.IGNORECASE),
        re.compile(r"\bchosen?\b", re.IGNORECASE),
        re.compile(r"\bselected\b", re.IGNORECASE),
        re.compile(r"\bsettled on\b", re.IGNORECASE),
    ],
    "corrections": [
        re.compile(r"\bcorrection\b", re.IGNORECASE),
        re.compile(r"\bactually\b", re.IGNORECASE),
        re.compile(r"\binstead\b", re.IGNORECASE),
        re.compile(r"\bmistake\b", re.IGNORECASE),
        re.compile(r"\bwrong\b", re.IGNORECASE),
        re.compile(r"\bfix(?:ed|ing)?\b", re.IGNORECASE),
        re.compile(r"\brevis(?:e|ed|ion)\b", re.IGNORECASE),
        re.compile(r"\bupdate(?:d)? the approach\b", re.IGNORECASE),
    ],
    "actions": [
        re.compile(r"\baction item\b", re.IGNORECASE),
        re.compile(r"\bnext step\b", re.IGNORECASE),
        re.compile(r"\bfollow[- ]up\b", re.IGNORECASE),
        re.compile(r"\bto do\b", re.IGNORECASE),
        re.compile(r"\bTODO\b"),
        re.compile(r"\bneed to\b", re.IGNORECASE),
        re.compile(r"\bwe should\b", re.IGNORECASE),
        re.compile(r"\bwill add\b", re.IGNORECASE),
        re.compile(r"\bimplement\b", re.IGNORECASE),
        re.compile(r"\bship\b", re.IGNORECASE),
    ],
}
ROLE_ALIASES = {
    "ai": "assistant",
    "assistant": "assistant",
    "bot": "assistant",
    "claude": "assistant",
    "codex": "assistant",
    "human": "user",
    "model": "assistant",
    "system": "system",
    "developer": "system",
    "dev": "system",
    "tool": "tool",
    "function": "tool",
    "user": "user",
}
TEXT_SPEAKER_RE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9 _-]{0,30})\s*:\s*(.+?)\s*$")
DATE_STEM_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{2,}")


@dataclass
class Message:
    role: str
    content: str
    timestamp: str | None = None
    speaker: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "role": self.role,
            "speaker": self.speaker,
            "timestamp": self.timestamp,
            "content": self.content,
        }


@dataclass
class Conversation:
    session_id: str
    source_path: str
    conversation_date: str
    participants: list[str]
    topics: list[str]
    messages: list[Message]
    metadata: dict[str, object] = field(default_factory=dict)
    signals: dict[str, list[str]] = field(
        default_factory=lambda: {
            "learnings": [],
            "decisions": [],
            "corrections": [],
            "actions": [],
        }
    )
    score: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "session_id": self.session_id,
            "source_path": self.source_path,
            "conversation_date": self.conversation_date,
            "participants": self.participants,
            "topics": self.topics,
            "messages": [message.to_dict() for message in self.messages],
            "metadata": self.metadata,
            "signals": self.signals,
            "score": self.score,
        }

    def combined_text(self) -> str:
        return "\n".join(message.content for message in self.messages if message.content).strip()


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def color(text: str, name: str, use_color: bool = True) -> str:
    if not use_color or os.environ.get("NO_COLOR"):
        return text
    return f"{ANSI[name]}{text}{ANSI['reset']}"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def canonical_role(raw_role: str | None) -> str:
    if not raw_role:
        return "unknown"
    normalized = re.sub(r"\s+", " ", raw_role.strip()).lower()
    return ROLE_ALIASES.get(normalized, normalized.replace(" ", "-"))


def display_participant(role: str, speaker: str | None) -> str:
    cleaned_speaker = (speaker or "").strip()
    if cleaned_speaker:
        lower = cleaned_speaker.lower()
        if lower not in ROLE_ALIASES:
            return cleaned_speaker
    return role.title()


def normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def clip(text: str, limit: int = 120) -> str:
    normalized = normalize_whitespace(text)
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3].rstrip() + "..."


def safe_slug(text: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", text.strip().lower()).strip("-")
    return slug or "conversation"


def parse_datetime_candidate(value: str | None) -> datetime | None:
    if not value:
        return None
    candidate = value.strip()
    if not candidate:
        return None
    candidate = candidate.replace("Z", "+00:00")
    for parser in (
        lambda item: datetime.fromisoformat(item),
        lambda item: datetime.strptime(item, "%Y-%m-%d %H:%M:%S"),
        lambda item: datetime.strptime(item, "%Y-%m-%d"),
    ):
        try:
            parsed = parser(candidate)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def conversation_datetime(path: Path, messages: list[Message]) -> datetime:
    for message in messages:
        parsed = parse_datetime_candidate(message.timestamp)
        if parsed is not None:
            return parsed
    stem_match = DATE_STEM_RE.search(path.stem)
    if stem_match:
        parsed = parse_datetime_candidate(stem_match.group(1))
        if parsed is not None:
            return parsed
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)


def normalize_snippet(text: str) -> str:
    cleaned = normalize_whitespace(text.strip(" -*"))
    cleaned = re.sub(r"^[\"'`]+|[\"'`]+$", "", cleaned)
    return cleaned


def split_fragments(text: str) -> list[str]:
    raw_fragments = re.split(r"(?:\n{2,}|(?<=[.!?])\s+)", text)
    fragments: list[str] = []
    for fragment in raw_fragments:
        cleaned = normalize_snippet(fragment)
        if len(cleaned) < 18:
            continue
        fragments.append(cleaned)
    return fragments


def flatten_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts = [flatten_text(item) for item in value]
        return "\n".join(part for part in parts if part).strip()
    if isinstance(value, dict):
        for key in ("text", "content", "body", "message", "value"):
            if key in value:
                flattened = flatten_text(value.get(key))
                if flattened:
                    return flattened
        if "parts" in value:
            return flatten_text(value.get("parts"))
        collected: list[str] = []
        for raw in value.values():
            if isinstance(raw, (str, list, dict)):
                flattened = flatten_text(raw)
                if flattened:
                    collected.append(flattened)
            if len(collected) >= 4:
                break
        return "\n".join(collected).strip()
    return str(value).strip()


def message_timestamp(raw: dict[str, Any]) -> str | None:
    for key in ("timestamp", "time", "created_at", "datetime", "date"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def message_speaker(raw: dict[str, Any]) -> str | None:
    for key in ("speaker", "participant", "name"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    author = raw.get("author")
    if isinstance(author, dict):
        for key in ("name", "role"):
            value = author.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    if isinstance(author, str) and author.strip():
        return author.strip()
    return None


def messages_from_mapping(raw: dict[str, Any]) -> list[Message]:
    direct_content = ""
    for key in ("content", "text", "body", "message"):
        if key in raw:
            direct_content = flatten_text(raw.get(key))
            if direct_content:
                break

    role_value = raw.get("role")
    if not isinstance(role_value, str):
        role_value = raw.get("speaker") if isinstance(raw.get("speaker"), str) else None
    if not isinstance(role_value, str):
        author = raw.get("author")
        if isinstance(author, dict):
            role_value = author.get("role") if isinstance(author.get("role"), str) else None
        elif isinstance(author, str):
            role_value = author

    speaker = message_speaker(raw)
    timestamp = message_timestamp(raw)

    if direct_content:
        return [
            Message(
                role=canonical_role(role_value or speaker),
                speaker=speaker,
                timestamp=timestamp,
                content=direct_content,
            )
        ]

    paired_messages: list[Message] = []
    pair_map = (
        ("prompt", "user"),
        ("question", "user"),
        ("input", "user"),
        ("response", "assistant"),
        ("answer", "assistant"),
        ("output", "assistant"),
    )
    for key, default_role in pair_map:
        if key not in raw:
            continue
        content = flatten_text(raw.get(key))
        if not content:
            continue
        paired_messages.append(
            Message(
                role=canonical_role(default_role),
                speaker=None,
                timestamp=timestamp,
                content=content,
            )
        )
    return paired_messages


def extract_messages_from_json_value(value: Any) -> list[Message]:
    if isinstance(value, list):
        collected: list[Message] = []
        for item in value:
            direct = messages_from_mapping(item) if isinstance(item, dict) else []
            if direct:
                collected.extend(direct)
                continue
            collected.extend(extract_messages_from_json_value(item))
        return collected

    if isinstance(value, dict):
        keyed_candidates: list[list[Message]] = []
        for key in ("messages", "turns", "conversation", "transcript", "history", "events", "chat", "entries"):
            if key not in value:
                continue
            candidate = extract_messages_from_json_value(value.get(key))
            if candidate:
                keyed_candidates.append(candidate)
        if keyed_candidates:
            keyed_candidates.sort(key=lambda item: (len(item), sum(len(msg.content) for msg in item)), reverse=True)
            return keyed_candidates[0]

        direct = messages_from_mapping(value)
        if direct:
            return direct

        collected: list[Message] = []
        for raw in value.values():
            collected.extend(extract_messages_from_json_value(raw))
        return collected

    return []


def extract_messages_from_text(text: str) -> list[Message]:
    messages: list[Message] = []
    current_label: str | None = None
    current_lines: list[str] = []

    def flush() -> None:
        nonlocal current_label, current_lines
        if not current_lines:
            return
        content = "\n".join(current_lines).strip()
        if content:
            role = canonical_role(current_label)
            speaker = current_label.strip() if current_label else None
            messages.append(Message(role=role, speaker=speaker, content=content))
        current_label = None
        current_lines = []

    for line in text.splitlines():
        match = TEXT_SPEAKER_RE.match(line)
        if match:
            label = match.group(1).strip()
            content = match.group(2).strip()
            if label.lower() in ROLE_ALIASES or label.istitle():
                flush()
                current_label = label
                current_lines = [content]
                continue
        if not line.strip():
            flush()
            continue
        current_lines.append(line.strip())

    flush()
    if messages:
        return messages

    paragraphs = [part.strip() for part in re.split(r"\n{2,}", text) if part.strip()]
    return [Message(role="unknown", content=paragraph) for paragraph in paragraphs]


def infer_topics(messages: Iterable[Message], fallback: str) -> list[str]:
    counter: Counter[str] = Counter()
    for message in messages:
        for match in WORD_RE.finditer(message.content):
            word = match.group(0).lower()
            if word in TOPIC_STOPWORDS or len(word) < 4:
                continue
            counter[word] += 1
    topics = [word for word, _count in counter.most_common(5)]
    if topics:
        return topics
    return [safe_slug(fallback).replace("-", " ")]


def infer_participants(messages: Iterable[Message]) -> list[str]:
    seen: list[str] = []
    for message in messages:
        participant = display_participant(message.role, message.speaker)
        if participant not in seen:
            seen.append(participant)
    return seen or ["Unknown"]


def parse_session_file(path: Path) -> Conversation | None:
    try:
        if path.suffix.lower() in {".json", ".jsonl"}:
            if path.suffix.lower() == ".jsonl":
                lines = [json.loads(line) for line in read_text(path).splitlines() if line.strip()]
                messages = extract_messages_from_json_value(lines)
                metadata: dict[str, object] = {"format": "jsonl"}
            else:
                payload = load_json(path)
                messages = extract_messages_from_json_value(payload)
                metadata = {"format": "json"}
        else:
            messages = extract_messages_from_text(read_text(path))
            metadata = {"format": "text"}
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return None

    messages = [message for message in messages if normalize_whitespace(message.content)]
    if not messages:
        return None

    conversation_time = conversation_datetime(path, messages)
    source_text = path.as_posix()
    participants = infer_participants(messages)
    topics = infer_topics(messages, path.stem)
    session_id = safe_slug(path.stem)
    metadata["message_count"] = len(messages)
    metadata["file_size_bytes"] = path.stat().st_size

    return Conversation(
        session_id=session_id,
        source_path=source_text,
        conversation_date=conversation_time.isoformat(),
        participants=participants,
        topics=topics,
        messages=messages,
        metadata=metadata,
    )


def discover_session_storage(project_root: Path) -> Path | None:
    candidates = [
        project_root / "sessions",
        project_root / "session_logs",
        project_root / "logs" / "sessions",
        Path.home() / ".openclaw" / "sessions",
        Path.home() / ".openclaw" / "storage" / "sessions",
        Path.home() / ".local" / "share" / "openclaw" / "sessions",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def iter_session_files(session_path: Path, cutoff: datetime) -> list[Path]:
    if session_path.is_file():
        if datetime.fromtimestamp(session_path.stat().st_mtime, tz=timezone.utc) >= cutoff:
            return [session_path]
        return []

    files: list[Path] = []
    for path in session_path.rglob("*"):
        if not path.is_file():
            continue
        if any(part in EXCLUDED_DIRECTORY_NAMES for part in path.parts):
            continue
        if path.suffix.lower() not in SUPPORTED_SESSION_SUFFIXES:
            continue
        modified_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        if modified_at < cutoff:
            continue
        files.append(path)
    files.sort(key=lambda item: item.stat().st_mtime, reverse=True)
    return files


def extract_conversations(
    session_path: str | Path,
    days: int = 7,
    now: datetime | None = None,
) -> list[Conversation]:
    """Pull recent conversations from OpenClaw session storage."""

    current_time = now or datetime.now(timezone.utc)
    cutoff = current_time - timedelta(days=days)
    path = Path(session_path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"Session path does not exist: {path}")

    conversations: list[Conversation] = []
    for file_path in iter_session_files(path, cutoff):
        conversation = parse_session_file(file_path)
        if conversation is None:
            continue
        conversations.append(conversation)

    conversations.sort(key=lambda item: item.conversation_date, reverse=True)
    return conversations


def analyze_conversation_value(conversation: Conversation) -> dict[str, object]:
    signals = {key: [] for key in SIGNAL_PATTERNS}
    highlights: list[str] = []

    for message in conversation.messages:
        matched_any = False
        for fragment in split_fragments(message.content):
            for category, patterns in SIGNAL_PATTERNS.items():
                if any(pattern.search(fragment) for pattern in patterns):
                    snippet = normalize_snippet(fragment)
                    if snippet and snippet not in signals[category]:
                        signals[category].append(snippet)
                        matched_any = True
            if matched_any and len(highlights) < 6:
                labelled = f"{display_participant(message.role, message.speaker)}: {clip(fragment, 140)}"
                if labelled not in highlights:
                    highlights.append(labelled)

    categories_hit = sum(1 for values in signals.values() if values)
    score = (categories_hit * 2) + sum(min(len(values), 3) for values in signals.values())
    total_hits = sum(len(values) for values in signals.values())
    is_valuable = bool(signals["decisions"]) or categories_hit >= 2 or total_hits >= 3
    return {
        "signals": signals,
        "score": score,
        "highlights": highlights,
        "is_valuable": is_valuable,
    }


def identify_valuable_exchanges(conversations: Iterable[Conversation]) -> list[Conversation]:
    """Filter conversations that include learnings, decisions, or corrections."""

    valuable: list[Conversation] = []
    for conversation in conversations:
        analysis = analyze_conversation_value(conversation)
        if not analysis["is_valuable"]:
            continue
        conversation.signals = analysis["signals"]
        conversation.score = int(analysis["score"])
        valuable.append(conversation)

    valuable.sort(key=lambda item: (item.score, item.conversation_date), reverse=True)
    return valuable


def summarize_key_points(conversation: Conversation) -> list[str]:
    key_points: list[str] = []
    ordered_sections = (
        conversation.signals.get("decisions", []),
        conversation.signals.get("learnings", []),
        conversation.signals.get("corrections", []),
        conversation.signals.get("actions", []),
    )
    for section in ordered_sections:
        for item in section:
            if item not in key_points:
                key_points.append(item)
            if len(key_points) >= 6:
                return key_points

    for message in conversation.messages:
        snippet = clip(message.content, 140)
        if snippet and snippet not in key_points:
            key_points.append(snippet)
        if len(key_points) >= 6:
            break
    return key_points


def generate_summary(conversation: Conversation) -> dict[str, object]:
    """Create a structured summary with metadata, decisions, and actions."""

    analysis = analyze_conversation_value(conversation)
    conversation.signals = analysis["signals"]
    conversation.score = int(analysis["score"])
    summary = {
        "metadata": {
            "date": conversation.conversation_date,
            "session_id": conversation.session_id,
            "source_path": conversation.source_path,
            "participants": conversation.participants,
            "topics": conversation.topics,
            "message_count": len(conversation.messages),
            "score": conversation.score,
        },
        "key_points": summarize_key_points(conversation),
        "learnings": conversation.signals.get("learnings", [])[:5],
        "decisions_made": conversation.signals.get("decisions", [])[:5],
        "corrections": conversation.signals.get("corrections", [])[:5],
        "actions_taken": conversation.signals.get("actions", [])[:5],
        "highlights": analysis["highlights"][:5],
    }
    return summary


def archive_root(project_root: Path | None = None) -> Path:
    return (project_root or repo_root()) / ARCHIVE_RELATIVE_DIR


def archive_index_path(project_root: Path | None = None) -> Path:
    return archive_root(project_root) / "index.json"


def read_archive_records(project_root: Path | None = None) -> list[dict[str, object]]:
    root = archive_root(project_root)
    index_path = archive_index_path(project_root)
    if index_path.exists():
        try:
            data = json.loads(index_path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            pass

    records: list[dict[str, object]] = []
    if not root.exists():
        return records

    for path in sorted(root.glob("*.json")):
        if path.name == "index.json":
            continue
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
    records.sort(key=lambda item: str(item.get("conversation_date", "")), reverse=True)
    return records


def write_archive_records(records: list[dict[str, object]], project_root: Path | None = None) -> None:
    path = archive_index_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(records, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def conversation_fingerprint(conversation: Conversation) -> str:
    payload = {
        "source_path": conversation.source_path,
        "messages": [normalize_whitespace(message.content) for message in conversation.messages],
    }
    encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
    return hashlib.sha1(encoded).hexdigest()


def unique_archive_base_path(base_path: Path) -> Path:
    if not base_path.exists() and not base_path.with_suffix(".json").exists() and not base_path.with_suffix(".md").exists():
        return base_path
    counter = 1
    while True:
        candidate = base_path.with_name(f"{base_path.name}-{counter}")
        if not candidate.exists() and not candidate.with_suffix(".json").exists() and not candidate.with_suffix(".md").exists():
            return candidate
        counter += 1


def render_markdown_summary(summary: dict[str, object]) -> str:
    metadata = summary["metadata"]
    lines = [
        f"# Conversation Archive: {metadata['session_id']}",
        "",
        f"- Date: {metadata['date']}",
        f"- Source: `{metadata['source_path']}`",
        f"- Participants: {', '.join(metadata['participants'])}",
        f"- Topics: {', '.join(metadata['topics'])}",
        f"- Messages: {metadata['message_count']}",
        f"- Value score: {metadata['score']}",
        "",
        "## Key Points",
        "",
    ]

    key_points: list[str] = summary["key_points"]
    if key_points:
        lines.extend(f"- {item}" for item in key_points)
    else:
        lines.append("- No key points extracted.")

    sections = (
        ("Learnings", summary["learnings"]),
        ("Decisions Made", summary["decisions_made"]),
        ("Corrections", summary["corrections"]),
        ("Actions Taken", summary["actions_taken"]),
        ("Highlights", summary["highlights"]),
    )
    for title, values in sections:
        lines.extend(["", f"## {title}", ""])
        if values:
            lines.extend(f"- {item}" for item in values)
        else:
            lines.append("- None captured.")

    return "\n".join(lines).rstrip() + "\n"


def archive_to_memory(
    conversation: Conversation,
    project_root: Path | None = None,
) -> dict[str, object]:
    """Persist a valuable conversation into .learnings/conversations."""

    summary = generate_summary(conversation)
    root = archive_root(project_root)
    root.mkdir(parents=True, exist_ok=True)
    records = read_archive_records(project_root)
    fingerprint = conversation_fingerprint(conversation)

    for record in records:
        if record.get("fingerprint") == fingerprint:
            return {
                "duplicate": True,
                "json_path": record.get("json_path"),
                "markdown_path": record.get("markdown_path"),
                "record": record,
            }

    base_name = f"{summary['metadata']['date'][:10]}-{safe_slug('-'.join(conversation.topics[:2]))}-{conversation.session_id}"
    base_path = unique_archive_base_path(root / base_name)
    json_path = base_path.with_suffix(".json")
    markdown_path = base_path.with_suffix(".md")
    archived_at = datetime.now(timezone.utc).isoformat()

    record = {
        "session_id": conversation.session_id,
        "conversation_date": conversation.conversation_date,
        "source_path": conversation.source_path,
        "participants": conversation.participants,
        "topics": conversation.topics,
        "message_count": len(conversation.messages),
        "score": conversation.score,
        "fingerprint": fingerprint,
        "signal_counts": {key: len(values) for key, values in conversation.signals.items()},
        "summary": summary,
        "messages": [message.to_dict() for message in conversation.messages],
        "json_path": json_path.as_posix(),
        "markdown_path": markdown_path.as_posix(),
        "archived_at": archived_at,
    }

    json_path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path.write_text(render_markdown_summary(summary), encoding="utf-8")

    records.append(
        {
            "session_id": conversation.session_id,
            "conversation_date": conversation.conversation_date,
            "source_path": conversation.source_path,
            "participants": conversation.participants,
            "topics": conversation.topics,
            "message_count": len(conversation.messages),
            "score": conversation.score,
            "fingerprint": fingerprint,
            "signal_counts": {key: len(values) for key, values in conversation.signals.items()},
            "json_path": json_path.as_posix(),
            "markdown_path": markdown_path.as_posix(),
            "archived_at": archived_at,
        }
    )
    records.sort(key=lambda item: str(item.get("conversation_date", "")), reverse=True)
    write_archive_records(records, project_root=project_root)

    return {
        "duplicate": False,
        "json_path": json_path.as_posix(),
        "markdown_path": markdown_path.as_posix(),
        "record": record,
    }


def render_summary_card(summary: dict[str, object], archived_to: str | None = None, use_color: bool = True) -> str:
    metadata = summary["metadata"]
    lines = [
        color(f"Conversation {metadata['session_id']}", "bold", use_color),
        f"{color('Date:', 'cyan', use_color)} {metadata['date']}",
        f"{color('Participants:', 'cyan', use_color)} {', '.join(metadata['participants'])}",
        f"{color('Topics:', 'cyan', use_color)} {', '.join(metadata['topics'])}",
        f"{color('Source:', 'cyan', use_color)} {metadata['source_path']}",
        f"{color('Value score:', 'cyan', use_color)} {metadata['score']}",
    ]
    if archived_to:
        lines.append(f"{color('Archive:', 'green', use_color)} {archived_to}")

    section_map = (
        ("Key points", summary["key_points"]),
        ("Decisions made", summary["decisions_made"]),
        ("Actions taken", summary["actions_taken"]),
        ("Corrections", summary["corrections"]),
        ("Learnings", summary["learnings"]),
    )
    for title, values in section_map:
        lines.extend(["", color(title, "blue", use_color)])
        if values:
            lines.extend(f"  - {clip(item, 160)}" for item in values[:4])
        else:
            lines.append("  - None")
    return "\n".join(lines)


def collect_archive_stats(records: Iterable[dict[str, object]]) -> dict[str, object]:
    records_list = list(records)
    participant_counts: Counter[str] = Counter()
    topic_counts: Counter[str] = Counter()
    signal_totals: Counter[str] = Counter()
    total_messages = 0
    dates: list[str] = []

    for record in records_list:
        total_messages += int(record.get("message_count", 0) or 0)
        if isinstance(record.get("conversation_date"), str):
            dates.append(record["conversation_date"])
        for participant in record.get("participants", []):
            participant_counts[str(participant)] += 1
        for topic in record.get("topics", []):
            topic_counts[str(topic)] += 1
        signal_counts = record.get("signal_counts", {})
        if isinstance(signal_counts, dict):
            for key, value in signal_counts.items():
                try:
                    signal_totals[str(key)] += int(value)
                except (TypeError, ValueError):
                    continue

    dates.sort()
    return {
        "total_conversations": len(records_list),
        "total_messages": total_messages,
        "date_range": (dates[0], dates[-1]) if dates else (None, None),
        "top_participants": participant_counts.most_common(5),
        "top_topics": topic_counts.most_common(5),
        "signal_totals": dict(signal_totals),
        "average_messages": round(total_messages / len(records_list), 1) if records_list else 0.0,
    }


def print_archive_list(records: list[dict[str, object]], use_color: bool = True) -> None:
    if not records:
        print(color("No archived conversations found.", "yellow", use_color))
        return
    print(color("Archived conversations", "bold", use_color))
    for record in records:
        topics = ", ".join(record.get("topics", [])[:4]) or "n/a"
        participants = ", ".join(record.get("participants", [])) or "Unknown"
        print(
            f"- {color(str(record.get('conversation_date', 'unknown'))[:19], 'cyan', use_color)} "
            f"{color(str(record.get('session_id', 'conversation')), 'green', use_color)} "
            f"[score {record.get('score', 0)}]"
        )
        print(f"  Participants: {participants}")
        print(f"  Topics: {topics}")
        print(f"  Source: {record.get('source_path', 'unknown')}")
        print(f"  Archive: {record.get('markdown_path', 'n/a')}")


def print_archive_stats(records: list[dict[str, object]], use_color: bool = True) -> None:
    stats = collect_archive_stats(records)
    print(color("Conversation archive stats", "bold", use_color))
    print(f"{color('Total archived:', 'cyan', use_color)} {stats['total_conversations']}")
    print(f"{color('Total messages:', 'cyan', use_color)} {stats['total_messages']}")
    print(f"{color('Average messages:', 'cyan', use_color)} {stats['average_messages']}")
    if stats["date_range"][0]:
        print(
            f"{color('Date range:', 'cyan', use_color)} "
            f"{stats['date_range'][0][:10]} -> {stats['date_range'][1][:10]}"
        )
    else:
        print(f"{color('Date range:', 'cyan', use_color)} n/a")

    print(color("Top participants", "blue", use_color))
    if stats["top_participants"]:
        for participant, count in stats["top_participants"]:
            print(f"  - {participant}: {count}")
    else:
        print("  - None")

    print(color("Top topics", "blue", use_color))
    if stats["top_topics"]:
        for topic, count in stats["top_topics"]:
            print(f"  - {topic}: {count}")
    else:
        print("  - None")

    print(color("Signal totals", "blue", use_color))
    if stats["signal_totals"]:
        for key in ("learnings", "decisions", "corrections", "actions"):
            print(f"  - {key}: {stats['signal_totals'].get(key, 0)}")
    else:
        print("  - None")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extract and archive valuable OpenClaw conversations.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    extract_parser = subparsers.add_parser("extract", help="Extract recent valuable conversations.")
    extract_parser.add_argument(
        "--session-path",
        type=Path,
        help="Path to OpenClaw session storage. Defaults to common OpenClaw session locations.",
    )
    extract_parser.add_argument("--days", type=int, default=7, help="Only inspect conversations from the last N days.")

    list_parser = subparsers.add_parser("list", help="Show archived conversations.")
    list_parser.add_argument("--limit", type=int, default=20, help="Maximum archived conversations to show.")

    stats_parser = subparsers.add_parser("stats", help="Show archive statistics.")
    stats_parser.add_argument("--limit", type=int, default=0, help="Optional limit for newest records to include.")
    return parser


def main(
    argv: Sequence[str] | None = None,
    root: Path | None = None,
    now: datetime | None = None,
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    project_root = root or repo_root()
    current_time = now or datetime.now(timezone.utc)

    if args.command == "extract":
        session_source = args.session_path.resolve() if args.session_path else discover_session_storage(project_root)
        if session_source is None or not session_source.exists():
            print(
                color(
                    "No session storage found. Provide --session-path or create a known OpenClaw sessions directory.",
                    "red",
                ),
                file=sys.stderr,
            )
            return 1

        try:
            conversations = extract_conversations(session_source, days=args.days, now=current_time)
        except FileNotFoundError as exc:
            print(color(str(exc), "red"), file=sys.stderr)
            return 1

        valuable = identify_valuable_exchanges(conversations)
        if not valuable:
            print(color("No valuable conversations found in the selected time window.", "yellow"))
            return 0

        archived_count = 0
        skipped_duplicates = 0
        print(color(f"Found {len(valuable)} valuable conversation(s).", "green"))
        for conversation in valuable:
            result = archive_to_memory(conversation, project_root=project_root)
            summary = generate_summary(conversation)
            if result["duplicate"]:
                skipped_duplicates += 1
                archive_path_text = result.get("markdown_path") or result.get("json_path")
                print(render_summary_card(summary, archived_to=str(archive_path_text), use_color=True))
                print(color("Duplicate archive detected; existing record reused.\n", "yellow"))
                continue
            archived_count += 1
            print(render_summary_card(summary, archived_to=str(result["markdown_path"]), use_color=True))
            print()

        print(
            color(
                f"Archived {archived_count} conversation(s); skipped {skipped_duplicates} duplicate(s).",
                "green" if archived_count else "yellow",
            )
        )
        return 0

    records = read_archive_records(project_root=project_root)
    if args.command == "list":
        limit = max(0, int(args.limit))
        print_archive_list(records[:limit] if limit else records, use_color=True)
        return 0

    if args.command == "stats":
        limited_records = records[: args.limit] if args.limit else records
        print_archive_stats(limited_records, use_color=True)
        return 0

    parser.error("Unsupported command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
