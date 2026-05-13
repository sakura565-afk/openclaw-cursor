"""Tests for scripts.conversation_extractor."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts import conversation_extractor as ce


def test_dated_memory_output_dir_uses_utc_date(tmp_path: Path) -> None:
    fixed = datetime(2026, 5, 13, 23, 0, 0, tzinfo=timezone.utc)
    d = ce.dated_memory_output_dir(tmp_path, fixed)
    assert d == tmp_path / "conversation_summaries" / "2026-05-13"
    assert d.is_dir()


def test_segments_from_chat_history_payload_accepts_result_wrapper() -> None:
    payload = {"result": {"messages": [{"role": "user", "content": "I prefer spaces over tabs for this repo."}]}}
    segs = ce.segments_from_chat_history_payload(payload)
    assert segs
    assert any("spaces" in t for _, _, t in segs)


def test_analyze_segments_preferences_and_corrections() -> None:
    segments = [
        (1, "user", "I prefer we always run pytest before pushing."),
        (2, "assistant", "Understood."),
        (3, "user", "Actually, I meant pytest -q, not verbose."),
        (4, "assistant", "Decision: use pytest -q in CI."),
    ]
    d = ce.analyze_segments(segments, "fixture")
    assert any("pytest" in p.lower() for p in d.user_preferences)
    assert d.corrections or d.decisions


def test_write_digest_creates_json_and_md(tmp_path: Path) -> None:
    at = datetime(2026, 5, 10, 12, 0, 0, tzinfo=timezone.utc)
    d = ce.ConversationDigest(
        source="test",
        generated_at_utc=at.isoformat(),
        segments=[(1, "user", "Learning: always pin dependency versions.")],
        decisions=["Ship on green CI."],
        learnings=["Pin versions."],
        user_preferences=["Use Python 3.11+."],
        corrections=["No, use 3.12 instead."],
    )
    md, js = ce.write_digest(d, tmp_path, "demo_session", output_at=at)
    assert md.suffix == ".md" and js.suffix == ".json"
    assert md.parent == tmp_path / "conversation_summaries" / "2026-05-10"
    body = md.read_text(encoding="utf-8")
    assert "User preferences" in body
    data = json.loads(js.read_text(encoding="utf-8"))
    assert data["counts"]["user_preferences"] == 1
    assert data["counts"]["corrections"] == 1


def test_digest_requires_new_fields() -> None:
    d = ce.ConversationDigest(
        source="x",
        generated_at_utc="",
        segments=[],
        decisions=[],
        learnings=[],
        user_preferences=[],
        corrections=[],
    )
    assert d.user_preferences == []
