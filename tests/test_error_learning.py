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
        self.assertEqual(store["schema_version"], error_learning.SCHEMA_VERSION)
        self.assertEqual(len(store["entries"]), 1)

        entry = store["entries"][0]
        self.assertEqual(
            set(entry.keys()),
            {
                "id",
                "timestamp",
                "category",
                "error",
                "lesson",
                "resolved",
                "file",
                "line",
                "error_type",
                "user_correction",
                "task_context",
            },
        )
        self.assertEqual(entry["category"], "runtime_error")
        self.assertEqual(entry["error_type"], "runtime_error")
        self.assertTrue(entry["resolved"])
        self.assertIsNone(entry["file"])
        self.assertIsNone(entry["line"])

    def test_add_with_context_and_patterns_json(self) -> None:
        code1, out1, err1 = self.run_cli(
            "add",
            "lint",
            "Missing import for Path",
            "Run ruff and add missing imports before committing",
            "--file",
            "scripts/foo.py",
            "--line",
            "12",
            "--error-type",
            "import_error",
            "--user-correction",
            "Add `from pathlib import Path`",
            "--task-context",
            "Refactor file utilities",
        )
        self.assertEqual(code1, 0, err1)
        self.assertIn("Saved", out1)

        code2, out2, err2 = self.run_cli(
            "patterns",
            "--by",
            "file_line",
            "--top",
            "5",
            "--json",
        )
        self.assertEqual(code2, 0, err2)
        data = json.loads(out2)
        self.assertTrue(any(row["key"] == "scripts/foo.py:12" for row in data))

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
        self.assertIn("Type:", stdout)

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

    def test_log_conversation_error_and_retrieve_relevant_errors(self) -> None:
        error_learning.log_conversation_error(
            self.log_path,
            category="agent",
            error="Used deprecated API for file reads",
            lesson="Prefer pathlib read_text with explicit encoding",
            error_type="api_misuse",
            file="src/tools.py",
            line=44,
            user_correction="Use Path.read_text(encoding='utf-8')",
            task_context="Harden file IO helpers",
        )
        found = error_learning.retrieve_relevant_errors(
            self.log_path,
            "Refactor file IO and pathlib usage",
            limit=5,
        )
        self.assertTrue(found)
        self.assertIn("deprecated API", found[0]["error"])
        self.assertEqual(found[0]["file"], "src/tools.py")
        self.assertEqual(found[0]["line"], 44)

    def test_append_lessons_to_prompt_injects_block(self) -> None:
        error_learning.add_entry(
            self.log_path,
            "style",
            "Inconsistent quote style in Python edits",
            "Match the file's existing quote style (single vs double)",
            task_context="Python formatting pass",
        )
        base = "You are a coding agent.\n"
        out = error_learning.append_lessons_to_prompt(
            base,
            "Format Python sources consistently",
            self.log_path,
            limit=3,
        )
        self.assertIn("Prior mistakes to avoid", out)
        self.assertIn("quote style", out.lower())

    def test_append_lessons_to_prompt_no_match_returns_unchanged(self) -> None:
        error_learning.add_entry(
            self.log_path,
            "network",
            "DNS lookup failed for internal registry",
            "Retry with the VPN connected",
        )
        base = "Short system prompt"
        out = error_learning.append_lessons_to_prompt(
            base,
            "completely unrelated knitting patterns",
            self.log_path,
            limit=3,
            min_score=0.9,
        )
        self.assertEqual(out, base)

    def test_v1_store_migrates_on_load(self) -> None:
        legacy = {
            "schema_version": 1,
            "entries": [
                {
                    "id": "abc123def456",
                    "timestamp": "2024-01-01T00:00:00Z",
                    "category": "test_cat",
                    "error": "Something broke",
                    "lesson": "Do the fix",
                    "resolved": True,
                }
            ],
        }
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_path.write_text(json.dumps(legacy), encoding="utf-8")

        store = error_learning.load_store(self.log_path)
        self.assertEqual(store["schema_version"], error_learning.SCHEMA_VERSION)
        ent = error_learning.validate_entry(store["entries"][0])
        self.assertEqual(ent["error_type"], "test_cat")
        self.assertIsNone(ent["file"])
        self.assertEqual(ent["user_correction"], "")

    def test_prompt_append_cli(self) -> None:
        error_learning.add_entry(
            self.log_path,
            "build",
            "Linker could not find -lfoo",
            "Install libfoo-dev or drop the optional feature flag",
            task_context="Compile native extension",
        )
        code, out, err = self.run_cli(
            "prompt-append",
            "Build the C extension with optional libfoo",
            "--task-context",
            "native extension compile with libfoo",
        )
        self.assertEqual(code, 0, err)
        self.assertIn("Build the C extension", out)
        self.assertIn("Prior mistakes", out)


if __name__ == "__main__":
    unittest.main()
