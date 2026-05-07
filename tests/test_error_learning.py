import importlib.util
import io
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "error_learning.py"
SPEC = importlib.util.spec_from_file_location("error_learning", MODULE_PATH)
error_learning = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = error_learning
SPEC.loader.exec_module(error_learning)


class LogErrorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tempdir.name) / ".learnings" / "ERRORS.md"

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_log_creates_file_and_record(self) -> None:
        record = error_learning.log_error(
            "ModuleNotFoundError: No module named 'requests'",
            category="import",
            context={"step": "boot"},
            path=self.path,
        )

        self.assertTrue(self.path.exists())
        self.assertEqual(record.occurrences, 1)
        self.assertEqual(record.category, "import")
        self.assertTrue(record.signature)
        self.assertIn("requests", record.message)
        # Suggested fix should auto-derive from built-in patterns.
        self.assertTrue(record.suggested_fix)

        text = self.path.read_text(encoding="utf-8")
        self.assertIn("# Error Learnings", text)
        self.assertIn("## [import]", text)
        self.assertIn("- **Signature**: " + record.signature, text)
        self.assertIn("\"step\": \"boot\"", text)

    def test_repeated_error_increments_counter(self) -> None:
        first = error_learning.log_error("KeyError: 'foo'", category="parse", path=self.path)
        second = error_learning.log_error("KeyError: 'foo'", category="parse", path=self.path)

        self.assertEqual(first.signature, second.signature)
        self.assertEqual(second.occurrences, 2)
        self.assertGreaterEqual(second.last_seen, first.first_seen)

        records = error_learning.get_recent_errors(path=self.path)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].occurrences, 2)

    def test_normalization_collapses_volatile_details(self) -> None:
        a = error_learning.log_error(
            'File "/abs/path/foo.py", line 12, in bar',
            category="runtime",
            path=self.path,
        )
        b = error_learning.log_error(
            'File "/different/place/baz.py", line 999, in bar',
            category="runtime",
            path=self.path,
        )
        self.assertEqual(a.signature, b.signature)
        self.assertEqual(b.occurrences, 2)

    def test_logging_exception_captures_traceback(self) -> None:
        try:
            raise ValueError("bad value")
        except ValueError as exc:
            record = error_learning.log_error(exc, path=self.path)

        self.assertEqual(record.category, "ValueError")
        self.assertIn("ValueError: bad value", record.message)
        self.assertIn("Traceback", record.traceback_text)
        self.assertIn("raise ValueError", record.traceback_text)

    def test_empty_message_rejected(self) -> None:
        with self.assertRaises(ValueError):
            error_learning.log_error("   ", path=self.path)

    def test_extra_metadata_persists(self) -> None:
        error_learning.log_error(
            "boom",
            category="x",
            extra={"Run ID": "abc-123"},
            path=self.path,
        )
        text = self.path.read_text(encoding="utf-8")
        self.assertIn("- **Run ID**: abc-123", text)
        records = error_learning.get_recent_errors(path=self.path)
        self.assertEqual(records[0].extra.get("Run ID"), "abc-123")

    def test_round_trip_preserves_fields(self) -> None:
        error_learning.log_error(
            "TypeError: missing 1 required positional argument: 'name'",
            category="api",
            context="calling foo()",
            path=self.path,
        )
        records = error_learning.get_recent_errors(path=self.path)
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record.category, "api")
        self.assertIn("TypeError", record.message)
        self.assertEqual(record.context.strip(), "calling foo()")
        self.assertEqual(record.occurrences, 1)


class GetRecentErrorsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tempdir.name) / "ERRORS.md"

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_missing_file_returns_empty(self) -> None:
        self.assertEqual(error_learning.get_recent_errors(path=self.path), [])

    def test_filters_and_sort_order(self) -> None:
        old = datetime(2025, 1, 1, tzinfo=timezone.utc)
        mid = datetime(2026, 1, 1, tzinfo=timezone.utc)
        new = datetime(2026, 5, 1, tzinfo=timezone.utc)

        error_learning.log_error("alpha", category="a", path=self.path, now=old)
        error_learning.log_error("beta", category="b", path=self.path, now=mid)
        error_learning.log_error("gamma", category="a", path=self.path, now=new)

        all_records = error_learning.get_recent_errors(path=self.path)
        self.assertEqual([r.message for r in all_records], ["gamma", "beta", "alpha"])

        category_a = error_learning.get_recent_errors(path=self.path, category="a")
        self.assertEqual([r.message for r in category_a], ["gamma", "alpha"])

        recent_only = error_learning.get_recent_errors(
            path=self.path,
            since=datetime(2025, 6, 1, tzinfo=timezone.utc),
        )
        self.assertEqual([r.message for r in recent_only], ["gamma", "beta"])

        limited = error_learning.get_recent_errors(path=self.path, limit=1)
        self.assertEqual(len(limited), 1)
        self.assertEqual(limited[0].message, "gamma")

    def test_since_accepts_timedelta(self) -> None:
        long_ago = datetime.now(timezone.utc) - timedelta(days=10)
        recent = datetime.now(timezone.utc) - timedelta(hours=1)
        error_learning.log_error("old", category="x", path=self.path, now=long_ago)
        error_learning.log_error("new", category="x", path=self.path, now=recent)

        records = error_learning.get_recent_errors(path=self.path, since=timedelta(days=1))
        self.assertEqual([r.message for r in records], ["new"])


class SuggestFixesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tempdir.name) / "ERRORS.md"

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_module_not_found_suggestion(self) -> None:
        tips = error_learning.suggest_fixes(
            "ModuleNotFoundError: No module named 'requests'",
            path=self.path,
        )
        joined = "\n".join(tips)
        self.assertIn("pip install requests", joined)

    def test_default_suggestion_when_no_pattern_matches(self) -> None:
        tips = error_learning.suggest_fixes("totally weird thing happened", path=self.path)
        self.assertEqual(len(tips), 1)
        self.assertIn("No matching pattern", tips[0])

    def test_prior_record_appears_first(self) -> None:
        error_learning.log_error(
            "ConnectionError: connection refused",
            category="net",
            suggested_fix="Restart the API container before retrying.",
            path=self.path,
        )
        tips = error_learning.suggest_fixes(
            "ConnectionError: connection refused",
            category="net",
            path=self.path,
        )
        self.assertTrue(tips[0].startswith("Seen "))
        self.assertIn("Restart the API container", tips[0])

    def test_exception_input_uses_class_name(self) -> None:
        tips = error_learning.suggest_fixes(KeyError("foo"))
        self.assertTrue(any("dict.get" in tip for tip in tips))


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tempdir.name) / "ERRORS.md"

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _run(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "stdout", out), mock.patch.object(sys, "stderr", err):
            code = error_learning.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_log_then_list_then_suggest(self) -> None:
        code, out, _ = self._run(
            "--path",
            str(self.path),
            "log",
            "--message",
            "FileNotFoundError: missing config",
            "--category",
            "boot",
            "--context-json",
            json.dumps({"path": "/etc/cfg"}),
        )
        self.assertEqual(code, 0)
        self.assertIn("logged:", out)

        code, out, _ = self._run(
            "--path", str(self.path), "list", "--json", "--limit", "5"
        )
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["category"], "boot")
        self.assertIn("FileNotFoundError", payload[0]["message"])

        code, out, _ = self._run(
            "--path",
            str(self.path),
            "suggest",
            "--message",
            "FileNotFoundError: missing config",
        )
        self.assertEqual(code, 0)
        self.assertTrue(out.strip())

    def test_log_rejects_invalid_extra(self) -> None:
        code, _, err = self._run(
            "--path",
            str(self.path),
            "log",
            "--message",
            "boom",
            "--extra",
            "no-equals",
        )
        self.assertEqual(code, 2)
        self.assertIn("KEY=VALUE", err)

    def test_log_rejects_invalid_context_json(self) -> None:
        code, _, err = self._run(
            "--path",
            str(self.path),
            "log",
            "--message",
            "boom",
            "--context-json",
            "{not json",
        )
        self.assertEqual(code, 2)
        self.assertIn("not valid JSON", err)

    def test_list_empty_file(self) -> None:
        code, out, _ = self._run("--path", str(self.path), "list")
        self.assertEqual(code, 0)
        self.assertIn("no errors recorded", out)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
