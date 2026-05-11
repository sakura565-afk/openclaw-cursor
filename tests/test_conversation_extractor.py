from __future__ import annotations

import io
import json
import os
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
        self.learnings = self.root / ".learnings"

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_extract_tags_user_correction_and_incremental_skip(self) -> None:
        transcript = self.root / "session.log"
        transcript.write_text(
            "user: Actually, use PostgreSQL instead of SQLite for this workload.\n"
            "assistant: Understood; I'll switch the adapter.\n"
            "user: That worked; CI is green now.\n",
            encoding="utf-8",
        )

        exit_code, _, _ = self._run_cli(
            "extract",
            str(transcript),
            "--workspace-root",
            str(self.root),
            "--learnings-dir",
            str(self.learnings),
        )
        self.assertEqual(exit_code, 0)

        patterns = list(conversation_extractor.iter_patterns_jsonl(self.learnings))
        types_found = {p["type"] for p in patterns}
        self.assertIn("error_fix", types_found)
        self.assertIn("solution", types_found)

        stdout2 = io.StringIO()
        with mock.patch("sys.stdout", stdout2):
            code2 = conversation_extractor.main(
                [
                    "extract",
                    str(transcript),
                    "--workspace-root",
                    str(self.root),
                    "--learnings-dir",
                    str(self.learnings),
                ]
            )
        self.assertEqual(code2, 0)
        self.assertIn("unchanged transcript", stdout2.getvalue())

    def test_query_keyword_and_tag(self) -> None:
        self.learnings.mkdir(parents=True)
        recs = [
            {
                "id": "aaa",
                "type": "workflow",
                "summary": "When debugging, reproduce with minimal fixture first.",
                "body": "",
                "turn": 1,
                "role": "assistant",
                "tags": ["workflow"],
                "source": "x.log",
                "extracted_at_utc": "2026-01-01T00:00:00+00:00",
            },
            {
                "id": "bbb",
                "type": "optimization",
                "summary": "Batch requests to reduce latency.",
                "body": "",
                "turn": 2,
                "role": "assistant",
                "tags": ["optimization"],
                "source": "y.log",
                "extracted_at_utc": "2026-01-01T00:00:00+00:00",
            },
        ]
        path = self.learnings / conversation_extractor.PATTERNS_FILE
        with path.open("w", encoding="utf-8") as fh:
            for r in recs:
                fh.write(json.dumps(r) + "\n")

        rows = conversation_extractor.query_patterns(
            self.learnings,
            keywords=["batch"],
            tags=[],
            limit=10,
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], "bbb")

        rows2 = conversation_extractor.query_patterns(
            self.learnings,
            keywords=[],
            tags=["workflow"],
            limit=10,
        )
        self.assertEqual(len(rows2), 1)
        self.assertEqual(rows2[0]["id"], "aaa")

    def test_pattern_dedup_across_runs(self) -> None:
        transcript = self.root / "dup.log"
        transcript.write_text(
            "assistant: Decision: ship the hotfix behind a feature flag.\n",
            encoding="utf-8",
        )
        args = [
            "extract",
            str(transcript),
            "--workspace-root",
            str(self.root),
            "--learnings-dir",
            str(self.learnings),
            "--no-incremental",
        ]
        self.assertEqual(self._run_cli(*args)[0], 0)
        self.assertEqual(self._run_cli(*args)[0], 0)
        patterns = list(conversation_extractor.iter_patterns_jsonl(self.learnings))
        self.assertEqual(len(patterns), 1)

    def _run_cli(self, *args: str) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        env = dict(os.environ)
        env.pop("NO_COLOR", None)
        with mock.patch.dict(os.environ, env, clear=True), mock.patch("sys.stdout", stdout), mock.patch(
            "sys.stderr", stderr
        ):
            code = conversation_extractor.main(list(args))
        return code, stdout.getvalue(), stderr.getvalue()


if __name__ == "__main__":
    unittest.main()
