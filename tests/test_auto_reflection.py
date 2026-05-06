import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from src.self_improvement.auto_reflection import AutoReflectionEngine, main


class AutoReflectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.logs = self.root / "logs"
        self.logs.mkdir(parents=True, exist_ok=True)

    def _write_json_transcript(self, name: str, messages) -> Path:
        path = self.logs / name
        path.write_text(json.dumps({"messages": messages}), encoding="utf-8")
        return path

    def test_run_writes_reflection_and_metrics(self) -> None:
        self._write_json_transcript(
            "transcript_recent.json",
            [
                {"role": "user", "content": "Please run tests and share next steps."},
                {"role": "assistant", "content": "I will run pytest. next step is fixing lint."},
                {"role": "assistant", "content": "No error found in test execution."},
            ],
        )

        engine = AutoReflectionEngine(root_dir=self.root, transcript_dirs=[self.logs])
        result = engine.run(days=7)

        reflection_file = Path(result["reflection_file"])
        self.assertTrue(reflection_file.exists())
        self.assertIn("auto_reflection_", reflection_file.name)

        metrics_path = self.root / ".learnings" / "quality_metrics.json"
        self.assertTrue(metrics_path.exists())
        metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
        self.assertEqual(metrics["runs"], 1)
        self.assertEqual(metrics["totals"]["transcripts_scanned"], 1)
        self.assertIn("rolling", metrics)

    def test_days_filter_excludes_old_transcripts(self) -> None:
        recent = self._write_json_transcript(
            "transcript_recent.json",
            [{"role": "user", "content": "new conversation"}],
        )
        old = self._write_json_transcript(
            "transcript_old.json",
            [{"role": "user", "content": "old conversation"}],
        )
        old_time = datetime.now(timezone.utc) - timedelta(days=15)
        recent_time = datetime.now(timezone.utc) - timedelta(days=1)
        old_ts = old_time.timestamp()
        recent_ts = recent_time.timestamp()
        old.touch()
        recent.touch()
        import os

        os.utime(old, (old_ts, old_ts))
        os.utime(recent, (recent_ts, recent_ts))

        engine = AutoReflectionEngine(root_dir=self.root, transcript_dirs=[self.logs])
        summary = engine.summary(days=7)
        self.assertEqual(summary["totals"]["transcripts_scanned"], 1)

    def test_digest_command_returns_markdown(self) -> None:
        self._write_json_transcript(
            "transcript_recent.json",
            [
                {"role": "user", "content": "please clarify the failure"},
                {"role": "assistant", "content": "next step is adding tests"},
            ],
        )
        engine = AutoReflectionEngine(root_dir=self.root, transcript_dirs=[self.logs])
        digest = engine.digest(days=7)
        self.assertIn("# Auto Reflection Digest", digest)
        self.assertIn("## Overview", digest)
        self.assertIn("## Top repeated patterns", digest)

    def test_cli_summary_command(self) -> None:
        self._write_json_transcript(
            "transcript_cli.json",
            [{"role": "user", "content": "run test suite"}],
        )
        with mock.patch("sys.stdout.write") as write_mock:
            exit_code = main(
                [
                    "summary",
                    "--days",
                    "7",
                    "--root-dir",
                    str(self.root),
                    "--transcript-dir",
                    str(self.logs),
                ]
            )
        self.assertEqual(exit_code, 0)
        rendered = "".join(call.args[0] for call in write_mock.call_args_list)
        self.assertIn('"lookback_days": 7', rendered)


if __name__ == "__main__":
    unittest.main()
