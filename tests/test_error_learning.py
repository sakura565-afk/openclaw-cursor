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

    def test_log_command_persists_schema_and_deduplicates(self) -> None:
        exit_code, first_stdout, first_stderr = self.run_cli(
            "log",
            "runtime_error",
            "OpenClaw session crashed after a timeout",
            "Retry with a smaller prompt and checkpoint intermediate state",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(first_stderr, "")
        self.assertIn("Saved error learning entry.", first_stdout)

        exit_code, second_stdout, second_stderr = self.run_cli(
            "log",
            "runtime_error",
            "OpenClaw session crashed after a timeout",
            "Retry with a smaller prompt and checkpoint intermediate state",
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(second_stderr, "")
        self.assertIn("Duplicate entry detected", second_stdout)

        store = self.read_store()
        self.assertEqual(store["schema_version"], 2)
        self.assertEqual(len(store["entries"]), 1)

        entry = store["entries"][0]
        self.assertEqual(
            set(entry.keys()),
            {"id", "timestamp", "error_type", "category", "description", "context", "fix", "resolved"},
        )
        self.assertEqual(entry["error_type"], "runtime_error")
        self.assertEqual(entry["description"], "OpenClaw session crashed after a timeout")
        self.assertEqual(entry["context"], "")
        self.assertTrue(entry["resolved"])

    def test_log_with_context_persists_context_field(self) -> None:
        code, out, err = self.run_cli(
            "log",
            "tool_failure",
            "MCP server returned an error",
            "Restart the MCP bridge and retry",
            "--context",
            "session=abc tool=filesystem",
        )
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertIn("Saved", out)
        entry = self.read_store()["entries"][0]
        self.assertEqual(entry["context"], "session=abc tool=filesystem")
        self.assertIn("What went wrong:", out)
        self.assertIn("Context:", out)

    def test_show_respects_category_and_limit(self) -> None:
        error_learning.log_agent_error(
            self.log_path,
            "api_error",
            "Provider returned HTTP 503",
            "Retry with exponential backoff",
        )
        error_learning.log_agent_error(
            self.log_path,
            "disk_warning",
            "Disk space dropped below the safe threshold",
            "Purge stale artifacts before retrying long sessions",
        )

        exit_code, stdout, stderr = self.run_cli("show", "--category", "api_error", "--limit", "5")

        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr, "")
        self.assertIn("HTTP 503", stdout)
        self.assertNotIn("Disk space", stdout)

    def test_retrieve_past_errors_function(self) -> None:
        error_learning.log_agent_error(self.log_path, "x", "hello world", "fix one")
        error_learning.log_agent_error(self.log_path, "y", "other", "fix two")
        rows = error_learning.retrieve_past_errors(self.log_path, limit=1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["description"], "other")

    def test_show_outputs_colorized_entries_and_status(self) -> None:
        error_learning.log_agent_error(
            self.log_path,
            "warning",
            "Disk space dropped below the safe threshold",
            "Purge stale artifacts before retrying long sessions",
            resolved=False,
        )
        error_learning.log_agent_error(
            self.log_path,
            "parser_error",
            "Structured output parser rejected an unterminated block",
            "Validate fenced blocks before handing them to the parser",
            resolved=True,
        )

        exit_code, stdout, stderr = self.run_cli("show")

        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr, "")
        self.assertIn("\033[", stdout)
        self.assertIn("warning", stdout)
        self.assertIn("[open]", stdout)
        self.assertIn("Fix applied:", stdout)
        self.assertIn("What went wrong:", stdout)

    def test_list_command_still_works(self) -> None:
        error_learning.log_agent_error(self.log_path, "a", "e", "f")
        exit_code, stdout, stderr = self.run_cli("list")
        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr, "")
        self.assertIn("e", stdout)

    def test_stats_and_search_surface_relevant_entries(self) -> None:
        error_learning.log_agent_error(
            self.log_path,
            "parser_error",
            "JSON payload was truncated before the closing brace",
            "Chunk large responses and validate JSON before parsing",
        )
        error_learning.log_agent_error(
            self.log_path,
            "parser_error",
            "Assistant returned invalid YAML front matter",
            "Require fenced output and normalize indentation first",
        )
        error_learning.log_agent_error(
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

    def test_patterns_and_suggest_use_signatures(self) -> None:
        error_learning.log_agent_error(
            self.log_path,
            "network",
            "Connection refused to 10.0.0.1:443",
            "Fail over to the secondary endpoint",
        )
        error_learning.log_agent_error(
            self.log_path,
            "network",
            "Connection refused to 192.168.0.2:443",
            "Fail over to the secondary endpoint",
        )
        error_learning.log_agent_error(
            self.log_path,
            "warning",
            "Model warmed up slowly after a cold restart",
            "Keep a lightweight health check running between batches",
        )

        p_code, p_out, p_err = self.run_cli("patterns", "--min-count", "2")
        self.assertEqual(p_code, 0)
        self.assertEqual(p_err, "")
        self.assertIn("Recurring error patterns", p_out)
        self.assertIn("×2", p_out)
        self.assertIn("Fail over", p_out)

        s_code, s_out, s_err = self.run_cli("suggest", "Connection refused to 203.0.113.9:443")
        self.assertEqual(s_code, 0)
        self.assertEqual(s_err, "")
        self.assertIn("secondary endpoint", s_out)
        self.assertIn("Fix applied:", s_out)

    def test_log_surfaces_prior_learnings_for_same_shape(self) -> None:
        error_learning.log_agent_error(
            self.log_path,
            "runtime",
            "Error on line 42 in /tmp/foo.py",
            "First mitigation",
        )
        error_learning.log_agent_error(
            self.log_path,
            "runtime",
            "Error on line 99 in /tmp/foo.py",
            "Second mitigation",
        )
        exit_code, stdout, stderr = self.run_cli(
            "log",
            "runtime",
            "Error on line 7 in /tmp/foo.py",
            "Third mitigation",
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr, "")
        self.assertIn("Earlier learnings with the same error shape (2)", stdout)
        self.assertIn("First mitigation", stdout)

    def test_open_lists_only_unresolved(self) -> None:
        error_learning.log_agent_error(
            self.log_path,
            "a",
            "e1",
            "l1",
            resolved=False,
        )
        error_learning.log_agent_error(self.log_path, "b", "e2", "l2", resolved=True)
        exit_code, stdout, stderr = self.run_cli("open")
        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr, "")
        self.assertIn("[open]", stdout)
        self.assertIn("e1", stdout)
        self.assertNotIn("e2", stdout)

    def test_show_unresolved_flag(self) -> None:
        error_learning.log_agent_error(self.log_path, "a", "open issue", "no fix yet", resolved=False)
        error_learning.log_agent_error(self.log_path, "b", "closed", "done", resolved=True)
        code, out, err = self.run_cli("show", "--unresolved")
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertIn("open issue", out)
        self.assertNotIn("closed", out)

    def test_add_hidden_alias_matches_log(self) -> None:
        code, _, err = self.run_cli(
            "add",
            "legacy_cat",
            "legacy message",
            "legacy lesson",
        )
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        entry = self.read_store()["entries"][0]
        self.assertEqual(entry["error_type"], "legacy_cat")
        self.assertEqual(entry["description"], "legacy message")
        self.assertEqual(entry["fix"], "legacy lesson")


if __name__ == "__main__":
    unittest.main()
