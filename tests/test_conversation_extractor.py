"""Tests for scripts/conversation_extractor.py."""

from __future__ import annotations

import json
from pathlib import Path

from scripts import conversation_extractor as ce


def test_analyze_segments_decisions_errors_followups_tools():
    segments = [
        (1, "user", "Decision: use SQLite for the cache."),
        (2, "assistant", "Error: connection refused on port 5432"),
        (3, "assistant", "TODO: retry with localhost after fix"),
        (4, "tool", "grep"),
        (5, "assistant", "Follow-up: deploy after tests pass"),
    ]
    d = ce.analyze_segments(segments, "inline")
    assert any("SQLite" in x for x in d.decisions)
    assert any("connection refused" in x.lower() for x in d.errors)
    assert d.followups
    assert d.all_tools().get("grep", 0) >= 1
    assert any(x.get("kind") == "error" for x in d.flagged)
    assert any(x.get("kind") == "followup" for x in d.flagged)


def test_digest_json_roundtrip_keys(tmp_path: Path):
    segments = [(1, "assistant", "Decision: ship it.")]
    d = ce.analyze_segments(segments, "t")
    blob = ce.digest_to_dict(d)
    assert "errors" in blob and "followups" in blob and "flagged" in blob
    s = json.dumps(blob)
    back = json.loads(s)
    assert back["decisions"]


def test_cli_extract_writes_files(tmp_path: Path, monkeypatch):
    log = tmp_path / "sess.txt"
    log.write_text("user: Decision: test the extractor.\nassistant: OK.\n", encoding="utf-8")
    out_mem = tmp_path / "mem"
    monkeypatch.chdir(tmp_path)
    code = ce.main(["extract", str(log), "--workspace-root", str(tmp_path), "--memory-dir", str(out_mem)])
    assert code == 0
    written = list(out_mem.glob("conversation_extract_*"))
    assert len(written) >= 2


def test_cli_summarize_json(tmp_path: Path, monkeypatch):
    log = tmp_path / "s.json"
    log.write_text(
        json.dumps(
            {
                "messages": [
                    {"role": "user", "content": "Next step: run tests"},
                    {"role": "assistant", "content": "Error: module not found"},
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        code = ce.main(["summarize", str(log), "--format", "json", "--workspace-root", str(tmp_path)])
    assert code == 0
    data = json.loads(buf.getvalue())
    assert data["counts"]["segments"] >= 1
