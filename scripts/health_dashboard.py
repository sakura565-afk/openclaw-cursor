#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""System Health Dashboard."""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import argparse
import json
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

LOG_DIR = Path.home() / ".openclaw" / "temp" / "openclaw-cursor" / "logs"
LOG_DIR.mkdir(exist_ok=True)
HEALTH_LOG = LOG_DIR / "health.log"

@dataclass
class HealthCheck:
    name: str
    status: str
    message: str
    details: dict = None

def check_disk_space():
    import shutil
    drives = {"C": "C:\\", "E": "E:\\", "H": "H:\\", "Q": "Q:\\"}
    results = {}
    alerts = []
    for name, path in drives.items():
        try:
            u = shutil.disk_usage(path)
            free = u.free / (1024**3)
            total = u.total / (1024**3)
            pct = (u.used / u.total) * 100
            results[name] = {"free_gb": round(free, 1), "total_gb": round(total, 1), "used_pct": round(pct, 1)}
            if free < 10: alerts.append(f"{name}: ONLY {free:.0f}GB FREE")
            elif free < 20: alerts.append(f"{name}: {free:.0f}GB free")
        except Exception as e:
            results[name] = {"error": str(e)}
    status = "fail" if any("ONLY" in a for a in alerts) else "warn" if alerts else "ok"
    msg = "; ".join(alerts) if alerts else "All drives OK"
    return HealthCheck("disk_space", status, msg, results)

def check_ollama():
    try:
        r = subprocess.run(["curl", "-s", "http://localhost:11434/api/tags"],
                          capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            data = json.loads(r.stdout)
            models = [m["name"] for m in data.get("models", [])]
            return HealthCheck("ollama", "ok", f"Running, {len(models)} models", {"models": models})
        return HealthCheck("ollama", "fail", "API error")
    except Exception as e:
        return HealthCheck("ollama", "fail", str(e))

def check_gpu():
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw",
                           "--format=csv,noheader,nounits"],
                          capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            v = [x.strip() for x in r.stdout.strip().split(",")]
            gpu_util, mem_used, mem_total, temp, power = int(v[0]), int(v[1]), int(v[2]), int(v[3]), float(v[4])
            status = "fail" if gpu_util > 95 or temp > 90 else "warn" if gpu_util > 80 or temp > 80 else "ok"
            msg = f"GPU {gpu_util}% | VRAM {mem_used}/{mem_total}MB | {temp}C | {power}W"
            return HealthCheck("gpu", status, msg, {"gpu_util": gpu_util, "mem_used": mem_used, "mem_total": mem_total, "temp": temp})
        return HealthCheck("gpu", "fail", "nvidia-smi error")
    except Exception as e:
        return HealthCheck("gpu", "fail", str(e))

def check_openclaw():
    try:
        r = subprocess.run(["curl", "-s", "http://localhost:18789/status"],
                          capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            return HealthCheck("openclaw", "ok", "Gateway responding")
        return HealthCheck("openclaw", "fail", "No response")
    except:
        return HealthCheck("openclaw", "fail", "Not reachable")

def run_all():
    return [check_disk_space(), check_ollama(), check_gpu(), check_openclaw()]

def format_console(checks):
    e = {"ok": "OK", "warn": "WARN", "fail": "FAIL", "skip": "SKIP"}
    lines = ["=" * 50, f"HEALTH {datetime.now().strftime('%Y-%m-%d %H:%M')}", "=" * 50]
    for c in checks:
        lines.append(f"[{e.get(c.status, '?')}] {c.name}: {c.message}")
    fails = sum(1 for c in checks if c.status == "fail")
    warns = sum(1 for c in checks if c.status == "warn")
    lines.append("=" * 50)
    if fails: lines.append(f"FAILURES: {fails}")
    if warns: lines.append(f"WARNINGS: {warns}")
    if not fails and not warns: lines.append("ALL OK")
    return "\n".join(lines)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--alert-only", action="store_true")
    args = parser.parse_args()
    checks = run_all()
    if args.alert_only:
        checks = [c for c in checks if c.status in ("fail", "warn")]
    print(format_console(checks))

if __name__ == "__main__":
    main()
