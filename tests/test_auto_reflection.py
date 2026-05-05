"""Tests for self_improvement.auto_reflection."""

import tempfile
import unittest
from pathlib import Path

from self_improvement.auto_reflection import (
    analyze_sessions,
    run_auto_reflection,
    sessions_list,
)


class TestAutoReflection(unittest.TestCase):
    def test_sessions_list_empty_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "sessions").mkdir(parents=True, exist_ok=True)
            self.assertEqual(sessions_list(root), [])

    def test_analyze_empty(self) -> None:
        result = analyze_sessions([])
        self.assertEqual(result["session_count"], 0)

    def test_run_writes_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "sessions").mkdir(parents=True, exist_ok=True)
            out = run_auto_reflection(root, day="2020-01-15")
            self.assertTrue(out.is_file())
            self.assertEqual(out.name, "2020-01-15.md")
            content = out.read_text(encoding="utf-8")
            self.assertIn("Auto-reflection", content)


if __name__ == "__main__":
    unittest.main()
