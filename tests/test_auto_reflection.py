import importlib.util
import io
import json
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from datetime import date, datetime, timezone
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "auto_reflection.py"
SPEC = importlib.util.spec_from_file_location("auto_reflection", MODULE_PATH)
auto_reflection = importlib.util.module_from_spec(SPEC)
sys.modules["auto_reflection"] = auto_reflection
assert SPEC.loader is not None
SPEC.loader.exec_module(auto_reflection)


FACESWAP_LOG = """## 2026-05-06 10:45:58 UTC | Batch summary

{'processed': 3, 'swapped': 3, 'skipped': 0, 'failed': 0}

## 2026-05-06 10:45:58 UTC | Self-test

OK | stats={'processed': 3, 'swapped': 3, 'skipped': 0, 'failed': 0}
"""

OBSIDIAN_LOG = """# Obsidian Dashboard Log

Dashboard runtime events are appended here.
## 2026-05-06 10:47:25 — Vault scan completed
- Path: `/workspace`
- Files: 106
- Broken links: 0
- Unlinked mentions: 0
## 2026-05-06 10:47:28 — Server starting
- Vault: `/workspace`
- Port: 5000
"""

FAILING_LOG = """## 2026-05-05 09:00:00 UTC | Ollama benchmark

Run failed with traceback:
- processed: 4
- failed: 2
- errors: 1

## 2026-05-05 09:30:00 UTC | Ollama retry

Retry attempt timed out; benchmark unhealthy.
"""


def _write_logs(directory: Path) -> None:
    (directory / "faceswap_log.md").write_text(FACESWAP_LOG, encoding="utf-8")
    (directory / "obsidian_dashboard_log.md").write_text(OBSIDIAN_LOG, encoding="utf-8")
    (directory / "ollama_log.md").write_text(FAILING_LOG, encoding="utf-8")


class ParseTranscriptTest(unittest.TestCase):
    def test_parses_blocks_timestamps_and_categories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "faceswap_log.md"
            log_path.write_text(FACESWAP_LOG, encoding="utf-8")

            entries = auto_reflection.parse_transcript(log_path)

            self.assertEqual(len(entries), 2)
            self.assertEqual(entries[0].category, "image")
            self.assertIn("image", entries[0].tags)
            self.assertEqual(
                entries[0].timestamp,
                datetime(2026, 5, 6, 10, 45, 58, tzinfo=timezone.utc),
            )
            self.assertEqual(entries[0].metrics.get("processed"), 3)
            self.assertEqual(entries[0].metrics.get("failed"), 0)

    def test_zero_value_failure_keywords_are_suppressed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "faceswap_log.md"
            log_path.write_text(FACESWAP_LOG, encoding="utf-8")

            entries = auto_reflection.parse_transcript(log_path)

            for entry in entries:
                self.assertFalse(
                    entry.is_failure,
                    msg=f"entry should not be flagged as failure: {entry.failure_signals}",
                )
                self.assertTrue(entry.is_success)

    def test_failure_keywords_are_kept_when_metric_is_nonzero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "ollama_log.md"
            log_path.write_text(FAILING_LOG, encoding="utf-8")

            entries = auto_reflection.parse_transcript(log_path)

            self.assertTrue(any(entry.is_failure for entry in entries))
            metrics_signals = [
                signal
                for entry in entries
                for signal in entry.failure_signals
                if signal.startswith("metric:")
            ]
            self.assertTrue(any("failed=2" in signal for signal in metrics_signals))


