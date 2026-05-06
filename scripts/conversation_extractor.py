#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


Message = dict[str, str]

TOPIC_KEYWORDS: dict[str, tuple[str, ...]] = {
    "engineering": ("code", "refactor", "function", "bug", "implementation", "api", "script"),
    "testing": ("test", "assert", "coverage", "failing", "regression", "pytest"),
    "docs": ("readme", "documentation", "docs", "guide", "comment"),
    "operations": ("deploy", "infra", "pipeline", "ci", "server", "monitor"),
    "planning": ("plan", "roadmap", "milestone", "scope", "priority", "timeline"),
    "product": ("user", "customer", "feature", "ux", "requirement", "feedback"),
}

MOOD_KEYWORDS: dict[str, tuple[str, ...]] = {
    "positive": ("great", "good", "clear", "resolved", "done", "success", "confident"),
    "neutral": ("consider", "maybe", "review", "question", "explore", "check"),
    "stressed": ("blocked", "urgent", "issue", "risk", "problem", "stuck", "failed"),
}

DECISION_PATTERNS = (
    r"\b(?:we|i)\s+(?:will|should|decided|choose|chose|prefer)\b",
    r"\bdecision[:\-\s]",
    r"\bgo with\b",
    r"\blet'?s\b",
)

LEARNING_PATTERNS = (
    r"\b(?:learned|learnt|found|discovered|realized)\b",
    r"\bturns out\b",
    r"\binsight[:\-\s]",
    r"\bnote that\b",
)


def _normalize(text: str) -> str:
    return " ".join(text.strip().split())


def _split_sentences(text: str) -> list[str]:
    normalized = _normalize(text)
    if not normalized:
        return []
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+", normalized) if part.strip()]


def _collect_text(messages: list[Message]) -> str:
    return "\n".join(_normalize(item.get("content", "")) for item in messages if item.get("content"))


def score_conversation(messages: list[Message]) -> dict[str, float]:
    """Score conversation quality on a 0-100 scale."""
    if not messages:
        return {"overall": 0.0, "engagement": 0.0, "clarity": 0.0, "actionability": 0.0, "balance": 0.0}

    contents = [_normalize(msg.get("content", "")) for msg in messages if msg.get("content")]
    total_chars = sum(len(text) for text in contents)
    turns = len(contents)
    questions = sum(text.count("?") for text in contents)
    actionable_hits = sum(
        1
        for text in contents
        if re.search(r"\b(?:next step|todo|action|implement|fix|ship|test|document)\b", text, flags=re.I)
    )
    explicit_outcomes = sum(
        1
        for text in contents
        if re.search(r"\b(?:done|resolved|merged|completed|confirmed|decided)\b", text, flags=re.I)
    )

    role_counts: dict[str, int] = {}
    for msg in messages:
        role = msg.get("role", "unknown")
        role_counts[role] = role_counts.get(role, 0) + 1
    max_count = max(role_counts.values()) if role_counts else 1
    min_count = min(role_counts.values()) if role_counts else 1
    balance = (min_count / max_count) * 100 if max_count else 0

    engagement = min(100.0, (turns * 8.0) + (questions * 4.0))
    avg_length = total_chars / max(1, turns)
    clarity = max(0.0, min(100.0, 100.0 - abs(avg_length - 220) / 2.5))
    actionability = min(100.0, (actionable_hits * 18.0) + (explicit_outcomes * 12.0))

    overall = round((engagement * 0.25) + (clarity * 0.25) + (actionability * 0.35) + (balance * 0.15), 2)
    return {
        "overall": overall,
        "engagement": round(engagement, 2),
        "clarity": round(clarity, 2),
        "actionability": round(actionability, 2),
        "balance": round(balance, 2),
    }


def _extract_by_patterns(messages: list[Message], patterns: tuple[str, ...]) -> list[str]:
    results: list[str] = []
    for item in messages:
        text = _normalize(item.get("content", ""))
        if not text:
            continue
        for sentence in _split_sentences(text):
            if any(re.search(pattern, sentence, flags=re.I) for pattern in patterns):
                results.append(sentence)
                break
    # Preserve order, drop duplicates.
    deduped: list[str] = []
    seen: set[str] = set()
    for line in results:
        lowered = line.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        deduped.append(line)
    return deduped


def extract_decisions(messages: list[Message]) -> list[str]:
    return _extract_by_patterns(messages, DECISION_PATTERNS)


def extract_learnings(messages: list[Message]) -> list[str]:
    return _extract_by_patterns(messages, LEARNING_PATTERNS)


def auto_tag_topics(messages: list[Message]) -> list[str]:
    corpus = _collect_text(messages).lower()
    if not corpus:
        return []
    scored: list[tuple[int, str]] = []
    for topic, words in TOPIC_KEYWORDS.items():
        count = sum(corpus.count(word) for word in words)
        if count:
            scored.append((count, topic))
    scored.sort(reverse=True)
    return [topic for _, topic in scored[:3]]


def auto_tag_mood(messages: list[Message]) -> str:
    corpus = _collect_text(messages).lower()
    if not corpus:
        return "neutral"
    mood_scores: dict[str, int] = {}
    for mood, words in MOOD_KEYWORDS.items():
        mood_scores[mood] = sum(corpus.count(word) for word in words)
    best_mood = max(mood_scores, key=lambda key: mood_scores[key])
    if mood_scores[best_mood] == 0:
        return "neutral"
    return best_mood


def format_list_output(items: list[str], title: str) -> str:
    if not items:
        return f"{title}:\n- (none)"
    rendered = [f"{title}:"]
    for idx, item in enumerate(items, start=1):
        rendered.append(f"{idx}. {item}")
    return "\n".join(rendered)


def extract_conversation_insights(messages: list[Message]) -> dict[str, Any]:
    decisions = extract_decisions(messages)
    learnings = extract_learnings(messages)
    return {
        "score": score_conversation(messages),
        "decisions": decisions,
        "learnings": learnings,
        "tags": {
            "topics": auto_tag_topics(messages),
            "mood": auto_tag_mood(messages),
        },
        "formatted": {
            "decisions": format_list_output(decisions, "Decisions"),
            "learnings": format_list_output(learnings, "Learnings"),
        },
    }


def _load_messages(path: Path) -> list[Message]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("Conversation file must contain a JSON array of message objects.")
    messages: list[Message] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role", "unknown"))
        content = str(item.get("content", ""))
        messages.append({"role": role, "content": content})
    return messages


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract conversation insights with scoring and tagging.")
    parser.add_argument("conversation_file", help="Path to JSON file containing message list.")
    parser.add_argument("--pretty", action="store_true", help="Print formatted decisions and learnings after JSON output.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    messages = _load_messages(Path(args.conversation_file))
    payload = extract_conversation_insights(messages)
    print(json.dumps(payload, indent=2, sort_keys=True))
    if args.pretty:
        print()
        print(payload["formatted"]["decisions"])
        print()
        print(payload["formatted"]["learnings"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
