from __future__ import annotations

import io
import json
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from urllib import error


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import ollama_monitor  # noqa: E402


class FakeResponse:
    def __init__(self, payload: dict, status: int = 200) -> None:
        self.payload = payload
        self.status = status

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


class FakeProcess:
    def __init__(self, pid: int = 4321, polls: list[int | None] | None = None, returncode: int = 0) -> None:
        self.pid = pid
        self._polls = list(polls or [None])
        self.returncode = returncode

    def poll(self) -> int | None:
        if self._polls:
            value = self._polls.pop(0)
            if value is not None:
                self.returncode = value
            return value
        return None


class OllamaMonitorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        (self.root / "logs").mkdir()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def read_json(self, relative_path: str) -> dict:
        return json.loads((self.root / relative_path).read_text(encoding="utf-8"))

    def test_check_health_reports_connection_refused(self) -> None:
        with mock.patch.object(
            ollama_monitor.request,
            "urlopen",
            side_effect=error.URLError(ConnectionRefusedError(111, "Connection refused")),
        ):
            result = ollama_monitor.check_health()

        self.assertFalse(result["healthy"])
        self.assertEqual(result["error_type"], "connection_refused")

    def test_check_health_reports_timeout(self) -> None:
        with mock.patch.object(
            ollama_monitor.request,
            "urlopen",
            side_effect=error.URLError(socket.timeout("timed out")),
        ):
            result = ollama_monitor.check_health()

        self.assertFalse(result["healthy"])
        self.assertEqual(result["error_type"], "timeout")

    def test_check_vram_reports_threshold_breach(self) -> None:
        completed = SimpleNamespace(
            returncode=0,
            stdout="0, 9200, 10000\n1, 1000, 10000\n",
            stderr="",
        )
        with mock.patch.object(ollama_monitor.subprocess, "run", return_value=completed):
            result = ollama_monitor.check_vram()

        self.assertTrue(result["available"])
        self.assertTrue(result["over_threshold"])
        self.assertEqual(result["gpus"][0]["usage_percent"], 92.0)

    def test_gather_status_syncs_stderr_and_updates_daily_log(self) -> None:
        stderr_path = self.root / "logs" / "ollama_20260504.stderr.log"
        stderr_path.write_text("fatal: gpu reset\n", encoding="utf-8")
        state = ollama_monitor.default_state()
        state["stderr_log_path"] = str(stderr_path)
        state["ollama_start_time"] = "2026-05-04T20:00:00+00:00"
        ollama_monitor.save_state(
            self.root,
            state,
            now=ollama_monitor.parse_timestamp("2026-05-04T20:05:00+00:00"),
        )

        with mock.patch.object(
            ollama_monitor,
            "check_health",
            return_value={"healthy": False, "error": "connection refused", "checked_at": "2026-05-04T20:05:00+00:00"},
        ), mock.patch.object(
            ollama_monitor,
            "check_vram",
            return_value={
                "available": True,
                "over_threshold": True,
                "gpus": [{"index": 0, "used_mb": 9500, "total_mb": 10000, "usage_ratio": 0.95, "usage_percent": 95.0}],
                "checked_at": "2026-05-04T20:05:00+00:00",
                "error": None,
            },
        ), mock.patch.object(
            ollama_monitor,
            "utc_now",
            return_value=ollama_monitor.parse_timestamp("2026-05-04T20:05:00+00:00"),
        ):
            status = ollama_monitor.gather_status(self.root)

        self.assertFalse(status["healthy"])
        self.assertEqual(status["stderr_lines_synced"], 1)
        log_payload = self.read_json("logs/ollama_20260504.json")
        events = log_payload["events"]
        self.assertEqual(events[0]["event"], "stderr")
        self.assertEqual(events[1]["event"], "vram_threshold_exceeded")
        self.assertEqual(log_payload["summary"]["last_error"], "connection refused")

    def test_restart_starts_ollama_when_unhealthy(self) -> None:
        fake_process = FakeProcess(pid=2468, polls=[None, None])
        health_results = [
            {"healthy": False, "error": "connection refused", "checked_at": "2026-05-04T20:10:00+00:00"},
            {"healthy": False, "error": "connection refused", "checked_at": "2026-05-04T20:10:00+00:00"},
            {"healthy": True, "error": None, "checked_at": "2026-05-04T20:10:01+00:00", "model_count": 1},
        ]
        sleep_calls: list[float] = []

        def fake_health() -> dict:
            return health_results.pop(0)

        def fake_sleep(seconds: float) -> None:
            sleep_calls.append(seconds)

        with mock.patch.object(
            ollama_monitor,
            "utc_now",
            return_value=ollama_monitor.parse_timestamp("2026-05-04T20:10:00+00:00"),
        ):
            result = ollama_monitor.restart_ollama(
                self.root,
                now=ollama_monitor.parse_timestamp("2026-05-04T20:10:00+00:00"),
                popen=lambda *args, **kwargs: fake_process,
                health_func=fake_health,
                sleep_func=fake_sleep,
            )

        self.assertTrue(result["restarted"])
        self.assertEqual(result["state"]["managed_pid"], 2468)
        self.assertEqual(result["state"]["restarts_count"], 1)
        self.assertEqual(sleep_calls, [1])
        log_payload = self.read_json("logs/ollama_20260504.json")
        self.assertEqual(log_payload["events"][0]["event"], "ollama_restart")

    def test_cli_status_command_prints_json(self) -> None:
        stdout = io.StringIO()
        with mock.patch.object(
            ollama_monitor,
            "gather_status",
            return_value={"healthy": True, "managed_pid": None, "health": {"healthy": True}, "vram": {"available": False}},
        ):
            code = ollama_monitor.main(["status"], root=self.root, stdout=stdout)

        self.assertEqual(code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertTrue(payload["healthy"])

    def test_cli_logs_command_prints_latest_log(self) -> None:
        latest_path = self.root / "logs" / "ollama_20260504.json"
        latest_path.write_text(json.dumps({"events": [{"event": "monitor_started"}]}, indent=2), encoding="utf-8")

        stdout = io.StringIO()
        with mock.patch.object(
            ollama_monitor,
            "utc_now",
            return_value=ollama_monitor.parse_timestamp("2026-05-04T20:15:00+00:00"),
        ):
            code = ollama_monitor.main(["logs"], root=self.root, stdout=stdout)

        self.assertEqual(code, 0)
        self.assertIn("monitor_started", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
