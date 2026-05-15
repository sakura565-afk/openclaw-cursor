"""Tests for src.self_improvement.auto_reflection (weekly cron)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.auto_reflection import Insight

from src.self_improvement import auto_reflection as weekly


class WeeklyAutoReflectionTests(unittest.TestCase):
    def test_iso_week_label_format(self):
        dt = weekly.utc_now().replace(year=2026, month=5, day=15)
        self.assertRegex(weekly.iso_week_label(dt), r"^\d{4}-W\d{2}$")

    def test_rank_top_errors_prefers_loss_and_error_severity(self):
        insights = [
            Insight(text="minor note", category="general", severity="info"),
            Insight(text="Traceback in deploy", category="loss", severity="error", source_paths=["a", "b"]),
            Insight(text="timeout on API", category="integration", severity="warning"),
        ]
        top = weekly.rank_top_errors(insights, limit=3)
        self.assertEqual(top[0].text, "Traceback in deploy")

    def test_rank_top_wins_filters_win_category(self):
        insights = [
            Insight(text="fixed caching", category="win", source_paths=["x"]),
            Insight(text="random error", category="loss", severity="error"),
        ]
        top = weekly.rank_top_wins(insights, limit=3)
        self.assertEqual(len(top), 1)
        self.assertEqual(top[0].category, "win")

    def test_build_weekly_report_contains_required_sections(self):
        md = weekly.build_weekly_report_markdown(
            weekly.utc_now(),
            files_scanned=2,
            window_start=weekly.utc_now(),
            window_end=weekly.utc_now(),
            top_errors=[Insight(text="Error: db down", category="loss", severity="error")],
            top_wins=[Insight(text="All tests pass", category="win")],
            agents_recommendations=["Add rule: verify migrations before deploy."],
            insights=[],
        )
        self.assertIn("## Top 3 errors", md)
        self.assertIn("## Top 3 wins", md)
        self.assertIn("## Recommended AGENTS.md updates", md)
        self.assertIn("Error: db down", md)
        self.assertIn("All tests pass", md)

    def test_run_weekly_reflection_writes_weekly_file(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "logs").mkdir(parents=True)
            (root / "logs" / "run.log").write_text(
                "Fatal: database migration failed.\n"
                "Lesson learned: run migrations in staging first.\n"
                "Fixed: cache invalidation now works.\n",
                encoding="utf-8",
            )

            run_dry = weekly.run_weekly_reflection(
                root,
                since_hours=24,
                dry_run=True,
            )
            self.assertFalse((root / ".learnings" / "weekly-reflections").exists())
            self.assertGreaterEqual(len(run_dry.top_errors), 1)

            run = weekly.run_weekly_reflection(root, since_hours=24, dry_run=False, force=True)
            report = root / ".learnings" / "weekly-reflections" / f"{run.iso_week}.md"
            self.assertTrue(report.exists())
            body = report.read_text(encoding="utf-8")
            self.assertIn("## Top 3 errors", body)
            self.assertIn("## Top 3 wins", body)
            self.assertIn("## Recommended AGENTS.md updates", body)

            latest = json.loads((root / ".learnings" / "latest_weekly.json").read_text(encoding="utf-8"))
            self.assertEqual(latest["iso_week"], run.iso_week)

    def test_main_module_entrypoint(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "logs").mkdir()
            (root / "logs" / "x.log").write_text("Error: something failed.\n", encoding="utf-8")
            with mock.patch.object(sys, "argv", ["auto_reflection", "--root", str(root), "--dry-run"]):
                rc = weekly.main(["--root", str(root), "--since-hours", "24", "--dry-run"])
            self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
