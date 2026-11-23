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

# Контракт цепочки: только модели, проверенные живым прогоном ``ollama run``
# на хосте 21.10.2026 (ollama 0.40.7). Меняется вместе с DEFAULT_CHAIN и
# только после новой проверки — поэтому список зафиксирован здесь, а не
# импортирован из модуля.
VERIFIED_MODELS = frozenset(
    {
        "gpt-oss:20b:cloud",
        "gemma4:31b-cloud",
        "nemotron-3-nano:30b-cloud",
        "lfm2.5-thinking:latest",
        "gemma4:e4b",
        "deepcoder:1.5b",
    }
)

# Мёртвые модели: 402 Payment Required, retired 15.07.2026 либо вовсе не
# существуют в Ollama (grok-4.5 — это xAI по HTTP с отдельным ключом).
RETIRED_OR_PAID_MODELS = frozenset(
    {
        "minimax-m3:cloud",
        "qwen3-coder-next:cloud",
        "devstral-small-2:24b:cloud",
        "gpt-oss:20b",
        "devstral:24b",
        "nemotron-3-nano:4b",
        "grok-4.5",
    }
)


class OllamaChainTests(unittest.TestCase):
    def test_default_chain_contains_only_verified_models(self) -> None:
        self.assertTrue(ollama_chain.DEFAULT_CHAIN)
        self.assertEqual(set(ollama_chain.DEFAULT_CHAIN), VERIFIED_MODELS)
        self.assertEqual(
            len(set(ollama_chain.DEFAULT_CHAIN)),
            len(ollama_chain.DEFAULT_CHAIN),
            "DEFAULT_CHAIN must not repeat models",
        )

    def test_default_chain_has_no_paid_or_retired_models(self) -> None:
        for model in ollama_chain.DEFAULT_CHAIN:
            self.assertNotIn(model, RETIRED_OR_PAID_MODELS)
            self.assertFalse(
                ollama_chain.is_xai_model(model),
                f"{model} needs XAI_API_KEY and cannot be part of the default chain",
            )

    def test_default_chain_starts_with_cloud_then_local(self) -> None:
        flags = [ollama_chain.is_cloud_model(model) for model in ollama_chain.DEFAULT_CHAIN]
        self.assertTrue(flags[0], "the chain must start with a cloud model")
        self.assertFalse(flags[-1], "the chain must end with a local fallback")
        self.assertEqual(flags, sorted(flags, reverse=True), "cloud models must come first")

    def test_is_cloud_model_distinguishes_cloud_and_local(self) -> None:
        self.assertTrue(ollama_chain.is_cloud_model("gpt-oss:20b:cloud"))
        self.assertTrue(ollama_chain.is_cloud_model("gemma4:31b-cloud"))
        self.assertFalse(ollama_chain.is_cloud_model("gemma4:e4b"))
        self.assertFalse(ollama_chain.is_cloud_model("lfm2.5-thinking:latest"))
        self.assertFalse(ollama_chain.is_cloud_model(""))

    def test_model_timeout_is_larger_for_local_models(self) -> None:
        cloud = ollama_chain.model_timeout("gpt-oss:20b:cloud")
        local = ollama_chain.model_timeout("gemma4:e4b")
        self.assertGreater(local, cloud)
        self.assertLessEqual(cloud, 25.0)
        self.assertGreaterEqual(local, 60.0)
        # Неизвестная модель получает общий дефолт, а не жёсткие 15 c.
        self.assertEqual(ollama_chain.model_timeout("who-knows:1b"), 30.0)

    def test_chain_models_passthrough(self) -> None:
        self.assertEqual(ollama_chain.chain_models(), ollama_chain.DEFAULT_CHAIN)
        self.assertEqual(ollama_chain.chain_models(["a", "b"]), ("a", "b"))

    def test_run_model_returns_triple(self) -> None:
        captured: dict[str, object] = {}

        class FakePopen:
            returncode = 0

            def __init__(self, cmd, **kwargs):  # type: ignore[no-untyped-def]
                captured["cmd"] = cmd
                captured["kwargs"] = kwargs

            def communicate(self, input=None, timeout=None):  # type: ignore[no-untyped-def]
                captured["input"] = input
                captured["timeout"] = timeout
                return "4\n", "eval count: 2\n"

        result = ollama_chain.run_model("gemma4:e4b", "2+2?", popen=FakePopen)

        self.assertEqual(result, (0, "4\n", "eval count: 2\n"))
        self.assertEqual(captured["cmd"], ["ollama", "run", "gemma4:e4b"])
        self.assertEqual(captured["input"], "2+2?\n")
        # timeout=None -> персональный таймаут модели.
        self.assertEqual(captured["timeout"], ollama_chain.model_timeout("gemma4:e4b"))

    def test_run_model_raises_timeout_expired(self) -> None:
        killed: list[str] = []

        class FakePopen:
            returncode = None

            def __init__(self, cmd, **kwargs):  # type: ignore[no-untyped-def]
                self.cmd = cmd

            def communicate(self, input=None, timeout=None):  # type: ignore[no-untyped-def]
                if killed:
                    return "", ""
                raise subprocess.TimeoutExpired(cmd=self.cmd, timeout=timeout or 0)

            def kill(self) -> None:
                killed.append("kill")

        with self.assertRaises(subprocess.TimeoutExpired):
            ollama_chain.run_model("gemma4:e4b", "2+2?", timeout=1.0, popen=FakePopen)
        self.assertEqual(killed, ["kill"])

    def test_is_model_installed_handles_cloud_and_unknown_list(self) -> None:
        installed = ("gemma4:e4b", "deepcoder:1.5b")
        self.assertTrue(ollama_chain.is_model_installed("gemma4:e4b", installed))
        self.assertFalse(ollama_chain.is_model_installed("devstral:24b", installed))
        # Облачные модели Ollama тянет по требованию: в списке их может не быть.
        self.assertTrue(ollama_chain.is_model_installed("gpt-oss:20b:cloud", installed))
        # Список неизвестен — проверяем живым прогоном, а не отказом.
        self.assertTrue(ollama_chain.is_model_installed("devstral:24b", None))

    def test_list_installed_models_parses_header(self) -> None:
        stdout = "NAME                ID      SIZE\ngemma4:e4b          abc     9.6 GB\ndeepcoder:1.5b      def     1.1 GB\n"

        def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
            self.assertEqual(cmd, ["ollama", "list"])
            return subprocess.CompletedProcess(cmd, 0, stdout, "")

        self.assertEqual(
            ollama_chain.list_installed_models(run_command=fake_run),
            ("gemma4:e4b", "deepcoder:1.5b"),
        )

    def test_provider_unavailable_reason_detects_paid_and_retired(self) -> None:
        self.assertIsNotNone(
            ollama_chain.provider_unavailable_reason(
                "Error: 402 this model is not included in your free usage"
            )
        )
        self.assertIsNotNone(
            ollama_chain.provider_unavailable_reason("model was retired on July 15, 2026")
        )
        self.assertIsNone(ollama_chain.provider_unavailable_reason("eval count: 2"))

    def test_call_chain_falls_back_to_the_next_model(self) -> None:
        seen: list[str] = []

        def fake_ollama(model, prompt, *, timeout=None, **kwargs):  # type: ignore[no-untyped-def]
            seen.append(model)
            if model == ollama_chain.DEFAULT_CHAIN[0]:
                return {"model": model, "ok": False, "status": "error", "text": "", "error": "boom"}
            return {"model": model, "ok": True, "status": "ok", "text": "4", "error": None}

        result = ollama_chain.call_chain("2+2?", ollama_caller=fake_ollama)

        self.assertTrue(result["ok"])
        self.assertEqual(result["model"], ollama_chain.DEFAULT_CHAIN[1])
        self.assertEqual(result["text"], "4")
        self.assertEqual(seen, list(ollama_chain.DEFAULT_CHAIN[:2]))

    def test_call_chain_skips_models_without_xai_key(self) -> None:
        result = ollama_chain.call_chain(
            "2+2?",
            ["grok-4.5"],
            env={},
            ollama_caller=lambda *a, **k: self.fail("xAI model must not go through ollama"),
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["attempts"][0]["status"], ollama_chain.STATUS_SKIPPED)
        self.assertIn("XAI_API_KEY", result["attempts"][0]["error"])

    def test_call_chain_reports_missing_local_model(self) -> None:
        result = ollama_chain.call_chain(
            "2+2?",
            ["devstral:24b"],
            installed=["gemma4:e4b"],
            ollama_caller=lambda *a, **k: self.fail("a missing model must not be run"),
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["attempts"][0]["status"], ollama_chain.STATUS_NOT_INSTALLED)


class LoadChainYamlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_missing_config_returns_module_defaults(self) -> None:
        config = ollama_chain.load_chain_yaml(self.root / "nope.yaml")
        self.assertEqual(config["chain"], ollama_chain.DEFAULT_CHAIN)
        self.assertIsNone(config["source"])

    def test_yaml_config_overrides_chain_and_timeouts(self) -> None:
        path = self.root / "ollama_chain.yaml"
        path.write_text(
            "\n".join(
                [
                    "# цепочка для теста",
                    "chain:",
                    "  - gpt-oss:20b:cloud",
                    "  - gemma4:e4b",
                    "timeouts:",
                    "  gemma4:e4b: 77",
                    "prompt: 2+2?",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        config = ollama_chain.load_chain_yaml(path)

        self.assertEqual(config["chain"], ("gpt-oss:20b:cloud", "gemma4:e4b"))
        self.assertEqual(config["timeouts"]["gemma4:e4b"], 77.0)
        self.assertEqual(config["prompt"], "2+2?")
        self.assertEqual(config["source"], str(path))

    def test_yaml_config_parses_without_pyyaml(self) -> None:
        path = self.root / "ollama_chain.yaml"
        path.write_text(
            "chain:\n  - gemma4:e4b\ntimeouts:\n  gemma4:e4b: 42\n",
            encoding="utf-8",
        )

        real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __import__

        def no_yaml(name, *args, **kwargs):  # type: ignore[no-untyped-def]
            if name == "yaml":
                raise ImportError("no yaml")
            return real_import(name, *args, **kwargs)

        with mock.patch("builtins.__import__", side_effect=no_yaml):
            config = ollama_chain.load_chain_yaml(path)

        self.assertEqual(config["chain"], ("gemma4:e4b",))
        self.assertEqual(config["timeouts"]["gemma4:e4b"], 42.0)

    def test_json_config_is_supported(self) -> None:
        path = self.root / "ollama_chain.json"
        path.write_text(json.dumps({"chain": ["gemma4:e4b"]}), encoding="utf-8")
        self.assertEqual(ollama_chain.load_chain_yaml(path)["chain"], ("gemma4:e4b",))


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
            "gemma4:e4b",
            runner=runner,
            time_fn=lambda: next(times),
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], ollama_chain.STATUS_OK)
        self.assertEqual(result["eval_count"], 2)
        self.assertEqual(result["latency_s"], 2.5)
        self.assertAlmostEqual(result["tokens_per_second"], 2.0)

    def test_probe_uses_per_model_timeout_by_default(self) -> None:
        seen: list[float | None] = []

        def runner(model: str, prompt: str, timeout: float | None = None) -> tuple[int, str, str]:
            seen.append(timeout)
            return 0, "4", ""

        cloud = smoke.probe_model("gpt-oss:20b:cloud", runner=runner, time_fn=lambda: 0.0)
        local = smoke.probe_model("gemma4:e4b", runner=runner, time_fn=lambda: 0.0)

        self.assertEqual(seen, [25.0, 120.0])
        self.assertTrue(cloud["cloud"])
        self.assertFalse(local["cloud"])

    def test_probe_timeout_marks_fail(self) -> None:
        def runner(model: str, prompt: str, timeout: float | None = None) -> tuple[int, str, str]:
            raise subprocess.TimeoutExpired(cmd=["ollama", "run", model], timeout=timeout or 30)

        times = iter([1.0, 16.0])
        result = smoke.probe_model("gemma4:e4b", runner=runner, time_fn=lambda: next(times))
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], ollama_chain.STATUS_TIMEOUT)
        self.assertIn("timeout", result["error"])

    def test_probe_error_is_not_a_skip(self) -> None:
        def runner(model: str, prompt: str, timeout: float | None = None) -> tuple[int, str, str]:
            return 1, "", "Error: something broke"

        result = smoke.probe_model("gemma4:e4b", runner=runner, time_fn=lambda: 0.0)
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], ollama_chain.STATUS_ERROR)
        self.assertFalse(result["skipped"])

    def test_missing_local_model_is_not_installed_not_error(self) -> None:
        def runner(model: str, prompt: str, timeout: float | None = None) -> tuple[int, str, str]:
            raise AssertionError("a model that is not installed must not be run")

        result = smoke.probe_model(
            "devstral:24b",
            runner=runner,
            installed=["gemma4:e4b", "deepcoder:1.5b"],
        )

        self.assertEqual(result["status"], ollama_chain.STATUS_NOT_INSTALLED)
        self.assertNotEqual(result["status"], ollama_chain.STATUS_ERROR)
        self.assertTrue(result["skipped"])
        self.assertTrue(result["ok"], "a missing local model is a skip, not a failure")
        self.assertIn("ollama pull devstral:24b", result["error"])

    def test_cloud_model_absent_from_list_is_still_probed(self) -> None:
        # gpt-oss:20b:cloud отсутствует в ollama list, но отвечает: облачные
        # модели тянутся по требованию.
        def runner(model: str, prompt: str, timeout: float | None = None) -> tuple[int, str, str]:
            return 0, "4", ""

        result = smoke.probe_model(
            "gpt-oss:20b:cloud",
            runner=runner,
            installed=["gemma4:e4b"],
            time_fn=lambda: 0.0,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], ollama_chain.STATUS_OK)

    def test_paid_or_retired_model_is_skipped_not_failed(self) -> None:
        responses = {
            "minimax-m3:cloud": (
                1,
                "",
                "Error: 402 this model is not included in your free usage, add usage credits",
            ),
            "qwen3-coder-next:cloud": (1, "", "Error: model was retired on July 15, 2026"),
        }

        for model, response in responses.items():
            with self.subTest(model=model):
                result = smoke.probe_model(
                    model,
                    runner=lambda m, p, timeout=None, _r=response: _r,
                    time_fn=lambda: 0.0,
                )
                self.assertEqual(result["status"], ollama_chain.STATUS_SKIPPED)
                self.assertTrue(result["skipped"])
                self.assertTrue(result["ok"])

    def test_skip_xai_without_key(self) -> None:
        result = smoke.probe_model("grok-4.5", env={})
        self.assertTrue(result["ok"])
        self.assertTrue(result["skipped"])
        self.assertEqual(result["status"], ollama_chain.STATUS_SKIPPED)
        self.assertNotEqual(result["status"], ollama_chain.STATUS_ERROR)
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

    def test_run_smoke_writes_report_for_the_whole_chain(self) -> None:
        def runner(model: str, prompt: str, timeout: float | None = None) -> tuple[int, str, str]:
            return 0, "4", ""

        alert = mock.Mock(return_value={"ok": True})
        with mock.patch.object(smoke, "send_telegram_alert", alert):
            report = smoke.run_smoke(
                root=self.root,
                runner=runner,
                list_models=lambda: ollama_chain.DEFAULT_CHAIN,
                send_alerts=True,
            )

        self.assertEqual(report["model_count"], len(ollama_chain.DEFAULT_CHAIN))
        self.assertEqual(report["ok_count"], len(ollama_chain.DEFAULT_CHAIN))
        self.assertEqual(report["fail_count"], 0)
        self.assertEqual(report["alert_failure_count"], 0)
        alert.assert_not_called()

        payload = json.loads(Path(report["output_path"]).read_text(encoding="utf-8"))
        self.assertEqual(len(payload["results"]), len(ollama_chain.DEFAULT_CHAIN))
        self.assertEqual(payload["status_counts"], {ollama_chain.STATUS_OK: report["ok_count"]})

    def test_run_smoke_writes_report_when_everything_is_skipped(self) -> None:
        def runner(model: str, prompt: str, timeout: float | None = None) -> tuple[int, str, str]:
            raise AssertionError("nothing should be probed when every model is skipped")

        alert = mock.Mock(return_value={"ok": True})
        with mock.patch.object(smoke, "send_telegram_alert", alert):
            report = smoke.run_smoke(
                root=self.root,
                models=["devstral:24b", "nemotron-3-nano:4b", "grok-4.5"],
                runner=runner,
                list_models=lambda: [],
                env={},
                send_alerts=True,
            )

        self.assertEqual(report["skipped_count"], 3)
        self.assertEqual(report["fail_count"], 0)
        self.assertEqual(report["alert_failure_count"], 0)
        alert.assert_not_called()

        out = Path(report["output_path"])
        self.assertTrue(out.exists(), "the report must be written even when every probe is skipped")
        payload = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(len(payload["results"]), 3)
        self.assertEqual(
            payload["status_counts"],
            {ollama_chain.STATUS_NOT_INSTALLED: 2, ollama_chain.STATUS_SKIPPED: 1},
        )

    def test_run_smoke_survives_an_unavailable_ollama_list(self) -> None:
        def runner(model: str, prompt: str, timeout: float | None = None) -> tuple[int, str, str]:
            return 0, "4", ""

        def broken_list() -> list[str]:
            raise FileNotFoundError("ollama")

        report = smoke.run_smoke(
            root=self.root,
            models=["gemma4:e4b"],
            runner=runner,
            list_models=broken_list,
            send_alerts=False,
        )

        self.assertIsNone(report["installed_models"])
        self.assertEqual(report["ok_count"], 1)

    def test_alert_on_non_exempt_failure(self) -> None:
        def runner(model: str, prompt: str, timeout: float | None = None) -> tuple[int, str, str]:
            if model == "gemma4:e4b":
                raise subprocess.TimeoutExpired(cmd=["ollama"], timeout=timeout or 30)
            return 0, "4", ""

        captured: list[str] = []

        def fake_alert(message: str, **kwargs: object) -> dict:
            captured.append(message)
            return {"ok": True}

        with mock.patch.object(smoke, "send_telegram_alert", side_effect=fake_alert):
            report = smoke.run_smoke(
                root=self.root,
                models=["gpt-oss:20b:cloud", "gemma4:e4b", "grok-4.5"],
                runner=runner,
                list_models=lambda: ["gemma4:e4b"],
                send_alerts=True,
                env={},
            )

        self.assertEqual(report["alert_failure_count"], 1)
        self.assertEqual(len(captured), 1)
        self.assertIn("gemma4:e4b", captured[0])
        self.assertNotIn("grok-4.5", captured[0])


if __name__ == "__main__":
    unittest.main()
