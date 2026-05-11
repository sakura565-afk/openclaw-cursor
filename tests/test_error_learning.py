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
        self.assertEqual(store["schema_version"], 2)
        self.assertEqual(len(store["entries"]), 1)

        entry = store["entries"][0]
        self.assertEqual(
            set(entry.keys()),
            {
                "id",
                "fingerprint",
                "timestamp",
                "first_seen",
                "last_seen",
                "occurrence_count",
                "category",
                "error",
                "lesson",
                "resolved",
                "pattern_tags",
                "root_cause_hint",
                "actionable_insights",
                "sources",
            },
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

    def test_fingerprint_merge_bumps_occurrence_and_combines_lessons(self) -> None:
        first, s1 = error_learning.add_entry(
            self.log_path,
            "service",
            "ollama list returned exit code 1",
            "Check ollama service status.",
            resolved=False,
        )
        self.assertEqual(s1, "created")
        second, s2 = error_learning.add_entry(
            self.log_path,
            "service",
            "ollama list returned exit code 1",
            "Verify model cache permissions.",
            resolved=False,
        )
        self.assertEqual(s2, "merged")
        self.assertEqual(first["fingerprint"], second["fingerprint"])
        self.assertEqual(second["occurrence_count"], 2)
        self.assertIn("permissions", second["lesson"].lower())

    def test_sync_cli_ingests_auto_improvement_failures(self) -> None:
        imp_dir = self.root / "logs"
        imp_dir.mkdir(parents=True, exist_ok=True)
        log_file = imp_dir / "auto_improvements_20990101.json"
        log_file.write_text(
            json.dumps(
                [
                    {
                        "timestamp": "2099-01-01T00:00:00+00:00",
                        "category": "service",
                        "action": "restart_ollama",
                        "outcome": "failed",
                        "details": {"stderr": "connection refused", "returncode": 1},
                    }
                ]
            ),
            encoding="utf-8",
        )
        exit_code, stdout, stderr = self.run_cli(
            "sync",
            "--improvement-log-dir",
            str(imp_dir),
            "--since-days",
            "36500",
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr, "")
        self.assertIn("learned", stdout)
        store = self.read_store()
        self.assertEqual(len(store["entries"]), 1)
        self.assertIn("connection refused", store["entries"][0]["error"])

    def test_patterns_json_output(self) -> None:
        error_learning.add_entry(
            self.log_path,
            "a",
            "same failure text",
            "lesson one",
            resolved=False,
        )
        error_learning.add_entry(
            self.log_path,
            "a",
            "same failure text",
            "lesson two",
            resolved=False,
        )
        exit_code, stdout, stderr = self.run_cli("patterns", "--json", "--min-occurrences", "1")
        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr, "")
        data = json.loads(stdout)
        self.assertEqual(len(data), 1)
        self.assertGreaterEqual(data[0]["occurrence_total"], 2)


if __name__ == "__main__":
    unittest.main()
