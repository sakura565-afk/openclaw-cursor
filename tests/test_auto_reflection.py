import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "auto_reflection.py"
SPEC = importlib.util.spec_from_file_location("auto_reflection", MODULE_PATH)
auto_reflection = importlib.util.module_from_spec(SPEC)
sys.modules["auto_reflection"] = auto_reflection
assert SPEC.loader is not None
SPEC.loader.exec_module(auto_reflection)


class AutoReflectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 5, 6, 6, 10, tzinfo=timezone.utc)

    def write_json_transcript(self, path: Path) -> None:
        payload = {
            "messages": [
                {
                    "role": "user",
                    "content": "Need to add a self-reflection cron script and keep the schedule in config.",
                },
                {
                    "role": "assistant",
                    "content": (
                        "Implemented scripts/auto_reflection.py and added tests. "
                        "Focused verification passed after the change."
                    ),
                },
                {
                    "role": "assistant",
                    "content": (
                        "The first parsing attempt failed because the session storage path was missing, "
                        "so the config should capture the right directory."
                    ),
                },
            ]
        }
        path.write_text(json.dumps(payload), encoding="utf-8")

    def write_text_transcript(self, path: Path) -> None:
        path.write_text(
            "\n".join(
                [
                    "2026-05-06 05:30 - Tests passed after the refactor.",
                    "2026-05-06 05:35 - Missing session path caused an error in the first run.",
                    "2026-05-06 05:40 - Next step: document the schedule and add tests for summary output.",
                    "2026-05-06 05:45 - Focus stayed on scripts/auto_reflection.py and tests/test_auto_reflection.py.",
                ]
            ),
            encoding="utf-8",
        )

    def write_config(self, root: Path, interval_hours: int = 24) -> Path:
        config = {
            "lookback_days": 2,
            "max_chars_per_transcript": 40000,
            "max_transcripts": 10,
            "schedule": {
                "enabled": True,
                "minimum_interval_hours": interval_hours,
            },
            "session_roots": [
                "{openclaw_home}/sessions",
            ],
            "transcript_extensions": [".json", ".log", ".txt"],
        }
        config_path = root / ".learnings" / "auto_reflection_config.json"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        return config_path

    def test_run_creates_reflection_and_quality_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            openclaw_home = root / "mock-home"
            session_dir = openclaw_home / "sessions"
            session_dir.mkdir(parents=True)
            self.write_config(root)
            self.write_json_transcript(session_dir / "session-a.json")
            self.write_text_transcript(session_dir / "session-b.log")

            with patch.dict(os.environ, {"OPENCLAW_HOME": str(openclaw_home)}, clear=False):
                stdout = io.StringIO()
                with redirect_stdout(stdout):
                    exit_code = auto_reflection.main(["run", "--days", "2"], root=root, now=self.now)

            self.assertEqual(exit_code, 0)
            output = stdout.getvalue()
            self.assertIn("Wrote reflection", output)
            self.assertIn("Quality score", output)

            reflection_path = root / "memory" / "2026-05-06-reflection.md"
            metrics_path = root / ".learnings" / "quality_metrics.json"
            state_path = root / ".learnings" / "auto_reflection_state.json"

            self.assertTrue(reflection_path.exists())
            self.assertTrue(metrics_path.exists())
            self.assertTrue(state_path.exists())

            reflection = reflection_path.read_text(encoding="utf-8")
            self.assertIn("## What went well", reflection)
            self.assertIn("## What went wrong", reflection)
            self.assertIn("## Actionable insights", reflection)
            self.assertIn("scripts/auto_reflection.py", reflection)

            metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
            self.assertIn("current_score", metrics)
            self.assertEqual(metrics["history"][0]["date"], "2026-05-06")
            self.assertEqual(metrics["history"][0]["transcript_count"], 2)

    def test_schedule_blocks_second_run_without_force(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            openclaw_home = root / "mock-home"
            session_dir = openclaw_home / "sessions"
            session_dir.mkdir(parents=True)
            self.write_config(root, interval_hours=24)
            self.write_text_transcript(session_dir / "session-b.log")

            with patch.dict(os.environ, {"OPENCLAW_HOME": str(openclaw_home)}, clear=False):
                first_stdout = io.StringIO()
                with redirect_stdout(first_stdout):
                    first_exit = auto_reflection.main(["run"], root=root, now=self.now)

                second_stdout = io.StringIO()
                with redirect_stdout(second_stdout):
                    second_exit = auto_reflection.main(
                        ["run"],
                        root=root,
                        now=self.now + timedelta(hours=1),
                    )

            self.assertEqual(first_exit, 0)
            self.assertEqual(second_exit, 0)
            self.assertIn("Skipping reflection until schedule is due", second_stdout.getvalue())

            metrics = json.loads((root / ".learnings" / "quality_metrics.json").read_text(encoding="utf-8"))
            self.assertEqual(len(metrics["history"]), 1)

    def test_summary_and_digest_render_cached_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            openclaw_home = root / "mock-home"
            session_dir = openclaw_home / "sessions"
            session_dir.mkdir(parents=True)
            self.write_config(root)
            self.write_text_transcript(session_dir / "session-b.log")

            with patch.dict(os.environ, {"OPENCLAW_HOME": str(openclaw_home)}, clear=False):
                auto_reflection.main(["run", "--force"], root=root, now=self.now)

                summary_stdout = io.StringIO()
                with redirect_stdout(summary_stdout):
                    summary_exit = auto_reflection.main(["summary"], root=root, now=self.now)

                digest_stdout = io.StringIO()
                with redirect_stdout(digest_stdout):
                    digest_exit = auto_reflection.main(["digest"], root=root, now=self.now)

            self.assertEqual(summary_exit, 0)
            self.assertEqual(digest_exit, 0)
            self.assertIn("OpenClaw reflection summary", summary_stdout.getvalue())
            self.assertIn("Key insights", summary_stdout.getvalue())
            self.assertIn("Reflection digest for 2026-05-06.", digest_stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
