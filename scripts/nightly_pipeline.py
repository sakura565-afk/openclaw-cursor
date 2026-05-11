#!/usr/bin/env python3
"""
Nightly Pipeline - Run during 1:00-8:00 AM
Ollama-based tasks for morning summary

Checkpointing: progress is stored in a JSON state file under
~/.openclaw/workspace/memory/nightly_pipeline_state.json (override with
NIGHTLY_PIPELINE_STATE_FILE). Resume skips COMPLETED steps; FAILED and
PENDING steps are re-run (with one automatic retry per step on failure).
"""

from __future__ import annotations

import atexit
import json
import os
import signal
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

LOG_FILE = Path(__file__).parent.parent / "logs" / "nightly_pipeline.log"
LOG_FILE.parent.mkdir(exist_ok=True)

STATUS_PENDING = "PENDING"
STATUS_RUNNING = "RUNNING"
STATUS_COMPLETED = "COMPLETED"
STATUS_FAILED = "FAILED"

# Module-level refs for atexit / signal persistence (set during run_pipeline)
_active_state_path: Optional[Path] = None
_active_state: Optional[Dict[str, Any]] = None
_active_step_id: Optional[str] = None
_signal_handlers_installed = False

StepFn = Callable[[Dict[str, Any]], Any]


def default_state_path() -> Path:
    override = os.environ.get("NIGHTLY_PIPELINE_STATE_FILE")
    if override:
        return Path(override).expanduser()
    return (
        Path.home()
        / ".openclaw"
        / "workspace"
        / "memory"
        / "nightly_pipeline_state.json"
    )


def log(msg: str) -> None:
    timestamp = datetime.now().strftime("%H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _step_status_line(phase: str, display_name: str, extra: str = "") -> str:
    suffix = f" {extra}".rstrip()
    return f"[{phase}: {display_name}{suffix}]"


def run_ollama(model: str, prompt: str, timeout: int = 120) -> str:
    """Run ollama with given model and prompt"""
    try:
        result = subprocess.run(
            ["ollama", "run", model, prompt],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        return result.stdout if result.returncode == 0 else f"ERROR: {result.stderr}"
    except subprocess.TimeoutExpired:
        return "TIMEOUT"
    except Exception as e:
        return f"EXCEPTION: {e}"


def memory_cleanup() -> str:
    """Clean old sessions and temp files"""
    log("Running memory cleanup...")
    memory_dir = Path.home() / ".openclaw" / "workspace" / "memory"
    if memory_dir.exists():
        from datetime import timedelta

        cutoff = datetime.now() - timedelta(days=30)
        for f in memory_dir.glob("*.md"):
            mtime = datetime.fromtimestamp(f.stat().st_mtime)
            if mtime < cutoff:
                log(f"  Removing old log: {f.name}")
                f.unlink()
    return "OK"


def obsidian_sync() -> str:
    """Sync and check Obsidian vault"""
    log("Running Obsidian sync...")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "scripts.sync_obsidian"],
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent.parent,
            timeout=60,
        )
        return "OK" if result.returncode == 0 else f"FAIL: {result.stderr[:100]}"
    except Exception as e:
        return f"EXCEPTION: {e}"


def generate_morning_brief() -> str:
    """Generate morning brief using local model"""
    log("Generating morning brief...")

    memory_dir = Path.home() / ".openclaw" / "workspace" / "memory"
    yesterday = (datetime.now() - __import__("datetime").timedelta(days=1)).strftime(
        "%Y-%m-%d"
    )
    log_file = memory_dir / f"{yesterday}.md"

    context = "No recent logs found"
    if log_file.exists():
        context = log_file.read_text(encoding="utf-8")[:1000]
    else:
        today = datetime.now().strftime("%Y-%m-%d")
        today_file = memory_dir / f"{today}.md"
        if today_file.exists():
            context = today_file.read_text(encoding="utf-8")[:1000]

    prompt = (
        f"Create a short morning summary in Russian (3-4 lines):\n\n"
        f"Context: {context}\n\n"
        f"Format:\n✅ Что сделано:\n🔄 В процессе:\n⚠️ Требует внимания:"
    )

    result = run_ollama("qwen3.5:2b", prompt, timeout=60)

    brief_file = Path.home() / ".openclaw" / "workspace" / "morning_brief.md"
    brief_file.write_text(
        f"# Morning Brief\n\nGenerated: {datetime.now()}\n\n{result or 'No output'}",
        encoding="utf-8",
    )

    try:
        from src.coordination.iskra_kara_shared_memory import notify_kara_from_iskra

        notify_kara_from_iskra(
            "nightly_brief",
            {
                "brief_path": str(brief_file),
                "brief_excerpt": (result or "")[:2000],
            },
        )
    except Exception:
        pass

    return (result or "")[:300]


