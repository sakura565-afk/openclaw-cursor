#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib import error, request


OLLAMA_TAGS_URL = "http://localhost:11434/api/tags"
HEALTH_CHECK_INTERVAL_SECONDS = 5 * 60
VRAM_CHECK_INTERVAL_SECONDS = 60
HEALTH_CHECK_TIMEOUT_SECONDS = 5
RESTART_STARTUP_TIMEOUT_SECONDS = 60
VRAM_THRESHOLD_RATIO = 0.90
STATE_FILE_NAME = "ollama_monitor_state.json"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def isoformat_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def format_duration(seconds: int) -> str:
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:d}h {minutes:02d}m {seconds:02d}s"


def logs_dir(root: Path) -> Path:
    return root / "logs"


def state_path(root: Path) -> Path:
    return logs_dir(root) / STATE_FILE_NAME


def stderr_log_path(root: Path, now: datetime) -> Path:
    return logs_dir(root) / f"ollama_{now.strftime('%Y%m%d')}.stderr.log"


def daily_log_path(root: Path, now: datetime) -> Path:
    return logs_dir(root) / f"ollama_{now.strftime('%Y%m%d')}.json"


def ensure_logs_dir(root: Path) -> Path:
    path = logs_dir(root)
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_state() -> dict:
    return {
        "managed_pid": None,
        "ollama_start_time": None,
        "total_uptime_seconds": 0,
        "restarts_count": 0,
        "stderr_log_path": None,
        "stderr_log_position": 0,
        "last_health": None,
        "last_vram": None,
        "last_error": None,
        "updated_at": None,
    }


def load_json_file(path: Path, fallback: dict) -> dict:
    if not path.exists():
        return dict(fallback)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return dict(fallback)
    if not isinstance(data, dict):
        return dict(fallback)
    merged = dict(fallback)
    merged.update(data)
    return merged


def summarize_state(state: dict, now: datetime | None = None) -> dict:
    now = now or utc_now()
    start_time = parse_timestamp(state.get("ollama_start_time"))
    current_uptime_seconds = 0
    if start_time is not None:
        current_uptime_seconds = max(0, int((now - start_time).total_seconds()))

    total_uptime_seconds = int(state.get("total_uptime_seconds", 0)) + current_uptime_seconds
    managed_pid = state.get("managed_pid")
    return {
        "managed_pid": managed_pid,
        "managed_pid_running": is_pid_running(managed_pid) if managed_pid else False,
        "ollama_start_time": state.get("ollama_start_time"),
        "current_uptime_seconds": current_uptime_seconds,
        "current_uptime_human": format_duration(current_uptime_seconds),
        "total_uptime_seconds": total_uptime_seconds,
        "total_uptime_human": format_duration(total_uptime_seconds),
        "restarts_count": int(state.get("restarts_count", 0)),
        "last_health": state.get("last_health"),
        "last_vram": state.get("last_vram"),
        "last_error": state.get("last_error"),
        "updated_at": state.get("updated_at"),
    }


def load_daily_log(root: Path, now: datetime | None = None) -> tuple[Path, dict]:
    now = now or utc_now()
    path = daily_log_path(root, now)
    payload = load_json_file(path, {"date": now.strftime("%Y-%m-%d"), "events": [], "summary": {}})
    if not isinstance(payload.get("events"), list):
        payload["events"] = []
    if not isinstance(payload.get("summary"), dict):
        payload["summary"] = {}
    return path, payload


