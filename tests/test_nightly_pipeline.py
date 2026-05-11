import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "nightly_pipeline.py"
SPEC = importlib.util.spec_from_file_location("nightly_pipeline_mod", MODULE_PATH)
nightly = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = nightly
SPEC.loader.exec_module(nightly)


class NightlyPipelineCheckpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.state_path = Path(self.tmp.name) / "nightly_pipeline_state.json"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_load_save_roundtrip(self) -> None:
        ids = ["a", "b"]
        st = nightly.default_state(ids)
        nightly.save_state(self.state_path, st)
        loaded = nightly.load_state(self.state_path, ids)
        self.assertEqual(st["steps"], loaded["steps"])
        self.assertEqual(st["version"], loaded["version"])

    def test_running_normalized_to_pending_on_load(self) -> None:
        raw = {
            "version": 1,
            "steps": {
                "memory_cleanup": {
                    "status": nightly.STATUS_RUNNING,
                    "last_run_timestamp": "2026-05-11T10:00:00",
                }
            },
        }
        self.state_path.write_text(json.dumps(raw), encoding="utf-8")
        loaded = nightly.load_state(self.state_path, ["memory_cleanup"])
        self.assertEqual(
            nightly.STATUS_PENDING, loaded["steps"]["memory_cleanup"]["status"]
        )

    def test_resume_skips_completed(self) -> None:
        calls: list[str] = []

        def s1(ctx):
            calls.append("s1")
            return "OK"

        def s2(ctx):
            calls.append("s2")
            return "OK"

        def s3(ctx):
            calls.append("s3")
            return "OK"

        specs = [
            ("memory_cleanup", "Memory Cleanup", s1),
            ("obsidian_sync", "Obsidian Sync", s2),
            ("generate_morning_brief", "Morning Brief", s3),
        ]

        pre = {
            "version": 1,
            "steps": {
                "memory_cleanup": {
                    "status": nightly.STATUS_COMPLETED,
                    "last_run_timestamp": "2026-05-11T09:00:00",
                },
                "obsidian_sync": nightly._empty_step_record(),
                "generate_morning_brief": nightly._empty_step_record(),
            },
        }
        self.state_path.write_text(json.dumps(pre), encoding="utf-8")

        summary = nightly.run_pipeline(
            state_path=self.state_path,
            step_specs=specs,
            register_signal_handlers=False,
        )

        self.assertEqual(["s2", "s3"], calls)
        self.assertEqual(["Memory Cleanup"], summary["skipped_completed"])
        self.assertIn("Obsidian Sync", summary["started_this_run"])
        self.assertTrue(summary["all_done"])

        final = nightly.load_state(self.state_path, [s[0] for s in specs])
        for sid in final["steps"]:
            self.assertEqual(nightly.STATUS_PENDING, final["steps"][sid]["status"])

    def test_start_crash_resume_finish(self) -> None:
        """Crash snapshot: step1 completed, step2 RUNNING on disk -> resume with step2 only."""
        calls: list[str] = []

        def s1(ctx):
            calls.append("s1")
            return "OK"

        def s2(ctx):
            calls.append("s2")
            return "OK"

        specs = [
            ("memory_cleanup", "Memory Cleanup", s1),
            ("obsidian_sync", "Obsidian Sync", s2),
        ]

        crash = {
            "version": 1,
            "steps": {
                "memory_cleanup": {
                    "status": nightly.STATUS_COMPLETED,
                    "last_run_timestamp": "2026-05-11T09:00:00",
                },
                "obsidian_sync": {
                    "status": nightly.STATUS_RUNNING,
                    "last_run_timestamp": "2026-05-11T09:01:00",
                },
            },
        }
        self.state_path.write_text(json.dumps(crash), encoding="utf-8")

        summary = nightly.run_pipeline(
            state_path=self.state_path,
            step_specs=specs,
            register_signal_handlers=False,
        )
        self.assertEqual(["s2"], calls)
        self.assertTrue(summary["all_done"])

    def test_retry_then_succeed(self) -> None:
        n = {"c": 0}

        def flaky(ctx):
            n["c"] += 1
            return "OK" if n["c"] >= 2 else "FAIL: nope"

        specs = [("memory_cleanup", "Memory Cleanup", flaky)]
        summary = nightly.run_pipeline(
            state_path=self.state_path,
            step_specs=specs,
            register_signal_handlers=False,
        )
        self.assertEqual(2, n["c"])
        self.assertEqual(["Memory Cleanup"], summary["completed_this_run"])
        self.assertFalse(summary["failed"])

    def test_fail_after_retry_stops_pipeline(self) -> None:
        def bad(ctx):
            return "FAIL: always"

        specs = [
            ("memory_cleanup", "Memory Cleanup", bad),
            ("obsidian_sync", "Obsidian Sync", lambda ctx: "OK"),
        ]
        summary = nightly.run_pipeline(
            state_path=self.state_path,
            step_specs=specs,
            register_signal_handlers=False,
        )
        self.assertEqual(["Memory Cleanup"], summary["failed"])
        self.assertEqual(nightly.STATUS_FAILED, summary["final_state"]["steps"]["memory_cleanup"]["status"])
        self.assertEqual(
            nightly.STATUS_PENDING, summary["final_state"]["steps"]["obsidian_sync"]["status"]
        )

    def test_persist_interrupt_marks_running_as_pending(self) -> None:
        specs = [
            ("memory_cleanup", "Memory Cleanup", lambda ctx: "OK"),
            ("obsidian_sync", "Obsidian Sync", lambda ctx: "OK"),
        ]
        st = nightly.default_state([s[0] for s in specs])
        st["steps"]["memory_cleanup"]["status"] = nightly.STATUS_RUNNING
        nightly.save_state(self.state_path, st)

        nightly._active_state_path = self.state_path
        nightly._active_state = nightly.load_state(self.state_path, [s[0] for s in specs])
        nightly._active_state["steps"]["memory_cleanup"]["status"] = nightly.STATUS_RUNNING
        nightly._active_step_id = "memory_cleanup"

        nightly._persist_interrupt()

        reloaded = nightly.load_state(self.state_path, [s[0] for s in specs])
        self.assertEqual(
            nightly.STATUS_PENDING, reloaded["steps"]["memory_cleanup"]["status"]
        )

        nightly._active_state = None
        nightly._active_state_path = None
        nightly._active_step_id = None


if __name__ == "__main__":
    unittest.main()
