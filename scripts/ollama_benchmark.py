#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any


TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[^\w\s]", re.UNICODE)


@dataclass(frozen=True)
class BenchmarkPrompt:
    name: str
    prompt: str
    expected_answer: str
    keywords: tuple[str, ...] = ()


STANDARD_PROMPTS: tuple[BenchmarkPrompt, ...] = (
    BenchmarkPrompt(
        name="capital_france",
        prompt="Answer with one word only: What is the capital of France?",
        expected_answer="Paris",
        keywords=("paris",),
    ),
    BenchmarkPrompt(
        name="arithmetic",
        prompt="Answer with digits only: What is 12 * 8?",
        expected_answer="96",
        keywords=("96",),
    ),
    BenchmarkPrompt(
        name="sorting",
        prompt="Return only the numbers in ascending order: 9, 1, 4, 1, 5",
        expected_answer="1, 1, 4, 5, 9",
        keywords=("1", "4", "5", "9"),
    ),
    BenchmarkPrompt(
        name="translation",
        prompt='Translate "hello" to Spanish. Answer with one word only.',
        expected_answer="hola",
        keywords=("hola",),
    ),
    BenchmarkPrompt(
        name="antonym",
        prompt='Answer with one word only: What is the opposite of "cold"?',
        expected_answer="hot",
        keywords=("hot",),
    ),
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark installed Ollama models.")
    parser.add_argument(
        "--log-dir",
        default="logs",
        help="Directory where benchmark logs are stored (default: logs).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run a benchmark across models.")
    run_parser.add_argument(
        "models",
        nargs="*",
        help="Specific Ollama model names. If omitted, installed models are discovered via `ollama list`.",
    )

    compare_parser = subparsers.add_parser("compare", help="Compare two benchmark runs.")
    compare_parser.add_argument(
        "--current",
        help="Current run identifier in the form YYYYMMDD:index or a JSON log path.",
    )
    compare_parser.add_argument(
        "--previous",
        help="Previous run identifier in the form YYYYMMDD:index or a JSON log path.",
    )

    history_parser = subparsers.add_parser("history", help="Show historical benchmark runs.")
    history_parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum number of runs to show (default: 10).",
    )

    return parser.parse_args(argv)


def run_command(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        check=False,
    )


def estimate_token_count(text: str) -> int:
    return len(TOKEN_RE.findall(text))


def normalize_text(text: str) -> str:
    lowered = text.lower()
    cleaned = re.sub(r"[^a-z0-9]+", " ", lowered)
    return " ".join(cleaned.split())


def score_prompt_output(output: str, prompt: BenchmarkPrompt) -> float:
    normalized_output = normalize_text(output)
    normalized_expected = normalize_text(prompt.expected_answer)
    if not normalized_output:
        return 0.0

    exact = 1.0 if normalized_output == normalized_expected else 0.0
    contains = 1.0 if normalized_expected and normalized_expected in normalized_output else 0.0
    similarity = SequenceMatcher(None, normalized_output, normalized_expected).ratio()

    keyword_hits = 0
    for keyword in prompt.keywords:
        if normalize_text(keyword) in normalized_output:
            keyword_hits += 1
    keyword_score = keyword_hits / len(prompt.keywords) if prompt.keywords else contains

    # Favor exact/contained answers, with a softer fallback for near misses.
    score = (exact * 0.5) + (contains * 0.2) + (keyword_score * 0.2) + (similarity * 0.1)
    return round(score * 100, 2)


def format_number(value: float | int | None, digits: int = 2) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, int):
        return str(value)
    return f"{value:.{digits}f}"


def render_markdown_table(rows: list[dict[str, Any]], columns: list[tuple[str, str]]) -> str:
    headers = [title for title, _ in columns]
    divider = ["---"] * len(columns)
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(divider) + " |",
    ]
    for row in rows:
        values = [str(row.get(key, "")) for _, key in columns]
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def list_models() -> list[str]:
    result = run_command(["ollama", "list"])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Failed to list Ollama models.")

    models: list[str] = []
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if not stripped or stripped.lower().startswith("name "):
            continue
        model_name = stripped.split()[0]
        if model_name:
            models.append(model_name)
    if not models:
        raise RuntimeError("No Ollama models were found. Pass model names explicitly or install models first.")
    return models


def read_vram_usage_mb() -> int | None:
    result = run_command(
        [
            "nvidia-smi",
            "--query-gpu=memory.used",
            "--format=csv,noheader,nounits",
        ]
    )
    if result.returncode != 0:
        return None

    values: list[int] = []
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            values.append(int(float(stripped)))
        except ValueError:
            continue
    if not values:
        return None
    return sum(values)


