from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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

    def test_resolve_session_path_prefers_repo_local_sessions(self) -> None:
        sid = "abc-session"
        sess_dir = self.root / ".openclaw" / "sessions" / sid
        sess_dir.mkdir(parents=True)
        json_path = sess_dir / "session.json"
        json_path.write_text('{"messages": []}', encoding="utf-8")
        found = conversation_extractor.resolve_session_path(sid, workspace=self.root)
        self.assertEqual(found, json_path.resolve())

    def test_analyze_segments_builds_qa_and_errors(self) -> None:
        path = self.root / "err.json"
        path.write_text(
            json.dumps(
                {
                    "messages": [
                        {"role": "user", "content": "How do I fix the connection refused to Redis on port 6379?"},
                        {
                            "role": "assistant",
                            "content": "Error: connection refused when dialing 127.0.0.1:6379.\n"
                            "Decision: enable the local redis service before running tests.",
                        },
                    ]
                }
            ),
            encoding="utf-8",
        )
        segments = conversation_extractor.parse_session_log(path)
        d = conversation_extractor.analyze_segments(segments, "err.json")
        self.assertTrue(d.qa_pairs, "expected at least one substantive Q&A pair")
        self.assertTrue(d.errors, "expected an error log entry")
        payload = conversation_extractor.digest_to_dict(d)
        self.assertEqual(payload["extraction_schema_version"], 2)
        self.assertGreaterEqual(payload["counts"]["errors"], 1)

    def test_main_extract_subcommand_writes_summary(self) -> None:
        sid = "cli-test-sess"
        sess_dir = self.root / ".openclaw" / "sessions" / sid
        sess_dir.mkdir(parents=True)
        (sess_dir / "session.json").write_text(
            json.dumps(
                {
                    "messages": [
                        {"role": "user", "content": "Explain idempotency keys for payment APIs in two sentences."},
                        {"role": "assistant", "content": "An idempotency key lets the server recognize retries."},
                    ]
                }
            ),
            encoding="utf-8",
        )
        out_md = self.root / "out" / "x.md"
        with mock.patch.object(conversation_extractor.sys, "stdout", io.StringIO()):
            rc = conversation_extractor.main(
                [
                    "extract",
                    sid,
                    "--output",
                    str(out_md),
                    "--workspace-root",
                    str(self.root),
                ]
            )
        self.assertEqual(rc, 0)
        self.assertTrue(out_md.is_file())
        self.assertTrue(out_md.with_suffix(".json").is_file())
        text = out_md.read_text(encoding="utf-8")
        self.assertIn("Q&A pairs", text)


if __name__ == "__main__":
    unittest.main()
