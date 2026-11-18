#!/usr/bin/env python3
"""
Ollama Queue Monitor - runs every hour, checks progress and launches next model.
"""
import subprocess
import time
import sys
from pathlib import Path

QUEUE = [
    ("functiongemma:latest", "functiongemma"),
    ("deepcoder:1.5b", "deepcoder:1.5b"),
    ("gemma4:e2b", "gemma4:e2b"),
    ("deepseek-r1:14b", "deepseek-r1:14b"),
    ("gemma4:e4b", "gemma4:e4b"),
    ("deepcoder:14b", "deepcoder:14b"),
    # Removed nemotron-reward (doesn't exist in Ollama library)
    # Removed lfm2.5 (use maternion/lfm2.5 if needed)
    # Removed bespoke (doesn't exist, only bespoke-minicheck)
]

LOG = Path(__file__).parent.parent / "logs" / "ollama_queue_monitor.log"
LOG.parent.mkdir(parents=True, exist_ok=True)

def log(msg: str):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG, "a") as f:
        f.write(line + "\n")

def get_ollama_models() -> list:
    try:
        result = subprocess.run(
            ["ollama", "list"],
            capture_output=True,
            text=True,
            timeout=60 # Increased timeout
        )
        log(f"ollama list stdout: {result.stdout.strip()}")
        log(f"ollama list stderr: {result.stderr.strip()}")
        log(f"ollama list returncode: {result.returncode}")

        # Plain text table: NAME (30 chars) | ID | SIZE | MODIFIED
        lines = result.stdout.strip().split("\n")
        if len(lines) <= 1:
            log("No models found or only header returned by ollama list.")
            return []
        models = []
        for line in lines[1:]:  # Skip header
            if line.strip():
                # NAME column is first 30 characters, stripped
                name = line[:30].strip()
                if name:
                    models.append(name)
        return models
    except Exception as e:
        log(f"ERROR getting ollama models: {e}")
        return []
        return []

def is_ollama_pull_active() -> bool:
    """Check if an ollama *pull* subprocess is currently active.
    The Ollama server (ollama serve) is always running, so checking for any
    ollama.exe would yield a constant false positive. We use WMIC to inspect
    the command line and only flag processes whose command starts with
    'ollama pull'.
    """
    try:
        result = subprocess.run(
            ["wmic", "process", "where",
             "name='ollama.exe'",
             "get", "CommandLine", "/format:list"],
            capture_output=True,
            text=True,
            timeout=15
        )
        # Iterate each block separated by blank lines; each block has
        # CommandLine=... If any block contains a pull invocation, return True.
        text = result.stdout or ""
        for block in text.split("\n\n"):
            for line in block.splitlines():
                if line.lower().startswith("commandline="):
                    cmd = line.split("=", 1)[1].strip().lower()
                    # Treat as a pull if command line is 'ollama pull ...'
                    if "ollama" in cmd and "pull" in cmd:
                        log(f"Detected active ollama pull: {cmd}")
                        return True
        return False
    except FileNotFoundError:
        log("WMIC unavailable; falling back to broader heuristic.")
        # Fallback: any ollama.exe other than the persistent server is suspicious.
        # Use tasklist to at least count processes.
        try:
            res = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq ollama.exe"],
                capture_output=True, text=True, timeout=10
            )
            lines = [l for l in res.stdout.splitlines() if "ollama.exe" in l.lower()]
            # If more than one ollama.exe, a pull is likely in progress
            if len(lines) > 1:
                log(f"Multiple ollama.exe processes ({len(lines)}); assuming pull active.")
                return True
            return False
        except Exception as inner:
            log(f"Fallback process check failed: {inner}")
            return False
    except Exception as e:
        log(f"ERROR checking for active ollama pull: {e}")
        return False

def launch_pull(model: str) -> bool:
    log(f"LAUNCHING: ollama pull {model}")
    try:
        subprocess.Popen(
            ["ollama", "pull", model],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        return True
    except Exception as e:
        log(f"ERROR launching {model}: {e}")
        return False

def main():
    log("=== Ollama Queue Monitor START ===")
    models = get_ollama_models()
    log(f"Current models: {models}")

    # Check if an ollama process is running (potential pull or inference)
    if is_ollama_pull_active():
        log("An Ollama process is active, skipping new launch to avoid conflicts.")
        log("=== DONE ===")
        return

    for model_full, model_short in QUEUE:
        # Check if this model is already installed
        # Try exact match first, then partial
        installed = any(model_short.lower() in m.lower() for m in models)
        if installed:
            log(f"SKIP (already installed): {model_full}")
            continue

        # Not installed and nothing downloading → launch it
        launch_pull(model_full)
        log(f"Launched: {model_full}")
        log("=== DONE (model launched) ===")
        return

    log("=== ALL DONE - queue complete ===")

if __name__ == "__main__":
    main()