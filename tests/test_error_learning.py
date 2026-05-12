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

    def test_add_writes_structured_event_and_summary(self) -> None:
        exit_code, stdout, stderr = self.run_cli(
            "add",
            "api",
            "OpenAI returned 429 too many requests for the batch",
            "Throttle parallel tool calls",
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr, "")
        errors_dir = self.log_path.parent / "errors"
        events = list((errors_dir / "events").glob("*.json"))
        self.assertEqual(len(events), 1)
        payload = json.loads(events[0].read_text(encoding="utf-8"))
        self.assertEqual(payload["error_type"], "rate_limit")
        self.assertEqual(payload["severity"], "high")
        self.assertIn("root_cause", payload)
        self.assertTrue((errors_dir / "summary.md").is_file())
        summary = (errors_dir / "summary.md").read_text(encoding="utf-8")
        self.assertIn("rate_limit", summary)
        self.assertIn("Unique error patterns", summary)

    def test_ingest_session_log_and_query_filters(self) -> None:
        session_log = self.root / "session.md"
        session_log.write_text(
            "2026-05-01 run\nERROR: tool call failed for mcp_github\nContext window exceeded on retry\n",
            encoding="utf-8",
        )
        exit_code, out, err = self.run_cli(
            "ingest",
            str(session_log),
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(err, "")
        self.assertIn("Scanned 1 file", out)

        errors_dir = self.log_path.parent / "errors"
        self.assertTrue((errors_dir / "summary.md").exists())

        exit_code2, out2, err2 = self.run_cli(
            "ingest",
            str(session_log),
        )
        self.assertEqual(exit_code2, 0)
        events = list((errors_dir / "events").glob("*.json"))
        self.assertLessEqual(len(events), 3)

        q_code, q_out, q_err = self.run_cli("query", "--type", "tool_failure", "--limit", "5")
        self.assertEqual(q_code, 0)
        self.assertEqual(q_err, "")
        self.assertIn("tool_failure", q_out)

        q2_code, q2_out, q2_err = self.run_cli(
            "query",
            "--since",
            "2020-01-01",
            "--until",
            "2099-12-31",
            "--severity",
            "high",
        )
        self.assertEqual(q2_code, 0)
        self.assertEqual(q2_err, "")

    def test_categorize_error_helpers(self) -> None:
        t, sev, rc = error_learning.categorize_error("Connection refused while calling upstream")
        self.assertEqual(t, "network")
        self.assertEqual(sev, "medium")
        fp1 = error_learning.structured_fingerprint("network", "same msg")
        fp2 = error_learning.structured_fingerprint("network", "same msg")
        self.assertEqual(fp1, fp2)
        self.assertEqual(len(fp1), 16)

    def test_rebuild_summary_command(self) -> None:
        error_learning.record_structured_event(
            self.log_path.parent / "errors",
            message="JSONDecodeError: unexpected end",
            source="test",
        )
        code, out, err = self.run_cli("rebuild-summary")
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertIn("summary.md", out)


if __name__ == "__main__":
    unittest.main()
