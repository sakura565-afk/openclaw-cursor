"""Tests for scripts/conversation_extractor.py."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MODULE_PATH = ROOT / "scripts" / "conversation_extractor.py"
SPEC = importlib.util.spec_from_file_location("conversation_extractor", MODULE_PATH)
ce = importlib.util.module_from_spec(SPEC)
sys.modules["conversation_extractor"] = ce
assert SPEC.loader is not None
SPEC.loader.exec_module(ce)


class ConversationExtractorTests(unittest.TestCase):
    def test_messages_tool_failure_tags(self) -> None:
        payload = {
            "messages": [
                {"role": "user", "content": "Run the build"},
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "call_1",
                            "name": "shell",
                            "input": {"cmd": "make"},
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "call_1",
                            "is_error": True,
                            "content": "Error: make failed with exit code 2",
                        }
                    ],
                },
            ]
        }
        segs = ce.messages_to_segments(payload["messages"], "t")
        failed = [s for s in segs if "failed_tool" in s.tags]
        self.assertTrue(failed)
        self.assertEqual(failed[0].tool_status, "failure")
        self.assertIn("error_learning", failed[0].tags)

    def test_intent_debugging(self) -> None:
        scores = ce._score_intents("Traceback (most recent call last)")
        self.assertEqual(ce._primary_intent(scores), "debugging")

    def test_date_filter_memory_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "MEMORY.md"
            p.write_text(
                "- 2020-01-01 old note\n\n- 2026-04-01 recent note about Python\n",
                encoding="utf-8",
            )
            conv = ce.extract_conversation(p, ROOT)
            start = datetime(2026, 3, 1, tzinfo=timezone.utc)
            end = datetime(2026, 12, 31, tzinfo=timezone.utc)
            filtered = ce.filter_segments_by_date(conv, start, end)
            self.assertEqual(len(filtered.segments), 1)
            self.assertIn("2026-04-01", filtered.segments[0].entry_date or "")

    def test_cli_stdin_json(self) -> None:
        payload = {"messages": [{"role": "user", "content": "What is asyncio?"}]}
        import subprocess

        proc = subprocess.run(
            [sys.executable, str(MODULE_PATH), "--stdin-json", "--indent", "0"],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        doc = json.loads(proc.stdout)
        self.assertEqual(doc["summary"]["conversation_count"], 1)
        kinds = {s["segment_type"] for s in doc["conversations"][0]["segments"]}
        self.assertIn("user_request", kinds)


if __name__ == "__main__":
    unittest.main()
