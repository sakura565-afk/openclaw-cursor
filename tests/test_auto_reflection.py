"""Tests for scripts.auto_reflection."""

from __future__ import annotations

import importlib.util
import io
import sys
import tempfile
import unittest
from datetime import timedelta, timezone
from pathlib import Path
from unittest import mock

MODULE_PATH = Path(__file__).resolve().parent.parent / "scripts" / "auto_reflection.py"
SPEC = importlib.util.spec_from_file_location("auto_reflection", MODULE_PATH)
auto_reflection = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules.setdefault("auto_reflection", auto_reflection)
SPEC.loader.exec_module(auto_reflection)


class AutoReflectionHelpersTests(unittest.TestCase):
    def test_validate_cron_expression(self):
        self.assertTrue(auto_reflection.validate_cron_expression("0 9 * * *"))
        self.assertTrue(auto_reflection.validate_cron_expression("0 0 * * 0"))
        self.assertFalse(auto_reflection.validate_cron_expression("not a cron"))
        self.assertFalse(auto_reflection.validate_cron_expression(""))

    def test_filter_sessions_since_keeps_unknown_timestamps(self):
        since = auto_reflection.utc_now() - timedelta(hours=1)
        rows = [
            {"key": "a", "updatedAt": since.astimezone(timezone.utc).isoformat()},
            {"key": "b"},
        ]
        out = auto_reflection.filter_sessions_since(rows, since)
        self.assertEqual(len(out), 2)

    def test_resolve_output_path_file_vs_dir(self):
        day = auto_reflection.utc_now()
        with tempfile.TemporaryDirectory() as mem_raw:
            mem = Path(mem_raw)
            p = auto_reflection.resolve_output_path(None, memory_dir=mem, day=day)
            self.assertTrue(p.name.endswith("-reflection.md"))
            self.assertEqual(p.parent, mem)
            with tempfile.TemporaryDirectory() as d_raw:
                d = Path(d_raw)
                p2 = auto_reflection.resolve_output_path(str(d), memory_dir=mem, day=day)
                self.assertTrue(p2.name.endswith("-reflection.md"))
                self.assertEqual(p2.parent, d.resolve())


class AutoReflectionRunOnceTests(unittest.TestCase):
    def test_skips_write_when_no_sessions(self):
        with tempfile.TemporaryDirectory() as raw:
            ws = Path(raw)
            (ws / "memory").mkdir(parents=True)
            with mock.patch.object(auto_reflection, "sessions_list", return_value={"sessions": []}):
                rc, path = auto_reflection.run_once(
                    hours=1.0,
                    output=None,
                    workspace=ws,
                    all_agents=True,
                    list_limit=50,
                    history_last=10,
                    max_sessions=5,
                    dry_run=False,
                    cron_note=None,
                )
            self.assertEqual(rc, 0)
            self.assertIsNone(path)
            self.assertFalse(any((ws / "memory").glob("*-reflection.md")))

    def test_writes_reflection_with_expected_sections(self):
        with tempfile.TemporaryDirectory() as raw:
            ws = Path(raw)
            (ws / "memory").mkdir(parents=True)

            listed = {
                "sessions": [
                    {"key": "agent:test:main", "updatedAt": auto_reflection.utc_now().isoformat()},
                ]
            }
            hist = {
                "messages": [
                    {"role": "assistant", "content": "Lesson learned: add retries around flaky tools."},
                    {"role": "assistant", "content": "Error: simulated failure for unit test."},
                    {"role": "assistant", "content": "Task completed: shipped the cron script."},
                ]
            }

            with mock.patch.object(auto_reflection, "sessions_list", return_value=listed):
                with mock.patch.object(auto_reflection, "sessions_history", return_value=hist):
                    rc, path = auto_reflection.run_once(
                        hours=2.0,
                        output=None,
                        workspace=ws,
                        all_agents=True,
                        list_limit=50,
                        history_last=50,
                        max_sessions=5,
                        dry_run=False,
                        cron_note="0 9 * * *",
                    )

            self.assertEqual(rc, 0)
            assert path is not None
            self.assertTrue(path.exists())
            body = path.read_text(encoding="utf-8")
            for heading in ("## Summary", "## Wins", "## Challenges", "## Learnings", "## Next Steps"):
                self.assertIn(heading, body)
            self.assertIn("agent:test:main", body)
            self.assertIn("Schedule note", body)


class AutoReflectionMainTests(unittest.TestCase):
    def test_main_rejects_daemon_without_cron(self):
        stderr = io.StringIO()
        with mock.patch.object(auto_reflection.sys, "stderr", stderr):
            rc = auto_reflection.main(["--daemon"])
        self.assertEqual(rc, 2)

    def test_main_dry_run_prints_markdown(self):
        with tempfile.TemporaryDirectory() as raw:
            ws = Path(raw)
            (ws / "memory").mkdir(parents=True)
            listed = {"sessions": [{"key": "k1"}]}
            hist = {"messages": [{"role": "user", "content": "hello"}]}
            stdout = io.StringIO()
            stderr = io.StringIO()
            with mock.patch.object(auto_reflection, "sessions_list", return_value=listed):
                with mock.patch.object(auto_reflection, "sessions_history", return_value=hist):
                    with mock.patch.object(auto_reflection.sys, "stdout", stdout):
                        with mock.patch.object(auto_reflection.sys, "stderr", stderr):
                            rc = auto_reflection.main(
                                [
                                    "--workspace",
                                    str(ws),
                                    "--dry-run",
                                    "--hours",
                                    "1",
                                    "--max-sessions",
                                    "1",
                                ]
                            )
            self.assertEqual(rc, 0)
            self.assertIn("## Summary", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
