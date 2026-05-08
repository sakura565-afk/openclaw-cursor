import json
import os
import tempfile
import unittest
from pathlib import Path

from src.coordination.error_learning import (
    categorize_error,
    extract_error_signals,
    fingerprint_excerpt,
    merge_lesson_into_memory,
    process_signals,
    register_learning,
)


class ErrorLearningTests(unittest.TestCase):
    def test_categorize_network(self) -> None:
        self.assertEqual(
            categorize_error("Connection refused connecting to host:5432"),
            "network",
        )

    def test_categorize_import(self) -> None:
        self.assertEqual(
            categorize_error("ModuleNotFoundError: No module named 'foo'"),
            "import_module",
        )

    def test_extract_traceback(self) -> None:
        log = """Traceback (most recent call last):
  File "app.py", line 1, in <module>
    main()
  File "app.py", line 2, in main
    raise ValueError("bad")
ValueError: bad
"""
        sigs = extract_error_signals(log)
        self.assertEqual(len(sigs), 1)
        self.assertIn("ValueError", sigs[0].excerpt)
        self.assertEqual(sigs[0].category, "runtime")

    def test_merge_lesson_inserts_section(self) -> None:
        text = "# Notes\n\nSome intro.\n"
        bullet = "- **network** (2026-05-08): lesson — hint"
        new_text, changed = merge_lesson_into_memory(text, bullet, section_heading="Error learnings")
        self.assertTrue(changed)
        self.assertIn("## Error learnings", new_text)
        self.assertIn("lesson — hint", new_text)

    def test_merge_lesson_idempotent(self) -> None:
        bullet = "- **network** (2026-05-08): duplicate — hint"
        base = "# x\n\n## Error learnings\n\n" + bullet + "\n"
        new_text, changed = merge_lesson_into_memory(base, bullet)
        self.assertFalse(changed)
        self.assertEqual(new_text, base)

    def test_process_signals_writes_jsonl_and_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            jsonl = root / "learned.jsonl"
            memory = root / "MEMORY.md"
            prev = os.environ.get("OPENCLAW_MEMORY_PATH")
            os.environ["OPENCLAW_MEMORY_PATH"] = str(memory)
            try:
                signals = extract_error_signals("ERROR: deadline exceeded when calling upstream")
                w, s = process_signals(
                    signals,
                    source="test",
                    jsonl_path=jsonl,
                    memory_path=memory,
                    skip_if_seen=False,
                )
                self.assertGreaterEqual(w, 1)
                self.assertEqual(s, 0)
                self.assertTrue(jsonl.exists())
                row = json.loads(jsonl.read_text(encoding="utf-8").strip().splitlines()[0])
                self.assertEqual(row["category"], "timeout")
                mem_text = memory.read_text(encoding="utf-8")
                self.assertIn("Error learnings", mem_text)
                self.assertIn("timeout", mem_text.lower())
            finally:
                if prev is None:
                    os.environ.pop("OPENCLAW_MEMORY_PATH", None)
                else:
                    os.environ["OPENCLAW_MEMORY_PATH"] = prev

    def test_register_learning_skip_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            jsonl = root / "x.jsonl"
            excerpt = "Same failure message"
            r1 = register_learning(
                excerpt=excerpt,
                jsonl_path=jsonl,
                memory_path=root / "m.md",
                write_memory=False,
                skip_if_seen=True,
            )
            self.assertIsNotNone(r1)
            fp = fingerprint_excerpt(excerpt)
            self.assertEqual(r1.fingerprint, fp)
            r2 = register_learning(
                excerpt=excerpt,
                jsonl_path=jsonl,
                memory_path=root / "m.md",
                write_memory=False,
                skip_if_seen=True,
            )
            self.assertIsNone(r2)


if __name__ == "__main__":
    unittest.main()
