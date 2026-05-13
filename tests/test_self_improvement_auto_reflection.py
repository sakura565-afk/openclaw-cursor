"""Tests for scripts.self_improvement.auto_reflection."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.self_improvement import auto_reflection as ar


class SelfImprovementAutoReflectionTests(unittest.TestCase):
    def test_run_writes_daily_and_learnings(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "memory").mkdir(parents=True)
            (root / "memory" / "session.md").write_text(
                "Error: connection refused when calling the API.\n"
                "Fixed: switched base URL to the staging gateway.\n"
                "Lesson learned: verify environment before batch runs.\n",
                encoding="utf-8",
            )

            result = ar.run_daily_memory_reflection(root, lookback_days=7, dry_run=False)

            self.assertTrue(result.daily_path.exists())
            text = result.daily_path.read_text(encoding="utf-8")
            self.assertIn(ar.MARKER_START, text)
            self.assertIn("### Errors found", text)
            self.assertIn("### Corrections made", text)
            self.assertIn("### Patterns detected", text)
            self.assertIn("### Suggested actions", text)

            learn = root / ".learnings" / "LEARNINGS.md"
            self.assertTrue(learn.exists())
            body = learn.read_text(encoding="utf-8")
            self.assertIn("Lesson learned", body)

    def test_dry_run_skips_writes(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "memory").mkdir()
            (root / "memory" / "x.md").write_text("Error: oops\n", encoding="utf-8")
            result = ar.run_daily_memory_reflection(root, lookback_days=7, dry_run=True)
            self.assertFalse((root / ".learnings").exists())
            self.assertFalse(result.daily_path.exists())

    def test_learnings_dedupes_same_insight_text(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "memory").mkdir()
            (root / "memory" / "a.md").write_text("Error: duplicate signal\n", encoding="utf-8")
            ar.run_daily_memory_reflection(root, lookback_days=7, dry_run=False)
            ar.run_daily_memory_reflection(root, lookback_days=7, dry_run=False)
            learn = root / ".learnings" / "LEARNINGS.md"
            count = learn.read_text(encoding="utf-8").count("duplicate signal")
            self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()
