from __future__ import annotations

import io
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import ollama_model_manager as manager  # noqa: E402


LIST_OUTPUT = """NAME                    ID              SIZE      MODIFIED
llama3.2:latest         8934d96d3f08    4.7 GB    2 hours ago
mixtral:latest          1234567890ab    26 GB     45 days ago
"""


class OllamaModelManagerTests(unittest.TestCase):
    def test_parse_model_list_extracts_name_size_and_age(self) -> None:
        models = manager.parse_model_list(LIST_OUTPUT, now=datetime(2026, 5, 4, 12, 0, 0))

        self.assertEqual(len(models), 2)
        self.assertEqual(models[0].name, "llama3.2:latest")
        self.assertEqual(models[0].size, "4.7 GB")
        self.assertEqual(models[0].modified, "2 hours ago")
        self.assertAlmostEqual(models[0].age_days or 0.0, 2 / 24, places=4)
        self.assertEqual(models[1].name, "mixtral:latest")
        self.assertGreater(models[1].age_days or 0.0, 30)

    def test_parse_pull_progress_from_json_and_compute_speed_eta(self) -> None:
        first = manager.parse_pull_progress(
            '{"status":"pulling 8daa9615cce3","digest":"sha256:8daa9615cce3","total":104857600,"completed":10485760}'
        )
        second = manager.parse_pull_progress(
            '{"status":"pulling 8daa9615cce3","digest":"sha256:8daa9615cce3","total":104857600,"completed":31457280}'
        )

        assert first is not None
        assert second is not None

        state: dict[str, dict[str, float]] = {}
        first = manager.update_pull_metrics(first, state, now=10.0)
        second = manager.update_pull_metrics(second, state, now=12.0)

        self.assertAlmostEqual(first.percent or 0.0, 10.0)
        self.assertAlmostEqual(second.percent or 0.0, 30.0)
        self.assertIsNotNone(second.speed_bps)
        self.assertAlmostEqual(second.speed_bps or 0.0, 10 * 1024 * 1024, places=3)
        self.assertIsNotNone(second.eta_seconds)
        self.assertAlmostEqual(second.eta_seconds or 0.0, 7.0, places=3)

    def test_get_disk_space_warns_below_ten_gib(self) -> None:
        fake_usage = (100 * 1024**3, 92 * 1024**3, 8 * 1024**3)
        with mock.patch("scripts.ollama_model_manager.shutil.disk_usage", return_value=fake_usage):
            table, rows, warning = manager.get_disk_space(Path("/workspace"))

        self.assertTrue(warning)
        self.assertIn("8.0 GB", table)
        self.assertEqual(rows[2][0], "Free")

    def test_cleanup_suggestions_flags_models_older_than_30_days(self) -> None:
        completed = subprocess.CompletedProcess(
            args=["ollama", "list"],
            returncode=0,
            stdout=LIST_OUTPUT,
            stderr="",
        )
        with mock.patch("scripts.ollama_model_manager.run_ollama_command", return_value=completed):
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                exit_code = manager.cleanup_suggestions(now=datetime(2026, 5, 4, 12, 0, 0))

        output = buffer.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertIn("mixtral:latest", output)
        self.assertIn("Review or remove", output)
        self.assertNotIn("llama3.2:latest", output)

    def test_remove_model_prompts_for_confirmation(self) -> None:
        completed = subprocess.CompletedProcess(
            args=["ollama", "rm", "llama3.2"],
            returncode=0,
            stdout="deleted 'llama3.2'",
            stderr="",
        )
        with (
            mock.patch("builtins.input", return_value="yes"),
            mock.patch("scripts.ollama_model_manager.run_ollama_command", return_value=completed) as run_mock,
        ):
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                exit_code = manager.remove_model("llama3.2")

        self.assertEqual(exit_code, 0)
        run_mock.assert_called_once_with(["rm", "llama3.2"])
        self.assertIn("deleted 'llama3.2'", buffer.getvalue())

    def test_pull_model_renders_progress_and_success_message(self) -> None:
        class FakeProcess:
            def __init__(self) -> None:
                self.stdout = io.StringIO(
                    '{"status":"pulling manifest"}\n'
                    '{"status":"pulling 8daa9615cce3","digest":"sha256:8daa9615cce3","total":104857600,"completed":10485760}\n'
                    '{"status":"pulling 8daa9615cce3","digest":"sha256:8daa9615cce3","total":104857600,"completed":31457280}\n'
                    '{"status":"success"}\n'
                )

            def wait(self) -> int:
                return 0

        with (
            mock.patch("scripts.ollama_model_manager.ensure_ollama_available"),
            mock.patch("scripts.ollama_model_manager.get_disk_space", return_value=("disk-table", [], False)),
            mock.patch("scripts.ollama_model_manager.subprocess.Popen", return_value=FakeProcess()),
            mock.patch("scripts.ollama_model_manager.time.monotonic", side_effect=[10.0, 12.0, 12.5, 13.0]),
        ):
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                exit_code = manager.pull_model("llama3.2")

        output = buffer.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertIn("Disk space check", output)
        self.assertIn("10.0%", output)
        self.assertIn("30.0%", output)
        self.assertIn("10.0 MB/s", output)
        self.assertIn("1m 10s", output)
        self.assertIn("pulled successfully", output)

    def test_main_returns_error_for_unsupported_search(self) -> None:
        with mock.patch(
            "scripts.ollama_model_manager.run_ollama_command",
            side_effect=manager.OllamaManagerError("This Ollama version does not support 'ollama search'."),
        ):
            stdout_buffer = io.StringIO()
            stderr_buffer = io.StringIO()
            with redirect_stdout(stdout_buffer), mock.patch("sys.stderr", stderr_buffer):
                exit_code = manager.main(["search", "llama"])

        self.assertEqual(exit_code, 1)
        self.assertIn("does not support 'ollama search'", stderr_buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
