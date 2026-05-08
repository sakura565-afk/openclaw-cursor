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
    ("nemotron-reward:latest", "nemotron-reward"),
    ("lfm2.5:latest", "lfm2.5"),
    ("bespoke:latest", "bespoke"),
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
            ["ollama", "list", "--json"],
            capture_output=True,
            text=True,
            timeout=10
        )
        import json
        models = json.loads(result.stdout)
        return [m["name"] for m in models]
    except:
        return []

def is_model_downloading(model_short: str) -> bool:
    """Check if Ollama is currently downloading a model (running ollama pull)."""
    try:
        result = subprocess.run(
            ["curl", "-s", "http://localhost:11434/api/ps"],
            capture_output=True,
            text=True,
            timeout=5
        )
        import json
        data = json.loads(result.stdout)
        # If there's a running model with "pulling" in status, it's downloading
        if data.get("model"):
            return "pulling" in str(data).lower()
        return False
    except:
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

    # Check if something is still downloading
    if is_model_downloading(""):
        log("A model is still downloading, skip launching new one")
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