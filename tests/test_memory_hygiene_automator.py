from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import memory_hygiene_automator as mha  # noqa: E402


class MemoryHygieneAutomatorTests(unittest.TestCase):
    def test_extract_promotions_round_trip(self) -> None:
        raw = """# Day

keep me
<!-- openclaw-memory-promotion -->
One **block**
<!-- /openclaw-memory-promotion -->
tail
"""
        cleaned, bodies = mha.extract_promotions(raw)
        self.assertEqual(bodies, ["One **block**"])
        self.assertIn("keep me", cleaned)
        self.assertIn("tail", cleaned)
        self.assertNotIn("openclaw-memory-promotion", cleaned)

    def test_dedupe_memory_md_keeps_one_copy(self) -> None:
        text = """# Memory

## First

Same fact line.

## Second

Same fact line.
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "MEMORY.md"
            path.write_text(text, encoding="utf-8")
            report = mha.dedupe_memory_md(path, dry_run=False)
            self.assertFalse(report.get("skipped"))
            self.assertEqual(report.get("removed"), 1)
            new_text = path.read_text(encoding="utf-8")
            self.assertEqual(new_text.count("Same fact line."), 1)


if __name__ == "__main__":
    unittest.main()
