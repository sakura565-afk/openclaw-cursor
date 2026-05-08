import json
import tempfile
import unittest
from pathlib import Path

from src.coordination.cross_bot_sync import parse_memory_entries
from src.error_learning.engine import (
    ErrorLearningEngine,
    classify_error_line,
    memory_line,
    normalize_for_fingerprint,
    stable_fingerprint_id,
)


class ErrorLearningEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_classify_network_and_syntax(self) -> None:
        self.assertEqual(classify_error_line("Connection refused to 127.0.0.1:443"), "network")
        self.assertEqual(classify_error_line("SyntaxError: invalid syntax"), "syntax")

    def test_fingerprint_stable(self) -> None:
        n = normalize_for_fingerprint("ERROR: timeout after 99 seconds")
        fp = stable_fingerprint_id("network", n)
        self.assertEqual(len(fp), 16)
        self.assertEqual(fp, stable_fingerprint_id("network", n))

    def test_ingest_dedupes_and_merges_lesson(self) -> None:
        store = self.root / "store.json"
        eng = ErrorLearningEngine(store_path=store)
        line_a = "task_runner ERROR connection refused on port 8080"
        line_b = "task_runner ERROR connection refused on port 9090"
        eng.ingest_line(line_a, source="test")
        eng.ingest_line(line_b, source="test", lesson="Retry with backoff")
        self.assertEqual(len(eng.records), 1)
        rec = next(iter(eng.records.values()))
        self.assertEqual(rec.occurrences, 2)
        self.assertEqual(rec.lesson, "Retry with backoff")

    def test_relaxed_ingest_records_unknown_without_error_keyword(self) -> None:
        store = self.root / "store.json"
        eng = ErrorLearningEngine(store_path=store)
        eng.ingest_line("some benign status line", source="t", require_error_signal=False)
        self.assertEqual(len(eng.records), 1)

    def test_save_and_reload(self) -> None:
        store = self.root / "store.json"
        eng = ErrorLearningEngine(store_path=store)
        eng.ingest_line("ValueError: bad value", source="x")
        eng.save()
        eng2 = ErrorLearningEngine(store_path=store)
        self.assertEqual(len(eng2.records), 1)

    def test_sync_memory_respects_normalize_memory_key(self) -> None:
        store = self.root / "store.json"
        mem = self.root / "MEMORY.md"
        eng = ErrorLearningEngine(store_path=store)
        eng.ingest_line("ImportError: no module named 'z'", source="log")
        eng.sync_memory(mem)
        text = mem.read_text(encoding="utf-8")
        self.assertIn("## Error learnings", text)
        keys = parse_memory_entries(text)
        self.assertTrue(any(k.startswith("[err-") for k in keys))

    def test_sync_memory_dry_run_no_write(self) -> None:
        store = self.root / "store.json"
        mem = self.root / "MEMORY.md"
        eng = ErrorLearningEngine(store_path=store)
        eng.ingest_line("RuntimeError: boom", source="log")
        eng.sync_memory(mem, dry_run=True)
        self.assertFalse(mem.exists())

    def test_memory_line_single_colon_for_key_stability(self) -> None:
        store = self.root / "store.json"
        eng = ErrorLearningEngine(store_path=store)
        eng.ingest_line("TypeError: unsupported operand", source="log")
        rec = next(iter(eng.records.values()))
        line = memory_line(rec).strip()
        # First ':' separates key prefix for normalize_memory_key in cross_bot_sync.
        before_first, _, after = line.partition(":")
        self.assertIn("[err-", before_first)
        self.assertIn("validation", before_first.lower())
        self.assertIn("note:", after)


class ErrorLearningCliTests(unittest.TestCase):
    def test_classify_command(self) -> None:
        import io
        from contextlib import redirect_stdout

        import scripts.error_learning as el

        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = el.main(["classify", "--text", "HTTP 503 Service Unavailable"])
        self.assertEqual(rc, 0)
        self.assertEqual(buf.getvalue().strip(), "network")


if __name__ == "__main__":
    unittest.main()
