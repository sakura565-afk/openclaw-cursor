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

from scripts.self_improvement import error_learning  # noqa: E402


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
        self.assertIn("Saved error learning entry.", first_stdout)

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
        self.assertEqual(store["schema_version"], 1)
        self.assertEqual(len(store["entries"]), 1)

        entry = store["entries"][0]
        self.assertEqual(
            set(entry.keys()),
            {"id", "timestamp", "category", "error", "lesson", "resolved"},
        )
        self.assertEqual(entry["category"], "runtime_error")
        self.assertTrue(entry["resolved"])

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
            resolved=True,
        )

        exit_code, stdout, stderr = self.run_cli("list")

        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr, "")
        self.assertIn("\033[", stdout)
        self.assertIn("warning", stdout)
        self.assertIn("[open]", stdout)
        self.assertIn("Lesson:", stdout)
        self.assertIn("Error:", stdout)

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

    def test_classify_error_type_heuristics(self) -> None:
        self.assertEqual(
            error_learning.classify_error_type("HTTP 429 rate limit exceeded for api.example.com"),
            "api",
        )
        self.assertEqual(
            error_learning.classify_error_type("bash: gerp: command not found"),
            "tool",
        )
        self.assertEqual(
            error_learning.classify_error_type("TypeError: unsupported operand type(s)"),
            "logic",
        )

    def test_analyze_updates_markdown_and_respects_dry_run(self) -> None:
        logs = self.root / "logs"
        logs.mkdir(parents=True)
        session = logs / "agent.log"
        session.write_text(
            "turn 1\nuser: hi\nassistant: calling API\n"
            "Error: HTTP 503 Service Unavailable from upstream\n",
            encoding="utf-8",
        )
        md_path = self.root / ".learnings" / "error_log.md"

        dry_code, dry_out, dry_err = self.run_cli(
            "analyze",
            "--root",
            str(self.root),
            "--md-path",
            str(md_path),
            "--since-hours",
            "8760",
            "--glob",
            "logs/**/*.log",
            "--dry-run",
        )
        self.assertEqual(dry_code, 0)
        self.assertEqual(dry_err, "")
        self.assertIn("Dry run", dry_out)
        self.assertIn("503", dry_out)
        self.assertFalse(md_path.exists())

        write_code, write_out, write_err = self.run_cli(
            "analyze",
            "--root",
            str(self.root),
            "--md-path",
            str(md_path),
            "--since-hours",
            "8760",
            "--glob",
            "logs/**/*.log",
        )
        self.assertEqual(write_code, 0)
        self.assertEqual(write_err, "")
        self.assertTrue(md_path.exists())
        md_text = md_path.read_text(encoding="utf-8")
        self.assertIn(error_learning.MD_AUTO_START, md_text)
        self.assertIn("503", md_text)
        self.assertIn("Updated markdown report", write_out)


if __name__ == "__main__":
    unittest.main()
