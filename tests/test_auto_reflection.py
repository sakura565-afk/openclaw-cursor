"""Tests for scripts.auto_reflection."""

from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

MODULE_PATH = Path(__file__).resolve().parent.parent / "scripts" / "auto_reflection.py"
SPEC = importlib.util.spec_from_file_location("auto_reflection", MODULE_PATH)
auto_reflection = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules.setdefault("auto_reflection", auto_reflection)
SPEC.loader.exec_module(auto_reflection)


class AutoReflectionTests(unittest.TestCase):
    def test_dedupe_events_merges_severity(self) -> None:
        a = auto_reflection.ReflectionEvent(
            kind="error",
            text="Error: timeout contacting host",
            source="a.json",
            severity="warning",
        )
        b = auto_reflection.ReflectionEvent(
            kind="error",
            text="Error: timeout contacting host",
            source="b.json",
            severity="error",
        )
        out = auto_reflection.dedupe_events([a, b])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].severity, "error")

    def test_json_session_surfaces_nested_errors(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            home = Path(raw) / "oc"
            sdir = home / "sessions"
            sdir.mkdir(parents=True)
            path = sdir / "agent.json"
            path.write_text(
                json.dumps(
                    {
                        "turns": [
                            {"detail": "Traceback (most recent call last): simulated failure"},
                            {"message": "all good"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            events = list(auto_reflection.extract_events_for_file(path, sdir))
        kinds = [e.kind for e in events]
        self.assertIn("error", kinds)

    def test_run_reflection_writes_dated_learnings(self) -> None:
        fixed = datetime(2040, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as raw:
            home = Path(raw) / "oc"
            sess = home / "sessions" / "thr"
            sess.mkdir(parents=True)
            (sess / "t.log").write_text(
                "Lesson learned: verify credentials before calling the API.\n"
                "Decision: ship the minimal fix first, then iterate.\n",
                encoding="utf-8",
            )
            ts = fixed.timestamp()
            os.utime(sess / "t.log", (ts, ts))

            with mock.patch.object(auto_reflection, "utc_now", return_value=fixed):
                run = auto_reflection.run_reflection(home, days=7, dry_run=False)

            out = home / ".learnings" / "auto_reflection_2040-06-15.md"
            self.assertTrue(out.exists())
            self.assertEqual(run.output_path, str(out))
            body = out.read_text(encoding="utf-8")
            self.assertIn("openclaw-auto-reflection", body)
            self.assertIn("Lesson learned:", body)
            self.assertIn("ship the minimal fix", body)
            self.assertIn("Self-improvement", body)

    def test_run_reflection_dry_run_skips_write(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            home = Path(raw) / "oc"
            (home / "sessions").mkdir(parents=True)

            with mock.patch.object(auto_reflection, "utc_now", return_value=datetime.now(timezone.utc)):
                auto_reflection.run_reflection(home, days=1, dry_run=True)

            self.assertFalse((home / ".learnings").exists())


class AutoReflectionMainTests(unittest.TestCase):
    def test_main_writes_and_prints_summary(self) -> None:
        fixed = datetime(2040, 1, 2, 8, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as raw:
            home = Path(raw) / "oc"
            s = home / "sessions"
            s.mkdir(parents=True)
            (s / "x.md").write_text("Error: something failed in the session.\n", encoding="utf-8")
            ts = fixed.timestamp()
            os.utime(s / "x.md", (ts, ts))

            stderr = io.StringIO()
            stdout = io.StringIO()
            with mock.patch.object(auto_reflection, "utc_now", return_value=fixed):
                with mock.patch.object(auto_reflection.sys, "stderr", stderr):
                    with mock.patch.object(auto_reflection.sys, "stdout", stdout):
                        rc = auto_reflection.main(
                            [
                                "--openclaw-home",
                                str(home),
                                "--days",
                                "1",
                                "--stdout-summary",
                            ]
                        )

            self.assertEqual(rc, 0)
            self.assertIn("Wrote", stderr.getvalue())
            self.assertIn("OpenClaw auto-reflection", stdout.getvalue())
            written = home / ".learnings" / "auto_reflection_2040-01-02.md"
            self.assertTrue(written.exists())

    def test_main_rejects_non_positive_days(self) -> None:
        stderr = io.StringIO()
        with mock.patch.object(auto_reflection.sys, "stderr", stderr):
            rc = auto_reflection.main(["--days", "0", "--openclaw-home", "/tmp"])
        self.assertEqual(rc, 2)
        self.assertIn("--days", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
