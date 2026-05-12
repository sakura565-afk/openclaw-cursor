"""Tests for scripts.conversation_extractor."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from scripts import conversation_extractor as ce


class ConversationExtractorTests(unittest.TestCase):
    def test_analyze_segments_decisions_errors_followups_and_flags(self) -> None:
        segments = [
            (1, "assistant", "Decision: ship the hotfix branch first."),
            (2, "tool_output", "Error: connection refused to localhost:5432"),
            (3, "user", "TODO: verify backups before deploy"),
            (4, "assistant", "calling tool `read_file` for context"),
        ]
        d = ce.analyze_segments(segments, "inline")
        self.assertEqual(len(d.decisions), 1)
        self.assertEqual(d.decisions[0].text, "ship the hotfix branch first.")
        self.assertGreaterEqual(len(d.errors), 1)
        self.assertTrue(any("connection refused" in e.text.lower() for e in d.errors))
        self.assertTrue(any("verify backups" in f.text.lower() for f in d.followups))
        tools = d.all_tools()
        self.assertIn("read_file", tools)

    def test_summarize_json_command(self) -> None:
        payload = json.dumps(
            [{"role": "assistant", "content": "Decision: use UUID v4 for new IDs."}],
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            f.write(payload)
            path = f.name
        buf = io.StringIO()
        try:
            with redirect_stdout(buf):
                rc = ce.main(["summarize", path, "--json"])
        finally:
            Path(path).unlink(missing_ok=True)
        self.assertEqual(rc, 0)
        data = json.loads(buf.getvalue())
        self.assertGreaterEqual(data["decisions"]["count"], 1)

    def test_extract_writes_files(self) -> None:
        root = Path(__file__).resolve().parents[1]
        log = root / "sample_prompts.txt"
        if not log.exists():
            self.skipTest("sample_prompts.txt missing")
        with tempfile.TemporaryDirectory() as tmp:
            memory = Path(tmp)
            rc = ce.main(
                [
                    "extract",
                    "--memory-dir",
                    str(memory),
                    "--workspace-root",
                    str(root),
                    str(log),
                ]
            )
            self.assertEqual(rc, 0)
            written = list(memory.glob("conversation_extract_*"))
            self.assertGreaterEqual(len(written), 2)


if __name__ == "__main__":
    unittest.main()
