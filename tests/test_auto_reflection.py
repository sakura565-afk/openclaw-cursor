import json
import os
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from src.self_improvement import auto_reflection
from src.self_improvement.auto_engine import AutoImprovementEngine


class AutoReflectionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        (self.root / "logs").mkdir(parents=True, exist_ok=True)
        self.learnings = self.root / ".learnings"
        self.learnings.mkdir(parents=True, exist_ok=True)
        self.sessions = self.root / "memory"
        self.sessions.mkdir(parents=True, exist_ok=True)

    def test_score_text_counts_signals(self):
        text = "We fixed the bug and tests passed. Later a timeout caused failure."
        s, f, r = auto_reflection.score_text(text)
        self.assertGreaterEqual(s, 2)
        self.assertGreaterEqual(f, 1)

    def test_build_report_and_writes_artifacts(self):
        (self.learnings / "note.md").write_text(
            "---\ntitle: CI hygiene\ntags: [stability, ci]\n---\n"
            "Resolved flaky tests; deployment succeeded.\n",
            encoding="utf-8",
        )
        tr = self.sessions / "session_transcript_1.txt"
        tr.write_text("error: unable to connect\n" * 3, encoding="utf-8")
        os.utime(tr, (time.time(), time.time()))

        config = auto_reflection.ReflectionConfig(
            root_dir=self.root,
            log_dir=self.root / "logs",
            learnings_dir=self.learnings,
            transcript_dirs=(self.sessions,),
            lookback_days=7,
        )
        fixed = datetime(2099, 5, 1, 12, 0, 0, tzinfo=timezone.utc)

        with mock.patch("src.self_improvement.auto_reflection._now_utc", return_value=fixed):
            report = auto_reflection.run_reflection(config)
            md_path = self.root / "logs" / "self_reflection_20990501.md"
            js_path = self.root / "logs" / "self_reflection_20990501.json"

        self.assertTrue(md_path.exists())
        self.assertTrue(js_path.exists())
        self.assertGreater(report.files_scanned, 0)
        payload = json.loads(js_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["lookback_days"], 7)
        self.assertIn("aggregate_success_hits", payload)

    def test_parse_path_list_respects_os_pathsep(self):
        with mock.patch("src.self_improvement.auto_reflection.os.pathsep", ";"):
            paths = auto_reflection._parse_path_list(r"C:\a\x;D:\b\y")
        self.assertEqual(len(paths), 2)

    def test_auto_engine_run_self_reflection_composable(self):
        engine = AutoImprovementEngine(root_dir=self.root, log_dir=self.root / "logs", color=False)
        (self.learnings / "tip.md").write_text("Worked well: resolved quickly.\n", encoding="utf-8")
        data = engine.run_self_reflection(lookback_days=1, transcript_dirs=(self.sessions,))
        self.assertIn("generated_at", data)
        self.assertTrue(any(self.root.joinpath("logs").glob("self_reflection_*.json")))


if __name__ == "__main__":
    unittest.main()
