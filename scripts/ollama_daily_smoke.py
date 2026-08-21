#!/usr/bin/env python3
"""Daily Ollama chain health smoke test.

Walks ``DEFAULT_CHAIN`` from ``scripts.ollama_chain``, probes each model with a
short prompt (15s timeout), writes ``data/ollama_health_YYYY-MM-DD.json`` (UTC),
alerts via Telegram on unexpected failures, and prunes health files older than
30 days.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.ollama_chain import DEFAULT_CHAIN, run_model  # noqa: E402

SMOKE_PROMPT = "2+2? Just the number, no explanation."
MODEL_TIMEOUT_SECONDS = 15.0
HEALTH_RETENTION_DAYS = 30
PAID_XAI_MODELS = frozenset({"grok-4.5", "grok-4.5:latest", "grok-4.5:cloud"})
ALERT_EXEMPT_MODELS = frozenset(PAID_XAI_MODELS)

DEFAULT_TELEGRAM_SCRIPT = Path(
    r"C:\Users\user\.openclaw\skills\telegram-media-send\scripts\telegram_media_send_v2.py"
)
DEFAULT_TELEGRAM_CONFIG = Path(
    r"C:\Users\user\.openclaw\agents\tasks\workspace\scripts\telegram_config.json"
)
DEFAULT_CHAT_ID = "25979298"

TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[^\w\s]", re.UNICODE)
EVAL_COUNT_RE = re.compile(r"eval\s*count:\s*(\d+)", re.IGNORECASE)
EVAL_DURATION_RE = re.compile(r"eval\s*duration:\s*([\d.]+)\s*s", re.IGNORECASE)
TOKENS_PER_SEC_RE = re.compile(r"([\d.]+)\s*tokens?/s", re.IGNORECASE)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def repo_root() -> Path:
    return ROOT


def data_dir(root: Path | None = None) -> Path:
    return (root or repo_root()) / "data"


def health_path_for_date(day: datetime, root: Path | None = None) -> Path:
    return data_dir(root) / f"ollama_health_{day.astimezone(timezone.utc).strftime('%Y-%m-%d')}.json"


def estimate_token_count(text: str) -> int:
    return len(TOKEN_RE.findall(text or ""))


def parse_verbose_metrics(stderr: str) -> tuple[int | None, float | None]:
    """Extract eval_count and tokens/s from ``ollama --verbose`` style stderr."""
    eval_count: int | None = None
    tokens_per_second: float | None = None

    count_match = EVAL_COUNT_RE.search(stderr or "")
    if count_match:
        eval_count = int(count_match.group(1))

    tps_match = TOKENS_PER_SEC_RE.search(stderr or "")
    if tps_match:
        tokens_per_second = float(tps_match.group(1))
    else:
        duration_match = EVAL_DURATION_RE.search(stderr or "")
        if duration_match and eval_count is not None:
            duration_s = float(duration_match.group(1))
            if duration_s > 0:
                tokens_per_second = eval_count / duration_s

    return eval_count, tokens_per_second


def is_paid_xai_model(model: str) -> bool:
    name = (model or "").strip().lower()
    if name in {m.lower() for m in PAID_XAI_MODELS}:
        return True
    return name.startswith("grok-")


def should_skip_xai(model: str, env: dict[str, str] | None = None) -> bool:
    environ = env if env is not None else os.environ
    return is_paid_xai_model(model) and not environ.get("XAI_API_KEY")


def is_alert_exempt(model: str, result: dict[str, Any]) -> bool:
    if model in ALERT_EXEMPT_MODELS or is_paid_xai_model(model):
        return True
    if result.get("skipped"):
        return True
    error = str(result.get("error") or "")
    if "403" in error and is_paid_xai_model(model):
        return True
    return False


def probe_model(
    model: str,
    *,
    prompt: str = SMOKE_PROMPT,
    timeout: float = MODEL_TIMEOUT_SECONDS,
    env: dict[str, str] | None = None,
    runner: Callable[..., tuple[int, str, str]] | None = None,
    time_fn: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Probe one model; returns a result dict with ok/latency/metrics/error."""
    result: dict[str, Any] = {
        "model": model,
        "ok": False,
        "latency_s": None,
        "eval_count": None,
        "tokens_per_second": None,
        "error": None,
        "response_preview": None,
        "skipped": False,
    }

    if should_skip_xai(model, env=env):
        result["skipped"] = True
        result["ok"] = True  # graceful skip — not an operational failure
        result["error"] = "skipped: XAI_API_KEY missing"
        result["latency_s"] = 0.0
        return result

    run = runner or run_model
    started = time_fn()
    try:
        returncode, stdout, stderr = run(model, prompt, timeout=timeout)
        latency = max(0.0, time_fn() - started)
        result["latency_s"] = round(latency, 3)

        combined_err = (stderr or "").strip()
        preview = (stdout or "").strip()
        result["response_preview"] = preview[:200] if preview else None

        eval_count, tokens_per_second = parse_verbose_metrics(combined_err)
        if eval_count is None:
            eval_count = estimate_token_count(preview) if preview else 0
        if tokens_per_second is None and latency > 0 and eval_count:
            tokens_per_second = eval_count / latency

        result["eval_count"] = int(eval_count or 0)
        result["tokens_per_second"] = (
            round(float(tokens_per_second), 3) if tokens_per_second is not None else 0.0
        )

        if returncode != 0:
            result["error"] = combined_err or f"ollama exited with code {returncode}"
            result["ok"] = False
            return result

        if not preview:
            result["error"] = "empty response"
            result["ok"] = False
            return result

        result["ok"] = True
        return result
    except subprocess.TimeoutExpired:
        latency = max(0.0, time_fn() - started)
        result["latency_s"] = round(latency, 3)
        result["error"] = f"timeout after {timeout}s"
        result["ok"] = False
        return result
    except FileNotFoundError as exc:
        result["latency_s"] = round(max(0.0, time_fn() - started), 3)
        result["error"] = f"ollama not found: {exc}"
        result["ok"] = False
        return result
    except OSError as exc:
        result["latency_s"] = round(max(0.0, time_fn() - started), 3)
        result["error"] = str(exc)
        result["ok"] = False
        return result


