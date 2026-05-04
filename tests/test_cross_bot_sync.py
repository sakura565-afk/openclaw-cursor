import json
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from src.coordination.cross_bot_sync import (
    CrossBotSyncCoordinator,
    FileLock,
    LockTimeoutError,
    parse_memory_entries,
)


class CrossBotSyncCoordinatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.base = Path(self.temp_dir.name)
        self.shared_dir = self.base / "shared"
        self.main_memory = self.base / "main" / "MEMORY.md"
        self.tasks_memory = self.base / "tasks" / "MEMORY.md"
        self.main_memory.parent.mkdir(parents=True, exist_ok=True)
        self.tasks_memory.parent.mkdir(parents=True, exist_ok=True)
        self.coordinator = CrossBotSyncCoordinator(shared_dir=self.shared_dir, lock_timeout=0.2)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_sync_memory_merges_entries_across_bots(self) -> None:
        self.main_memory.write_text("- Alpha memory\n- Shared fact\n", encoding="utf-8")
        result_main = self.coordinator.sync_memory("main", self.main_memory)

        self.tasks_memory.write_text("- Shared fact\n- Beta memory\n", encoding="utf-8")
        result_tasks = self.coordinator.sync_memory("tasks", self.tasks_memory)

        expected = {
            "alpha memory",
            "shared fact",
            "beta memory",
        }
        self.assertEqual(set(parse_memory_entries(self.main_memory.read_text(encoding="utf-8"))), expected)
        self.assertEqual(set(parse_memory_entries(self.tasks_memory.read_text(encoding="utf-8"))), expected)
        self.assertEqual(result_main["entry_count"], 2)
        self.assertEqual(result_tasks["entry_count"], 3)

        state = json.loads((self.shared_dir / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(set(state["memory"]["entries"]), expected)
        self.assertIn("main", state["memory"]["bots"])
        self.assertIn("tasks", state["memory"]["bots"])

    def test_sync_memory_prefers_newer_entry_on_conflict(self) -> None:
        self.main_memory.write_text("- Result: old value\n", encoding="utf-8")
        self.coordinator.sync_memory("main", self.main_memory)

        time.sleep(1.1)
        self.tasks_memory.write_text("- Result: new value\n", encoding="utf-8")
        self.coordinator.sync_memory("tasks", self.tasks_memory)

        self.assertIn("new value", self.main_memory.read_text(encoding="utf-8"))
        self.assertIn("new value", self.tasks_memory.read_text(encoding="utf-8"))
        self.assertNotIn("old value", self.main_memory.read_text(encoding="utf-8"))

    def test_task_handoff_prevents_duplicate_claims(self) -> None:
        first = self.coordinator.handoff_task("Investigate flaky CI", bot_name="main")
        second = self.coordinator.handoff_task("Investigate flaky CI", bot_name="tasks")
        takeover = self.coordinator.handoff_task(
            "Investigate flaky CI",
            bot_name="main",
            target_bot="tasks",
            allow_takeover=True,
        )
        released = self.coordinator.handoff_task(
            "Investigate flaky CI",
            bot_name="tasks",
            release=True,
        )

        self.assertTrue(first["claimed"])
        self.assertFalse(second["claimed"])
        self.assertEqual(second["owner"], "main")
        self.assertTrue(takeover["claimed"])
        self.assertEqual(takeover["owner"], "tasks")
        self.assertTrue(released["released"])

    def test_write_status_persists_shared_status_file(self) -> None:
        payload = self.coordinator.write_status(
            bot_name="main",
            status="working",
            task="Sync memories",
            details={"progress": 50},
        )

        self.assertEqual(payload["status"], "working")
        status_file = self.shared_dir / "bot_status.json"
        content = json.loads(status_file.read_text(encoding="utf-8"))
        self.assertEqual(content["bots"]["main"]["task"], "Sync memories")
        self.assertEqual(content["bots"]["main"]["details"]["progress"], 50)

    def test_unlock_removes_lock_file(self) -> None:
        lock = FileLock(self.shared_dir / "state.lock", timeout=0.1)
        lock.acquire()
        try:
            self.assertTrue((self.shared_dir / "state.lock").exists())
        finally:
            removed = self.coordinator.unlock()
            self.assertTrue(removed)

    def test_lock_timeout_raises_when_lock_is_held(self) -> None:
        first = FileLock(self.shared_dir / "state.lock", timeout=0.1)
        second = FileLock(self.shared_dir / "state.lock", timeout=0.1)
        first.acquire()
        try:
            with self.assertRaises(LockTimeoutError):
                second.acquire()
        finally:
            first.release()

    def test_cli_status_command_writes_status(self) -> None:
        env = os.environ.copy()
        env["PYTHONPATH"] = "/workspace"
        command = [
            sys.executable,
            "-m",
            "src.coordination.cross_bot_sync",
            "--shared-dir",
            str(self.shared_dir),
            "status",
            "--bot",
            "tasks",
            "--state",
            "idle",
            "--task",
            "Waiting",
            "--details",
            '{"queue": 0}',
        ]
        result = subprocess.run(command, check=True, capture_output=True, text=True, env=env)

        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "idle")
        status_file = json.loads((self.shared_dir / "bot_status.json").read_text(encoding="utf-8"))
        self.assertEqual(status_file["bots"]["tasks"]["details"]["queue"], 0)


if __name__ == "__main__":
    unittest.main()
