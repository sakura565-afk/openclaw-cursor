#!/usr/bin/env python3
"""Task runner for OpenClaw orchestration specs."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class TaskStep:
    name: str
    action: str
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass
class TaskSpec:
    name: str
    description: str
    steps: list[TaskStep]
    metadata: dict[str, Any] = field(default_factory=dict)


class TaskValidationError(ValueError):
    """Raised when a task spec is invalid."""


def load_task_spec(task_path: str | Path) -> TaskSpec:
    path = Path(task_path)
    if not path.exists():
        raise FileNotFoundError(f"Task file not found: {path}")

    with path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}

    if not isinstance(raw, dict):
        raise TaskValidationError("Task spec must be a YAML mapping.")

    name = raw.get("name")
    description = raw.get("description", "").strip()
    steps_raw = raw.get("steps", [])
    metadata = raw.get("metadata", {})

    if not name or not isinstance(name, str):
        raise TaskValidationError("Task spec requires a non-empty string 'name'.")
    if not isinstance(description, str):
        raise TaskValidationError("'description' must be a string.")
    if not isinstance(steps_raw, list) or not steps_raw:
        raise TaskValidationError("Task spec requires a non-empty list 'steps'.")
    if not isinstance(metadata, dict):
        raise TaskValidationError("'metadata' must be a mapping.")

    steps: list[TaskStep] = []
    for index, step in enumerate(steps_raw, start=1):
        if not isinstance(step, dict):
            raise TaskValidationError(f"Step {index} must be a mapping.")
        step_name = step.get("name")
        action = step.get("action")
        parameters = step.get("parameters", {})
        if not step_name or not isinstance(step_name, str):
            raise TaskValidationError(f"Step {index} has invalid or missing 'name'.")
        if not action or not isinstance(action, str):
            raise TaskValidationError(f"Step {index} has invalid or missing 'action'.")
        if not isinstance(parameters, dict):
            raise TaskValidationError(f"Step {index} 'parameters' must be a mapping.")
        steps.append(TaskStep(name=step_name, action=action, parameters=parameters))

    return TaskSpec(name=name, description=description, steps=steps, metadata=metadata)


def execute_task(task: TaskSpec, dry_run: bool = False) -> list[str]:
    log_lines: list[str] = []
    header = f"Executing task '{task.name}' ({len(task.steps)} step(s))"
    if dry_run:
        header += " [dry-run]"
    log_lines.append(header)

    for idx, step in enumerate(task.steps, start=1):
        action_line = (
            f"[{idx}/{len(task.steps)}] {step.name}: action='{step.action}' "
            f"parameters={step.parameters}"
        )
        if dry_run:
            action_line = f"DRY-RUN {action_line}"
        log_lines.append(action_line)

        # Replace this block with integrations into your own OpenClaw runtime
        # handlers. The scaffold keeps behavior deterministic and CI-friendly.
        if not dry_run:
            log_lines.append(f"Completed step '{step.name}'.")

    log_lines.append(f"Task '{task.name}' finished successfully.")
    return log_lines


def run_task_file(task_path: str | Path, dry_run: bool = False) -> int:
    task = load_task_spec(task_path)
    output = execute_task(task, dry_run=dry_run)
    for line in output:
        print(line)
    return 0
