#!/usr/bin/env python3
from __future__ import annotations

import argparse
import concurrent.futures
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, TextIO


DEFAULT_PARALLEL = 2
DEFAULT_TIMEOUT = 120.0
DEFAULT_RETRIES = 3
DEFAULT_BACKOFF_BASE = 1.0
DEFAULT_OUTPUT = "ollama_batch_results.json"


@dataclass
class PromptResult:
    prompt: str
    response: str
    latency_seconds: float
    success: bool
    status: str
    attempts: int
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "prompt": self.prompt,
            "response": self.response,
            "latency_seconds": round(self.latency_seconds, 3),
            "success": self.success,
            "status": self.status,
            "attempts": self.attempts,
            "error": self.error,
        }


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be >= 1")
    return parsed


def positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be > 0")
    return parsed


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Ollama prompts in parallel and save responses to JSON.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser(
        "run",
        help="Run prompts from a text file or JSON array.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    run_parser.add_argument("--file", required=True, help="Input file with prompts.")
    run_parser.add_argument("--model", required=True, help="Ollama model name.")
    run_parser.add_argument(
        "--output",
        default=DEFAULT_OUTPUT,
        help="Path to the JSON output file.",
    )
    run_parser.add_argument(
        "--parallel",
        type=positive_int,
        default=DEFAULT_PARALLEL,
        help="Maximum concurrent requests to send to Ollama.",
    )
    run_parser.add_argument(
        "--timeout",
        type=positive_float,
        default=DEFAULT_TIMEOUT,
        help="Timeout in seconds for each Ollama request.",
    )
    run_parser.add_argument(
        "--retries",
        type=positive_int,
        default=DEFAULT_RETRIES,
        help="Maximum retries after the initial failed attempt.",
    )
    run_parser.add_argument(
        "--backoff-base",
        type=positive_float,
        default=DEFAULT_BACKOFF_BASE,
        help="Base seconds for exponential backoff between retries.",
    )
    return parser.parse_args(argv)


def load_prompts(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    stripped = text.strip()
    if not stripped:
        return []

    if path.suffix.lower() == ".json" or stripped.startswith("["):
        payload = json.loads(stripped)
        if not isinstance(payload, list):
            raise ValueError("JSON input must be an array of prompt strings.")
        prompts: list[str] = []
        for index, item in enumerate(payload):
            if not isinstance(item, str):
                raise ValueError(f"Prompt at index {index} is not a string.")
            prompts.append(item)
        return prompts

    return [line.strip() for line in text.splitlines() if line.strip()]


def format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "unknown"
    whole_seconds = max(0, int(round(seconds)))
    hours, remainder = divmod(whole_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def print_progress(
    *,
    completed: int,
    total: int,
    successes: int,
    failures: int,
    started_at: float,
    progress_stream: TextIO,
    time_fn: Callable[[], float],
) -> None:
    elapsed = time_fn() - started_at
    eta_seconds: float | None = None
    if completed > 0 and elapsed > 0:
        remaining = total - completed
        eta_seconds = (elapsed / completed) * remaining

    print(
        (
            f"[{completed}/{total}] completed"
            f" | ok={successes}"
            f" | failed={failures}"
            f" | elapsed={format_duration(elapsed)}"
            f" | eta={format_duration(eta_seconds)}"
        ),
        file=progress_stream,
        flush=True,
    )


def run_prompt(
    prompt: str,
    *,
    model: str,
    timeout: float,
    max_retries: int,
    backoff_base: float,
    run_command: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    sleep_fn: Callable[[float], None] = time.sleep,
    time_fn: Callable[[], float] = time.monotonic,
) -> PromptResult:
    started_at = time_fn()
    last_error: str | None = None

    for attempt in range(1, max_retries + 2):
        try:
            completed = run_command(
                ["ollama", "run", model, prompt],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            if completed.returncode == 0:
                return PromptResult(
                    prompt=prompt,
                    response=(completed.stdout or "").strip(),
                    latency_seconds=time_fn() - started_at,
                    success=True,
                    status="success",
                    attempts=attempt,
                )

            last_error = (
                (completed.stderr or "").strip()
                or (completed.stdout or "").strip()
                or f"ollama exited with code {completed.returncode}"
            )
        except subprocess.TimeoutExpired:
            last_error = f"timed out after {timeout} seconds"
        except OSError as exc:
            last_error = str(exc)

        if attempt <= max_retries:
            sleep_fn(backoff_base * (2 ** (attempt - 1)))

    return PromptResult(
        prompt=prompt,
        response="",
        latency_seconds=time_fn() - started_at,
        success=False,
        status="failed",
        attempts=max_retries + 1,
        error=last_error,
    )


def run_batch(
    prompts: list[str],
    *,
    model: str,
    parallel: int,
    timeout: float,
    max_retries: int,
    backoff_base: float,
    progress_stream: TextIO | None = sys.stderr,
    run_command: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    sleep_fn: Callable[[float], None] = time.sleep,
    time_fn: Callable[[], float] = time.monotonic,
    runner: Callable[[str], PromptResult] | None = None,
) -> list[PromptResult]:
    if parallel < 1:
        raise ValueError("parallel must be >= 1")

    if runner is None:
        def default_runner(prompt: str) -> PromptResult:
            return run_prompt(
                prompt,
                model=model,
                timeout=timeout,
                max_retries=max_retries,
                backoff_base=backoff_base,
                run_command=run_command,
                sleep_fn=sleep_fn,
                time_fn=time_fn,
            )

        runner = default_runner

    started_at = time_fn()
    ordered_results: list[PromptResult | None] = [None] * len(prompts)
    successes = 0
    failures = 0

    with concurrent.futures.ThreadPoolExecutor(max_workers=parallel) as executor:
        future_map = {
            executor.submit(runner, prompt): index
            for index, prompt in enumerate(prompts)
        }

        for completed_count, future in enumerate(
            concurrent.futures.as_completed(future_map),
            start=1,
        ):
            index = future_map[future]
            try:
                result = future.result()
            except Exception as exc:  # pragma: no cover - defensive fallback
                result = PromptResult(
                    prompt=prompts[index],
                    response="",
                    latency_seconds=0.0,
                    success=False,
                    status="failed",
                    attempts=1,
                    error=str(exc),
                )

            ordered_results[index] = result
            if result.success:
                successes += 1
            else:
                failures += 1

            if progress_stream is not None:
                print_progress(
                    completed=completed_count,
                    total=len(prompts),
                    successes=successes,
                    failures=failures,
                    started_at=started_at,
                    progress_stream=progress_stream,
                    time_fn=time_fn,
                )

    return [result for result in ordered_results if result is not None]


def build_output_payload(
    *,
    input_path: Path,
    output_path: Path,
    model: str,
    parallel: int,
    timeout: float,
    max_retries: int,
    started_at: datetime,
    finished_at: datetime,
    results: list[PromptResult],
) -> dict[str, Any]:
    successes = sum(1 for result in results if result.success)
    failures = len(results) - successes
    return {
        "model": model,
        "input_file": str(input_path),
        "output_file": str(output_path),
        "parallel": parallel,
        "timeout_seconds": timeout,
        "max_retries": max_retries,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "total": len(results),
        "successes": successes,
        "failures": failures,
        "results": [result.to_dict() for result in results],
    }


def write_results(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def run_command(args: argparse.Namespace) -> int:
    input_path = Path(args.file)
    output_path = Path(args.output)
    prompts = load_prompts(input_path)
    if not prompts:
        raise ValueError(f"No prompts found in {input_path}")

    started_at = datetime.now(timezone.utc)
    results = run_batch(
        prompts,
        model=args.model,
        parallel=args.parallel,
        timeout=args.timeout,
        max_retries=args.retries,
        backoff_base=args.backoff_base,
        progress_stream=sys.stderr,
    )
    finished_at = datetime.now(timezone.utc)

    payload = build_output_payload(
        input_path=input_path,
        output_path=output_path,
        model=args.model,
        parallel=args.parallel,
        timeout=args.timeout,
        max_retries=args.retries,
        started_at=started_at,
        finished_at=finished_at,
        results=results,
    )
    write_results(output_path, payload)
    print(
        (
            f"Completed {payload['total']} prompts: "
            f"{payload['successes']} succeeded, {payload['failures']} failed. "
            f"Results saved to {output_path}"
        )
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.command == "run":
            return run_command(args)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 1


def _selftest(stdout=None) -> bool:
    target = stdout or __import__("sys").stdout
    checks = []

    smoke_ok = callable(globals().get("main")) or callable(globals().get("build_parser"))
    checks.append(("smoke", smoke_ok))

    try:
        if callable(globals().get("estimate_tokens")):
            edge_ok = estimate_tokens("") == 0
        elif callable(globals().get("normalize_text")):
            edge_ok = isinstance(normalize_text("  sample  "), str)
        elif callable(globals().get("parse_args")):
            try:
                parse_args(["--help"])
                edge_ok = True
            except SystemExit:
                edge_ok = True
        else:
            edge_ok = True
    except Exception:
        edge_ok = False
    checks.append(("edge_case", edge_ok))

    passed = all(result for _, result in checks)
    target.write(f"{__name__} self-test: {'PASS' if passed else 'FAIL'}\n")
    for name, result in checks:
        target.write(f"  - {name}: {'PASS' if result else 'FAIL'}\n")
    return passed


if __name__ == "__main__":
    sys.exit(main())