def read_ram_usage_mb() -> int | None:
    meminfo_path = Path("/proc/meminfo")
    if not meminfo_path.exists():
        return None

    values: dict[str, int] = {}
    for line in meminfo_path.read_text(encoding="utf-8").splitlines():
        parts = line.split(":", 1)
        if len(parts) != 2:
            continue
        key = parts[0].strip()
        match = re.search(r"(\d+)", parts[1])
        if not match:
            continue
        values[key] = int(match.group(1))

    total = values.get("MemTotal")
    available = values.get("MemAvailable")
    if total is None or available is None:
        return None
    used_kb = max(total - available, 0)
    return round(used_kb / 1024)


def safe_delta(after: int | None, before: int | None) -> int | None:
    if after is None or before is None:
        return None
    return max(after - before, 0)


def benchmark_prompt(model_name: str, prompt: BenchmarkPrompt) -> dict[str, Any]:
    started = time.perf_counter()
    result = run_command(["ollama", "run", model_name, prompt.prompt])
    elapsed = max(time.perf_counter() - started, 1e-9)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"`ollama run {model_name}` failed.")

    output = result.stdout.strip()
    token_count = estimate_token_count(output)
    tokens_per_sec = token_count / elapsed if token_count else 0.0
    return {
        "prompt_name": prompt.name,
        "prompt": prompt.prompt,
        "expected_answer": prompt.expected_answer,
        "output": output,
        "duration_sec": round(elapsed, 4),
        "token_count": token_count,
        "tokens_per_sec": round(tokens_per_sec, 2),
        "quality_score": score_prompt_output(output, prompt),
    }


def benchmark_model(model_name: str, prompts: tuple[BenchmarkPrompt, ...] = STANDARD_PROMPTS) -> dict[str, Any]:
    vram_before = read_vram_usage_mb()
    ram_before = read_ram_usage_mb()

    prompt_results: list[dict[str, Any]] = []
    vram_after_load: int | None = None
    ram_after_load: int | None = None

    for index, prompt in enumerate(prompts):
        prompt_result = benchmark_prompt(model_name, prompt)
        prompt_results.append(prompt_result)
        if index == 0:
            vram_after_load = read_vram_usage_mb()
            ram_after_load = read_ram_usage_mb()

    total_tokens = sum(item["token_count"] for item in prompt_results)
    total_duration = sum(item["duration_sec"] for item in prompt_results)
    tokens_per_sec = total_tokens / total_duration if total_duration else 0.0
    quality_score = (
        sum(item["quality_score"] for item in prompt_results) / len(prompt_results)
        if prompt_results
        else 0.0
    )

    return {
        "model_name": model_name,
        "tokens_per_sec": round(tokens_per_sec, 2),
        "vram_mb": safe_delta(vram_after_load, vram_before),
        "quality_score": round(quality_score, 2),
        "memory_mb": safe_delta(ram_after_load, ram_before),
        "vram_before_mb": vram_before,
        "vram_after_load_mb": vram_after_load,
        "ram_before_mb": ram_before,
        "ram_after_load_mb": ram_after_load,
        "prompt_results": prompt_results,
    }


