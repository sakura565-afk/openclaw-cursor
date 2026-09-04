from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def _load_error_learning_module():
    path = ROOT / ".learnings" / "error_learning.py"
    spec = importlib.util.spec_from_file_location("dotlearnings_error_learning", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class DotLearningsErrorLearningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.openclaw_home = Path(self.tempdir.name) / "oc"
        self.openclaw_home.mkdir(parents=True)
        self.env_patch = mock.patch.dict(os.environ, {"OPENCLAW_HOME": str(self.openclaw_home)}, clear=False)
        self.env_patch.start()
        self.el = _load_error_learning_module()

    def tearDown(self) -> None:
        self.env_patch.stop()
        self.tempdir.cleanup()

    def log_file(self) -> Path:
        return self.el.default_errors_path()

    def test_default_errors_path_under_openclaw_home(self) -> None:
        expected = (self.openclaw_home / ".learnings" / "errors.jsonl").resolve()
        self.assertEqual(self.el.default_errors_path(), expected)

    def test_log_and_read_roundtrip(self) -> None:
        try:
            raise ValueError("boom")
        except ValueError as exc:
            self.el.log_tool_error("Shell", {"command": "ls -la"}, exc)

        lines = self.log_file().read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 1)
        row = json.loads(lines[0])
        self.assertEqual(row["tool"], "Shell")
        self.assertEqual(row["args"]["command"], "ls -la")
        self.assertEqual(row["error_type"], "ValueError")
        self.assertEqual(row["error_message"], "boom")
        self.assertIn("timestamp", row)

        recent = self.el.read_recent_errors(5, log_path=self.log_file())
        self.assertEqual(len(recent), 1)
        self.assertEqual(recent[0]["error_message"], "boom")

    def test_run_tool_with_logging_reraises_and_logs(self) -> None:
        def boom() -> None:
            raise RuntimeError("nope")

        with self.assertRaises(RuntimeError):
            self.el.run_tool_with_logging("Grep", {"pattern": "x"}, boom)

        recent = self.el.read_recent_errors(10, log_path=self.log_file())
        self.assertEqual(len(recent), 1)
        self.assertEqual(recent[0]["tool"], "Grep")

    def test_cli_recent_json(self) -> None:
        self.el.log_tool_error("Read", {"path": "/tmp/a"}, OSError("missing"))
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch("sys.stdout", stdout), mock.patch("sys.stderr", stderr):
            code = self.el.main(["recent", "--limit", "3", "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(stderr.getvalue(), "")
        data = json.loads(stdout.getvalue())
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["tool"], "Read")


if __name__ == "__main__":
    unittest.main()
