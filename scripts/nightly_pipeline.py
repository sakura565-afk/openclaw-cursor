#!/usr/bin/env python3
"""
Nightly Pipeline - Run during 1:00-8:00 AM
Ollama-based tasks for morning summary
"""

import subprocess
import sys
import json
import os
from datetime import datetime
from pathlib import Path

LOG_FILE = Path(__file__).parent.parent / "logs" / "nightly_pipeline.log"
LOG_FILE.parent.mkdir(exist_ok=True)

def log(msg: str):
    timestamp = datetime.now().strftime("%H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")

def run_ollama(model: str, prompt: str, timeout: int = 120) -> str:
    """Run ollama with given model and prompt"""
    try:
        result = subprocess.run(
            ["ollama", "run", model, prompt],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=timeout
        )
        return result.stdout if result.returncode == 0 else f"ERROR: {result.stderr}"
    except subprocess.TimeoutExpired:
        return "TIMEOUT"
    except Exception as e:
        return f"EXCEPTION: {e}"

def step(name: str, func, *args):
    """Run a pipeline step with logging"""
    log(f"START: {name}")
    start = datetime.now()
    result = func(*args)
    duration = (datetime.now() - start).seconds
    log(f"DONE: {name} ({duration}s)")
    return result

def memory_cleanup():
    """Clean old sessions and temp files"""
    log("Running memory cleanup...")
    # Cleanup old daily logs (>30 days)
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

def obsidian_sync():
    """Sync and check Obsidian vault"""
    log("Running Obsidian sync...")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "scripts.sync_obsidian"],
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent.parent,
            timeout=60
        )
        return "OK" if result.returncode == 0 else f"FAIL: {result.stderr[:100]}"
    except Exception as e:
        return f"EXCEPTION: {e}"

def generate_morning_brief():
    """Generate morning brief using local model"""
    log("Generating morning brief...")
    
    # Read last 24h logs
    memory_dir = Path.home() / ".openclaw" / "workspace" / "memory"
    yesterday = (datetime.now() - __import__('datetime').timedelta(days=1)).strftime("%Y-%m-%d")
    log_file = memory_dir / f"{yesterday}.md"
    
    context = "No recent logs found"
    if log_file.exists():
        context = log_file.read_text(encoding='utf-8')[:1000]
    else:
        # Try today
        today = datetime.now().strftime("%Y-%m-%d")
        today_file = memory_dir / f"{today}.md"
        if today_file.exists():
            context = today_file.read_text(encoding='utf-8')[:1000]
    
    prompt = f"Create a short morning summary in Russian (3-4 lines):\n\nContext: {context}\n\nFormat:\n✅ Что сделано:\n🔄 В процессе:\n⚠️ Требует внимания:"
    
    result = run_ollama("qwen3.5:2b", prompt, timeout=60)
    
    # Save to workspace
    brief_file = Path.home() / ".openclaw" / "workspace" / "morning_brief.md"
    brief_file.write_text(f"# Morning Brief\n\nGenerated: {datetime.now()}\n\n{result or 'No output'}", encoding='utf-8')
    
    return (result or '')[:300]

def send_telegram_summary(brief: str):
    """Send morning brief to Telegram"""
    log("Sending Telegram summary...")
    try:
        script_path = Path.home() / ".openclaw" / "skills" / "telegram-media-send" / "scripts" / "telegram_media_send_v2.py"
        if not script_path.exists():
            log("Telegram script not found, skipping")
            return "SKIP"
        
        # Format for Telegram
        msg = f"🌅 *Утренний брифинг*\n\n{brief}"
        # Write to temp file
        msg_file = Path.temp / "brief_msg.txt"
        msg_file.write_text(msg, encoding='utf-8')
        
        # Note: telegram send needs actual file, we'll just log for now
        log(f"Brief ready at: {Path.home() / '.openclaw' / 'workspace' / 'morning_brief.md'}")
        return "OK"
    except Exception as e:
        return f"EXCEPTION: {e}"

def main():
    log("=== NIGHTLY PIPELINE START ===")
    log(f"Time window: 1:00 - 8:00 AM")
    
    # Check if within time window (allow override via env)
    hour = datetime.now().hour
    force = os.environ.get("NIGHTLY_FORCE") == "1"
    if not force and (hour < 1 or hour >= 8):
        log(f"Outside time window (hour={hour}), exiting")
        return
    
    steps = [
        ("Memory Cleanup", memory_cleanup),
        ("Obsidian Sync", obsidian_sync),
        ("Morning Brief", generate_morning_brief),
    ]
    
    results = {}
    for name, func in steps:
        results[name] = step(name, func)
    
    log("=== PIPELINE COMPLETE ===")
    for name, result in results.items():
        log(f"  {name}: {result[:100]}")
    
    # Generate and save brief
    brief = results.get("Morning Brief", "")
    if brief and brief != "TIMEOUT":
        send_telegram_summary(brief)

if __name__ == "__main__":
    main()