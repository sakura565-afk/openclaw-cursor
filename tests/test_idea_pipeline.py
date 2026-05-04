import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path

from src.ideation.idea_pipeline import IdeaPipeline, main


class IdeaPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.project_root = Path(self.temp_dir.name)
        fixed_now = datetime(2026, 5, 4, 19, 41, 0, tzinfo=timezone.utc)
        self.pipeline = IdeaPipeline(
            project_root=self.project_root,
            now_func=lambda: fixed_now,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_research_phase_creates_daily_log(self) -> None:
        result = self.pipeline.run_phase("research", "OpenClaw mission planner")

        self.assertEqual(result["phase"], "research")
        self.assertTrue(result["log_path"].endswith("idea_pipeline_20260504.json"))

        log_path = Path(result["log_path"])
        self.assertTrue(log_path.exists())

        payload = json.loads(log_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["date"], "2026-05-04")
        self.assertEqual(payload["ideas"][0]["topic"], "OpenClaw mission planner")
        self.assertEqual(payload["ideas"][0]["status"]["completed_phases"], ["research"])
        self.assertEqual(payload["ideas"][0]["status"]["current_phase"], "research")
        self.assertEqual(payload["ideas"][0]["status"]["pipeline_state"], "research_complete")

    def test_later_phase_backfills_prerequisites(self) -> None:
        result = self.pipeline.run_phase("pr", "Telemetry dashboard")

        self.assertEqual(result["phase"], "pr")
        log_path = Path(result["log_path"])
        payload = json.loads(log_path.read_text(encoding="utf-8"))
        phases = payload["ideas"][0]["phases"]

        self.assertEqual(list(phases.keys()), ["draft", "pr", "research", "review"])
        self.assertEqual(
            payload["ideas"][0]["status"]["completed_phases"],
            ["research", "draft", "review", "pr"],
        )
        self.assertEqual(payload["ideas"][0]["status"]["pipeline_state"], "ready_for_pr")
        self.assertTrue(result["result"]["title"].startswith("Idea pipeline: Telemetry dashboard"))
        self.assertEqual(result["result"]["tests"], ["python -m unittest tests.test_idea_pipeline"])

    def test_invalid_phase_returns_error_code(self) -> None:
        stdout = StringIO()
        stderr = StringIO()

        with redirect_stdout(stdout), redirect_stderr(stderr):
            exit_code = main(["ship", "Telemetry"])

        self.assertEqual(exit_code, 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("Unknown phase 'ship'", stderr.getvalue())

    def test_cli_prints_json_for_successful_run(self) -> None:
        stdout = StringIO()
        stderr = StringIO()
        fixed_now = datetime(2026, 5, 4, 19, 41, 0, tzinfo=timezone.utc)

        with tempfile.TemporaryDirectory() as temp_dir:
            original_cwd = Path.cwd()
            try:
                import os

                os.chdir(temp_dir)
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    exit_code = main(["review", "Autonomous research mode"])
            finally:
                os.chdir(original_cwd)

        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr.getvalue(), "")
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["phase"], "review")
        self.assertEqual(payload["topic"], "Autonomous research mode")
        self.assertTrue(payload["result"]["issues"])


if __name__ == "__main__":
    unittest.main()
