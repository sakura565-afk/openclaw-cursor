import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from src.openclaw_orchestration.task_runner import (
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_SKIPPED,
    STATUS_SUCCESS,
    TaskRunner,
    main,
)


class TaskRunnerTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.temp_dir.name)
        (self.base_dir / "tasks").mkdir()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write_task_file(self, name: str, content: str) -> Path:
        path = self.base_dir / "tasks" / name
        path.write_text(content, encoding="utf-8")
        return path

    def _build_runner(self, **kwargs) -> TaskRunner:
        defaults = {
            "base_dir": self.base_dir,
            "tasks_dir": "tasks",
            "log_path": "logs/task_results.jsonl",
            "sleeper": lambda seconds: None,
        }
        defaults.update(kwargs)
        return TaskRunner(**defaults)

    def test_load_tasks_uses_file_stem_as_default_name(self) -> None:
        self._write_task_file(
            "hello.yaml",
            """
type: script
runner: python
code: "print('hello')"
""".strip(),
        )

        runner = self._build_runner()
        tasks = runner.load_tasks()

        self.assertIn("hello", tasks)
        self.assertEqual("script", tasks["hello"].task_type)
        self.assertEqual(STATUS_PENDING, runner.status_by_task["hello"])

    def test_run_script_task_writes_success_log(self) -> None:
        self._write_task_file(
            "script.yaml",
            """
name: script-task
type: script
runner: python
code: "print('script ok')"
timeout: 5
""".strip(),
        )

        runner = self._build_runner()
        result = runner.run_task("script-task")

        self.assertEqual(STATUS_SUCCESS, result.status)
        self.assertEqual("script ok", result.output)
        records = self._read_log_records()
        self.assertEqual(1, len(records))
        self.assertEqual("script-task", records[0]["name"])
        self.assertEqual(STATUS_SUCCESS, records[0]["status"])

    def test_retry_logic_uses_exponential_backoff(self) -> None:
        self._write_task_file(
            "retry.yaml",
            """
name: flaky-task
type: script
runner: python
path: flaky.py
retries: 3
""".strip(),
        )
        (self.base_dir / "flaky.py").write_text(
            """
from pathlib import Path

state_file = Path("flaky_state.txt")
current = int(state_file.read_text() if state_file.exists() else "0")
if current < 2:
    state_file.write_text(str(current + 1))
    raise SystemExit(1)
print("recovered")
""".strip(),
            encoding="utf-8",
        )

        sleeps: list[float] = []
        runner = self._build_runner(sleeper=sleeps.append)
        result = runner.run_task("flaky-task")

        self.assertEqual(STATUS_SUCCESS, result.status)
        self.assertEqual(3, result.attempts)
        self.assertEqual([1.0, 2.0], sleeps)

    def test_composite_task_marks_remaining_subtasks_skipped(self) -> None:
        self._write_task_file(
            "workflow.yaml",
            """
tasks:
  - name: first-task
    type: script
    runner: python
    code: "print('first')"
  - name: failing-task
    type: script
    runner: python
    code: "raise SystemExit(1)"
  - name: final-task
    type: script
    runner: python
    code: "print('never runs')"
  - name: parent-workflow
    type: composite
    subtasks:
      - first-task
      - failing-task
      - final-task
""".strip(),
        )

        runner = self._build_runner()
        result = runner.run_task("parent-workflow")

        self.assertEqual(STATUS_FAILED, result.status)
        self.assertEqual(
            [STATUS_SUCCESS, STATUS_FAILED, STATUS_SKIPPED],
            [child.status for child in result.children],
        )
        self.assertEqual(STATUS_SKIPPED, runner.status_by_task["final-task"])

    def test_tool_task_supports_external_api_calls(self) -> None:
        self._write_task_file(
            "tool.yaml",
            """
name: api-task
type: tool
url: https://example.com/endpoint
method: POST
payload:
  hello: world
""".strip(),
        )

        captured: dict[str, object] = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self) -> bytes:
                return b'{"ok": true}'

        def fake_urlopen(request, timeout=None):
            captured["full_url"] = request.full_url
            captured["method"] = request.get_method()
            captured["data"] = request.data
            captured["timeout"] = timeout
            return FakeResponse()

        runner = self._build_runner(urlopen=fake_urlopen)
        result = runner.run_task("api-task")

        self.assertEqual(STATUS_SUCCESS, result.status)
        self.assertEqual('{"ok": true}', result.output)
        self.assertEqual("https://example.com/endpoint", captured["full_url"])
        self.assertEqual("POST", captured["method"])
        self.assertEqual(b'{"hello": "world"}', captured["data"])

    def test_show_status_uses_latest_log_entries(self) -> None:
        self._write_task_file(
            "status.yaml",
            """
tasks:
  - name: one
    type: script
    runner: python
    code: "print('one')"
  - name: two
    type: script
    runner: python
    code: "print('two')"
""".strip(),
        )

        runner = self._build_runner()
        runner.run_task("one")

        statuses = runner.show_status()

        self.assertEqual(STATUS_SUCCESS, statuses["one"]["status"])
        self.assertEqual(1, statuses["one"]["attempts"])
        self.assertEqual(STATUS_PENDING, statuses["two"]["status"])

    def test_cli_commands_cover_list_run_and_status(self) -> None:
        self._write_task_file(
            "cli.yaml",
            """
tasks:
  - name: cli-task
    type: script
    runner: python
    code: "print('cli')"
""".strip(),
        )

        stdout = io.StringIO()
        with redirect_stdout(stdout):
            exit_code = main(["--base-dir", str(self.base_dir), "list-tasks"])
        self.assertEqual(0, exit_code)
        self.assertIn("cli-task\tscript", stdout.getvalue())

        stdout = io.StringIO()
        with redirect_stdout(stdout):
            exit_code = main(["--base-dir", str(self.base_dir), "run-task", "cli-task"])
        self.assertEqual(0, exit_code)
        payload = json.loads(stdout.getvalue())
        self.assertEqual("cli-task", payload["name"])
        self.assertEqual(STATUS_SUCCESS, payload["status"])

        stdout = io.StringIO()
        with redirect_stdout(stdout):
            exit_code = main(["--base-dir", str(self.base_dir), "show-status"])
        self.assertEqual(0, exit_code)
        self.assertIn("cli-task\tsuccess\tscript\tattempts=1", stdout.getvalue())

    def _read_log_records(self) -> list[dict[str, object]]:
        log_path = self.base_dir / "logs" / "task_results.jsonl"
        return [
            json.loads(line)
            for line in log_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]


if __name__ == "__main__":
    unittest.main()