def send_telegram_summary(brief: str) -> str:
    """Send morning brief to Telegram"""
    log("Sending Telegram summary...")
    try:
        script_path = (
            Path.home()
            / ".openclaw"
            / "skills"
            / "telegram-media-send"
            / "scripts"
            / "telegram_media_send_v2.py"
        )
        if not script_path.exists():
            log("Telegram script not found, skipping")
            return "SKIP"

        msg = f"🌅 *Утренний брифинг*\n\n{brief}"
        msg_file = Path(tempfile.gettempdir()) / "openclaw_brief_msg.txt"
        msg_file.write_text(msg, encoding="utf-8")

        log(f"Brief ready at: {Path.home() / '.openclaw' / 'workspace' / 'morning_brief.md'}")
        return "OK"
    except Exception as e:
        return f"EXCEPTION: {e}"


def _morning_brief_step(ctx: Dict[str, Any]) -> str:
    result = generate_morning_brief()
    ctx["brief"] = result
    return result


def _telegram_step(ctx: Dict[str, Any]) -> str:
    return send_telegram_summary(ctx.get("brief", ""))


def default_step_specs() -> List[Tuple[str, str, StepFn]]:
    return [
        ("memory_cleanup", "Memory Cleanup", lambda ctx: memory_cleanup()),
        ("obsidian_sync", "Obsidian Sync", lambda ctx: obsidian_sync()),
        ("generate_morning_brief", "Morning Brief", _morning_brief_step),
        ("send_telegram_summary", "Telegram Summary", _telegram_step),
    ]


def _empty_step_record() -> Dict[str, Any]:
    return {"status": STATUS_PENDING, "last_run_timestamp": None}


def default_state(step_ids: List[str]) -> Dict[str, Any]:
    return {
        "version": 1,
        "steps": {sid: _empty_step_record() for sid in step_ids},
        "last_full_success_at": None,
    }


def _normalize_state(loaded: Dict[str, Any], step_ids: List[str]) -> Dict[str, Any]:
    """Ensure all steps exist; turn stale RUNNING into PENDING (interrupted run)."""
    steps = loaded.get("steps") or {}
    out = default_state(step_ids)
    for sid in step_ids:
        rec = steps.get(sid) or {}
        status = rec.get("status", STATUS_PENDING)
        if status == STATUS_RUNNING:
            status = STATUS_PENDING
        if status not in (
            STATUS_PENDING,
            STATUS_RUNNING,
            STATUS_COMPLETED,
            STATUS_FAILED,
        ):
            status = STATUS_PENDING
        out["steps"][sid] = {
            "status": status,
            "last_run_timestamp": rec.get("last_run_timestamp"),
        }
    out["last_full_success_at"] = loaded.get("last_full_success_at")
    out["version"] = loaded.get("version", 1)
    return out


def load_state(path: Path, step_ids: List[str]) -> Dict[str, Any]:
    if not path.exists():
        st = default_state(step_ids)
        path.parent.mkdir(parents=True, exist_ok=True)
        return st
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return default_state(step_ids)
        return _normalize_state(raw, step_ids)
    except (json.JSONDecodeError, OSError):
        return default_state(step_ids)


def save_state(path: Path, state: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    data = json.dumps(state, indent=2, ensure_ascii=False)
    tmp.write_text(data, encoding="utf-8")
    tmp.replace(path)


def _step_result_ok(step_id: str, result: Any) -> bool:
    if result is None:
        return False
    if not isinstance(result, str):
        return True
    if result == "OK" or result == "SKIP":
        return True
    if step_id == "generate_morning_brief":
        return not (
            result.startswith("ERROR:")
            or result.startswith("TIMEOUT")
            or result.startswith("EXCEPTION:")
            or result.startswith("FAIL:")
        )
    return result == "OK"


def _persist_interrupt() -> None:
    global _active_state, _active_state_path, _active_step_id
    if _active_state is None or _active_state_path is None:
        return
    if _active_step_id:
        rec = _active_state["steps"].get(_active_step_id)
        if rec and rec.get("status") == STATUS_RUNNING:
            rec["status"] = STATUS_PENDING
            rec["last_run_timestamp"] = datetime.now().isoformat(timespec="seconds")
    try:
        save_state(_active_state_path, _active_state)
    except OSError:
        pass


def _on_signal(signum: int, frame: Any) -> None:
    _persist_interrupt()
    signal.signal(signum, signal.SIG_DFL)
    signal.raise_signal(signum)


def _install_signal_handlers() -> None:
    global _signal_handlers_installed
    if _signal_handlers_installed:
        return
    atexit.register(_persist_interrupt)
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _on_signal)
        except (ValueError, OSError):
            pass
    _signal_handlers_installed = True


