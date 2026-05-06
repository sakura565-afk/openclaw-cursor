import json
import tempfile
import unittest
from pathlib import Path

from src.self_improvement.error_learning import ErrorLearningSystem, main


class ErrorLearningTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.logs = self.root / "logs"
        self.logs.mkdir(parents=True, exist_ok=True)
        self.learnings_file = self.root / ".learnings" / "error_log.json"
        self.system = ErrorLearningSystem(
            root_dir=self.root,
            session_log_dir=self.logs,
            learnings_file=self.learnings_file,
        )

    def test_add_entry_deduplicates_similar_errors(self):
        self.system.add_entry(
            category="runtime",
            error="ValueError: bad value 123",
            lesson="Validate input shape.",
        )
        self.system.add_entry(
            category="runtime",
            error="ValueError: bad value 999",
            lesson="",
        )

        entries = self.system.load_entries()
        self.assertEqual(len(entries), 1)
        self.assertGreaterEqual(entries[0].occurrences, 2)

    def test_capture_recurring_errors_from_session_logs(self):
        (self.logs / "openclaw_session_1.log").write_text(
            "\n".join(
                [
                    "INFO start",
                    "ERROR Connection timed out contacting api",
                    "ERROR Connection timed out contacting api",
                    "Traceback (most recent call last):",
                    "ValueError: malformed payload",
                ]
            ),
            encoding="utf-8",
        )

        captured = self.system.capture_recurring_errors(min_occurrences=2)
        self.assertEqual(captured, 1)
        payload = json.loads(self.learnings_file.read_text(encoding="utf-8"))
        self.assertEqual(len(payload), 1)
        self.assertIn("category", payload[0])
        self.assertIn("error", payload[0])
        self.assertIn("lesson", payload[0])
        self.assertIn("resolved", payload[0])
        self.assertEqual(payload[0]["resolved"], False)

    def test_mark_resolved_toggles_status(self):
        self.system.add_entry(
            category="runtime",
            error="TypeError: unsupported operand type",
            lesson="Ensure numeric coercion before calculation.",
        )
        updated = self.system.mark_resolved("TypeError", resolved=True)
        self.assertEqual(updated, 1)
        entries = self.system.search_entries("unsupported operand")
        self.assertTrue(entries[0].resolved)

        reopened = self.system.mark_resolved("TypeError", resolved=False)
        self.assertEqual(reopened, 1)
        entries = self.system.search_entries("unsupported operand")
        self.assertFalse(entries[0].resolved)

    def test_cli_add_list_search_resolve(self):
        base_args = ["--root-dir", str(self.root), "--learnings-file", str(self.learnings_file)]

        self.assertEqual(
            main(
                base_args
                + [
                    "add",
                    "--category",
                    "runtime",
                    "--error",
                    "RuntimeError: model unavailable",
                    "--lesson",
                    "Retry with fallback model.",
                ]
            ),
            0,
        )
        self.assertEqual(main(base_args + ["list"]), 0)
        self.assertEqual(main(base_args + ["search", "model unavailable"]), 0)
        self.assertEqual(main(base_args + ["resolve", "model unavailable"]), 0)


if __name__ == "__main__":
    unittest.main()