def write_daily_log(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def append_log_event(root: Path, event: str, details: dict | None = None, now: datetime | None = None) -> Path:
    now = now or utc_now()
    path, payload = load_daily_log(root, now)
    payload["events"].append(
        {
            "timestamp": isoformat_timestamp(now),
            "event": event,
            "details": details or {},
        }
    )
    write_daily_log(path, payload)
    return path


def load_state(root: Path) -> dict:
    ensure_logs_dir(root)
    return load_json_file(state_path(root), default_state())


def save_state(root: Path, state: dict, now: datetime | None = None) -> dict:
    now = now or utc_now()
    ensure_logs_dir(root)
    state = dict(default_state(), **state)
    state["updated_at"] = isoformat_timestamp(now)
    state_path(root).write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    log_path, payload = load_daily_log(root, now)
    payload["summary"] = summarize_state(state, now)
    write_daily_log(log_path, payload)
    return state


def is_pid_running(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except (OSError, ValueError):
        return False
    return True


def sync_stderr_to_json(root: Path, state: dict, now: datetime | None = None) -> tuple[dict, list[str]]:
    now = now or utc_now()
    stderr_path_value = state.get("stderr_log_path")
    if not stderr_path_value:
        return state, []

    path = Path(stderr_path_value)
    if not path.is_absolute():
        path = root / path
    if not path.exists():
        return state, []

    position = int(state.get("stderr_log_position", 0) or 0)
    size = path.stat().st_size
    if size <= position:
        return state, []

    with path.open("r", encoding="utf-8", errors="replace") as handle:
        handle.seek(position)
        chunk = handle.read()

    state["stderr_log_position"] = size
    lines = [line.strip() for line in chunk.splitlines() if line.strip()]
    for line in lines:
        append_log_event(
            root,
            "stderr",
            {
                "line": line,
                "source": str(path.relative_to(root) if path.is_relative_to(root) else path),
            },
            now=now,
        )
    return state, lines


def finalize_current_uptime(state: dict, now: datetime | None = None) -> dict:
    now = now or utc_now()
    start_time = parse_timestamp(state.get("ollama_start_time"))
    if start_time is None:
        return state
    elapsed = max(0, int((now - start_time).total_seconds()))
    state["total_uptime_seconds"] = int(state.get("total_uptime_seconds", 0)) + elapsed
    state["ollama_start_time"] = None
    return state


def mark_service_healthy(state: dict, now: datetime | None = None, inferred: bool = False) -> dict:
    now = now or utc_now()
    if state.get("ollama_start_time") is None:
        state["ollama_start_time"] = isoformat_timestamp(now)
        if inferred:
            state["last_error"] = None
    return state


def check_health(
    url: str = OLLAMA_TAGS_URL,
    timeout: float = HEALTH_CHECK_TIMEOUT_SECONDS,
) -> dict:
    checked_at = isoformat_timestamp(utc_now())
    try:
        with request.urlopen(url, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            try:
                payload = json.loads(body or "{}")
            except json.JSONDecodeError:
                payload = {}
            models = payload.get("models", [])
            return {
                "checked_at": checked_at,
                "healthy": 200 <= getattr(response, "status", 200) < 300,
                "status_code": getattr(response, "status", 200),
                "model_count": len(models) if isinstance(models, list) else None,
                "error_type": None,
                "error": None,
            }
    except error.HTTPError as exc:
        return {
            "checked_at": checked_at,
            "healthy": False,
            "status_code": exc.code,
            "model_count": None,
            "error_type": "http_error",
            "error": str(exc),
        }
    except error.URLError as exc:
        reason = exc.reason
        error_type = "connection_error"
        if isinstance(reason, (socket.timeout, TimeoutError)):
            error_type = "timeout"
        elif isinstance(reason, ConnectionRefusedError):
            error_type = "connection_refused"
        elif isinstance(reason, OSError) and getattr(reason, "errno", None) == 111:
            error_type = "connection_refused"
        return {
            "checked_at": checked_at,
            "healthy": False,
            "status_code": None,
            "model_count": None,
            "error_type": error_type,
            "error": str(reason),
        }
    except TimeoutError as exc:
        return {
            "checked_at": checked_at,
            "healthy": False,
            "status_code": None,
            "model_count": None,
            "error_type": "timeout",
            "error": str(exc),
        }


def check_vram() -> dict:
    checked_at = isoformat_timestamp(utc_now())
    command = [
        "nvidia-smi",
        "--query-gpu=index,memory.used,memory.total",
        "--format=csv,noheader,nounits",
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        return {
            "checked_at": checked_at,
            "available": False,
            "over_threshold": False,
            "threshold_ratio": VRAM_THRESHOLD_RATIO,
            "gpus": [],
            "error": "nvidia-smi not found",
        }

    if result.returncode != 0:
        return {
            "checked_at": checked_at,
            "available": False,
            "over_threshold": False,
            "threshold_ratio": VRAM_THRESHOLD_RATIO,
            "gpus": [],
            "error": (result.stderr or result.stdout).strip() or "nvidia-smi failed",
        }

    gpus: list[dict] = []
    over_threshold = False
    for raw_line in result.stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = [part.strip() for part in line.split(",")]
        if len(parts) != 3:
            continue
        try:
            gpu_index = int(parts[0])
            used_mb = int(parts[1])
            total_mb = int(parts[2])
        except ValueError:
            continue
        ratio = float(used_mb) / float(total_mb) if total_mb else 0.0
        gpus.append(
            {
                "index": gpu_index,
                "used_mb": used_mb,
                "total_mb": total_mb,
                "usage_ratio": round(ratio, 4),
                "usage_percent": round(ratio * 100, 2),
            }
        )
        if ratio > VRAM_THRESHOLD_RATIO:
            over_threshold = True

    return {
        "checked_at": checked_at,
        "available": bool(gpus),
        "over_threshold": over_threshold,
        "threshold_ratio": VRAM_THRESHOLD_RATIO,
        "gpus": gpus,
        "error": None if gpus else "no GPU data returned",
    }


def stop_managed_ollama(
    root: Path,
    state: dict,
    now: datetime | None = None,
    sleep_func=None,
) -> dict:
    now = now or utc_now()
    sleep_func = sleep_func or time.sleep
    pid = state.get("managed_pid")
    if not pid:
        return state

    if is_pid_running(pid):
        os.kill(int(pid), signal.SIGTERM)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if not is_pid_running(pid):
                break
            sleep_func(0.25)
        if is_pid_running(pid):
            os.kill(int(pid), signal.SIGKILL)

    state = finalize_current_uptime(state, now)
    state["managed_pid"] = None
    state["last_error"] = None
    append_log_event(root, "ollama_stopped", {"pid": pid}, now=now)
    return save_state(root, state, now=now)


def start_ollama_process(
    root: Path,
    state: dict | None = None,
    now: datetime | None = None,
    popen=None,
    health_func=None,
    sleep_func=None,
    startup_timeout: int = RESTART_STARTUP_TIMEOUT_SECONDS,
) -> tuple[dict, dict]:
    now = now or utc_now()
    popen = popen or subprocess.Popen
    health_func = health_func or check_health
    sleep_func = sleep_func or time.sleep
    state = dict(load_state(root) if state is None else state)
    ensure_logs_dir(root)

    stderr_path = stderr_log_path(root, now)
    stderr_start = stderr_path.stat().st_size if stderr_path.exists() else 0
    with stderr_path.open("a", encoding="utf-8") as stderr_handle:
        process = popen(
            ["ollama", "serve"],
            cwd=str(root),
            stdout=subprocess.DEVNULL,
            stderr=stderr_handle,
            start_new_session=True,
        )

    state["managed_pid"] = process.pid
    state["stderr_log_path"] = str(stderr_path)
    state["stderr_log_position"] = stderr_start
    state["last_error"] = None
    state = save_state(root, state, now=now)

    deadline = time.monotonic() + startup_timeout
    last_health = None
    while time.monotonic() < deadline:
        last_health = health_func()
        state["last_health"] = last_health
        if last_health.get("healthy"):
            state["restarts_count"] = int(state.get("restarts_count", 0)) + 1
            state["ollama_start_time"] = isoformat_timestamp(now)
            state["last_error"] = None
            state = save_state(root, state, now=now)
            append_log_event(
                root,
                "ollama_restart",
                {"pid": process.pid, "health": last_health},
                now=now,
            )
            return state, {
                "started": True,
                "pid": process.pid,
                "health": last_health,
            }

        if process.poll() is not None:
            state, stderr_lines = sync_stderr_to_json(root, state, now=now)
            state["managed_pid"] = None
            state["last_error"] = "ollama serve exited during startup"
            state = save_state(root, state, now=now)
            append_log_event(
                root,
                "ollama_restart_failed",
                {
                    "returncode": process.returncode,
                    "health": last_health,
                    "stderr_lines": stderr_lines,
                },
                now=now,
            )
            return state, {
                "started": False,
                "pid": process.pid,
                "returncode": process.returncode,
                "health": last_health,
            }

        sleep_func(1)

    state, stderr_lines = sync_stderr_to_json(root, state, now=now)
    if is_pid_running(process.pid):
        os.kill(process.pid, signal.SIGTERM)
    state["managed_pid"] = None
    state["last_error"] = "startup timeout waiting for healthy Ollama"
    state = save_state(root, state, now=now)
    append_log_event(
        root,
        "ollama_restart_failed",
        {
            "reason": "startup_timeout",
            "health": last_health,
            "stderr_lines": stderr_lines,
        },
        now=now,
    )
    return state, {
        "started": False,
        "pid": process.pid,
        "reason": "startup_timeout",
        "health": last_health,
    }


def restart_ollama(
    root: Path,
    now: datetime | None = None,
    popen=None,
    health_func=None,
    sleep_func=None,
) -> dict:
    now = now or utc_now()
    popen = popen or subprocess.Popen
    health_func = health_func or check_health
    sleep_func = sleep_func or time.sleep
    state = load_state(root)
    state, _ = sync_stderr_to_json(root, state, now=now)

    if state.get("managed_pid") and is_pid_running(state.get("managed_pid")):
        state = stop_managed_ollama(root, state, now=now, sleep_func=sleep_func)
    else:
        current_health = health_func()
        state["last_health"] = current_health
        if current_health.get("healthy"):
            state = mark_service_healthy(state, now=now, inferred=True)
            state = save_state(root, state, now=now)
            append_log_event(
                root,
                "restart_skipped",
                {"reason": "healthy_unmanaged_service_detected", "health": current_health},
                now=now,
            )
            return {
                "restarted": False,
                "reason": "healthy_unmanaged_service_detected",
                "health": current_health,
                "state": summarize_state(state, now=now),
            }
        state = finalize_current_uptime(state, now=now)
        state = save_state(root, state, now=now)

    state, result = start_ollama_process(
        root,
        state=state,
        now=now,
        popen=popen,
        health_func=health_func,
        sleep_func=sleep_func,
    )
    return {
        "restarted": result.get("started", False),
        "result": result,
        "state": summarize_state(state, now=now),
    }


def gather_status(
    root: Path,
    now: datetime | None = None,
    health_func=None,
    vram_func=None,
) -> dict:
    now = now or utc_now()
    health_func = health_func or check_health
    vram_func = vram_func or check_vram
    state = load_state(root)
    state, stderr_lines = sync_stderr_to_json(root, state, now=now)

    health = health_func()
    state["last_health"] = health
    if health.get("healthy"):
        state = mark_service_healthy(state, now=now, inferred=True)
        state["last_error"] = None
    else:
        state = finalize_current_uptime(state, now=now)
        state["last_error"] = health.get("error")

    vram = vram_func()
    state["last_vram"] = vram
    if vram.get("over_threshold"):
        append_log_event(root, "vram_threshold_exceeded", vram, now=now)

    state = save_state(root, state, now=now)
    status = summarize_state(state, now=now)
    status.update(
        {
            "healthy": health.get("healthy", False),
            "health": health,
            "vram": vram,
            "stderr_lines_synced": len(stderr_lines),
        }
    )
    return status


def run_monitor_loop(
    root: Path,
    sleep_func=None,
    health_func=None,
    vram_func=None,
) -> int:
    sleep_func = sleep_func or time.sleep
    health_func = health_func or check_health
    vram_func = vram_func or check_vram
    ensure_logs_dir(root)
    append_log_event(root, "monitor_started", {"pid": os.getpid()})
    last_health_check = 0.0
    last_vram_check = 0.0

    while True:
        now = utc_now()
        state = load_state(root)
        state, _ = sync_stderr_to_json(root, state, now=now)
        save_state(root, state, now=now)

        monotonic_now = time.monotonic()
        if monotonic_now - last_vram_check >= VRAM_CHECK_INTERVAL_SECONDS:
            vram = vram_func()
            state["last_vram"] = vram
            if vram.get("over_threshold"):
                append_log_event(root, "vram_threshold_exceeded", vram, now=now)
            save_state(root, state, now=now)
            last_vram_check = monotonic_now

        if monotonic_now - last_health_check >= HEALTH_CHECK_INTERVAL_SECONDS:
            health = health_func()
            state["last_health"] = health
            if health.get("healthy"):
                state = mark_service_healthy(state, now=now, inferred=True)
                state["last_error"] = None
                save_state(root, state, now=now)
            else:
                state = finalize_current_uptime(state, now=now)
                state["last_error"] = health.get("error")
                save_state(root, state, now=now)
                append_log_event(root, "health_check_failed", health, now=now)
                restart_ollama(
                    root,
                    now=now,
                    popen=subprocess.Popen,
                    health_func=health_func,
                    sleep_func=sleep_func,
                )
            last_health_check = monotonic_now

        sleep_func(1)


def latest_json_log(root: Path) -> Path | None:
    candidates = sorted(
        path
        for path in logs_dir(root).glob("ollama_*.json")
        if path.name != STATE_FILE_NAME
    )
    return candidates[-1] if candidates else None


def print_json(payload: dict, stdout=None) -> None:
    target = stdout or sys.stdout
    target.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def print_logs(root: Path, stdout=None) -> int:
    state = load_state(root)
    now = utc_now()
    state, _ = sync_stderr_to_json(root, state, now=now)
    save_state(root, state, now=now)

    path = latest_json_log(root)
    if path is None:
        target = stdout or sys.stdout
        target.write("No Ollama log file found.\n")
        return 1

    target = stdout or sys.stdout
    content = path.read_text(encoding="utf-8")
    target.write(content)
    if not content.endswith("\n"):
        target.write("\n")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Monitor and auto-restart Ollama for OpenClaw.")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("status", help="Run a one-time health and VRAM status check.")
    subparsers.add_parser("restart", help="Restart managed Ollama if needed.")
    subparsers.add_parser("logs", help="Print the latest Ollama JSON log.")
    return parser.parse_args(argv)


def main(
    argv: list[str] | None = None,
    root: Path | None = None,
    stdout=None,
    health_func=None,
    vram_func=None,
    sleep_func=None,
    popen=None,
) -> int:
    args = parse_args(argv)
    root = root or Path.cwd()
    health_func = health_func or check_health
    vram_func = vram_func or check_vram
    sleep_func = sleep_func or time.sleep
    popen = popen or subprocess.Popen

    if args.command == "status":
        status = gather_status(root, health_func=health_func, vram_func=vram_func)
        print_json(status, stdout=stdout)
        return 0 if status["healthy"] else 1

    if args.command == "restart":
        result = restart_ollama(
            root,
            popen=popen,
            health_func=health_func,
            sleep_func=sleep_func,
        )
        print_json(result, stdout=stdout)
        return 0 if result.get("restarted") or result.get("reason") == "healthy_unmanaged_service_detected" else 1

    if args.command == "logs":
        return print_logs(root, stdout=stdout)

    return run_monitor_loop(root, sleep_func=sleep_func, health_func=health_func, vram_func=vram_func)


if __name__ == "__main__":
    sys.exit(main())
