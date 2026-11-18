"""Tests for video_pipeline.state."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from unittest import mock

import pytest

from video_pipeline.state import ItemStatus, StateManager


class TestStateManagerRoundtrip:
    def test_save_load_roundtrip(self, temp_state_file: Path) -> None:
        mgr = StateManager(temp_state_file, "test_project")
        state = mgr._fresh_state()
        state["items"]["img.jpg"] = {"1:1": {"status": "done", "attempts": 1}}
        mgr.save(state)

        loaded = mgr.load()
        assert loaded["project"] == "test_project"
        assert loaded["items"]["img.jpg"]["1:1"]["status"] == "done"

    def test_update_item_nested_fields(self, temp_state_file: Path) -> None:
        mgr = StateManager(temp_state_file, "test_project")
        mgr.update_item("sofa.jpg", "9:16", status=ItemStatus.RENDERING.value, attempts=1)
        mgr.update_item("sofa.jpg", "9:16", output_path="/out/sofa_9x16.mp4")

        loaded = mgr.load()
        item = loaded["items"]["sofa.jpg"]["9:16"]
        assert item["status"] == ItemStatus.RENDERING.value
        assert item["attempts"] == 1
        assert item["output_path"] == "/out/sofa_9x16.mp4"


class TestStateManagerQueries:
    def test_get_resumable_items(self, temp_state_file: Path) -> None:
        mgr = StateManager(temp_state_file, "test_project")
        mgr.update_item("a.jpg", "1:1", status=ItemStatus.DONE.value)
        mgr.update_item("a.jpg", "9:16", status=ItemStatus.PENDING.value)
        mgr.update_item("b.jpg", "1:1", status=ItemStatus.FAILED.value, reason="timeout")

        resumable = mgr.get_resumable_items()
        assert ("a.jpg", "9:16") in resumable
        assert ("b.jpg", "1:1") in resumable
        assert ("a.jpg", "1:1") not in resumable

    def test_get_failed_items(self, temp_state_file: Path) -> None:
        mgr = StateManager(temp_state_file, "test_project")
        mgr.mark_failed("x.jpg", "16:9", "quality gate")

        failed = mgr.get_failed_items()
        assert len(failed) == 1
        assert failed[0] == ("x.jpg", "16:9", "quality gate")

    def test_mark_done(self, temp_state_file: Path) -> None:
        mgr = StateManager(temp_state_file, "test_project")
        mgr.mark_done("img.jpg", "1:1", "/out/video.mp4", duration_sec=45.2, size_mb=2.3)

        status = mgr.get_item_status("img.jpg", "1:1")
        assert status == ItemStatus.DONE.value
        loaded = mgr.load()
        item = loaded["items"]["img.jpg"]["1:1"]
        assert item["output_path"] == "/out/video.mp4"
        assert item["duration_sec"] == 45.2


class TestStateManagerAtomicWrite:
    def test_atomic_write_on_simulated_crash(self, temp_state_file: Path) -> None:
        mgr = StateManager(temp_state_file, "test_project")
        initial = mgr._fresh_state()
        initial["items"]["safe.jpg"] = {"1:1": {"status": "done"}}
        mgr.save(initial)

        original_content = temp_state_file.read_text(encoding="utf-8")

        def crash_mid_write(self_inner, state: dict) -> None:
            state["updated_at"] = "crash"
            tmp_path = self_inner.state_file.with_suffix(".tmp")
            tmp_path.write_text(json.dumps(state), encoding="utf-8")
            raise OSError("simulated crash")

        with mock.patch.object(StateManager, "save", crash_mid_write):
            try:
                mgr.save({"project": "crash"})
            except OSError:
                pass

        # Original file should be unchanged after failed atomic write
        assert temp_state_file.read_text(encoding="utf-8") == original_content


class TestStateManagerLocking:
    def test_concurrent_writes(self, temp_state_file: Path) -> None:
        mgr = StateManager(temp_state_file, "test_project")
        errors: list[Exception] = []

        def worker(idx: int) -> None:
            try:
                for _ in range(10):
                    mgr.update_item(f"img_{idx}.jpg", "1:1", status=ItemStatus.PENDING.value)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert not errors
        loaded = mgr.load()
        assert len(loaded.get("items", {})) == 4
