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
        self.assertIn("Action:", stdout)
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

    def test_patterns_and_suggest_use_signatures(self) -> None:
        error_learning.add_entry(
            self.log_path,
            "network",
            "Connection refused to 10.0.0.1:443",
            "Fail over to the secondary endpoint",
        )
        error_learning.add_entry(
            self.log_path,
            "network",
            "Connection refused to 192.168.0.2:443",
            "Fail over to the secondary endpoint",
        )
        error_learning.add_entry(
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
        self.assertIn("Action:", s_out)

    def test_add_surfaces_prior_learnings_for_same_shape(self) -> None:
        error_learning.add_entry(
            self.log_path,
            "runtime",
            "Error on line 42 in /tmp/foo.py",
            "First mitigation",
        )
        error_learning.add_entry(
            self.log_path,
            "runtime",
            "Error on line 99 in /tmp/foo.py",
            "Second mitigation",
        )
        exit_code, stdout, stderr = self.run_cli(
            "add",
            "runtime",
            "Error on line 7 in /tmp/foo.py",
            "Third mitigation",
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr, "")
        self.assertIn("Earlier learnings with the same error shape (2)", stdout)
        self.assertIn("First mitigation", stdout)

    def test_open_lists_only_unresolved(self) -> None:
        error_learning.add_entry(
            self.log_path,
            "a",
            "e1",
            "l1",
            resolved=False,
        )
        error_learning.add_entry(self.log_path, "b", "e2", "l2", resolved=True)
        exit_code, stdout, stderr = self.run_cli("open")
        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr, "")
        self.assertIn("[open]", stdout)
        self.assertIn("e1", stdout)
        self.assertNotIn("e2", stdout)

    def test_parse_markdown_sections_and_similarity(self) -> None:
        learnings = self.root / ".learnings"
        md = learnings / "notes" / "lesson.md"
        md.parent.mkdir(parents=True)
        md.write_text(
            "## Error pattern\n"
            "JSON parser choked on trailing commas\n\n"
            "## Root cause\n"
            "Model emitted invalid JSON\n\n"
            "## What fixed it\n"
            "Strip trailing commas before json.loads\n",
            encoding="utf-8",
        )
        parsed = error_learning.parse_markdown_file(md, learnings_root=learnings)
        self.assertEqual(len(parsed), 1)
        item = parsed[0]
        self.assertIn("trailing commas", item.error_pattern.lower())
        self.assertIn("invalid json", item.root_cause.lower())
        self.assertIn("json.loads", item.fix.lower())

        hits = error_learning.find_similar_markdown_learnings(
            "json parse trailing comma",
            learnings,
            limit=5,
            min_score=0.1,
        )
        self.assertTrue(hits)
        self.assertGreater(hits[0][0], 0.5)

    def test_write_summary_merges_json_and_markdown(self) -> None:
        learnings = self.root / ".learnings"
        (learnings / "errors").mkdir(parents=True)
        (learnings / "errors" / "dup.md").write_text(
            "## Error pattern\nOops timeout\n## Root cause\nSlow network\n## Fix\nRetry\n",
            encoding="utf-8",
        )
        (learnings / "errors" / "dup2.md").write_text(
            "## Error pattern\nOops timeout\n## Root cause\nVPN\n## Fix\nRetry harder\n",
            encoding="utf-8",
        )
        error_learning.add_entry(self.log_path, "net", "Connection timeout to 1.2.3.4", "Increase client timeout")

        out = error_learning.write_error_summary_md(learnings, self.log_path)
        self.assertEqual(out, learnings / "auto" / "error_summary.md")
        text = out.read_text(encoding="utf-8")
        self.assertIn("Error learning summary", text)
        self.assertIn("JSON log", text)
        self.assertIn("markdown learnings", text)
        self.assertIn("×2", text)
        self.assertIn("timeout", text.lower())

    def test_add_interactive_writes_markdown(self) -> None:
        learnings = self.root / ".learnings"
        learnings.mkdir(parents=True)
        inputs = iter(
            [
                "Disk full during pip install",
                "Build cache grew without bound",
                "Prune cache and retry",
                "disk, ci",
            ]
        )
        stdout = io.StringIO()
        stderr = io.StringIO()
        env = dict(os.environ)
        env.pop("NO_COLOR", None)
        with mock.patch.dict(os.environ, env, clear=True), mock.patch("sys.stdout", stdout), mock.patch(
            "sys.stderr", stderr
        ), mock.patch("builtins.input", lambda _p="": next(inputs)):
            code = error_learning.main(
                ["--log-path", str(self.log_path), "--learnings-dir", str(learnings), "add-interactive"]
            )
        self.assertEqual(code, 0)
        self.assertEqual(stderr.getvalue(), "")
        self.assertIn("Wrote", stdout.getvalue())
        written = list((learnings / "errors").glob("entry_*.md"))
        self.assertEqual(len(written), 1)
        body = written[0].read_text(encoding="utf-8")
        self.assertIn("Disk full", body)
        self.assertIn("Prune cache", body)

    def test_md_search_cli(self) -> None:
        learnings = self.root / ".learnings"
        learnings.mkdir(parents=True)
        (learnings / "x.md").write_text(
            "## Error pattern\nYAML indentation drifted\n## Root cause\nTabs mixed with spaces\n"
            "## Fix\nRun formatter\n",
            encoding="utf-8",
        )
        exit_code, stdout, stderr = self.run_cli(
            "--learnings-dir",
            str(learnings),
            "md-search",
            "yaml tabs",
            "--min-score",
            "0.2",
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr, "")
        self.assertIn("YAML", stdout)
        self.assertIn("score=", stdout)


if __name__ == "__main__":
    unittest.main()
