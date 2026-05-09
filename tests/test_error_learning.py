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

from scripts import error_learning  # noqa: E402


class ErrorLearningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.log_path = self.root / ".learnings" / "error_log.json"

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def read_store(self) -> dict[str, object]:
        return json.loads(self.log_path.read_text(encoding="utf-8"))

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

    def test_add_command_persists_schema_and_deduplicates(self) -> None:
        exit_code, first_stdout, first_stderr = self.run_cli(
            "add",
            "runtime_error",
            "OpenClaw session crashed after a timeout",
            "Retry with a smaller prompt and checkpoint intermediate state",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(first_stderr, "")
        self.assertIn("Saved agent error entry.", first_stdout)

        exit_code, second_stdout, second_stderr = self.run_cli(
            "add",
            "runtime_error",
            "OpenClaw session crashed after a timeout",
            "Retry with a smaller prompt and checkpoint intermediate state",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(second_stderr, "")
        self.assertIn("Duplicate entry detected", second_stdout)

        store = self.read_store()
        self.assertEqual(store["schema_version"], error_learning.SCHEMA_VERSION)
        self.assertEqual(len(store["entries"]), 1)

        entry = store["entries"][0]
        self.assertEqual(
            set(entry.keys()),
            {"id", "timestamp", "error_type", "message", "context", "suggested_fix", "resolved"},
        )
        self.assertEqual(entry["error_type"], "runtime_error")
        self.assertEqual(entry["context"], {})
        self.assertTrue(entry["resolved"])

    def test_add_with_context_json(self) -> None:
        exit_code, stdout, stderr = self.run_cli(
            "add",
            "api_error",
            "Rate limit exceeded",
            "Backoff exponentially and reduce parallel requests.",
            "--context-json",
            '{"endpoint": "/v1/chat", "status": 429}',
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr, "")
        entry = self.read_store()["entries"][0]
        self.assertEqual(entry["context"], {"endpoint": "/v1/chat", "status": 429})

    def test_list_command_outputs_colorized_entries_and_status(self) -> None:
        error_learning.add_entry(
            self.log_path,
            "warning",
            "Disk space dropped below the safe threshold",
            "Purge stale artifacts before retrying long sessions",
            resolved=False,
        )
        error_learning.add_entry(
            self.log_path,
            "parser_error",
            "Structured output parser rejected an unterminated block",
            "Validate fenced blocks before handing them to the parser",
        )

        exit_code, stdout, stderr = self.run_cli("list")

        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr, "")
        self.assertIn("\033[", stdout)
        self.assertIn("warning", stdout)
        self.assertIn("[open]", stdout)
        self.assertIn("Suggested fix:", stdout)
        self.assertIn("Message:", stdout)

    def test_stats_and_search_surface_relevant_entries(self) -> None:
        error_learning.add_entry(
            self.log_path,
            "parser_error",
            "JSON payload was truncated before the closing brace",
            "Chunk large responses and validate JSON before parsing",
        )
        error_learning.add_entry(
            self.log_path,
            "parser_error",
            "Assistant returned invalid YAML front matter",
            "Require fenced output and normalize indentation first",
        )
        error_learning.add_entry(
            self.log_path,
            "warning",
            "Model warmed up slowly after a cold restart",
            "Keep a lightweight health check running between batches",
        )

        stats_code, stats_stdout, stats_stderr = self.run_cli("stats")
        self.assertEqual(stats_code, 0)
        self.assertEqual(stats_stderr, "")
        self.assertIn("parser_error", stats_stdout)
        self.assertIn("2", stats_stdout)

        search_code, search_stdout, search_stderr = self.run_cli("search", "json parsing")
        self.assertEqual(search_code, 0)
        self.assertEqual(search_stderr, "")
        self.assertIn("JSON payload was truncated", search_stdout)
        self.assertNotIn("cold restart", search_stdout)

    def test_load_migrates_v1_entries(self) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        legacy = {
            "schema_version": 1,
            "entries": [
                {
                    "id": "abc",
                    "timestamp": "2026-01-01T00:00:00Z",
                    "category": "old_cat",
                    "error": "old message",
                    "lesson": "old lesson",
                    "resolved": True,
                }
            ],
        }
        self.log_path.write_text(json.dumps(legacy) + "\n", encoding="utf-8")
        store = error_learning.load_store(self.log_path)
        e = error_learning.validate_entry(store["entries"][0])
        self.assertEqual(e["error_type"], "old_cat")
        self.assertEqual(e["message"], "old message")
        self.assertEqual(e["suggested_fix"], "old lesson")
        self.assertEqual(e["context"], {})

    def test_demo_runs_cleanly(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.dict(os.environ, {"NO_COLOR": "1"}, clear=False), mock.patch("sys.stdout", stdout), mock.patch(
            "sys.stderr", stderr
        ):
            code = error_learning.run_demo()
        self.assertEqual(code, 0)
        self.assertEqual(stderr.getvalue(), "")
        out = stdout.getvalue()
        self.assertIn("tool_timeout", out)
        self.assertIn("parse_error", out)


if __name__ == "__main__":
    unittest.main()