def cleanup_old_health_files(
    root: Path | None = None,
    *,
    retention_days: int = HEALTH_RETENTION_DAYS,
    now: datetime | None = None,
) -> list[str]:
    """Delete ``data/ollama_health_*.json`` older than *retention_days* (UTC)."""
    base = data_dir(root)
    if not base.is_dir():
        return []

    cutoff = (now or utc_now()).astimezone(timezone.utc) - timedelta(days=retention_days)
    deleted: list[str] = []
    for path in sorted(base.glob("ollama_health_*.json")):
        stem = path.stem  # ollama_health_YYYY-MM-DD
        date_part = stem.removeprefix("ollama_health_")
        try:
            file_day = datetime.strptime(date_part, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if file_day < cutoff.replace(hour=0, minute=0, second=0, microsecond=0):
            try:
                path.unlink()
                deleted.append(path.name)
            except OSError:
                continue
    return deleted


def load_telegram_config(config_path: Path | None = None) -> dict[str, Any]:
    path = config_path or Path(
        os.environ.get("OLLAMA_HEALTH_TELEGRAM_CONFIG", str(DEFAULT_TELEGRAM_CONFIG))
    )
    if not path.is_file():
        return {
            "bot_token": os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("BOT_TOKEN"),
            "chat_id": os.environ.get("TELEGRAM_CHAT_ID") or DEFAULT_CHAT_ID,
            "bot_name": "Istranewbot",
        }
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    token = (
        data.get("bot_token")
        or data.get("token")
        or data.get("BOT_TOKEN")
        or os.environ.get("TELEGRAM_BOT_TOKEN")
    )
    chat_id = str(
        data.get("chat_id")
        or data.get("CHAT_ID")
        or os.environ.get("TELEGRAM_CHAT_ID")
        or DEFAULT_CHAT_ID
    )
    return {
        "bot_token": token,
        "chat_id": chat_id,
        "bot_name": data.get("bot_name") or data.get("name") or "Istranewbot",
        "config_path": str(path),
    }


def format_alert_message(report: dict[str, Any]) -> str:
    failures = [r for r in report.get("results", []) if not r.get("ok") and not is_alert_exempt(r.get("model", ""), r)]
    lines = [
        "⚠️ Ollama chain health smoke FAILED",
        f"date_utc: {report.get('date_utc')}",
        f"failures: {len(failures)} / {report.get('model_count', 0)}",
        "",
    ]
    for item in failures:
        lines.append(
            f"- {item.get('model')}: {item.get('error') or 'unknown error'} "
            f"(latency_s={item.get('latency_s')})"
        )
    return "\n".join(lines)


def send_telegram_alert(
    message: str,
    *,
    script_path: Path | None = None,
    config_path: Path | None = None,
    run_command: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    """Send alert via ``telegram_media_send_v2.py`` using Istranewbot config."""
    script = script_path or Path(
        os.environ.get("OLLAMA_HEALTH_TELEGRAM_SCRIPT", str(DEFAULT_TELEGRAM_SCRIPT))
    )
    config = load_telegram_config(config_path)
    if not script.is_file():
        return {"ok": False, "error": f"telegram script not found: {script}"}

    token = config.get("bot_token")
    chat_id = str(config.get("chat_id") or DEFAULT_CHAT_ID)
    if not token:
        return {"ok": False, "error": "telegram bot_token missing in config"}

    env = os.environ.copy()
    env["TELEGRAM_BOT_TOKEN"] = str(token)
    env["BOT_TOKEN"] = str(token)
    env["TELEGRAM_CHAT_ID"] = chat_id
    env["CHAT_ID"] = chat_id

    # Prefer explicit flags; fall back to positional message for older skill builds.
    attempts: list[list[str]] = [
        [sys.executable, str(script), "--token", str(token), "--chat-id", chat_id, "--text", message],
        [sys.executable, str(script), "--bot-token", str(token), "--chat-id", chat_id, "--message", message],
        [sys.executable, str(script), "--text", message],
        [sys.executable, str(script), message],
    ]

    last_error = "no attempts"
    for cmd in attempts:
        completed = run_command(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            env=env,
            timeout=60,
        )
        if completed.returncode == 0:
            return {
                "ok": True,
                "command": cmd,
                "stdout": (completed.stdout or "").strip()[:500],
            }
        last_error = (
            (completed.stderr or "").strip()
            or (completed.stdout or "").strip()
            or f"exit {completed.returncode}"
        )
        # argparse "unrecognized arguments" → try next signature
        if "unrecognized arguments" not in last_error.lower() and "invalid" not in last_error.lower():
            # Non-CLI mismatch failure — still try remaining signatures once
            continue

    return {"ok": False, "error": last_error}


def failing_results_for_alert(results: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in results if not r.get("ok") and not is_alert_exempt(str(r.get("model") or ""), r)]


def run_smoke(
    *,
    models: Sequence[str] | None = None,
    root: Path | None = None,
    prompt: str = SMOKE_PROMPT,
    timeout: float = MODEL_TIMEOUT_SECONDS,
    send_alerts: bool = True,
    retention_days: int = HEALTH_RETENTION_DAYS,
    now: datetime | None = None,
    runner: Callable[..., tuple[int, str, str]] | None = None,
    telegram_script: Path | None = None,
    telegram_config: Path | None = None,
    run_command: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Run the full daily smoke and persist the health report."""
    base = root or repo_root()
    stamp = (now or utc_now()).astimezone(timezone.utc)
    chain = tuple(models) if models is not None else DEFAULT_CHAIN

    results: list[dict[str, Any]] = []
    for model in chain:
        results.append(
            probe_model(
                model,
                prompt=prompt,
                timeout=timeout,
                env=env,
                runner=runner,
            )
        )

    deleted = cleanup_old_health_files(base, retention_days=retention_days, now=stamp)

    alert_failures = failing_results_for_alert(results)
    report: dict[str, Any] = {
        "date_utc": stamp.strftime("%Y-%m-%d"),
        "generated_at": stamp.replace(microsecond=0).isoformat(),
        "prompt": prompt,
        "timeout_s": timeout,
        "model_count": len(chain),
        "ok_count": sum(1 for r in results if r.get("ok")),
        "fail_count": sum(1 for r in results if not r.get("ok")),
        "alert_failure_count": len(alert_failures),
        "results": results,
        "cleaned_up": deleted,
        "telegram_alert": None,
    }

    out_path = health_path_for_date(stamp, base)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    report["output_path"] = str(out_path)

    if send_alerts and alert_failures:
        report["telegram_alert"] = send_telegram_alert(
            format_alert_message(report),
            script_path=telegram_script,
            config_path=telegram_config,
            run_command=run_command,
        )
    else:
        report["telegram_alert"] = {"ok": True, "skipped": True, "reason": "no alertable failures"}

    # Rewrite with telegram_alert included.
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Daily Ollama DEFAULT_CHAIN health smoke test.")
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Repository root (default: parent of scripts/).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=MODEL_TIMEOUT_SECONDS,
        help=f"Per-model timeout in seconds (default: {MODEL_TIMEOUT_SECONDS}).",
    )
    parser.add_argument(
        "--no-alert",
        action="store_true",
        help="Skip Telegram alerts even when models fail.",
    )
    parser.add_argument(
        "--retention-days",
        type=int,
        default=HEALTH_RETENTION_DAYS,
        help=f"Delete health JSON older than this many days (default: {HEALTH_RETENTION_DAYS}).",
    )
    parser.add_argument(
        "--model",
        action="append",
        dest="models",
        help="Override chain with one or more --model values (repeatable).",
    )
    parser.add_argument(
        "--telegram-script",
        type=Path,
        default=None,
        help="Path to telegram_media_send_v2.py",
    )
    parser.add_argument(
        "--telegram-config",
        type=Path,
        default=None,
        help="Path to telegram_config.json",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the full report JSON to stdout.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = run_smoke(
        models=args.models,
        root=args.root,
        timeout=args.timeout,
        send_alerts=not args.no_alert,
        retention_days=args.retention_days,
        telegram_script=args.telegram_script,
        telegram_config=args.telegram_config,
    )

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(f"Wrote {report.get('output_path')}")
        print(
            f"ok={report.get('ok_count')}/{report.get('model_count')} "
            f"fail={report.get('fail_count')} "
            f"alertable={report.get('alert_failure_count')}"
        )
        for item in report.get("results", []):
            status = "OK" if item.get("ok") else "FAIL"
            if item.get("skipped"):
                status = "SKIP"
            print(
                f"  [{status}] {item.get('model')} "
                f"latency_s={item.get('latency_s')} "
                f"eval_count={item.get('eval_count')} "
                f"tok/s={item.get('tokens_per_second')} "
                f"error={item.get('error')}"
            )

    # Non-zero if any non-exempt model failed.
    return 1 if report.get("alert_failure_count", 0) else 0


if __name__ == "__main__":
    raise SystemExit(main())
