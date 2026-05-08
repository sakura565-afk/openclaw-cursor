"""Tests for error learning taxonomy, store, memory bridge, and engine."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from src.error_learning.engine import ErrorLearningEngine
from src.error_learning.memory_bridge import merge_error_learning_section
from src.error_learning.store import compute_fingerprint
from src.error_learning.taxonomy import ErrorCategory, classify_error_text


class TaxonomyTests(unittest.TestCase):
    def test_import_error(self) -> None:
        result = classify_error_text("ModuleNotFoundError: No module named 'foo'")
        self.assertEqual(result.category, ErrorCategory.IMPORT)

    def test_network_error(self) -> None:
        result = classify_error_text("Connection refused to host api.example:443")
        self.assertEqual(result.category, ErrorCategory.NETWORK)

    def test_runtime_error(self) -> None:
        result = classify_error_text("ValueError: invalid literal for int()")
        self.assertEqual(result.category, ErrorCategory.RUNTIME)

    def test_unknown_signal(self) -> None:
        result = classify_error_text("something failed without tokens")
        self.assertEqual(result.category, ErrorCategory.UNKNOWN)

    def test_fingerprint_stable(self) -> None:
        a = compute_fingerprint("network", "connection refused #")
        b = compute_fingerprint("network", "connection refused #")
        self.assertEqual(a, b)


class MemoryBridgeTests(unittest.TestCase):
    def test_inserts_section(self) -> None:
        bullets = ["- **[network]** test (seen 1×, last 2026-05-08)"]
        out = merge_error_learning_section("# Memory\n", bullets)
        self.assertIn("## Error learning", out)
        self.assertIn("network", out)

    def test_merges_existing_bullets(self) -> None:
        base = "# Mem\n\n## Error learning\n\n- **[io]** old\n\n## Other\n\nx\n"
        bullets = ["- **[network]** new (seen 2×, last 2026-05-08)"]
        out = merge_error_learning_section(base, bullets)
        self.assertIn("## Other", out)
        self.assertIn("old", out)
        self.assertIn("network", out)


class EngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_ingest_and_dedupe(self) -> None:
        store = self.root / "store.json"
        engine = ErrorLearningEngine(root_dir=self.root, store_path=store, log_roots=[])
        line = "2026-05-08 beta ERROR ValueError: bad data 42"
        o1 = engine.ingest_line(line, source="/tmp/a.log")
        o2 = engine.ingest_line(line, source="/tmp/a.log")
        self.assertIsNotNone(o1)
        self.assertIsNotNone(o2)
        self.assertEqual(o2.count, 2)
        obs = engine.observations()
        self.assertEqual(len(obs), 1)

    def test_ignores_clean_lines(self) -> None:
        store = self.root / "store.json"
        engine = ErrorLearningEngine(root_dir=self.root, store_path=store, log_roots=[])
        self.assertIsNone(engine.ingest_line("all good here"))


class ScriptSmokeTests(unittest.TestCase):
    def test_cli_classify(self) -> None:
        root = Path(__file__).resolve().parents[1]
        proc = subprocess.run(
            [sys.executable, str(root / "scripts" / "error_learning.py"), "classify", "--text", "ImportError: x"],
            cwd=str(root),
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONPATH": str(root)},
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("import", proc.stdout)
