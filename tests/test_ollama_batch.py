from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from io import StringIO
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import ollama_batch  # noqa: E402


class OllamaBatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def write(self, relative_path: str, content: str) -> Path:
        path = self.root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def make_fake_ollama(self) -> Path:
        script = self.write(
            "bin/ollama",
            textwrap.dedent(
                """\
                #!/usr/bin/env python3
                import json
                import os
                import sys
                import time
                from pathlib import Path

                state_path = Path(os.environ["FAKE_OLLAMA_STATE"])
                prompt = sys.argv[3]
                state = {}
                if state_path.exists():
                    state = json.loads(state_path.read_text(encoding="utf-8"))

                if prompt == "slow prompt":
                    time.sleep(0.4)
                    print("slow response")
                    sys.exit(0)

                if prompt == "retry prompt":
                    attempts = state.get(prompt, 0) + 1
                    state[prompt] = attempts
                    state_path.write_text(json.dumps(state), encoding="utf-8")
                    if attempts < 3:
                        print("temporary failure", file=sys.stderr)
                        sys.exit(1)
                    print("retry success")
                    sys.exit(0)

                if prompt == "always fail":
                    attempts = state.get(prompt, 0) + 1
                    state[prompt] = attempts
                    state_path.write_text(json.dumps(state), encoding="utf-8")
                    print("permanent failure", file=sys.stderr)
                    sys.exit(1)

                print(f"echo:{prompt}")
                sys.exit(0)
                """
            ),
        )
        script.chmod(0o755)
        return script

    def base_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT)
        env["FAKE_OLLAMA_STATE"] = str(self.root / "ollama_state.json")
        env["PATH"] = f"{self.root / 'bin'}{os.pathsep}{env.get('PATH', '')}"
        return env

    def test_load_prompts_supports_text_and_json(self) -> None:
        text_file = self.write("prompts.txt", "first\n\nsecond\n")
        json_file = self.write("prompts.json", '["alpha", "beta"]\n')

        self.assertEqual(ollama_batch.load_prompts(text_file), ["first", "second"])
        self.assertEqual(ollama_batch.load_prompts(json_file), ["alpha", "beta"])

    def test_run_prompt_retries_with_exponential_backoff(self) -> None:
        calls: list[int] = []
        sleeps: list[float] = []
        times = iter([10.0, 10.2, 10.5, 11.0])

        def fake_run_command(*args, **kwargs):  # type: ignore[no-untyped-def]
            calls.append(1)
            if len(calls) < 3:
                return subprocess.CompletedProcess(args[0], 1, "", "try again")
            return subprocess.CompletedProcess(args[0], 0, "completed", "")

        result = ollama_batch.run_prompt(
            "hello",
            model="llama3",
            timeout=30.0,
            max_retries=3,
            backoff_base=1.5,
            run_command=fake_run_command,
            sleep_fn=sleeps.append,
            time_fn=lambda: next(times),
        )

        self.assertTrue(result.success)
        self.assertEqual(result.response, "completed")
        self.assertEqual(result.attempts, 3)
        self.assertEqual(sleeps, [1.5, 3.0])
        self.assertAlmostEqual(result.latency_seconds, 0.2)

    def test_run_batch_preserves_input_order_and_prints_progress(self) -> None:
        progress = StringIO()
        current_time = {"value": 100.0}

        def fake_time() -> float:
            current_time["value"] += 1.0
            return current_time["value"]

        def fake_runner(prompt: str) -> ollama_batch.PromptResult:
            if prompt == "second":
                return ollama_batch.PromptResult(
                    prompt=prompt,
                    response="bad",
                    latency_seconds=0.2,
                    success=False,
                    status="failed",
                    attempts=1,
                    error="boom",
                )
            return ollama_batch.PromptResult(
                prompt=prompt,
                response=prompt.upper(),
                latency_seconds=0.1,
                success=True,
                status="success",
                attempts=1,
            )

        results = ollama_batch.run_batch(
            ["first", "second", "third"],
            model="llama3",
            parallel=2,
            timeout=30.0,
            max_retries=1,
            backoff_base=1.0,
            progress_stream=progress,
            time_fn=fake_time,
            runner=fake_runner,
        )

        self.assertEqual([item.prompt for item in results], ["first", "second", "third"])
        self.assertEqual([item.success for item in results], [True, False, True])
        progress_lines = [line for line in progress.getvalue().splitlines() if line.strip()]
        self.assertEqual(len(progress_lines), 3)
        self.assertTrue(all("eta=" in line for line in progress_lines))
        self.assertTrue(progress_lines[-1].startswith("[3/3] completed"))

    def test_cli_runs_batch_and_writes_json_output(self) -> None:
        self.make_fake_ollama()
        prompts = self.write(
            "sample_prompts.txt",
            "first prompt\nretry prompt\nalways fail\n",
        )
        output_path = self.root / "results" / "batch.json"

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.ollama_batch",
                "run",
                "--file",
                str(prompts),
                "--model",
                "llama3",
                "--parallel",
                "2",
                "--output",
                str(output_path),
                "--timeout",
                "1",
            ],
            cwd=ROOT,
            env=self.base_env(),
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("Completed 3 prompts", result.stdout)
        self.assertIn("[3/3] completed", result.stderr)

        payload = json.loads(output_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["model"], "llama3")
        self.assertEqual(payload["parallel"], 2)
        self.assertEqual(payload["successes"], 2)
        self.assertEqual(payload["failures"], 1)
        self.assertEqual([item["prompt"] for item in payload["results"]], ["first prompt", "retry prompt", "always fail"])

        first, retry, failed = payload["results"]
        self.assertEqual(first["response"], "echo:first prompt")
        self.assertTrue(retry["success"])
        self.assertEqual(retry["attempts"], 3)
        self.assertFalse(failed["success"])
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["attempts"], 4)
        self.assertEqual(failed["error"], "permanent failure")

    def test_cli_accepts_json_input_file(self) -> None:
        self.make_fake_ollama()
        prompts = self.write("prompts.json", '["alpha", "beta"]\n')
        output_path = self.root / "json_results.json"

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.ollama_batch",
                "run",
                "--file",
                str(prompts),
                "--model",
                "llama3",
                "--output",
                str(output_path),
            ],
            cwd=ROOT,
            env=self.base_env(),
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(output_path.read_text(encoding="utf-8"))
        self.assertEqual([item["response"] for item in payload["results"]], ["echo:alpha", "echo:beta"])


if __name__ == "__main__":
    unittest.main()
