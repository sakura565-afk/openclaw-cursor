from __future__ import annotations

import io
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import error_learning  # noqa: E402


SCRIPT_KEY = "scripts/example_tool.py"


class ErrorLearningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db_path = Path(os.environ.get("TMPDIR", "/tmp")) / f"err_hist_{os.getpid()}_test.json"
        if self.db_path.exists():
            self.db_path.unlink()

    def tearDown(self) -> None:
        if self.db_path.exists():
            self.db_path.unlink()

    def read_store(self) -> dict[str, object]:
        return json.loads(self.db_path.read_text(encoding="utf-8"))

    def run_cli(self, *args: str) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        env = dict(os.environ)
        env.pop("NO_COLOR", None)
        with mock.patch.dict(os.environ, env, clear=True), mock.patch("sys.stdout", stdout), mock.patch(
            "sys.stderr", stderr
        ):
            exit_code = error_learning.main(["--db", str(self.db_path), *args])
        return exit_code, stdout.getvalue(), stderr.getvalue()

    def test_record_exception_persists_context(self) -> None:
        try:
            raise ValueError("bad value")
        except ValueError as exc:
            rec = error_learning.record_exception(exc, SCRIPT_KEY, db_path=self.db_path)

        self.assertEqual(rec["script"], SCRIPT_KEY)
        self.assertEqual(rec["error_type"], "ValueError")
        self.assertIn("bad value", rec["message"])
        self.assertIn("ValueError: bad value", rec["traceback"])
        self.assertEqual(rec["occurrence_count"], 1)
        store = self.read_store()
        self.assertEqual(store["schema_version"], error_learning.SCHEMA_VERSION)
        self.assertEqual(len(store["records"]), 1)

    def test_repeat_exception_merges_by_fingerprint(self) -> None:
        for _ in range(2):
            try:
                raise RuntimeError("same")
            except RuntimeError as exc:
                error_learning.record_exception(exc, SCRIPT_KEY, db_path=self.db_path)
        store = self.read_store()
        self.assertEqual(len(store["records"]), 1)
        self.assertEqual(store["records"][0]["occurrence_count"], 2)

    def test_preflight_surfaces_prior_failure(self) -> None:
        try:
            raise OSError("disk full")
        except OSError as exc:
            error_learning.record_exception(exc, SCRIPT_KEY, db_path=self.db_path)
        warns = error_learning.preflight_warnings(SCRIPT_KEY, db_path=self.db_path)
        self.assertEqual(len(warns), 1)
        self.assertIn("OSError", warns[0].format(stream=sys.stderr))

    def test_capture_errors_context_reraises(self) -> None:
        with self.assertRaises(KeyError):
            with error_learning.capture_errors(SCRIPT_KEY, db_path=self.db_path):
                raise KeyError("missing")

        store = self.read_store()
        self.assertEqual(len(store["records"]), 1)

    def test_guard_main_logs_and_preflights(self) -> None:
        try:
            raise TypeError("t")
        except TypeError as exc:
            error_learning.record_exception(exc, SCRIPT_KEY, db_path=self.db_path)

        buf_out = io.StringIO()
        buf_err = io.StringIO()

        @error_learning.guard_main(script_key=SCRIPT_KEY, db_path=self.db_path)
        def boom() -> None:
            raise ValueError("second")

        with mock.patch("sys.stdout", buf_out), mock.patch("sys.stderr", buf_err):
            with self.assertRaises(ValueError):
                boom()

        self.assertIn("Prior errors", buf_err.getvalue())
        store = self.read_store()
        self.assertEqual(len(store["records"]), 2)

    def test_cli_show_clear_export(self) -> None:
        try:
            raise json.JSONDecodeError("msg", "doc", 0)
        except json.JSONDecodeError as exc:
            error_learning.record_exception(exc, SCRIPT_KEY, db_path=self.db_path)

        code, out, err = self.run_cli("show", "--limit", "5")
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertIn("JSONDecodeError", out)
        self.assertIn("Traceback (tail):", out)

        export_path = self.db_path.with_suffix(".export.json")
        try:
            code, out, err = self.run_cli("export", "-o", str(export_path))
            self.assertEqual(code, 0)
            self.assertIn("Exported", out)
            exported = json.loads(export_path.read_text(encoding="utf-8"))
            self.assertIn("exported_at", exported)
            self.assertEqual(len(exported["records"]), 1)

            code, out, err = self.run_cli("clear")
            self.assertEqual(code, 0)
            self.assertIn("Cleared 1", out)
            cleared = json.loads(self.db_path.read_text(encoding="utf-8"))
            self.assertEqual(cleared["records"], [])
        finally:
            export_path.unlink(missing_ok=True)

    def test_cli_list_alias(self) -> None:
        for cmd in ("show", "list"):
            with self.subTest(cmd=cmd):
                code, _out, err = self.run_cli(cmd)
                self.assertEqual(code, 0)
                self.assertEqual(err, "")
