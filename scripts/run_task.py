#!/usr/bin/env python3
"""Run OpenClaw task definitions from YAML specs."""

from __future__ import annotations

import argparse
import pathlib
import sys

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC_PATH = PROJECT_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from openclaw_orchestration.runner import execute_task, load_task_spec  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run an OpenClaw YAML task.")
    parser.add_argument("--task", required=True, help="Path to task YAML definition.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print execution plan without running commands.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    task_path = pathlib.Path(args.task)
    if not task_path.exists():
        raise FileNotFoundError(f"Task file not found: {task_path}")
    spec = load_task_spec(task_path)
    log_lines = execute_task(spec, dry_run=args.dry_run)
    for line in log_lines:
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
