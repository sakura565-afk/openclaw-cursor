import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import auto_reflection as ar  # noqa: E402


class ExtractInsightsTests(unittest.TestCase):
    def test_prefers_last_json_fence(self) -> None:
        raw = """Here is noise.
        ```json
        {"summary": "wrong"}
        ```
        Final:
        ```json
        {"summary": "right", "themes": ["a"], "risks": [], "recommendations": [], "metrics_to_watch": [], "experiments": [], "open_questions": []}
        ```
        """
        ins = ar.extract_insights_from_response(raw)
        self.assertEqual(ins.summary, "right")
        self.assertEqual(ins.themes, ["a"])

    def test_fallback_bullets(self) -> None:
        raw = """### Themes
- one
- two

### Risks
- r1
"""
        ins = ar.extract_insights_from_response(raw)
        self.assertEqual(ins.themes, ["one", "two"])
        self.assertEqual(ins.risks, ["r1"])

    def test_whole_response_json(self) -> None:
        raw = json.dumps(
            {
                "summary": "s",
                "themes": ["t"],
                "risks": [],
                "recommendations": [],
                "metrics_to_watch": [],
                "experiments": [],
                "open_questions": [],
            }
        )
        ins = ar.extract_insights_from_response(raw)
        self.assertEqual(ins.summary, "s")


class RunOllamaPromptTests(unittest.TestCase):
    def test_returns_partial_stdout_on_failure(self) -> None:
        import subprocess

        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=1,
                stdout="partial model text",
                stderr="connection refused",
            )

        out, ok, err = ar.run_ollama_prompt(
            "hi",
            model="m",
            timeout=10.0,
            max_retries=0,
            backoff_base=0.01,
            ollama_bin="ollama",
            run_command=fake_run,
            sleep_fn=lambda _: None,
        )
        self.assertFalse(ok)
        self.assertIn("partial", out)
        self.assertIn("stderr", out)


class IntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.logs = self.root / "logs"
        self.logs.mkdir(parents=True)

    def test_dry_run_writes_markdown(self) -> None:
        day_log = self.logs / "auto_improvements_20260501.json"
        day_log.write_text(
            json.dumps(
                [
                    {
                        "timestamp": "2026-05-01T12:00:00+00:00",
                        "category": "warning",
                        "action": "gpu health issue detected",
                        "outcome": "logged",
                        "details": {},
                    }
                ]
            ),
            encoding="utf-8",
        )

        args = ar.parse_args(
            [
                "run",
                "--root-dir",
                str(self.root),
                "--log-dir",
                str(self.logs),
                "--dry-run",
            ]
        )
        stdout_buf = mock.Mock()
        stderr_buf = mock.Mock()
        code = ar.command_run(args, stdout_buf, stderr_buf)
        self.assertEqual(code, 0)
        md_files = list(self.logs.glob("auto_reflection_*.md"))
        self.assertEqual(len(md_files), 1)
        text = md_files[0].read_text(encoding="utf-8")
        self.assertIn("# Auto-reflection digest", text)
        self.assertIn("Dry run", text)

    def test_lock_blocks_second_run(self) -> None:
        lock = self.logs / "auto_reflection.lock"
        lock.write_text("12345\n", encoding="utf-8")

        args = ar.parse_args(
            [
                "run",
                "--root-dir",
                str(self.root),
                "--log-dir",
                str(self.logs),
                "--dry-run",
            ]
        )
        with mock.patch.dict("os.environ", {"AUTO_REFLECTION_SKIP_LOCK": ""}), mock.patch.object(
            ar.os, "kill", lambda *_a, **_kw: None
        ):
            code = ar.command_run(args, mock.Mock(), mock.Mock())
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