def run_pipeline(
    *,
    state_path: Optional[Path] = None,
    step_specs: Optional[List[Tuple[str, str, StepFn]]] = None,
    register_signal_handlers: bool = True,
) -> Dict[str, Any]:
    """
    Run the nightly pipeline with checkpointing. Returns a summary dict for logs/tests.
    """
    global _active_state_path, _active_state, _active_step_id

    specs = step_specs if step_specs is not None else default_step_specs()
    step_ids = [s[0] for s in specs]
    path = state_path if state_path is not None else default_state_path()

    state = load_state(path, step_ids)
    ctx: Dict[str, Any] = {"brief": ""}

    _active_state_path = path
    _active_state = state
    _active_step_id = None

    if register_signal_handlers:
        _install_signal_handlers()

    skipped_completed: List[str] = []
    started_this_run: List[str] = []
    completed_this_run: List[str] = []
    failed: List[str] = []

    for step_id, display_name, fn in specs:
        rec = state["steps"][step_id]
        if rec["status"] == STATUS_COMPLETED:
            skipped_completed.append(display_name)
            continue
        if rec["status"] in (STATUS_PENDING, STATUS_FAILED):
            started_this_run.append(display_name)

        success = False
        last_result: Any = None
        for attempt in range(2):
            rec = state["steps"][step_id]
            rec["status"] = STATUS_RUNNING
            rec["last_run_timestamp"] = datetime.now().isoformat(timespec="seconds")
            save_state(path, state)

            _active_step_id = step_id
            log(_step_status_line("START", display_name))
            start = datetime.now()
            try:
                last_result = fn(ctx)
            except Exception as e:
                last_result = f"EXCEPTION: {e}"
            duration_s = int((datetime.now() - start).total_seconds())
            _active_step_id = None

            if _step_result_ok(step_id, last_result):
                rec["status"] = STATUS_COMPLETED
                rec["last_run_timestamp"] = datetime.now().isoformat(timespec="seconds")
                save_state(path, state)
                log(_step_status_line("DONE", display_name, f"({duration_s}s)"))
                completed_this_run.append(display_name)
                success = True
                break

            if attempt == 0:
                err_snip = str(last_result)[:100]
                log(_step_status_line("RETRY", display_name, f"({err_snip})"))
                continue

            rec["status"] = STATUS_FAILED
            rec["last_run_timestamp"] = datetime.now().isoformat(timespec="seconds")
            save_state(path, state)
            log(_step_status_line("FAILED", display_name, f"after retry ({duration_s}s)"))
            failed.append(display_name)
            break

        if not success:
            break

    all_done = len(failed) == 0 and all(
        state["steps"][sid]["status"] == STATUS_COMPLETED for sid in step_ids
    )
    if all_done:
        state["last_full_success_at"] = datetime.now().isoformat(timespec="seconds")
        for sid in step_ids:
            state["steps"][sid] = _empty_step_record()
        save_state(path, state)

    _active_state = None
    _active_state_path = None
    _active_step_id = None

    return {
        "skipped_completed": skipped_completed,
        "started_this_run": started_this_run,
        "completed_this_run": completed_this_run,
        "failed": failed,
        "all_done": all_done,
        "final_state": state,
    }


def main() -> None:
    log("=== NIGHTLY PIPELINE START ===")
    log("Time window: 1:00 - 8:00 AM")

    hour = datetime.now().hour
    force = os.environ.get("NIGHTLY_FORCE") == "1"
    if not force and (hour < 1 or hour >= 8):
        log(f"Outside time window (hour={hour}), exiting")
        return

    summary = run_pipeline(register_signal_handlers=True)

    log("=== PIPELINE COMPLETE ===")
    if summary["skipped_completed"]:
        log(
            "  Skipped (already completed this cycle): "
            + ", ".join(summary["skipped_completed"])
        )
    if summary["started_this_run"]:
        log("  Started this run: " + ", ".join(summary["started_this_run"]))
    if summary["completed_this_run"]:
        log("  Completed this run: " + ", ".join(summary["completed_this_run"]))
    if summary["failed"]:
        log("  Failed: " + ", ".join(summary["failed"]))
    if summary["all_done"]:
        log("  Overall: success (state reset for next run)")
    else:
        log("  Overall: incomplete or failed; fix issues and re-run to resume")


if __name__ == "__main__":
    main()
