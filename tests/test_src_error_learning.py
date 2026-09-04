from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.self_improvement import error_learning  # noqa: E402


class SrcErrorLearningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.log_path = self.root / ".learnings" / "errors.log"

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def read_lines(self) -> list[dict[str, object]]:
        if not self.log_path.exists():
            return []
        out: list[dict[str, object]] = []
        for line in self.log_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(json.loads(line))
        return out

    def run_cli(self, *args: str) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        env = dict(os.environ)
        env.pop("NO_COLOR", None)
        with mock.patch.dict(os.environ, env, clear=True), mock.patch("sys.stdout", stdout), mock.patch(
            "sys.stderr", stderr
        ):
            exit_code = error_learning.main(["--log-path", str(self.log_path), *args])
        return exit_code, stdout.getvalue(), stderr.getvalue()

    def test_capture_persists_jsonl_and_deduplicates(self) -> None:
        code, out, err = self.run_cli(
            "add",
            "ollama batch download",
            "Connection refused to 10.0.0.1:443",
            "Retry via secondary endpoint",
        )
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertIn("Saved error learning entry.", out)

        code2, out2, _ = self.run_cli(
            "add",
            "ollama batch download",
            "Connection refused to 10.0.0.1:443",
            "Retry via secondary endpoint",
        )
        self.assertEqual(code2, 0)
        self.assertIn("Duplicate entry detected", out2)

        lines = self.read_lines()
        self.assertEqual(len(lines), 1)
        entry = lines[0]
        self.assertEqual(
            set(entry.keys()),
            {"schema_version", "id", "timestamp", "context", "root_cause", "fix_applied", "resolved"},
        )
        self.assertEqual(entry["context"], "ollama batch download")
        self.assertEqual(entry["root_cause"], "Connection refused to 10.0.0.1:443")
        self.assertEqual(entry["fix_applied"], "Retry via secondary endpoint")
        self.assertTrue(entry["resolved"])

    def test_suggest_matches_similar_signatures(self) -> None:
        error_learning.capture_error(
            self.log_path,
            "deploy",
            "Connection refused to 10.0.0.1:443",
            "Use failover host",
        )
        error_learning.capture_error(
            self.log_path,
            "deploy",
            "Connection refused to 192.168.0.2:443",
            "Use failover host",
        )

        code, out, err = self.run_cli("suggest", "Connection refused to 203.0.113.9:443")
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertIn("failover host", out)
        self.assertIn("Fix applied:", out)

    def test_patterns_groups_recurring_root_causes(self) -> None:
        error_learning.capture_error(self.log_path, "a", "Error on line 42 in /tmp/foo.py", "fix1")
        error_learning.capture_error(self.log_path, "b", "Error on line 99 in /tmp/foo.py", "fix2")

        code, out, err = self.run_cli("patterns", "--min-count", "2")
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertIn("Recurring error patterns", out)
        self.assertIn("×2", out)

    def test_add_surfaces_prior_same_shape(self) -> None:
        error_learning.capture_error(self.log_path, "t", "Error on line 1 in /x/a.py", "first")
        error_learning.capture_error(self.log_path, "t", "Error on line 2 in /x/b.py", "second")
        code, out, err = self.run_cli(
            "add",
            "t",
            "Error on line 3 in /x/c.py",
            "third",
        )
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertIn("Earlier fixes for the same error shape (2)", out)
        self.assertIn("first", out)

    def test_module_main_entrypoint(self) -> None:
        result = subprocess_run_module(["--help"])
        self.assertEqual(result.returncode, 0)
        self.assertIn("error learning", result.stdout.lower())


def subprocess_run_module(argv: list[str]):
    import subprocess

    return subprocess.run(
        [sys.executable, "-m", "src.self_improvement.error_learning", *argv],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )


if __name__ == "__main__":
    unittest.main()
