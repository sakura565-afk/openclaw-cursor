from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import error_learning  # noqa: E402


class ErrorLearningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.errors_dir = self.root / ".learnings" / "errors"

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def day_path(self) -> Path:
        return error_learning.day_file_path(self.errors_dir, datetime.now(timezone.utc).date())

    def run_cli(self, *args: str) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        env = dict(os.environ)
        env.pop("NO_COLOR", None)
        with mock.patch.dict(os.environ, env, clear=True), mock.patch("sys.stdout", stdout), mock.patch(
            "sys.stderr", stderr
        ):
            exit_code = error_learning.main(["--errors-dir", str(self.errors_dir), *args])
        return exit_code, stdout.getvalue(), stderr.getvalue()

    def test_log_persists_per_day_json_and_deduplicates_same_day(self) -> None:
        exit_code, first_stdout, first_stderr = self.run_cli(
            "log",
            "RuntimeError",
            "OpenClaw session crashed after a timeout",
            "--file",
            "src/foo.py",
            "--func",
            "run_batch",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(first_stderr, "")
        self.assertIn("Logged new error entry.", first_stdout)

        path = self.day_path()
        self.assertTrue(path.exists())
        doc = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(doc["schema_version"], 1)
        utc_today = datetime.now(timezone.utc).date()
        self.assertEqual(doc["date"], utc_today.isoformat())
        self.assertEqual(len(doc["entries"]), 1)
        entry = doc["entries"][0]
        self.assertEqual(
            set(entry.keys()),
            {"id", "fingerprint", "timestamp", "error_type", "message", "file", "function"},
        )
        self.assertEqual(entry["error_type"], "RuntimeError")
        self.assertEqual(entry["file"], "src/foo.py")
        self.assertEqual(entry["function"], "run_batch")

        exit_code, second_stdout, second_stderr = self.run_cli(
            "log",
            "RuntimeError",
            "different message same fingerprint",
            "--file",
            "src/foo.py",
            "--func",
            "run_batch",
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(second_stderr, "")
        self.assertIn("Duplicate fingerprint for today", second_stdout)

        doc2 = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(len(doc2["entries"]), 1)
        self.assertEqual(doc2["entries"][0]["message"], entry["message"])

    def test_pattern_seen_before_reports_timestamps(self) -> None:
        e1, _ = error_learning.log_error(
            self.errors_dir,
            "ValueError",
            "bad value",
            file="a.py",
            function="f",
        )
        hist = error_learning.pattern_seen_before(
            self.errors_dir,
            "ValueError",
            file="a.py",
            function="f",
        )
        self.assertTrue(hist.seen)
        self.assertEqual(hist.first_seen_iso, e1["timestamp"])
        self.assertEqual(hist.last_seen_iso, e1["timestamp"])

    def test_seen_subcommand(self) -> None:
        error_learning.log_error(self.errors_dir, "KeyError", "missing key", file="cfg.json", function="load")
        code, out, err = self.run_cli("seen", "KeyError", "--file", "cfg.json", "--func", "load")
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertIn("Fingerprint matched", out)
        self.assertIn("Occurrences:", out)

        code2, out2, err2 = self.run_cli("seen", "DoesNotExist")
        self.assertEqual(code2, 0)
        self.assertIn("No matching error fingerprint", out2)

    def test_review_shows_recent_entries_and_suggestions(self) -> None:
        error_learning.log_error(self.errors_dir, "KeyError", "missing 'timeout' in config", file="x.py")
        error_learning.log_error(self.errors_dir, "Warning", "disk low", function="check_disk")

        exit_code, stdout, stderr = self.run_cli("review", "--limit", "5", "--days", "1")

        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr, "")
        self.assertIn("KeyError", stdout)
        self.assertIn("Suggested fix:", stdout)
        self.assertIn("dict.get", stdout.lower())

    def test_review_empty_directory(self) -> None:
        exit_code, stdout, stderr = self.run_cli("review")
        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr, "")
        self.assertIn("No entries found.", stdout)


if __name__ == "__main__":
    unittest.main()