class BuildReportTest(unittest.TestCase):
    def _collect_entries(self, memory_dir: Path) -> list:
        entries: list = []
        for transcript in auto_reflection.collect_transcripts(memory_dir):
            entries.extend(auto_reflection.parse_transcript(transcript))
        return entries

    def test_window_filters_and_aggregates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            memory_dir = Path(tmp)
            _write_logs(memory_dir)

            entries = self._collect_entries(memory_dir)
            report = auto_reflection.build_report(
                entries=entries,
                memory_dir=memory_dir,
                reference_date=date(2026, 5, 7),
                lookback_days=7,
                generated_at=datetime(2026, 5, 7, 0, 0, tzinfo=timezone.utc),
            )

            self.assertEqual(report.window_start, date(2026, 5, 1))
            self.assertEqual(report.window_end, date(2026, 5, 7))
            self.assertGreaterEqual(report.summary["entries_in_window"], 4)
            self.assertGreaterEqual(report.summary["failures"], 1)
            self.assertGreaterEqual(report.summary["successes"], 1)
            self.assertIn("image", report.category_stats)
            self.assertIn("model", report.category_stats)
            self.assertTrue(any("`model`" in insight for insight in report.insights))

    def test_empty_window_emits_dormancy_insight(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            memory_dir = Path(tmp)
            _write_logs(memory_dir)

            entries = self._collect_entries(memory_dir)
            report = auto_reflection.build_report(
                entries=entries,
                memory_dir=memory_dir,
                reference_date=date(2025, 1, 1),
                lookback_days=3,
            )

            self.assertEqual(report.summary["entries_in_window"], 0)
            self.assertTrue(report.insights)
            self.assertIn("No transcript activity", report.insights[0])


class RenderingAndPersistenceTest(unittest.TestCase):
    def test_idempotent_section_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            memory_dir = Path(tmp) / "memory"
            memory_dir.mkdir()
            _write_logs(memory_dir)
            learnings_file = Path(tmp) / ".learnings" / "LEARNINGS.md"

            report = auto_reflection.run_reflection(
                memory_dir=memory_dir,
                learnings_file=learnings_file,
                reference_date=date(2026, 5, 7),
                lookback_days=7,
                lock_path=Path(tmp) / ".learnings" / ".cron.lock",
            )

            first_text = learnings_file.read_text(encoding="utf-8")
            self.assertIn("# LEARNINGS", first_text)
            self.assertIn("2026-05-07 — Auto-Reflection", first_text)
            self.assertEqual(first_text.count("auto-reflection:section 2026-05-07"), 1)

            auto_reflection.run_reflection(
                memory_dir=memory_dir,
                learnings_file=learnings_file,
                reference_date=date(2026, 5, 7),
                lookback_days=7,
                lock_path=Path(tmp) / ".learnings" / ".cron.lock",
            )
            second_text = learnings_file.read_text(encoding="utf-8")
            self.assertEqual(second_text.count("auto-reflection:section 2026-05-07"), 1)

            auto_reflection.run_reflection(
                memory_dir=memory_dir,
                learnings_file=learnings_file,
                reference_date=date(2026, 5, 8),
                lookback_days=7,
                lock_path=Path(tmp) / ".learnings" / ".cron.lock",
            )
            third_text = learnings_file.read_text(encoding="utf-8")
            self.assertEqual(third_text.count("auto-reflection:section 2026-05-07"), 1)
            self.assertEqual(third_text.count("auto-reflection:section 2026-05-08"), 1)
            self.assertLess(
                third_text.index("2026-05-07 — Auto-Reflection"),
                third_text.index("2026-05-08 — Auto-Reflection"),
            )
            self.assertEqual(report.reference_date, date(2026, 5, 7))

    def test_json_dump(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            memory_dir = Path(tmp) / "memory"
            memory_dir.mkdir()
            _write_logs(memory_dir)
            learnings_file = Path(tmp) / ".learnings" / "LEARNINGS.md"
            json_out = Path(tmp) / "report.json"

            auto_reflection.run_reflection(
                memory_dir=memory_dir,
                learnings_file=learnings_file,
                reference_date=date(2026, 5, 7),
                lookback_days=7,
                json_path=json_out,
                lock_path=Path(tmp) / ".learnings" / ".cron.lock",
            )

            payload = json.loads(json_out.read_text(encoding="utf-8"))
            self.assertEqual(payload["reference_date"], "2026-05-07")
            self.assertIn("summary", payload)
            self.assertIn("insights", payload)


class LockingTest(unittest.TestCase):
    def test_lock_blocks_concurrent_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / "cron.lock"
            with auto_reflection.ReflectionLock(lock_path, timeout_seconds=60):
                with self.assertRaises(auto_reflection.LockHeldError):
                    other = auto_reflection.ReflectionLock(lock_path, timeout_seconds=60)
                    other.acquire()
            self.assertFalse(lock_path.exists())

    def test_stale_lock_is_reclaimed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / "cron.lock"
            lock_path.write_text("stale", encoding="utf-8")
            past = time.time() - 3600
            import os

            os.utime(lock_path, (past, past))

            lock = auto_reflection.ReflectionLock(lock_path, timeout_seconds=60)
            lock.acquire()
            try:
                self.assertTrue(lock_path.exists())
            finally:
                lock.release()
            self.assertFalse(lock_path.exists())


class CLITest(unittest.TestCase):
    def test_cli_writes_learnings_and_prints_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            memory_dir = Path(tmp) / "memory"
            memory_dir.mkdir()
            _write_logs(memory_dir)
            learnings_file = Path(tmp) / ".learnings" / "LEARNINGS.md"
            lock_file = Path(tmp) / ".learnings" / ".cron.lock"

            argv = [
                "--memory-dir",
                str(memory_dir),
                "--learnings-file",
                str(learnings_file),
                "--days",
                "7",
                "--reference-date",
                "2026-05-07",
                "--lock-file",
                str(lock_file),
            ]
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                exit_code = auto_reflection.main(argv)

            self.assertEqual(exit_code, 0)
            self.assertTrue(learnings_file.exists())
            self.assertIn("auto_reflection:", buffer.getvalue())

    def test_cli_rejects_invalid_days(self) -> None:
        argv = [
            "--memory-dir",
            "/nonexistent",
            "--learnings-file",
            "/tmp/_no_write.md",
            "--days",
            "0",
        ]
        exit_code = auto_reflection.main(argv)
        self.assertEqual(exit_code, 2)


if __name__ == "__main__":
    unittest.main()
