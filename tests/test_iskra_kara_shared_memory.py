import json
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scripts.kara_poll_iskra_results import NO_REPLY, poll_once
from src.coordination.iskra_kara_shared_memory import (
    append_iskra_result,
    collect_fallback_tasks_results,
    commit_fallback_consumed,
    default_results_path,
    drain_shared_memory_entries,
    format_kara_message,
    new_empty_queue_document,
    notify_kara_from_iskra,
)


class IskraKaraSharedMemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.workspace = Path(self.tmp.name) / "ws"
        self.workspace.mkdir(parents=True)
        self.results = default_results_path(self.workspace)

    def test_append_and_drain_round_trip(self) -> None:
        append_iskra_result("reflection", {"summary_markdown": "## Hi"}, results_path=self.results)
        entries, status = drain_shared_memory_entries(results_path=self.results)
        self.assertEqual(status, "drained")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["kind"], "reflection")
        self.assertIn("Hi", entries[0]["payload"]["summary_markdown"])

        again, st2 = drain_shared_memory_entries(results_path=self.results)
        self.assertEqual(st2, "empty")
        self.assertEqual(again, [])

    def test_drain_missing_file(self) -> None:
        entries, status = drain_shared_memory_entries(results_path=self.results)
        self.assertEqual(status, "missing")
        self.assertEqual(entries, [])

    def test_corrupt_queue_file_returns_corrupt(self) -> None:
        self.results.parent.mkdir(parents=True, exist_ok=True)
        self.results.write_text("{broken json", encoding="utf-8")
        entries, status = drain_shared_memory_entries(results_path=self.results)
        self.assertEqual(status, "corrupt")
        self.assertEqual(entries, [])
        self.assertIn("{broken json", self.results.read_text(encoding="utf-8"))

    def test_append_heals_corrupt_file(self) -> None:
        self.results.parent.mkdir(parents=True, exist_ok=True)
        self.results.write_text("not-json", encoding="utf-8")
        append_iskra_result("dream", {"title": "x"}, results_path=self.results)
        raw = json.loads(self.results.read_text(encoding="utf-8"))
        self.assertEqual(len(raw["entries"]), 1)
        self.assertEqual(raw["entries"][0]["kind"], "dream")

    @patch(
        "src.coordination.iskra_kara_shared_memory.append_iskra_result",
        side_effect=RuntimeError("simulated writer failure"),
    )
    def test_notify_never_raises(self, _mock: object) -> None:
        self.assertFalse(notify_kara_from_iskra("reflection", {}))

    def test_format_kara_message_hides_internal_payload_keys(self) -> None:
        text = format_kara_message(
            [
                {
                    "id": "1",
                    "kind": "tasks_result_file",
                    "payload": {"relative_path": "tasks/results/x.md", "content": "body", "_file_mtime": 1.0},
                }
            ]
        )
        self.assertIn("body", text)
        self.assertNotIn("_file_mtime", text)

    def test_fallback_collect_and_commit(self) -> None:
        res_dir = self.workspace / "tasks" / "results"
        res_dir.mkdir(parents=True)
        f = res_dir / "note.md"
        f.write_text("hello fallback", encoding="utf-8")

        first, _ = collect_fallback_tasks_results(self.workspace)
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0]["payload"]["content"], "hello fallback")

        second, _ = collect_fallback_tasks_results(self.workspace)
        self.assertEqual(len(second), 1)

        commit_fallback_consumed(first, self.workspace)

        third, _ = collect_fallback_tasks_results(self.workspace)
        self.assertEqual(third, [])

        time.sleep(0.05)
        f.write_text("updated", encoding="utf-8")
        fourth, _ = collect_fallback_tasks_results(self.workspace)
        self.assertEqual(len(fourth), 1)
        self.assertIn("updated", fourth[0]["payload"]["content"])


class KaraPollScriptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.workspace = Path(self.tmp.name) / "ws"
        self.workspace.mkdir(parents=True)
        self.results = default_results_path(self.workspace)

    def test_poll_shared_priority_over_fallback(self) -> None:
        append_iskra_result("reflection", {"summary_markdown": "A"}, results_path=self.results)
        res_dir = self.workspace / "tasks" / "results"
        res_dir.mkdir(parents=True)
        (res_dir / "b.md").write_text("B", encoding="utf-8")

        entries, source = poll_once(workspace=self.workspace, results_path=self.results, use_fallback=True)
        self.assertEqual(source, "shared")
        self.assertEqual(entries[0]["kind"], "reflection")

    def test_poll_fallback_when_corrupt(self) -> None:
        self.results.parent.mkdir(parents=True, exist_ok=True)
        self.results.write_text("{", encoding="utf-8")
        res_dir = self.workspace / "tasks" / "results"
        res_dir.mkdir(parents=True)
        (res_dir / "c.md").write_text("C", encoding="utf-8")

        entries, source = poll_once(workspace=self.workspace, results_path=self.results, use_fallback=True)
        self.assertEqual(source, "fallback")
        self.assertEqual(entries[0]["payload"]["content"], "C")

    def test_poll_empty_no_fallback(self) -> None:
        self.results.parent.mkdir(parents=True, exist_ok=True)
        atomic_write = json.dumps(new_empty_queue_document(), indent=2)
        self.results.write_text(atomic_write + "\n", encoding="utf-8")
        res_dir = self.workspace / "tasks" / "results"
        res_dir.mkdir(parents=True)
        (res_dir / "d.md").write_text("D", encoding="utf-8")

        entries, source = poll_once(workspace=self.workspace, results_path=self.results, use_fallback=True)
        self.assertEqual(source, "none")
        self.assertEqual(entries, [])

    def test_cli_json_stdout(self) -> None:
        append_iskra_result("reflection", {"summary_markdown": "Z"}, results_path=self.results)
        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
        out = subprocess.check_output(
            [
                sys.executable,
                "-m",
                "scripts.kara_poll_iskra_results",
                "--workspace",
                str(self.workspace),
                "--results-path",
                str(self.results),
                "--json",
            ],
            env=env,
            text=True,
        )
        payload = json.loads(out)
        self.assertEqual(payload["source"], "shared")
        self.assertEqual(len(payload["entries"]), 1)

    def test_cli_no_reply_when_empty(self) -> None:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
        out = subprocess.check_output(
            [
                sys.executable,
                "-m",
                "scripts.kara_poll_iskra_results",
                "--workspace",
                str(self.workspace),
                "--results-path",
                str(self.results),
            ],
            env=env,
            text=True,
        )
        self.assertEqual(out.strip(), NO_REPLY)


class DrainLockTimeoutTests(unittest.TestCase):
    def test_inaccessible_when_lock_held(self) -> None:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        workspace = Path(tmp.name) / "ws"
        workspace.mkdir()
        results = default_results_path(workspace)
        results.parent.mkdir(parents=True, exist_ok=True)
        results.write_text(json.dumps(new_empty_queue_document()) + "\n", encoding="utf-8")
        lock_path = results.with_name(results.name + ".lock")

        from src.coordination.cross_bot_sync import FileLock

        first = FileLock(lock_path, timeout=60.0)
        first.acquire()
        try:
            entries, status = drain_shared_memory_entries(results_path=results, lock_timeout=0.05)
            self.assertEqual(status, "inaccessible")
            self.assertEqual(entries, [])
        finally:
            first.release()


if __name__ == "__main__":
    unittest.main()