def benchmark_rows(results: list[dict[str, Any]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for item in results:
        rows.append(
            {
                "model_name": item["model_name"],
                "tokens_per_sec": format_number(item["tokens_per_sec"]),
                "vram_mb": format_number(item["vram_mb"], digits=0),
                "quality_score": format_number(item["quality_score"]),
                "memory_mb": format_number(item["memory_mb"], digits=0),
            }
        )
    return rows


def ensure_log_dir(log_dir: Path) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)


def benchmark_log_path(log_dir: Path, run_at: datetime) -> Path:
    return log_dir / f"benchmark_{run_at.strftime('%Y%m%d')}.json"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_runs_from_payload(path: Path, payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict) and isinstance(payload.get("runs"), list):
        runs = []
        for index, run in enumerate(payload["runs"]):
            if isinstance(run, dict):
                run_copy = dict(run)
                run_copy["_source_path"] = str(path)
                run_copy["_run_index"] = index
                runs.append(run_copy)
        return runs
    if isinstance(payload, dict) and "results" in payload:
        payload_copy = dict(payload)
        payload_copy["_source_path"] = str(path)
        payload_copy["_run_index"] = 0
        return [payload_copy]
    return []


def load_history(log_dir: Path) -> list[dict[str, Any]]:
    if not log_dir.exists():
        return []

    runs: list[dict[str, Any]] = []
    for path in sorted(log_dir.glob("benchmark_*.json")):
        try:
            payload = read_json(path)
        except json.JSONDecodeError:
            continue
        runs.extend(load_runs_from_payload(path, payload))

    def run_sort_key(run: dict[str, Any]) -> tuple[str, int]:
        return (str(run.get("run_at", "")), int(run.get("_run_index", 0)))

    return sorted(runs, key=run_sort_key)


def save_run(log_dir: Path, run_at: datetime, run_payload: dict[str, Any]) -> Path:
    ensure_log_dir(log_dir)
    log_path = benchmark_log_path(log_dir, run_at)
    if log_path.exists():
        try:
            existing = read_json(log_path)
        except json.JSONDecodeError:
            existing = {}
    else:
        existing = {}

    runs: list[dict[str, Any]] = []
    if isinstance(existing, dict) and isinstance(existing.get("runs"), list):
        runs = [item for item in existing["runs"] if isinstance(item, dict)]
    elif isinstance(existing, dict) and "results" in existing:
        runs = [existing]

    runs.append(run_payload)
    payload = {
        "date": run_at.strftime("%Y-%m-%d"),
        "updated_at": run_payload["run_at"],
        "runs": runs,
    }
    write_json(log_path, payload)
    return log_path


def resolve_run_identifier(identifier: str, log_dir: Path) -> dict[str, Any]:
    path_candidate = Path(identifier)
    if path_candidate.exists():
        runs = load_runs_from_payload(path_candidate, read_json(path_candidate))
        if not runs:
            raise RuntimeError(f"No benchmark runs found in {identifier}.")
        return runs[-1]

    if ":" in identifier:
        date_part, index_part = identifier.split(":", 1)
        try:
            index = int(index_part)
        except ValueError as exc:
            raise RuntimeError(f"Invalid run identifier: {identifier}") from exc
        path = log_dir / f"benchmark_{date_part}.json"
        if not path.exists():
            raise RuntimeError(f"Benchmark log not found for identifier: {identifier}")
        runs = load_runs_from_payload(path, read_json(path))
        if index < 0 or index >= len(runs):
            raise RuntimeError(f"Run index out of range for identifier: {identifier}")
        return runs[index]

    raise RuntimeError(f"Unsupported run identifier: {identifier}")


def compare_results(
    current_run: dict[str, Any],
    previous_run: dict[str, Any],
) -> list[dict[str, str]]:
    previous_by_model = {
        item["model_name"]: item for item in previous_run.get("results", []) if isinstance(item, dict)
    }
    rows: list[dict[str, str]] = []
    for current in current_run.get("results", []):
        model_name = current["model_name"]
        previous = previous_by_model.get(model_name)
        if previous is None:
            rows.append(
                {
                    "model_name": model_name,
                    "tokens_per_sec": format_number(current.get("tokens_per_sec")),
                    "tokens_delta": "new",
                    "quality_score": format_number(current.get("quality_score")),
                    "quality_delta": "new",
                    "vram_mb": format_number(current.get("vram_mb"), digits=0),
                    "memory_mb": format_number(current.get("memory_mb"), digits=0),
                }
            )
            continue

        tokens_delta = current.get("tokens_per_sec", 0.0) - previous.get("tokens_per_sec", 0.0)
        quality_delta = current.get("quality_score", 0.0) - previous.get("quality_score", 0.0)
        rows.append(
            {
                "model_name": model_name,
                "tokens_per_sec": format_number(current.get("tokens_per_sec")),
                "tokens_delta": f"{tokens_delta:+.2f}",
                "quality_score": format_number(current.get("quality_score")),
                "quality_delta": f"{quality_delta:+.2f}",
                "vram_mb": format_number(current.get("vram_mb"), digits=0),
                "memory_mb": format_number(current.get("memory_mb"), digits=0),
            }
        )
    return rows


def build_run_payload(
    models: list[str],
    results: list[dict[str, Any]],
    run_at: datetime,
    log_path: Path,
    previous_run: dict[str, Any] | None,
) -> dict[str, Any]:
    rows = benchmark_rows(results)
    markdown_table = render_markdown_table(
        rows,
        [
            ("model_name", "model_name"),
            ("tokens_per_sec", "tokens_per_sec"),
            ("vram_mb", "vram_mb"),
            ("quality_score", "quality_score"),
            ("memory_mb", "memory_mb"),
        ],
    )
    payload: dict[str, Any] = {
        "run_at": run_at.isoformat(timespec="seconds"),
        "models": models,
        "prompt_count": len(STANDARD_PROMPTS),
        "results": results,
        "report_markdown": markdown_table,
        "log_path": str(log_path),
    }
    if previous_run is not None:
        payload["comparison_to_previous"] = {
            "previous_run_at": previous_run.get("run_at"),
            "rows": compare_results(payload, previous_run),
        }
    return payload


def run_benchmarks(root: Path, log_dir: Path, models: list[str] | None = None) -> dict[str, Any]:
    run_at = datetime.now()
    selected_models = models or list_models()
    existing_runs = load_history(log_dir)
    previous_run = existing_runs[-1] if existing_runs else None

    results = [benchmark_model(model_name) for model_name in selected_models]
    log_path = benchmark_log_path(log_dir, run_at)
    run_payload = build_run_payload(selected_models, results, run_at, log_path, previous_run)
    saved_path = save_run(log_dir, run_at, run_payload)
    run_payload["log_path"] = str(saved_path)
    return run_payload


def latest_two_runs(log_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    runs = load_history(log_dir)
    if len(runs) < 2:
        raise RuntimeError("Need at least two benchmark runs to compare history.")
    return runs[-1], runs[-2]


def print_run_report(run_payload: dict[str, Any]) -> None:
    print(f"Run at: {run_payload['run_at']}")
    print(f"Saved to: {run_payload['log_path']}")
    print(run_payload["report_markdown"])
    comparison = run_payload.get("comparison_to_previous")
    if comparison:
        print()
        print(f"Compared to previous run: {comparison['previous_run_at']}")
        print(
            render_markdown_table(
                comparison["rows"],
                [
                    ("model_name", "model_name"),
                    ("tokens_per_sec", "tokens_per_sec"),
                    ("tokens_delta", "tokens_delta"),
                    ("quality_score", "quality_score"),
                    ("quality_delta", "quality_delta"),
                    ("vram_mb", "vram_mb"),
                    ("memory_mb", "memory_mb"),
                ],
            )
        )


def print_compare_report(current_run: dict[str, Any], previous_run: dict[str, Any]) -> None:
    print(f"Current run: {current_run.get('run_at', 'unknown')}")
    print(f"Previous run: {previous_run.get('run_at', 'unknown')}")
    rows = compare_results(current_run, previous_run)
    print(
        render_markdown_table(
            rows,
            [
                ("model_name", "model_name"),
                ("tokens_per_sec", "tokens_per_sec"),
                ("tokens_delta", "tokens_delta"),
                ("quality_score", "quality_score"),
                ("quality_delta", "quality_delta"),
                ("vram_mb", "vram_mb"),
                ("memory_mb", "memory_mb"),
            ],
        )
    )


def build_history_rows(runs: list[dict[str, Any]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for run in runs:
        results = [item for item in run.get("results", []) if isinstance(item, dict)]
        avg_tps = (
            sum(float(item.get("tokens_per_sec", 0.0)) for item in results) / len(results)
            if results
            else 0.0
        )
        avg_quality = (
            sum(float(item.get("quality_score", 0.0)) for item in results) / len(results)
            if results
            else 0.0
        )
        run_identifier = f"{Path(str(run.get('_source_path', ''))).stem.replace('benchmark_', '')}:{run.get('_run_index', 0)}"
        rows.append(
            {
                "run_id": run_identifier,
                "run_at": str(run.get("run_at", "")),
                "models": str(len(results)),
                "avg_tokens_per_sec": format_number(avg_tps),
                "avg_quality_score": format_number(avg_quality),
            }
        )
    return rows


def print_history_report(runs: list[dict[str, Any]], limit: int) -> None:
    selected = runs[-limit:] if limit > 0 else runs
    if not selected:
        print("No benchmark history found.")
        return
    rows = build_history_rows(selected)
    print(
        render_markdown_table(
            rows,
            [
                ("run_id", "run_id"),
                ("run_at", "run_at"),
                ("models", "models"),
                ("avg_tokens_per_sec", "avg_tokens_per_sec"),
                ("avg_quality_score", "avg_quality_score"),
            ],
        )
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = Path.cwd()
    log_dir = (root / args.log_dir).resolve()

    try:
        if args.command == "run":
            run_payload = run_benchmarks(root=root, log_dir=log_dir, models=args.models)
            print_run_report(run_payload)
            return 0

        if args.command == "compare":
            if args.current or args.previous:
                if not args.current or not args.previous:
                    raise SystemExit("Both --current and --previous are required when either is provided.")
                current_run = resolve_run_identifier(args.current, log_dir)
                previous_run = resolve_run_identifier(args.previous, log_dir)
            else:
                current_run, previous_run = latest_two_runs(log_dir)
            print_compare_report(current_run, previous_run)
            return 0

        if args.command == "history":
            runs = load_history(log_dir)
            print_history_report(runs, args.limit)
            return 0
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    raise SystemExit(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    sys.exit(main())
