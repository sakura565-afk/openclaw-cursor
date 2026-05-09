"""Tests for scripts.conversation_extractor."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.conversation_extractor import (
    analyze_segments,
    build_digest_from_file,
    digest_to_dict,
    render_summary_text,
)


def test_analyze_segments_decisions_errors_followups_flags() -> None:
    segments = [
        (1, "assistant", "Decision: ship the minimal API first."),
        (2, "user", "Error: connection refused to localhost:8080"),
        (3, "assistant", "TODO: add retries around the fetch helper."),
        (4, "tool", "grep"),
        (5, "tool_output", "Traceback (most recent call last):\n  File \"x.py\""),
    ]
    d = analyze_segments(segments, "inline")
    assert any("ship the minimal" in x.text for x in d.decisions)
    assert any("connection refused" in x.text.lower() for x in d.errors)
    assert any("retries" in x.text.lower() for x in d.followups)
    assert "grep" in d.all_tools() or len(d.segments) == 5
    assert isinstance(d.session_flags, list)


def test_digest_json_roundtrip_keys(tmp_path: Path) -> None:
    p = tmp_path / "session.json"
    p.write_text(
        json.dumps(
            {
                "messages": [
                    {"role": "assistant", "content": "Next steps: document the CLI."},
                    {"role": "assistant", "content": "Learning: cache invalidation is hard."},
                ]
            }
        ),
        encoding="utf-8",
    )
    d = build_digest_from_file(p, tmp_path)
    blob = digest_to_dict(d)
    assert "session_flags" in blob
    assert "errors" in blob
    assert "followups" in blob
    assert blob["decisions"] == [] or isinstance(blob["decisions"][0], dict)
    txt = render_summary_text(d, top_tools=3)
    assert "Source:" in txt


def test_cli_main_summarize_json(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    from scripts import conversation_extractor as ce

    p = tmp_path / "t.json"
    p.write_text(
        json.dumps({"messages": [{"role": "assistant", "content": "Decision: use JSONL."}]}),
        encoding="utf-8",
    )
    code = ce.main(["summarize", "--json", str(p), "--workspace-root", str(tmp_path)])
    assert code == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["counts"]["segments"] >= 1
