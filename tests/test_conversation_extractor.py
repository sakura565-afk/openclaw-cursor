import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class ConversationExtractorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.td = tempfile.TemporaryDirectory()
        self.root = Path(self.td.name)
        (self.root / "memory").mkdir(parents=True)
        self.transcript = self.root / "memory" / "session.json"
        self.transcript.write_text(
            json.dumps(
                {
                    "messages": [
                        {"role": "user", "content": "Decision: use SQLite for storage."},
                        {
                            "role": "assistant",
                            "content": (
                                "Learning: WAL mode improves concurrency.\n"
                                "Traceback (most recent call last):\nError: boom"
                            ),
                        },
                        {"role": "tool", "content": "stderr: command failed"},
                    ]
                }
            ),
            encoding="utf-8",
        )
        self.repo = Path(__file__).resolve().parent.parent
        self.script = self.repo / "scripts" / "conversation_extractor.py"

    def tearDown(self) -> None:
        self.td.cleanup()

    def _run(self, *extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(self.script), "--root", str(self.root), *extra],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_writes_markdown_and_reuse_then_rebuild(self) -> None:
        r = self._run()
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        out = self.root / ".learnings" / "conversation_extracts.md"
        self.assertTrue(out.exists(), r.stdout)
        text = out.read_text(encoding="utf-8")
        self.assertIn("memory/session.json", text)
        self.assertIn("SQLite", text)

        r2 = self._run()
        self.assertEqual(r2.returncode, 0, r2.stderr)
        self.assertIn("reused=1", r2.stdout)

        self.transcript.write_text(
            json.dumps(
                {
                    "messages": [
                        {"role": "user", "content": "Decision: migrate to PostgreSQL instead."},
                    ]
                }
            ),
            encoding="utf-8",
        )
        r3 = self._run()
        self.assertEqual(r3.returncode, 0, r3.stderr)
        self.assertIn("rebuilt=1", r3.stdout)
        body = out.read_text(encoding="utf-8")
        self.assertIn("PostgreSQL", body)


if __name__ == "__main__":
    unittest.main()
