#!/usr/bin/env python3
"""
Ollama Batch Model Downloader
Downloads models sequentially in background.
Usage: python ollama_batch_download.py [start_from]
"""
import subprocess
import sys
import time
from pathlib import Path

MODELS = [
    "phi4:latest",           # ~2.8 GB
    "functiongemma:latest",   # ~3 GB
    "deepcoder:1.5b",         # ~1.6 GB
    "gemma4:e2b",             # ~7.2 GB
    "deepseek-r1:14b",       # ~9 GB
    "gemma4:e4b",             # ~9.6 GB
    "deepcoder:14b",          # ~9 GB
    "nemotron-reward:latest", # ~14 GB
    "lfm2.5:latest",          # ~3 GB
    "bespoke:latest",         # ~3 GB
]

LOG = Path(__file__).parent.parent / "logs" / "ollama_batch_download.log"
LOG.parent.mkdir(parents=True, exist_ok=True)

def log(msg: str):
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG, "a") as f:
        f.write(line + "\n")

def pull(model: str) -> bool:
    log(f"START: ollama pull {model}")
    try:
        result = subprocess.run(
            ["ollama", "pull", model],
            capture_output=True,
            text=True,
            timeout=7200  # 2h max per model
        )
        ok = result.returncode == 0
        status = "OK" if ok else f"FAIL ({result.returncode})"
        log(f"DONE: {model} [{status}]")
        return ok
    except subprocess.TimeoutExpired:
        log(f"TIMEOUT: {model} (>2h)")
        return False
    except Exception as e:
        log(f"ERROR: {model} — {e}")
        return False

def main():
    start_from = sys.argv[1] if len(sys.argv) > 1 else None
    started = False if start_from else True

    for model in MODELS:
        if not started:
            if model == start_from:
                started = True
            else:
                log(f"SKIP: {model} (not reached start marker)")
                continue

        pull(model)
        time.sleep(2)  # brief pause between models

    log("=== ALL DONE ===")

if __name__ == "__main__":
    main()
