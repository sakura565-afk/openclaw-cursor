from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import ollama_benchmark  # noqa: E402


class OllamaBenchmarkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.log_dir = self.root / "logs"

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def read_json(self, relative_path: str) -> dict:
        return json.loads((self.root / relative_path).read_text(encoding="utf-8"))

    def test_score_prompt_output_prefers_exact_answers(self) -> None:
        prompt = ollama_benchmark.BenchmarkPrompt(
            name="capital",
            prompt="What is the capital of France?",
            expected_answer="Paris",
            keywords=("paris",),
        )

        exact = ollama_benchmark.score_prompt_output("Paris", prompt)
        partial = ollama_benchmark.score_prompt_output("The answer is Paris.", prompt)
        wrong = ollama_benchmark.score_prompt_output("London", prompt)

        self.assertGreater(exact, partial)
        self.assertGreater(partial, wrong)
        self.assertGreaterEqual(exact, 95.0)

    def test_run_benchmarks_saves_json_and_comparison(self) -> None:
        first_run = datetime(2026, 5, 4, 20, 0, 0)
        second_run = datetime(2026, 5, 4, 21, 0, 0)
        models = ["tiny-a", "tiny-b"]

        benchmark_results = {
            "tiny-a": {
                "model_name": "tiny-a",
                "tokens_per_sec": 10.0,
                "vram_mb": 120,
                "quality_score": 70.0,
                "memory_mb": 256,
                "prompt_results": [],
            },
            "tiny-b": {
                "model_name": "tiny-b",
                "tokens_per_sec": 8.0,
                "vram_mb": 80,
                "quality_score": 65.0,
                "memory_mb": 192,
                "prompt_results": [],
            },
            "tiny-a-fast": {
                "model_name": "tiny-a",
                "tokens_per_sec": 12.5,
                "vram_mb": 130,
                "quality_score": 82.0,
                "memory_mb": 260,
                "prompt_results": [],
            },
            "tiny-b-fast": {
                "model_name": "tiny-b",
                "tokens_per_sec": 9.5,
                "vram_mb": 82,
                "quality_score": 68.0,
                "memory_mb": 195,
                "prompt_results": [],
            },
        }

        with mock.patch.object(ollama_benchmark, "datetime") as mock_datetime, mock.patch.object(
            ollama_benchmark, "benchmark_model"
        ) as mock_benchmark_model:
            mock_datetime.now.return_value = first_run
            mock_datetime.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)
            mock_benchmark_model.side_effect = lambda model_name: benchmark_results[model_name]

            first_payload = ollama_benchmark.run_benchmarks(
                root=self.root,
                log_dir=self.log_dir,
                models=models,
            )

            self.assertNotIn("comparison_to_previous", first_payload)

            mock_datetime.now.return_value = second_run
            mock_benchmark_model.side_effect = lambda model_name: benchmark_results[f"{model_name}-fast"]

            second_payload = ollama_benchmark.run_benchmarks(
                root=self.root,
                log_dir=self.log_dir,
                models=models,
            )

        saved = self.read_json("logs/benchmark_20260504.json")
        self.assertEqual(saved["date"], "2026-05-04")
        self.assertEqual(len(saved["runs"]), 2)
        self.assertEqual(saved["updated_at"], "2026-05-04T21:00:00")

        comparison = second_payload["comparison_to_previous"]["rows"]
        self.assertEqual(comparison[0]["model_name"], "tiny-a")
        self.assertEqual(comparison[0]["tokens_delta"], "+2.50")
        self.assertEqual(comparison[0]["quality_delta"], "+12.00")
        self.assertIn("| model_name | tokens_per_sec | vram_mb | quality_score | memory_mb |", second_payload["report_markdown"])

    def test_compare_and_history_helpers_use_saved_runs(self) -> None:
        payload = {
            "date": "2026-05-04",
            "updated_at": "2026-05-04T21:00:00",
            "runs": [
                {
                    "run_at": "2026-05-04T20:00:00",
                    "results": [
                        {
                            "model_name": "tiny-a",
                            "tokens_per_sec": 10.0,
                            "quality_score": 70.0,
                            "vram_mb": 100,
                            "memory_mb": 200,
                        }
                    ],
                },
                {
                    "run_at": "2026-05-04T21:00:00",
                    "results": [
                        {
                            "model_name": "tiny-a",
                            "tokens_per_sec": 12.5,
                            "quality_score": 80.0,
                            "vram_mb": 110,
                            "memory_mb": 210,
                        }
                    ],
                },
            ],
        }
        self.log_dir.mkdir(parents=True, exist_ok=True)
        (self.log_dir / "benchmark_20260504.json").write_text(
            json.dumps(payload, indent=2) + "\n",
            encoding="utf-8",
        )

        runs = ollama_benchmark.load_history(self.log_dir)
        self.assertEqual(len(runs), 2)
        current, previous = ollama_benchmark.latest_two_runs(self.log_dir)
        self.assertEqual(current["run_at"], "2026-05-04T21:00:00")
        self.assertEqual(previous["run_at"], "2026-05-04T20:00:00")

        by_id = ollama_benchmark.resolve_run_identifier("20260504:0", self.log_dir)
        self.assertEqual(by_id["run_at"], "2026-05-04T20:00:00")

        rows = ollama_benchmark.compare_results(current, previous)
        self.assertEqual(rows[0]["tokens_delta"], "+2.50")
        self.assertEqual(rows[0]["quality_delta"], "+10.00")

        history_rows = ollama_benchmark.build_history_rows(runs)
        self.assertEqual(history_rows[0]["run_id"], "20260504:0")
        self.assertEqual(history_rows[1]["avg_tokens_per_sec"], "12.50")

    def test_cli_run_compare_and_history_with_fake_binaries(self) -> None:
        bin_dir = self.root / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)

        ollama_script = textwrap.dedent(
            """\
            #!/usr/bin/env python3
            import sys

            if sys.argv[1] == "list":
                print("NAME ID SIZE MODIFIED")
                print("demo-model abc 1GB now")
            elif sys.argv[1] == "run":
                prompt = sys.argv[3]
                responses = {
                    "Answer with one word only: What is the capital of France?": "Paris",
                    "Answer with digits only: What is 12 * 8?": "96",
                    "Return only the numbers in ascending order: 9, 1, 4, 1, 5": "1, 1, 4, 5, 9",
                    'Translate "hello" to Spanish. Answer with one word only.': "hola",
                    'Answer with one word only: What is the opposite of "cold"?': "hot",
                }
                print(responses.get(prompt, "unknown"))
            else:
                sys.exit(1)
            """
        )
        nvidia_script = textwrap.dedent(
            """\
            #!/usr/bin/env python3
            print("100")
            """
        )

        for name, script in {"ollama": ollama_script, "nvidia-smi": nvidia_script}.items():
            path = bin_dir / name
            path.write_text(script, encoding="utf-8")
            path.chmod(0o755)

        env = os.environ.copy()
        env["PATH"] = f"{bin_dir}:{env['PATH']}"
        env["PYTHONPATH"] = str(ROOT)

        run_result = subprocess.run(
            [sys.executable, "-m", "scripts.ollama_benchmark", "--log-dir", "logs", "run"],
            cwd=self.root,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(run_result.returncode, 0, msg=run_result.stderr)
        self.assertIn("| model_name | tokens_per_sec | vram_mb | quality_score | memory_mb |", run_result.stdout)
        self.assertTrue((self.root / "logs" / "benchmark_").parent.exists())
        benchmark_logs = list((self.root / "logs").glob("benchmark_*.json"))
        self.assertEqual(len(benchmark_logs), 1)

        compare_result = subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.ollama_benchmark",
                "--log-dir",
                "logs",
                "compare",
                "--current",
                str(benchmark_logs[0]),
                "--previous",
                str(benchmark_logs[0]),
            ],
            cwd=self.root,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(compare_result.returncode, 0, msg=compare_result.stderr)
        self.assertIn("Current run:", compare_result.stdout)
        self.assertIn("| model_name | tokens_per_sec | tokens_delta | quality_score | quality_delta | vram_mb | memory_mb |", compare_result.stdout)

        history_result = subprocess.run(
            [sys.executable, "-m", "scripts.ollama_benchmark", "--log-dir", "logs", "history"],
            cwd=self.root,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(history_result.returncode, 0, msg=history_result.stderr)
        self.assertIn("| run_id | run_at | models | avg_tokens_per_sec | avg_quality_score |", history_result.stdout)


if __name__ == "__main__":
    unittest.main()
