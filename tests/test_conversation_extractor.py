from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import conversation_extractor  # noqa: E402


class ConversationExtractorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.memory = self.root / "memory"
        self.memory.mkdir(parents=True)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_text_session_extracts_decisions_learnings_patterns(self) -> None:
        log = self.root / "session.txt"
        log.write_text(
            "\n".join(
                [
                    "Turn 1:",
                    "user: We need to ship the fix today.",
                    "assistant: Decision: merge the hotfix branch before the nightly build.",
                    "assistant: Learning: always run the smoke tests after rebasing.",
                    "assistant: Pattern: when CI is red, bisect from the last green main.",
                    "assistant: calling tool `read_file`",
                ]
            ),
            encoding="utf-8",
        )
        segments = conversation_extractor.parse_session_log(log)
        d = conversation_extractor.analyze_segments(segments, log.as_posix())

        self.assertTrue(any("merge the hotfix" in x for x in d.decisions))
        self.assertTrue(any("smoke tests" in x for x in d.learnings))
        self.assertTrue(any("bisect" in x for x in d.patterns))
        self.assertIn("read_file", d.all_tools())

    def test_json_session_messages_envelope(self) -> None:
        path = self.root / "s.json"
        path.write_text(
            json.dumps(
                {
                    "messages": [
                        {"role": "assistant", "content": "TL;DR: use uv for installs."},
                        {"role": "user", "content": "Workflow: document steps in README first."},
                    ]
                }
            ),
            encoding="utf-8",
        )
        segments = conversation_extractor.parse_session_log(path)
        d = conversation_extractor.analyze_segments(segments, "s.json")
        self.assertTrue(d.decisions or d.learnings)
        self.assertTrue(any("README" in p for p in d.patterns))

    def test_digest_json_schema_and_tools_ranked_shape(self) -> None:
        log = self.root / "mini.txt"
        log.write_text("assistant: Decision: pick option A.\n", encoding="utf-8")
        segments = conversation_extractor.parse_session_log(log)
        d = conversation_extractor.analyze_segments(segments, "mini.txt")
        payload = conversation_extractor.digest_to_dict(d)

        self.assertEqual(payload["artifact_type"], "conversation_knowledge")
        self.assertEqual(payload["schema_version"], 1)
        self.assertIn("key_decisions", payload)
        self.assertIn("lessons_learned", payload)
        self.assertIn("reusable_patterns", payload)
        self.assertIn("memory_integration", payload)
        self.assertIsInstance(payload["tools_ranked"], list)
        for row in payload["tools_ranked"]:
            self.assertEqual(set(row.keys()), {"name", "count"})

    def test_run_extraction_writes_md_and_json(self) -> None:
        log = self.root / "run.txt"
        log.write_text("assistant: Lesson: cache pip wheels locally.\n", encoding="utf-8")
        md_path, js_path = conversation_extractor.run_extraction(log, self.memory, self.root)

        self.assertTrue(md_path.is_file() and js_path.is_file())
        self.assertEqual(js_path.suffix, ".json")
        body = json.loads(js_path.read_text(encoding="utf-8"))
        self.assertEqual(body["artifact_type"], "conversation_knowledge")
        self.assertTrue(any("cache" in x for x in body["lessons_learned"]))


if __name__ == "__main__":
    unittest.main()
