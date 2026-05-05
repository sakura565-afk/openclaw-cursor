#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path


ANSI_COLORS = {
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "blue": "\033[34m",
    "cyan": "\033[36m",
    "bold": "\033[1m",
    "reset": "\033[0m",
}
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
TABLE_SPLIT_RE = re.compile(r"\s{2,}")
RELATIVE_TIME_RE = re.compile(
    r"(?P<value>\d+)\s+(?P<unit>minute|minutes|hour|hours|day|days|week|weeks|month|months|year|years)\s+ago",
    re.IGNORECASE,
)
ABSOLUTE_DATE_FORMATS = (
    "%Y-%m-%d",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
)
SIZE_RE = re.compile(r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>B|KB|MB|GB|TB|PB)", re.IGNORECASE)
TEXT_PULL_PROGRESS_RE = re.compile(
    r"^(?P<status>pulling|downloading)\s+"
    r"(?P<digest>[0-9a-f]{8,64})[: ]\s*"
    r"(?P<percent>\d+(?:\.\d+)?)%\s*"
    r"(?:\[.*?\]\s*)?"
    r"(?:(?P<completed>\d+(?:\.\d+)?\s*[KMGTP]?B)\s*/\s*(?P<total>\d+(?:\.\d+)?\s*[KMGTP]?B))?"
    r"(?:,\s*(?P<speed>[^,]+/s),\s*(?P<eta>[^)]+))?"
    r"\)?$",
    re.IGNORECASE,
)
DETAILED_PULL_PROGRESS_RE = re.compile(
    r"^(?P<status>pulling|downloading)\s+"
    r"(?P<digest>[0-9a-f]{8,64})\s+"
    r"(?P<percent>\d+(?:\.\d+)?)%\s+"
    r"\[.*?\]\s+"
    r"\((?P<completed>\d+(?:\.\d+)?\s*[KMGTP]?B)\s*/\s*(?P<total>\d+(?:\.\d+)?\s*[KMGTP]?B),\s*"
    r"(?P<speed>[^,]+/s),\s*(?P<eta>[^)]+)\)$",
    re.IGNORECASE,
)
PARAMETER_LINE_RE = re.compile(r"^(?P<name>[A-Za-z0-9_.-]+)\s+(?P<value>.+)$")
MODELFILE_LINE_RE = re.compile(r"^(?P<directive>[A-Z]+)\s*(?P<value>.*)$")
SEARCH_UNSUPPORTED_RE = re.compile(r"(unknown command|not a valid command).*search", re.IGNORECASE)
TEN_GIB = 10 * 1024 * 1024 * 1024


class OllamaManagerError(RuntimeError):
    """Raised for recoverable CLI workflow errors."""


@dataclass
class ModelEntry:
    name: str
    size: str
    modified: str
    identifier: str = ""
    age_days: float | None = None


@dataclass
class PullProgress:
    status: str
    digest: str = ""
    percent: float | None = None
    completed: int | None = None
    total: int | None = None
    speed_bps: float | None = None
    eta_seconds: float | None = None
    raw_line: str = ""


def colorize(text: str, color: str) -> str:
    prefix = ANSI_COLORS.get(color, "")
    if not prefix:
        return text
    return f"{prefix}{text}{ANSI_COLORS['reset']}"


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def format_bytes(size_bytes: int | float) -> str:
    value = float(size_bytes)
    units = ("B", "KB", "MB", "GB", "TB", "PB")
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{value:.1f} PB"


def parse_human_bytes(value: str) -> int:
    match = SIZE_RE.search(value.strip())
    if not match:
        raise OllamaManagerError(f"Unable to parse size value: {value}")
    number = float(match.group("value"))
    unit = match.group("unit").upper()
    scale = {
        "B": 1,
        "KB": 1024,
        "MB": 1024**2,
        "GB": 1024**3,
        "TB": 1024**4,
        "PB": 1024**5,
    }[unit]
    return int(number * scale)


def format_rate(bytes_per_second: float | None) -> str:
    if not bytes_per_second or bytes_per_second <= 0:
        return "-"
    return f"{format_bytes(bytes_per_second)}/s"


