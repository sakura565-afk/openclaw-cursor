from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib import error as urllib_error
from urllib import request as urllib_request

import yaml

STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"

VALID_TASK_TYPES = {"script", "tool", "composite"}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _isoformat(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


class TaskExecutionError(RuntimeError):
    def __init__(self, message: str, *, children: list["TaskResult"] | None = None) -> None:
        super().__init__(message)
        self.children = children or []


@dataclass(frozen=True)
class TaskDefinition:
    name: str
    task_type: str
    description: str | None = None
    config: dict[str, Any] = field(default_factory=dict)
    source_path: str | None = None

    @classmethod
    def from_mapping(
        cls,
        data: dict[str, Any],
        *,
        source_path: str | None = None,
        default_name: str | None = None,
    ) -> "TaskDefinition":
        if not isinstance(data, dict):
            raise ValueError("Task definition must be a mapping.")

        raw = dict(data)
        name = raw.pop("name", default_name)
        task_type = raw.pop("type", None)
        description = raw.pop("description", None)

        if not name:
            raise ValueError("Task definition is missing a name.")
        if task_type not in VALID_TASK_TYPES:
            raise ValueError(
                f"Task '{name}' has unsupported type '{task_type}'. "
                f"Expected one of {sorted(VALID_TASK_TYPES)}."
            )

        return cls(
            name=str(name),
            task_type=str(task_type),
            description=str(description) if description is not None else None,
            config=raw,
            source_path=source_path,
        )

    @property
    def timeout(self) -> float | None:
        raw_timeout = self.config.get("timeout")
        if raw_timeout in (None, ""):
            return None
        timeout = float(raw_timeout)
        if timeout <= 0:
            raise ValueError(f"Task '{self.name}' timeout must be positive.")
        return timeout

    @property
    def retries(self) -> int:
        raw_retries = self.config.get("retries", 3)
        retries = int(raw_retries)
        if retries < 0:
            raise ValueError(f"Task '{self.name}' retries cannot be negative.")
        return min(retries, 3)

    def with_timeout(self, timeout: float | None) -> "TaskDefinition":
        if timeout is None:
            return self
        new_config = dict(self.config)
        new_config["timeout"] = timeout
        return TaskDefinition(
            name=self.name,
            task_type=self.task_type,
            description=self.description,
            config=new_config,
            source_path=self.source_path,
        )


@dataclass
class TaskResult:
    name: str
    task_type: str
    status: str = STATUS_PENDING
    attempts: int = 0
    started_at: str | None = None
    ended_at: str | None = None
    duration_seconds: float | None = None
    output: Any = None
    error: str | None = None
    children: list["TaskResult"] = field(default_factory=list)
    source_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "task_type": self.task_type,
            "status": self.status,
            "attempts": self.attempts,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration_seconds": self.duration_seconds,
            "output": self.output,
            "error": self.error,
            "source_path": self.source_path,
            "children": [child.to_dict() for child in self.children],
        }


