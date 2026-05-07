import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from src.self_improvement.error_learning import ErrorContext, ErrorLearningEngine, ErrorCategory


class ErrorLearningTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.logs = self.root / "logs"
        self.logs.mkdir(parents=True, exist_ok=True)
        self.learnings = self.root / ".learnings"
        self.learnings.mkdir(parents=True, exist_ok=True)

    def make_engine(self):
        return ErrorLearningEngine(root_dir=self.root, log_dir=self.logs, learnings_dir=self.learnings)

    def test_log_error_writes_daily_json_with_context(self):
        engine = self.make_engine()
        exc = ValueError("bad input")
        event = engine.log_error(
            exc,
            context=ErrorContext(module="demo", user_action="submit_form", details={"field": "age"}),
            timestamp=datetime(2099, 1, 1, tzinfo=timezone.utc),
        )

        log_files = list(self.logs.glob("errors_*.json"))
        self.assertEqual(len(log_files), 1)
        payload = json.loads(log_files[0].read_text(encoding="utf-8"))
        self.assertEqual(payload[0]["error_type"], "ValueError")
        self.assertEqual(payload[0]["module"], "demo")
        self.assertEqual(payload[0]["user_action"], "submit_form")
        self.assertIn("stack_trace", payload[0])
        self.assertTrue(payload[0]["signature"])
        self.assertEqual(event.category, ErrorCategory.RECOVERABLE)

    def test_suggest_fix_returns_previous_solution(self):
        engine = self.make_engine()
        ctx = ErrorContext(module="parser", user_action="load_config", details={})
        exc = ModuleNotFoundError("No module named 'tomlkit'")
        engine.log_error(
            exc,
            context=ctx,
            suggested_fix="Install missing dependency",
            solution="Add `tomlkit` to requirements.txt and reinstall.",
            timestamp=datetime(2099, 1, 1, tzinfo=timezone.utc),
        )

        suggestion = engine.suggest_fix(
            error_type="ModuleNotFoundError",
            module="parser",
            message="No module named 'tomlkit'",
        )
        self.assertIn("Install", suggestion)

    def test_append_learning_entry_creates_learnings_md(self):
        engine = self.make_engine()
        ctx = ErrorContext(module="api", user_action="call_endpoint", details={"endpoint": "/v1"})
        exc = ConnectionError("connection reset")
        event = engine.log_error(
            exc,
            context=ctx,
            suggested_fix="Retry with backoff",
            solution="Added retry with exponential backoff for transient network failures.",
            timestamp=datetime(2099, 1, 2, tzinfo=timezone.utc),
        )

        md_path = self.learnings / "LEARNINGS.md"
        self.assertTrue(md_path.exists())
        content = md_path.read_text(encoding="utf-8")
        self.assertIn(event.signature, content)
        self.assertIn("### Solution", content)


if __name__ == "__main__":
    unittest.main()