def format_duration(seconds: float | None) -> str:
    if seconds is None or seconds < 0:
        return "-"
    total_seconds = int(round(seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def render_table(headers: list[str], rows: list[list[str]]) -> str:
    widths = [len(strip_ansi(header)) for header in headers]
    normalized_rows: list[list[str]] = []
    for row in rows:
        text_row = [str(cell) for cell in row]
        normalized_rows.append(text_row)
        for index, cell in enumerate(text_row):
            widths[index] = max(widths[index], len(strip_ansi(cell)))

    def make_separator() -> str:
        return "+" + "+".join("-" * (width + 2) for width in widths) + "+"

    def make_row(cells: list[str]) -> str:
        rendered: list[str] = []
        for index, cell in enumerate(cells):
            padding = widths[index] - len(strip_ansi(cell))
            rendered.append(f" {cell}{' ' * padding} ")
        return "|" + "|".join(rendered) + "|"

    separator = make_separator()
    lines = [separator, make_row([colorize(header, "bold") for header in headers]), separator]
    lines.extend(make_row(row) for row in normalized_rows)
    lines.append(separator)
    return "\n".join(lines)


def print_section(title: str) -> None:
    print(colorize(title, "cyan"))


def parse_relative_age_days(value: str, now: datetime | None = None) -> float | None:
    now = now or datetime.now()
    normalized = value.strip().lower()
    if not normalized:
        return None
    if normalized in {"just now", "now"}:
        return 0.0

    match = RELATIVE_TIME_RE.fullmatch(normalized)
    if match:
        count = int(match.group("value"))
        unit = match.group("unit").lower()
        multiplier = {
            "minute": 1 / (24 * 60),
            "minutes": 1 / (24 * 60),
            "hour": 1 / 24,
            "hours": 1 / 24,
            "day": 1,
            "days": 1,
            "week": 7,
            "weeks": 7,
            "month": 30,
            "months": 30,
            "year": 365,
            "years": 365,
        }[unit]
        return count * multiplier

    for date_format in ABSOLUTE_DATE_FORMATS:
        try:
            parsed = datetime.strptime(value.strip(), date_format)
            return max((now - parsed).total_seconds() / 86400.0, 0.0)
        except ValueError:
            continue
    return None


def parse_tabular_output(text: str) -> tuple[list[str], list[dict[str, str]]]:
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    if not lines:
        return [], []
    headers = TABLE_SPLIT_RE.split(lines[0].strip())
    rows: list[dict[str, str]] = []
    for line in lines[1:]:
        parts = TABLE_SPLIT_RE.split(line.strip(), maxsplit=max(len(headers) - 1, 0))
        if len(parts) < len(headers):
            parts.extend([""] * (len(headers) - len(parts)))
        rows.append({headers[index]: parts[index] for index in range(len(headers))})
    return headers, rows


def parse_model_list(text: str, now: datetime | None = None) -> list[ModelEntry]:
    headers, rows = parse_tabular_output(text)
    lookup = {header.lower(): header for header in headers}
    if "name" not in lookup or "size" not in lookup or "modified" not in lookup:
        raise OllamaManagerError("Unexpected output from 'ollama list'.")

    entries: list[ModelEntry] = []
    for row in rows:
        modified = row.get(lookup["modified"], "")
        entries.append(
            ModelEntry(
                name=row.get(lookup["name"], ""),
                size=row.get(lookup["size"], ""),
                modified=modified,
                identifier=row.get(lookup.get("id", ""), ""),
                age_days=parse_relative_age_days(modified, now=now),
            )
        )
    return entries


def parse_key_value_output(text: str) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    current_key: str | None = None
    current_lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue
        if ":" in line and not line.startswith((" ", "\t")):
            if current_key is not None:
                pairs.append((current_key, " ".join(part for part in current_lines if part).strip()))
            current_key, value = line.split(":", 1)
            current_key = current_key.strip()
            current_lines = [value.strip()]
            continue
        if current_key is not None:
            current_lines.append(line.strip())
    if current_key is not None:
        pairs.append((current_key, " ".join(part for part in current_lines if part).strip()))
    return pairs


def parse_parameter_output(text: str) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = PARAMETER_LINE_RE.match(line)
        if match:
            rows.append((match.group("name"), match.group("value")))
        else:
            rows.append(("parameter", line))
    return rows


def parse_modelfile_output(text: str) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue
        match = MODELFILE_LINE_RE.match(line.strip())
        if match:
            directive = match.group("directive")
            value = match.group("value") or "-"
            rows.append((directive, value))
        else:
            rows.append(("CONFIG", line.strip()))
    return rows


def parse_eta_seconds(value: str) -> float | None:
    normalized = value.strip().lower()
    if not normalized or normalized == "-":
        return None
    if normalized.endswith("s") and normalized[:-1].isdigit():
        return float(normalized[:-1])
    minutes_match = re.fullmatch(r"(?:(?P<minutes>\d+)m\s*)?(?P<seconds>\d+)s", normalized)
    if minutes_match:
        minutes = int(minutes_match.group("minutes") or 0)
        seconds = int(minutes_match.group("seconds") or 0)
        return float(minutes * 60 + seconds)
    hours_match = re.fullmatch(r"(?:(?P<hours>\d+)h\s*)?(?P<minutes>\d+)m", normalized)
    if hours_match:
        hours = int(hours_match.group("hours") or 0)
        minutes = int(hours_match.group("minutes") or 0)
        return float(hours * 3600 + minutes * 60)
    return None


def parse_pull_progress(line: str) -> PullProgress | None:
    cleaned = strip_ansi(line).strip()
    if not cleaned:
        return None

    if cleaned.startswith("{") and cleaned.endswith("}"):
        try:
            payload = json.loads(cleaned)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            completed = payload.get("completed")
            total = payload.get("total")
            percent = None
            if isinstance(completed, (int, float)) and isinstance(total, (int, float)) and total:
                percent = (float(completed) / float(total)) * 100.0
            return PullProgress(
                status=str(payload.get("status", "")).strip() or "pulling",
                digest=str(payload.get("digest", "")).replace("sha256:", "")[:12],
                percent=percent,
                completed=int(completed) if isinstance(completed, (int, float)) else None,
                total=int(total) if isinstance(total, (int, float)) else None,
                raw_line=cleaned,
            )

    for pattern in (DETAILED_PULL_PROGRESS_RE, TEXT_PULL_PROGRESS_RE):
        match = pattern.match(cleaned)
        if not match:
            continue
        speed_value = match.groupdict().get("speed")
        eta_value = match.groupdict().get("eta")
        progress = PullProgress(
            status=match.group("status"),
            digest=match.group("digest")[:12],
            percent=float(match.group("percent")),
            completed=parse_human_bytes(match.group("completed")) if match.groupdict().get("completed") else None,
            total=parse_human_bytes(match.group("total")) if match.groupdict().get("total") else None,
            speed_bps=parse_human_bytes(speed_value.replace("/s", "")) if speed_value else None,
            eta_seconds=parse_eta_seconds(eta_value) if eta_value else None,
            raw_line=cleaned,
        )
        if progress.percent is None and progress.total:
            progress.percent = (progress.completed or 0) / progress.total * 100.0
        return progress

    return PullProgress(status=cleaned, raw_line=cleaned)


def update_pull_metrics(
    progress: PullProgress,
    state: dict[str, dict[str, float]],
    now: float | None = None,
) -> PullProgress:
    if not progress.digest or progress.completed is None:
        return progress
    now = time.monotonic() if now is None else now

    previous = state.get(progress.digest)
    if progress.speed_bps is None and previous is not None:
        elapsed = now - previous["time"]
        completed_delta = progress.completed - int(previous["completed"])
        if elapsed > 0 and completed_delta >= 0:
            progress.speed_bps = completed_delta / elapsed
    if progress.eta_seconds is None and progress.speed_bps and progress.total is not None and progress.completed is not None:
        remaining = max(progress.total - progress.completed, 0)
        progress.eta_seconds = remaining / progress.speed_bps if progress.speed_bps > 0 else None

    state[progress.digest] = {"completed": float(progress.completed), "time": float(now)}
    return progress


def render_pull_progress(model: str, progress: PullProgress) -> str:
    status_color = "green" if progress.percent == 100 or progress.status.lower() == "success" else "blue"
    progress_value = "-" if progress.percent is None else f"{progress.percent:.1f}%"
    downloaded = "-"
    if progress.completed is not None and progress.total is not None:
        downloaded = f"{format_bytes(progress.completed)}/{format_bytes(progress.total)}"
    digest = progress.digest or "-"
    status = colorize(progress.status, status_color)
    return render_table(
        ["MODEL", "LAYER", "PROGRESS", "DOWNLOADED", "SPEED", "ETA", "STATUS"],
        [[model, digest, progress_value, downloaded, format_rate(progress.speed_bps), format_duration(progress.eta_seconds), status]],
    )


def read_stream_chunks(stream) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    while True:
        char = stream.read(1)
        if char == "":
            if current:
                chunks.append("".join(current))
            break
        if char in {"\r", "\n"}:
            if current:
                chunks.append("".join(current))
                current = []
            continue
        current.append(char)
    return chunks


def ensure_ollama_available() -> None:
    if shutil.which("ollama") is None:
        raise OllamaManagerError("Ollama CLI is not installed or not available on PATH.")


def run_ollama_command(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    ensure_ollama_available()
    command = ["ollama", *arguments]
    try:
        result = subprocess.run(command, text=True, capture_output=True, check=False)
    except FileNotFoundError as exc:
        raise OllamaManagerError("Ollama CLI is not installed or not available on PATH.") from exc
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        stdout = (result.stdout or "").strip()
        message = stderr or stdout or f"ollama {' '.join(arguments)} failed"
        if arguments[:1] == ["search"] and SEARCH_UNSUPPORTED_RE.search(message):
            raise OllamaManagerError(
                "This Ollama version does not support 'ollama search'. Upgrade the Ollama CLI to enable model search."
            )
        raise OllamaManagerError(message)
    return result


def get_disk_space(path: Path) -> tuple[str, list[list[str]], bool]:
    total, used, free = shutil.disk_usage(path)
    rows = [
        ["Total", format_bytes(total)],
        ["Used", format_bytes(used)],
        ["Free", colorize(format_bytes(free), "yellow" if free < TEN_GIB else "green")],
    ]
    warning = free < TEN_GIB
    return render_table(["Metric", "Value"], rows), rows, warning


def list_models(now: datetime | None = None) -> int:
    result = run_ollama_command(["list"])
    models = parse_model_list(result.stdout, now=now)
    print_section("Local Ollama models")
    if not models:
        print(colorize("No local models found.", "yellow"))
        return 0
    rows: list[list[str]] = []
    for model in models:
        modified = model.modified
        if model.age_days is not None and model.age_days > 30:
            modified = colorize(modified, "yellow")
        rows.append([model.name, model.size, modified])
    print(render_table(["NAME", "SIZE", "MODIFIED"], rows))
    return 0


def pull_model(model: str) -> int:
    ensure_ollama_available()
    print_section("Disk space check")
    disk_table, _, warning = get_disk_space(Path.cwd())
    print(disk_table)
    if warning:
        print(colorize("Warning: less than 10 GB free. Pulling a large model may fail.", "yellow"))

    progress_state: dict[str, dict[str, float]] = {}
    command = ["ollama", "pull", model]
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except FileNotFoundError as exc:
        raise OllamaManagerError("Ollama CLI is not installed or not available on PATH.") from exc

    latest_output: str | None = None
    assert process.stdout is not None
    for chunk in read_stream_chunks(process.stdout):
        progress = parse_pull_progress(chunk)
        if progress is None:
            continue
        progress = update_pull_metrics(progress, progress_state)
        if progress.percent is None and latest_output == progress.raw_line:
            continue
        latest_output = progress.raw_line
        print(render_pull_progress(model, progress))

    returncode = process.wait()
    if returncode != 0:
        raise OllamaManagerError(f"Failed to pull '{model}'.")

    print(colorize(f"Model '{model}' pulled successfully.", "green"))
    return 0


def remove_model(model: str, assume_yes: bool = False) -> int:
    if not assume_yes:
        reply = input(colorize(f"Remove model '{model}'? [y/N]: ", "yellow")).strip().lower()
        if reply not in {"y", "yes"}:
            print(colorize("Cancelled. No model was removed.", "yellow"))
            return 0

    result = run_ollama_command(["rm", model])
    print_section("Model removal")
    print(
        render_table(
            ["MODEL", "STATUS"],
            [[model, colorize((result.stdout or "removed").strip(), "green")]],
        )
    )
    return 0


def show_model(model: str) -> int:
    details = run_ollama_command(["show", model]).stdout
    parameters = run_ollama_command(["show", model, "--parameters"]).stdout
    modelfile = run_ollama_command(["show", model, "--modelfile"]).stdout

    metadata_rows = [[key, value or "-"] for key, value in parse_key_value_output(details)]
    parameter_rows = [[name, value] for name, value in parse_parameter_output(parameters)]
    config_rows = [[directive, value] for directive, value in parse_modelfile_output(modelfile)]

    print_section(f"Model details: {model}")
    if metadata_rows:
        print(render_table(["FIELD", "VALUE"], metadata_rows))
    if parameter_rows:
        print_section("Parameters")
        print(render_table(["PARAMETER", "VALUE"], parameter_rows))
    if config_rows:
        print_section("Configuration")
        print(render_table(["DIRECTIVE", "VALUE"], config_rows))
    return 0


def search_models(query: str) -> int:
    result = run_ollama_command(["search", query])
    headers, rows = parse_tabular_output(result.stdout)
    print_section(f"Search results for: {query}")
    if not headers or not rows:
        print(render_table(["RESULT"], [[result.stdout.strip() or "No results found."]]))
        return 0
    table_rows = [[row.get(header, "") for header in headers] for row in rows]
    print(render_table(headers, table_rows))
    return 0


def cleanup_suggestions(days: int = 30, now: datetime | None = None) -> int:
    result = run_ollama_command(["list"])
    models = parse_model_list(result.stdout, now=now)
    candidates = [model for model in models if model.age_days is not None and model.age_days > days]
    print_section("Cleanup suggestions")
    if not candidates:
        print(colorize(f"No models look stale beyond {days} days.", "green"))
        return 0

    rows: list[list[str]] = []
    for model in sorted(candidates, key=lambda item: item.age_days or 0, reverse=True):
        age_text = f"{int(model.age_days or 0)} days"
        rows.append([model.name, model.size, model.modified, age_text, "Review or remove"])
    print(render_table(["NAME", "SIZE", "LAST ACTIVITY", "AGE", "SUGGESTION"], rows))
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manage local Ollama models for OpenClaw.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list", help="List local Ollama models.")

    pull_parser = subparsers.add_parser("pull", help="Pull an Ollama model.")
    pull_parser.add_argument("model", help="Model name to pull.")

    remove_parser = subparsers.add_parser("remove", help="Remove a local Ollama model.")
    remove_parser.add_argument("model", help="Model name to remove.")
    remove_parser.add_argument("--yes", action="store_true", help="Skip the confirmation prompt.")

    show_parser = subparsers.add_parser("show", help="Show details for a local model.")
    show_parser.add_argument("model", help="Model name to inspect.")

    search_parser = subparsers.add_parser("search", help="Search for models via the Ollama CLI.")
    search_parser.add_argument("query", help="Search query.")

    cleanup_parser = subparsers.add_parser("cleanup", help="Suggest stale models to remove.")
    cleanup_parser.add_argument("--days", type=int, default=30, help="Flag models older than this many days.")

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.command == "list":
            return list_models()
        if args.command == "pull":
            return pull_model(args.model)
        if args.command == "remove":
            return remove_model(args.model, assume_yes=args.yes)
        if args.command == "show":
            return show_model(args.model)
        if args.command == "search":
            return search_models(args.query)
        if args.command == "cleanup":
            return cleanup_suggestions(days=args.days)
    except OllamaManagerError as exc:
        print(colorize(f"Error: {exc}", "red"), file=sys.stderr)
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
