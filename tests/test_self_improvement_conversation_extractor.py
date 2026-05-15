from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.self_improvement import conversation_extractor as ce  # noqa: E402


class SelfImprovementConversationExtractorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_extract_session_captures_all_fields(self) -> None:
        log = self.root / "session.txt"
        log.write_text(
            "\n".join(
                [
                    "assistant: Decision: ship the patch tonight.",
                    "assistant: Learning: run smoke tests after rebase.",
                    "assistant: Pattern: bisect when CI is red.",
                    "assistant: Error: module import failed for foo.bar",
                    "assistant: calling tool `grep`",
                ]
            ),
            encoding="utf-8",
        )
        row = ce.extract_session(log, root=self.root)
        self.assertIsNotNone(row)
        assert row is not None
        self.assertTrue(any("patch" in x for x in row.decisions_made))
        self.assertTrue(any("import failed" in x.lower() for x in row.errors_encountered))
        self.assertIn("grep", [t["name"] for t in row.tool_usage_patterns["tools_ranked"]])
        self.assertTrue(any("smoke tests" in x for x in row.key_insights))

    def test_run_daily_extraction_writes_dated_json(self) -> None:
        log = self.root / "logs" / "run.txt"
        log.parent.mkdir(parents=True)
        log.write_text(
            "assistant: Decision: use uv.\nassistant: Traceback: ValueError in parser\n",
            encoding="utf-8",
        )
        target = date(2026, 5, 15)
        payload = ce.run_daily_extraction(
            self.root,
            day=target,
            session_paths=[log],
        )
        out = self.root / ".learnings" / "conversations" / "2026-05-15.json"
        self.assertTrue(out.is_file())
        self.assertEqual(payload["date"], "2026-05-15")
        self.assertEqual(payload["artifact_type"], ce.ARTIFACT_TYPE)
        self.assertGreaterEqual(payload["summary"]["session_count"], 1)
        body = json.loads(out.read_text(encoding="utf-8"))
        self.assertIn("decisions_made", body["summary"])
        self.assertIn("errors_encountered", body["summary"])
        self.assertIn("tool_usage_patterns", body["summary"])
        self.assertIn("key_insights", body["summary"])

    def test_merge_skips_duplicate_fingerprint(self) -> None:
        log = self.root / "one.txt"
        log.write_text("assistant: Decision: A.\n", encoding="utf-8")
        day = date(2026, 1, 2)
        first = ce.run_daily_extraction(self.root, day=day, session_paths=[log])
        second = ce.run_daily_extraction(self.root, day=day, session_paths=[log])
        self.assertEqual(first["summary"]["session_count"], 1)
        self.assertEqual(second["summary"]["session_count"], 1)
        self.assertEqual(second["_sessions_merged_this_run"], 0)


if __name__ == "__main__":
    unittest.main()
