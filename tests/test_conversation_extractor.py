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
        self.conversations = self.root / ".learnings" / "conversations"
        self.conversations.mkdir(parents=True)

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

    def test_run_extraction_writes_digest_and_exchanges_under_learnings(self) -> None:
        log = self.root / "run.txt"
        log.write_text("assistant: Lesson: cache pip wheels locally.\n", encoding="utf-8")
        md_path, js_path = conversation_extractor.run_extraction(log, self.conversations, self.root)

        self.assertTrue(md_path.is_file() and js_path.is_file())
        self.assertEqual(js_path.suffix, ".json")
        self.assertIn(".learnings/conversations", md_path.as_posix())
        body = json.loads(js_path.read_text(encoding="utf-8"))
        self.assertEqual(body["artifact_type"], "conversation_knowledge")
        self.assertTrue(any("cache" in x for x in body["lessons_learned"]))
        self.assertIn("exchanges", body)
        self.assertIn("exchanges_written", body)

    def test_dedupe_skips_second_identical_exchange(self) -> None:
        log = self.root / "dup.txt"
        log.write_text(
            "user: What is 2+2?\nassistant: Learning: four is the answer.\n",
            encoding="utf-8",
        )
        conversation_extractor.run_extraction(log, self.conversations, self.root)
        before = list(self.conversations.glob("insights__*.md"))
        self.assertTrue(before)
        conversation_extractor.run_extraction(log, self.conversations, self.root)
        after = list(self.conversations.glob("insights__*.md"))
        self.assertEqual(len(before), len(after))

    def test_classify_error_correction(self) -> None:
        segments = [
            (1, "user", "Use approach A."),
            (1, "assistant", "Actually, approach B is correct; I was wrong about A."),
        ]
        ex = conversation_extractor.build_extracted_exchanges(segments, "t.txt")
        self.assertTrue(ex)
        self.assertEqual(ex[0].conversation_type, "error_corrections")

    def test_list_and_search_helpers(self) -> None:
        sample = self.conversations / "questions__deadbeefcafe.md"
        sample.write_text(
            "---\n"
            'conversation_type: "questions"\n'
            "content_fingerprint: deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef\n"
            'source: "x.txt"\n'
            "source_turn: 1\n"
            'title: "pytest markers"\n'
            "extracted_at_utc: 2026-01-01T00:00:00+00:00\n"
            "---\n\n### User\n\nHow do I run pytest?\n",
            encoding="utf-8",
        )
        listed = conversation_extractor.list_extracted_conversations(self.conversations)
        self.assertIn(sample.resolve(), [p.resolve() for p in listed])
        hits = conversation_extractor.search_extracted_conversations(self.conversations, "pytest")
        self.assertIn(sample.resolve(), [p.resolve() for p in hits])


if __name__ == "__main__":
    unittest.main()
