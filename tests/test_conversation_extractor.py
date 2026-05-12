"""Tests for OpenClaw session conversation extraction."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.conversation_extractor import (
    SELF_IMPROVEMENT_SCHEMA_VERSION,
    analyze_segments,
    digest_to_dict,
    parse_session_log,
)


class ConversationExtractorTests(unittest.TestCase):
    def test_json_session_decisions_tools_errors(self) -> None:
        payload = {
            "messages": [
                {"role": "user", "content": "Fix the deploy script."},
                {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "Decision: use systemd user units for ollama."},
                        {"type": "tool_use", "name": "read_file", "id": "1"},
                    ],
                },
                {
                    "role": "tool",
                    "content": "Error: connection refused to localhost:11434",
                },
                {
                    "role": "assistant",
                    "content": "Learning: always healthcheck ollama before batch runs.",
                },
            ]
        }
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "session.json"
            p.write_text(json.dumps(payload), encoding="utf-8")
            segments = parse_session_log(p)

        self.assertTrue(any(role == "tool" for _, role, _ in segments))
        digest = analyze_segments(segments, p.as_posix())
        self.assertIn("use systemd user units", " ".join(digest.decisions).lower())
        self.assertIn("read_file", digest.tool_structured)
        self.assertTrue(any("connection refused" in e.lower() for e in digest.errors))
        self.assertTrue(any("healthcheck" in ell.lower() for ell in digest.learnings))

        doc = digest_to_dict(digest)
        self.assertEqual(doc["schema_version"], SELF_IMPROVEMENT_SCHEMA_VERSION)
        self.assertEqual(doc["artifact_type"], "openclaw_conversation_extraction")
        self.assertIn("tools_used", doc)
        self.assertIn("ranked", doc["tools_used"])
        self.assertIn("errors", doc)
        self.assertEqual(doc["key_learnings"], doc["learnings"])


if __name__ == "__main__":
    unittest.main()
