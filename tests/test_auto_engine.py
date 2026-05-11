import json
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from src.self_improvement.auto_engine import AutoImprovementEngine, CheckResult


def completed(
    returncode=0,
    stdout="",
    stderr="",
):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


class AutoEngineTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.logs = self.root / "logs"
        self.logs.mkdir(parents=True, exist_ok=True)

    def make_engine(self, runner=None):
        return AutoImprovementEngine(
            root_dir=self.root,
            log_dir=self.logs,
            command_runner=runner,
            color=False,
        )

    def test_log_warning_writes_daily_json(self):
        engine = self.make_engine()
        action = engine.log_warning("disk pressure", details={"free_percent": 10})

        log_files = list(self.logs.glob("auto_improvements_*.json"))
        self.assertEqual(len(log_files), 1)
        payload = json.loads(log_files[0].read_text(encoding="utf-8"))
        self.assertEqual(payload[0]["action"], "disk pressure")
        self.assertEqual(payload[0]["outcome"], "logged")
        self.assertEqual(action.category, "warning")

    def test_clear_temp_files_only_removes_owned_paths(self):
        owned_dir = self.root / "openclaw_cache"
        owned_dir.mkdir()
        (owned_dir / "data.txt").write_text("cache", encoding="utf-8")
        owned_file = self.root / "ollama_model.tmp"
        owned_file.write_text("junk", encoding="utf-8")
        foreign_file = self.root / "keep_me.txt"
        foreign_file.write_text("safe", encoding="utf-8")

        engine = self.make_engine()
        with mock.patch("src.self_improvement.auto_engine.tempfile.gettempdir", return_value=str(self.root)):
            action = engine.clear_temp_files()

        self.assertFalse(owned_dir.exists())
        self.assertFalse(owned_file.exists())
        self.assertTrue(foreign_file.exists())
        self.assertEqual(action.outcome, "cleared")
        self.assertEqual(action.details["removed_items"], 2)

    def test_auto_fix_restarts_ollama_and_logs_warnings(self):
        calls = []

        def runner(command):
            calls.append(list(command))
            if command[:2] == ["ollama", "list"]:
                return completed(returncode=1, stderr="service unavailable")
            if command[:3] == ["systemctl", "--user", "restart"]:
                return completed(returncode=0, stdout="restarted")
            return completed(returncode=0)

        engine = self.make_engine(runner=runner)
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
            actions = engine.auto_fix()

        action_names = [action.action for action in actions]
        self.assertIn("restart_ollama", action_names)
        self.assertIn("clear_temp_files", action_names)
        self.assertIn("ollama health issue detected", action_names)
        self.assertIn("disk health issue detected", action_names)
        self.assertIn(["systemctl", "--user", "restart", "ollama"], calls)

    def test_generate_weekly_digest_summarizes_recent_entries(self):
        log_path = self.logs / "auto_improvements_20990101.json"
        log_path.write_text(
            json.dumps(
                [
                    {
                        "timestamp": "2099-01-01T00:00:00+00:00",
                        "category": "service",
                        "action": "restart_ollama",
                        "outcome": "restarted",
                        "details": {},
                    },
                    {
                        "timestamp": "2099-01-01T01:00:00+00:00",
                        "category": "cleanup",
                        "action": "clear_temp_files",
                        "outcome": "cleared",
                        "details": {},
                    },
                ]
            ),
            encoding="utf-8",
        )
        engine = self.make_engine()

        class FakeNow:
            def __init__(self):
                self._dt = datetime(2099, 1, 2, tzinfo=timezone.utc)

            def strftime(self, fmt):
                return self._dt.strftime(fmt)

            def date(self):
                return self._dt.date()

            def __sub__(self, other):
                return self._dt - other

        with mock.patch("src.self_improvement.auto_engine._now_utc", return_value=FakeNow()):
            digest = engine.generate_weekly_digest()

        self.assertIn("# Weekly Auto-Improvement Digest", digest)
        self.assertIn("- cleanup: 1", digest)
        self.assertIn("- service: 1", digest)
        self.assertIn("restart_ollama", digest)
        self.assertTrue((self.logs / "weekly_auto_improvement_digest.md").exists())

    def test_sync_error_learning_ingests_failed_actions(self):
        log_file = self.logs / "auto_improvements_20990101.json"
        log_file.write_text(
            json.dumps(
                [
                    {
                        "timestamp": "2099-01-01T00:00:00+00:00",
                        "category": "service",
                        "action": "restart_ollama",
                        "outcome": "failed",
                        "details": {"stderr": "unit test failure", "returncode": 1},
                    }
                ]
            ),
            encoding="utf-8",
        )
        engine = self.make_engine()
        err_log = self.root / ".learnings" / "error_log.json"
        summary = engine.sync_error_learning(err_log, since_days=36500)
        self.assertGreaterEqual(summary.get("learned", 0), 1)
        payload = json.loads(err_log.read_text(encoding="utf-8"))
        self.assertEqual(len(payload["entries"]), 1)
        self.assertIn("unit test failure", payload["entries"][0]["error"])


if __name__ == "__main__":
    unittest.main()
