"""Tests for scripts.conversation_extractor."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from scripts import conversation_extractor as ce

ROOT = Path(__file__).resolve().parents[1]


class ConversationExtractorTests(unittest.TestCase):
    def test_analyze_segments_decisions_errors_followups_flags(self) -> None:
        segments = [
            (1, "assistant", "Decision: ship the hotfix branch first.\nTODO: verify staging."),
            (2, "tool", "run_tests"),
            (3, "tool_output", "TypeError: NoneType has no attribute 'split'"),
            (4, "user", "Follow-up: schedule a retro."),
            (5, "assistant", "Decision: ship the hotfix branch first."),  # duplicate opener
        ]
        d = ce.analyze_segments(segments, "inline")
        self.assertTrue(any("ship the hotfix" in x for x in d.decisions))
        self.assertTrue(any("TypeError" in x or "NoneType" in x for x in d.errors))
        self.assertTrue(any("retro" in x.lower() for x in d.followups))
        codes = {f.get("code") for f in d.flags}
        self.assertIn("todo_marker", codes)
        self.assertIn("possible_duplicate_decisions", codes)

    def test_format_summary_json_roundtrip_keys(self) -> None:
        segments = [(1, "assistant", "Decision: go.\nNext steps: document API.")]
        d = ce.analyze_segments(segments, "t")
        raw = ce.format_summary(d, "json")
        data = json.loads(raw)
        self.assertIn("headline", data)
        self.assertIn("counts", data)
        self.assertIn("flags", data)

    def test_summarize_from_legacy_export_without_errors_key(self) -> None:
        legacy = {
            "source": "old.json",
            "counts": {"segments": 3, "decisions": 1, "learnings": 0, "tool_names_distinct": 1},
            "decisions": ["Use plan B"],
            "learnings": [],
            "tools_ranked": [["grep", 2]],
            "tools_structured": {},
            "tools_from_text_heuristic": {},
        }
        md = ce.format_summary_from_export(legacy, "md")
        self.assertIn("Transcript summary", md)
        self.assertIn("Use plan B", md)

    def test_cli_legacy_file_invokes_extract(self) -> None:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT)

        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            log = td_path / "session.log"
            log.write_text(
                textwrap.dedent(
                    """
                    assistant: Decision: archive old logs.
                    user: OK.
                    """
                ).strip(),
                encoding="utf-8",
            )
            mem = td_path / "mem"
            r = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "scripts.conversation_extractor",
                    str(log),
                    "--memory-dir",
                    str(mem),
                ],
                cwd=str(ROOT),
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            written = list(mem.glob("conversation_extract_*"))
            self.assertGreaterEqual(len(written), 2, msg=r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
