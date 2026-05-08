import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.self_improvement.auto_engine import AutoImprovementEngine, CheckResult
from src.self_improvement.error_learning import (
    SCHEMA_VERSION,
    ErrorCategory,
    ErrorLearningSystem,
    categorize_error,
    normalize_error_text,
)


class ErrorLearningTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.store = Path(self.temp_dir.name)

    def test_normalize_collapses_numbers_and_paths(self):
        n = normalize_error_text(
            "Connection refused to 192.168.1.99:443 on /home/user/proj/file.py line 42"
        )
        self.assertIn("<ip>", n)
        self.assertIn("<path>", n)
        self.assertIn("#", n)

    def test_same_semantics_share_signature(self):
        a = normalize_error_text("timeout after 30 seconds")
        b = normalize_error_text("timeout after 99 seconds")
        from src.self_improvement.error_learning import signature_for

        self.assertEqual(signature_for(a), signature_for(b))

    def test_categorize_exception_type_overrides_message(self):
        self.assertEqual(categorize_error("something vague", "MemoryError"), ErrorCategory.RESOURCE_MEMORY)

    def test_record_increments_and_persists(self):
        learner = ErrorLearningSystem(storage_dir=self.store, append_audit_log=False)
        r1 = learner.record("timeout after 30 seconds", source="test")
        r2 = learner.record("timeout after 31 seconds", source="test")
        self.assertEqual(r1.signature, r2.signature)
        self.assertEqual(r2.count, 2)
        self.assertGreaterEqual(r2.sources.get("test", 0), 1)

        learner2 = ErrorLearningSystem(storage_dir=self.store, append_audit_log=False)
        summary = learner2.summary()
        self.assertEqual(summary["distinct_patterns"], 1)
        self.assertEqual(summary["total_occurrences"], 2)

    def test_corrupt_store_is_quarantined_and_recovers(self):
        store_path = self.store / "error_patterns_v1.json"
        self.store.mkdir(parents=True, exist_ok=True)
        store_path.write_text("{not json", encoding="utf-8")
        learner = ErrorLearningSystem(storage_dir=self.store, append_audit_log=False)
        learner.record("new error after corrupt", source="recovery")
        self.assertTrue(store_path.exists())
        payload = json.loads(store_path.read_text(encoding="utf-8"))
        self.assertEqual(payload.get("schema_version"), SCHEMA_VERSION)
        self.assertEqual(len(payload.get("patterns", [])), 1)

    def test_ingest_traceback_block(self):
        learner = ErrorLearningSystem(storage_dir=self.store, append_audit_log=False)
        log = """
some info
Traceback (most recent call last):
  File "app.py", line 1, in <module>
    x = 1 / 0
ZeroDivisionError: division by zero
normal line
""".strip().splitlines()
        n = learner.ingest_log_stream(log, source="unit")
        self.assertGreaterEqual(n, 1)
        cats = [p.category for p in learner.top_patterns(limit=5)]
        self.assertIn(ErrorCategory.TYPE_VALUE, cats)

    def test_record_exception_chain(self):
        learner = ErrorLearningSystem(storage_dir=self.store, append_audit_log=False)
        try:
            raise ValueError("inner") from RuntimeError("outer")
        except ValueError as exc:
            row = learner.record_exception(exc, source="unit")
        self.assertIn("ValueError", row.samples[-1])

    def test_subprocess_failure_recorded(self):
        learner = ErrorLearningSystem(storage_dir=self.store, append_audit_log=False)
        proc = subprocess.CompletedProcess(
            args=["tool", "arg"],
            returncode=7,
            stdout="",
            stderr="disk full",
        )
        row = learner.record_subprocess(proc)
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row.count, 1)

    def test_prune_old_patterns(self):
        learner = ErrorLearningSystem(storage_dir=self.store, max_patterns=2, append_audit_log=False)
        learner.record("unique error one", source="a")
        learner.record("unique error two", source="b")
        learner.record("unique error three", source="c")
        self.assertLessEqual(learner.summary()["distinct_patterns"], 2)


class AutoEngineErrorLearningIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.logs = self.root / "logs"
        self.logs.mkdir(parents=True, exist_ok=True)
        self.el_store = self.root / "el"

    def test_auto_fix_feeds_error_learner(self):
        from src.self_improvement.error_learning import ErrorLearningSystem

        learner = ErrorLearningSystem(storage_dir=self.el_store, append_audit_log=False)

        def runner(command):
            cmd_list = list(command)
            if cmd_list[:2] == ["ollama", "list"]:
                return subprocess.CompletedProcess(cmd_list, 1, "", "service unavailable")
            if cmd_list[:3] == ["systemctl", "--user", "restart"]:
                return subprocess.CompletedProcess(cmd_list, 0, "ok", "")
            return subprocess.CompletedProcess(cmd_list, 0, "", "")

        engine = AutoImprovementEngine(
            root_dir=self.root,
            log_dir=self.logs,
            command_runner=runner,
            color=False,
            error_learner=learner,
        )
        with mock.patch("src.self_improvement.auto_engine.shutil.which", return_value="/usr/bin/tool"), mock.patch(
            "src.self_improvement.auto_engine.shutil.disk_usage",
            return_value=shutil._ntuple_diskusage(total=100, used=92, free=8),
        ), mock.patch(
            "src.self_improvement.auto_engine.tempfile.gettempdir",
            return_value=str(self.root),
        ), mock.patch.object(
            AutoImprovementEngine,
            "check_memory_usage",
            return_value=CheckResult(name="memory", status="ok", message="ok"),
        ):
            engine.auto_fix()

        self.assertGreater(learner.summary()["total_occurrences"], 0)


if __name__ == "__main__":
    unittest.main()
