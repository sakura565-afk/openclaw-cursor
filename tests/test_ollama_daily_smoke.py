from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import ollama_chain, ollama_daily_smoke as smoke  # noqa: E402


class OllamaChainTests(unittest.TestCase):
    def test_default_chain_has_nine_models(self) -> None:
        self.assertEqual(len(ollama_chain.DEFAULT_CHAIN), 9)
        self.assertEqual(ollama_chain.DEFAULT_CHAIN[0], "minimax-m3:cloud")
        self.assertEqual(ollama_chain.DEFAULT_CHAIN[-1], "grok-4.5")


class OllamaDailySmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_probe_success_records_metrics(self) -> None:
        def runner(model: str, prompt: str, timeout: float | None = None) -> tuple[int, str, str]:
            self.assertEqual(prompt, smoke.SMOKE_PROMPT)
            return 0, "4\n", "eval count: 2\neval duration: 1.0s\n"

        times = iter([100.0, 102.5])
        result = smoke.probe_model(
            "gpt-oss:20b",
            runner=runner,
            time_fn=lambda: next(times),
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["eval_count"], 2)
        self.assertEqual(result["latency_s"], 2.5)
        self.assertAlmostEqual(result["tokens_per_second"], 2.0)

    def test_probe_timeout_marks_fail(self) -> None:
        def runner(model: str, prompt: str, timeout: float | None = None) -> tuple[int, str, str]:
            raise subprocess.TimeoutExpired(cmd=["ollama", "run", model], timeout=timeout or 15)

        times = iter([1.0, 16.0])
        result = smoke.probe_model("devstral:24b", runner=runner, time_fn=lambda: next(times))
        self.assertFalse(result["ok"])
        self.assertIn("timeout", result["error"])

    def test_skip_xai_without_key(self) -> None:
        result = smoke.probe_model("grok-4.5", env={})
        self.assertTrue(result["ok"])
        self.assertTrue(result["skipped"])
        self.assertIn("XAI_API_KEY", result["error"])

    def test_cleanup_old_health_files(self) -> None:
        data = self.root / "data"
        data.mkdir(parents=True)
        (data / "ollama_health_2020-01-01.json").write_text("{}", encoding="utf-8")
        (data / "ollama_health_2099-01-01.json").write_text("{}", encoding="utf-8")
        deleted = smoke.cleanup_old_health_files(self.root, retention_days=30)
        self.assertEqual(deleted, ["ollama_health_2020-01-01.json"])
        self.assertFalse((data / "ollama_health_2020-01-01.json").exists())
        self.assertTrue((data / "ollama_health_2099-01-01.json").exists())

    def test_run_smoke_writes_report_and_skips_grok_alert(self) -> None:
        responses = {
            "minimax-m3:cloud": (0, "4", ""),
            "qwen3-coder-next:cloud": (0, "4", ""),
            "devstral-small-2:24b:cloud": (0, "4", ""),
            "gpt-oss:20b:cloud": (0, "4", ""),
            "gpt-oss:20b": (0, "4", ""),
            "devstral:24b": (0, "4", ""),
            "nemotron-3-nano:4b": (0, "4", ""),
            "lfm2.5-thinking:latest": (0, "4", ""),
            "grok-4.5": (1, "", "HTTP 403 Forbidden"),
        }

        def runner(model: str, prompt: str, timeout: float | None = None) -> tuple[int, str, str]:
            return responses[model]

        alert = mock.Mock(return_value={"ok": True})
        with mock.patch.object(smoke, "send_telegram_alert", alert):
            report = smoke.run_smoke(
                root=self.root,
                runner=runner,
                send_alerts=True,
                env={"XAI_API_KEY": "test-key"},
            )

        self.assertEqual(report["model_count"], 9)
        self.assertEqual(report["fail_count"], 1)
        self.assertEqual(report["alert_failure_count"], 0)
        alert.assert_not_called()
        out = Path(report["output_path"])
        self.assertTrue(out.exists())
        payload = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(len(payload["results"]), 9)

    def test_alert_on_non_exempt_failure(self) -> None:
        def runner(model: str, prompt: str, timeout: float | None = None) -> tuple[int, str, str]:
            if model == "gpt-oss:20b":
                raise subprocess.TimeoutExpired(cmd=["ollama"], timeout=15)
            if model == "grok-4.5":
                return 1, "", "403"
            return 0, "4", ""

        captured: list[str] = []

        def fake_alert(message: str, **kwargs: object) -> dict:
            captured.append(message)
            return {"ok": True}

        with mock.patch.object(smoke, "send_telegram_alert", side_effect=fake_alert):
            report = smoke.run_smoke(
                root=self.root,
                runner=runner,
                send_alerts=True,
                env={"XAI_API_KEY": "x"},
            )

        self.assertEqual(report["alert_failure_count"], 1)
        self.assertEqual(len(captured), 1)
        self.assertIn("gpt-oss:20b", captured[0])
        self.assertNotIn("403", captured[0])


if __name__ == "__main__":
    unittest.main()