class TaskRunner:
    def __init__(
        self,
        *,
        base_dir: str | os.PathLike[str] = ".",
        tasks_dir: str | os.PathLike[str] = "tasks",
        log_path: str | os.PathLike[str] = "logs/task_results.jsonl",
        sleeper: Callable[[float], None] = time.sleep,
        urlopen: Callable[..., Any] = urllib_request.urlopen,
    ) -> None:
        self.base_dir = Path(base_dir).resolve()
        self.tasks_dir = self._resolve_path(tasks_dir)
        self.log_path = self._resolve_path(log_path)
        self.sleeper = sleeper
        self.urlopen = urlopen
        self._tasks: dict[str, TaskDefinition] | None = None
        self.status_by_task: dict[str, str] = {}

    def _resolve_path(self, value: str | os.PathLike[str]) -> Path:
        path = Path(value)
        if not path.is_absolute():
            path = self.base_dir / path
        return path

    def load_tasks(self, *, reload: bool = False) -> dict[str, TaskDefinition]:
        if self._tasks is not None and not reload:
            return self._tasks

        tasks: dict[str, TaskDefinition] = {}
        if self.tasks_dir.exists():
            yaml_files = sorted(self.tasks_dir.glob("*.yaml")) + sorted(self.tasks_dir.glob("*.yml"))
            for task_file in yaml_files:
                content = yaml.safe_load(task_file.read_text(encoding="utf-8"))
                if content in (None, ""):
                    continue

                if isinstance(content, dict) and "tasks" in content:
                    raw_tasks = content["tasks"]
                    default_name = None
                else:
                    raw_tasks = [content]
                    default_name = task_file.stem

                if not isinstance(raw_tasks, list):
                    raise ValueError(
                        f"Task file '{task_file}' must define a task mapping or a top-level 'tasks' list."
                    )

                for entry in raw_tasks:
                    task = TaskDefinition.from_mapping(
                        entry,
                        source_path=str(task_file.relative_to(self.base_dir)),
                        default_name=default_name,
                    )
                    if task.name in tasks:
                        raise ValueError(f"Duplicate task name '{task.name}' encountered while loading tasks.")
                    tasks[task.name] = task

        self._tasks = tasks
        for task_name in tasks:
            self.status_by_task.setdefault(task_name, STATUS_PENDING)
        return tasks

    def list_tasks(self) -> list[TaskDefinition]:
        return [self.load_tasks()[name] for name in sorted(self.load_tasks())]

    def get_task(self, task_name: str) -> TaskDefinition:
        tasks = self.load_tasks()
        if task_name not in tasks:
            raise KeyError(f"Unknown task '{task_name}'.")
        return tasks[task_name]

    def run_task(self, task_name: str) -> TaskResult:
        task = self.get_task(task_name)
        return self._execute_task(task, stack=())

    def show_status(self) -> dict[str, dict[str, Any]]:
        statuses: dict[str, dict[str, Any]] = {}
        for task in self.list_tasks():
            statuses[task.name] = {
                "task_type": task.task_type,
                "status": self.status_by_task.get(task.name, STATUS_PENDING),
                "attempts": 0,
                "ended_at": None,
                "source_path": task.source_path,
            }

        if not self.log_path.exists():
            return statuses

        with self.log_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                record = json.loads(line)
                statuses[record["name"]] = {
                    "task_type": record["task_type"],
                    "status": record["status"],
                    "attempts": record["attempts"],
                    "ended_at": record["ended_at"],
                    "source_path": record.get("source_path"),
                }
        return statuses

    def _execute_task(self, task: TaskDefinition, *, stack: tuple[str, ...]) -> TaskResult:
        if task.name in stack:
            cycle = " -> ".join((*stack, task.name))
            cycle_result = TaskResult(
                name=task.name,
                task_type=task.task_type,
                status=STATUS_FAILED,
                attempts=0,
                started_at=_isoformat(_utc_now()),
                ended_at=_isoformat(_utc_now()),
                duration_seconds=0.0,
                error=f"Detected composite task cycle: {cycle}",
                source_path=task.source_path,
            )
            self.status_by_task[task.name] = STATUS_FAILED
            self._log_result(cycle_result)
            return cycle_result

        started = _utc_now()
        result = TaskResult(
            name=task.name,
            task_type=task.task_type,
            status=STATUS_RUNNING,
            started_at=_isoformat(started),
            source_path=task.source_path,
        )
        self.status_by_task[task.name] = STATUS_RUNNING

        retries = task.retries
        effective_stack = (*stack, task.name)

        for attempt in range(retries + 1):
            result.attempts = attempt + 1
            try:
                output, children = self._run_once(task, stack=effective_stack, timeout=task.timeout)
                result.output = output
                result.children = children
                result.error = None
                result.status = STATUS_SUCCESS
                break
            except TaskExecutionError as exc:
                result.output = None
                result.error = str(exc)
                result.children = exc.children
                if attempt == retries:
                    result.status = STATUS_FAILED
                    break
                self.sleeper(float(2**attempt))

        ended = _utc_now()
        result.ended_at = _isoformat(ended)
        result.duration_seconds = round((ended - started).total_seconds(), 6)
        self.status_by_task[result.name] = result.status
        self._log_result(result)
        return result

    def _run_once(
        self,
        task: TaskDefinition,
        *,
        stack: tuple[str, ...],
        timeout: float | None,
    ) -> tuple[Any, list[TaskResult]]:
        if task.task_type == "script":
            return self._execute_script(task, timeout=timeout), []
        if task.task_type == "tool":
            return self._execute_tool(task, timeout=timeout), []
        if task.task_type == "composite":
            return self._execute_composite(task, stack=stack, timeout=timeout)
        raise TaskExecutionError(f"Unsupported task type '{task.task_type}' for task '{task.name}'.")

    def _execute_script(self, task: TaskDefinition, *, timeout: float | None) -> str:
        runner = str(
            task.config.get("runner")
            or task.config.get("runtime")
            or task.config.get("language")
            or "shell"
        ).lower()
        env = os.environ.copy()
        task_env = task.config.get("env") or {}
        if not isinstance(task_env, dict):
            raise TaskExecutionError(f"Task '{task.name}' has a non-mapping 'env' value.")
        env.update({str(key): str(value) for key, value in task_env.items()})

        try:
            if runner == "python":
                return self._run_python_script(task, env=env, timeout=timeout)
            if runner == "shell":
                return self._run_shell_script(task, env=env, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise TaskExecutionError(
                f"Script task '{task.name}' timed out after {timeout} seconds."
            ) from exc

        raise TaskExecutionError(
            f"Script task '{task.name}' has unsupported runner '{runner}'. Use 'shell' or 'python'."
        )

    def _run_python_script(
        self,
        task: TaskDefinition,
        *,
        env: dict[str, str],
        timeout: float | None,
    ) -> str:
        if task.config.get("path"):
            script_path = self._resolve_path(str(task.config["path"]))
            command = [sys.executable, str(script_path)]
        else:
            code = task.config.get("code") or task.config.get("script") or task.config.get("command")
            if not code:
                raise TaskExecutionError(
                    f"Python task '{task.name}' requires 'code', 'script', or 'command'."
                )
            command = [sys.executable, "-c", str(code)]

        if task.config.get("args"):
            args = task.config["args"]
            if not isinstance(args, list):
                raise TaskExecutionError(f"Task '{task.name}' args must be a list.")
            command.extend(str(arg) for arg in args)

        completed = subprocess.run(
            command,
            cwd=self.base_dir,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if completed.returncode != 0:
            stderr = completed.stderr.strip() or completed.stdout.strip() or "Unknown script failure."
            raise TaskExecutionError(
                f"Script task '{task.name}' failed with exit code {completed.returncode}: {stderr}"
            )
        return completed.stdout.strip()

    def _run_shell_script(
        self,
        task: TaskDefinition,
        *,
        env: dict[str, str],
        timeout: float | None,
    ) -> str:
        command = task.config.get("command") or task.config.get("script")
        if not command:
            raise TaskExecutionError(f"Shell task '{task.name}' requires 'command' or 'script'.")

        completed = subprocess.run(
            str(command),
            cwd=self.base_dir,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=True,
            check=False,
        )
        if completed.returncode != 0:
            stderr = completed.stderr.strip() or completed.stdout.strip() or "Unknown shell failure."
            raise TaskExecutionError(
                f"Script task '{task.name}' failed with exit code {completed.returncode}: {stderr}"
            )
        return completed.stdout.strip()

    def _execute_tool(self, task: TaskDefinition, *, timeout: float | None) -> str:
        url = task.config.get("url") or task.config.get("endpoint") or task.config.get("api_url")
        if not url:
            raise TaskExecutionError(f"Tool task '{task.name}' requires 'url' or 'endpoint'.")

        method = str(task.config.get("method", "GET")).upper()
        headers = dict(task.config.get("headers") or {})
        payload = task.config.get("payload", task.config.get("body"))
        data: bytes | None = None

        if payload is not None:
            if isinstance(payload, (dict, list)):
                data = json.dumps(payload).encode("utf-8")
                headers.setdefault("Content-Type", "application/json")
            elif isinstance(payload, bytes):
                data = payload
            else:
                data = str(payload).encode("utf-8")

        request = urllib_request.Request(str(url), data=data, headers=headers, method=method)
        try:
            with self.urlopen(request, timeout=timeout) as response:
                body = response.read().decode("utf-8")
                return body.strip()
        except urllib_error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace").strip()
            raise TaskExecutionError(
                f"Tool task '{task.name}' failed with HTTP {exc.code}: {detail or exc.reason}"
            ) from exc
        except urllib_error.URLError as exc:
            raise TaskExecutionError(f"Tool task '{task.name}' failed: {exc.reason}") from exc
        except TimeoutError as exc:
            raise TaskExecutionError(
                f"Tool task '{task.name}' timed out after {timeout} seconds."
            ) from exc

    def _execute_composite(
        self,
        task: TaskDefinition,
        *,
        stack: tuple[str, ...],
        timeout: float | None,
    ) -> tuple[dict[str, Any], list[TaskResult]]:
        raw_subtasks = task.config.get("subtasks") or []
        if not isinstance(raw_subtasks, list):
            raise TaskExecutionError(f"Composite task '{task.name}' must define 'subtasks' as a list.")

        deadline = None if timeout is None else time.monotonic() + timeout
        children: list[TaskResult] = []

        for index, raw_subtask in enumerate(raw_subtasks):
            child_definition = self._resolve_subtask(raw_subtask, parent=task, index=index)

            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    children.extend(
                        self._skip_remaining_subtasks(task, raw_subtasks[index:], start_index=index)
                    )
                    raise TaskExecutionError(
                        f"Composite task '{task.name}' timed out after {timeout} seconds.",
                        children=children,
                    )
                child_definition = child_definition.with_timeout(
                    min(child_definition.timeout, remaining)
                    if child_definition.timeout is not None
                    else remaining
                )

            child_result = self._execute_task(child_definition, stack=stack)
            children.append(child_result)
            if child_result.status != STATUS_SUCCESS:
                remaining_subtasks = self._skip_remaining_subtasks(
                    task, raw_subtasks[index + 1 :], start_index=index + 1
                )
                children.extend(remaining_subtasks)
                raise TaskExecutionError(
                    f"Composite task '{task.name}' failed because subtask '{child_result.name}' "
                    f"finished with status '{child_result.status}'.",
                    children=children,
                )

        output = {
            "completed_subtasks": [child.name for child in children if child.status == STATUS_SUCCESS],
            "subtask_count": len(children),
        }
        return output, children

    def _resolve_subtask(
        self,
        raw_subtask: Any,
        *,
        parent: TaskDefinition,
        index: int,
    ) -> TaskDefinition:
        if isinstance(raw_subtask, str):
            return self.get_task(raw_subtask)
        if isinstance(raw_subtask, dict):
            generated_name = f"{parent.name}.subtask_{index + 1}"
            return TaskDefinition.from_mapping(
                raw_subtask,
                source_path=parent.source_path,
                default_name=generated_name,
            )
        raise TaskExecutionError(
            f"Composite task '{parent.name}' has invalid subtask at index {index}: {raw_subtask!r}"
        )

    def _skip_remaining_subtasks(
        self,
        parent: TaskDefinition,
        remaining: list[Any],
        *,
        start_index: int,
    ) -> list[TaskResult]:
        skipped_results: list[TaskResult] = []
        for offset, raw_subtask in enumerate(remaining):
            child_definition = self._resolve_subtask(raw_subtask, parent=parent, index=start_index + offset)
            now = _utc_now()
            skipped = TaskResult(
                name=child_definition.name,
                task_type=child_definition.task_type,
                status=STATUS_SKIPPED,
                attempts=0,
                started_at=_isoformat(now),
                ended_at=_isoformat(now),
                duration_seconds=0.0,
                error=f"Skipped because composite task '{parent.name}' did not complete successfully.",
                source_path=child_definition.source_path,
            )
            self.status_by_task[skipped.name] = STATUS_SKIPPED
            self._log_result(skipped)
            skipped_results.append(skipped)
        return skipped_results

    def _log_result(self, result: TaskResult) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(result.to_dict(), sort_keys=True))
            handle.write("\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run OpenClaw task workflows.")
    parser.add_argument("--base-dir", default=".", help="Repository root containing tasks/ and logs/.")
    parser.add_argument("--tasks-dir", default="tasks", help="Directory containing YAML task definitions.")
    parser.add_argument("--log-path", default="logs/task_results.jsonl", help="JSONL task log path.")

    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list-tasks", help="List available tasks.")
    list_parser.set_defaults(command="list-tasks")

    run_parser = subparsers.add_parser("run-task", help="Run a single task.")
    run_parser.add_argument("task_name", help="Task name to execute.")
    run_parser.set_defaults(command="run-task")

    status_parser = subparsers.add_parser("show-status", help="Show latest task status.")
    status_parser.set_defaults(command="show-status")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    runner = TaskRunner(base_dir=args.base_dir, tasks_dir=args.tasks_dir, log_path=args.log_path)

    if args.command == "list-tasks":
        for task in runner.list_tasks():
            description = task.description or ""
            print(f"{task.name}\t{task.task_type}\t{description}")
        return 0

    if args.command == "run-task":
        result = runner.run_task(args.task_name)
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
        return 0 if result.status == STATUS_SUCCESS else 1

    if args.command == "show-status":
        statuses = runner.show_status()
        for task_name in sorted(statuses):
            record = statuses[task_name]
            print(
                f"{task_name}\t{record['status']}\t{record['task_type']}\t"
                f"attempts={record['attempts']}"
            )
        return 0

    parser.error(f"Unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
