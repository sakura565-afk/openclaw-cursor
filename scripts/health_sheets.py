#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""System Health → Google Sheets + Telegram Alert."""

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("health_sheets")


# ─── Config ───────────────────────────────────────────────────────────────────

SHEETS_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
COMPOSIO_API_KEY = os.getenv("COMPOSIO_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
GOOGLE_SHEETS_ID = os.getenv("GOOGLE_SHEETS_ID", "")

ANOMALY_RAM_PCT = 80.0
ANOMALY_SESSION_TOKENS = 50_000
ANOMALY_DISK_GB = 10.0
TOKEN_ESTIMATE_CHARS = 4
SESSION_LOG_PATTERNS = ["session_*.json", "session_*.jsonl"]


# ─── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class HealthMetrics:
    ram_pct: float
    disk_gb: float
    session_tokens: int
    ollama_status: str
    gpu_util: int | None
    anomaly: bool
    anomaly_reasons: list[str]


# ─── Collection ────────────────────────────────────────────────────────────────

def collect_ram() -> float:
    """RAM usage percent."""
    try:
        import psutil
        return psutil.virtual_memory().percent
    except ImportError:
        log.warning("psutil not available, reading RAM from wmic")
        r = subprocess.run(
            ["wmic", "OS", "get", "FreePhysicalMemory,TotalVisibleMemorySize", "/format:list"],
            capture_output=True, text=True, timeout=15,
        )
        lines = r.stdout.strip().splitlines()
        free = total = 0
        for line in lines:
            if "=" in line:
                k, _, v = line.partition("=")
                if k.strip() == "FreePhysicalMemory":
                    free = int(v.strip())
                elif k.strip() == "TotalVisibleMemorySize":
                    total = int(v.strip())
        if total:
            return round((1 - free / total) * 100, 1)
        return 0.0


def collect_disk() -> float:
    """Free disk space on C: in GB."""
    try:
        import psutil
        return round(psutil.disk_usage("C:\\").free / (1024**3), 2)
    except ImportError:
        import shutil
        u = shutil.disk_usage("C:\\")
        return round(u.free / (1024**3), 2)


def collect_session_tokens() -> int:
    """Estimate tokens in latest session log."""
    workspace = Path.home() / ".openclaw" / "workspace"
    candidates = []
    for pattern in SESSION_LOG_PATTERNS:
        candidates.extend(workspace.glob(pattern))
    session_files = [f for f in candidates if "session" in f.name.lower()]
    if not session_files:
        logs_dir = workspace / "logs"
        if logs_dir.exists():
            session_files = (
                list(logs_dir.glob("session_*.json")) or
                list(logs_dir.glob("session_*.jsonl"))
            )
    if not session_files:
        return 0
    latest = max(session_files, key=lambda p: p.stat().st_mtime)
    try:
        content = latest.read_text(encoding="utf-8", errors="replace")
        if latest.suffix == ".jsonl":
            lines = [l for l in content.splitlines() if l.strip()]
            total = sum((len(l) + 3) // TOKEN_ESTIMATE_CHARS for l in lines)
        else:
            total = (len(content) + 3) // TOKEN_ESTIMATE_CHARS
        return total
    except Exception as e:
        log.warning("Failed to read session %s: %s", latest, e)
        return 0


def collect_ollama() -> str:
    """Ollama availability check."""
    try:
        r = subprocess.run(
            ["curl", "-s", "http://localhost:11434/api/tags"],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode == 0:
            data = json.loads(r.stdout)
            models = data.get("models", [])
            return f"ok ({len(models)} models)"
        return "unreachable"
    except Exception:
        return "error"


def collect_gpu() -> int | None:
    """GPU utilization % or None."""
    try:
        r = subprocess.run(
            [
                "nvidia-smi", "--query-gpu=utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode == 0:
            return int(r.stdout.strip())
        return None
    except Exception:
        return None


def collect_all() -> HealthMetrics:
    """Collect all metrics."""
    ram = collect_ram()
    disk = collect_disk()
    tokens = collect_session_tokens()
    ollama = collect_ollama()
    gpu = collect_gpu()

    reasons = []
    if ram > ANOMALY_RAM_PCT:
        reasons.append(f"RAM {ram:.1f}% > {ANOMALY_RAM_PCT}%")
    if disk < ANOMALY_DISK_GB:
        reasons.append(f"Disk {disk}GB < {ANOMALY_DISK_GB}GB")
    if tokens > ANOMALY_SESSION_TOKENS:
        reasons.append(f"Session {tokens} tokens > {ANOMALY_SESSION_TOKENS}")

    return HealthMetrics(
        ram_pct=ram,
        disk_gb=disk,
        session_tokens=tokens,
        ollama_status=ollama,
        gpu_util=gpu,
        anomaly=bool(reasons),
        anomaly_reasons=reasons,
    )


# ─── Google Sheets ─────────────────────────────────────────────────────────────

def append_to_sheets(spreadsheet_id: str, row: list) -> bool:
    """Append a row to Google Sheets via REST API (bearer fromComposio)."""
    if not COMPOSIO_API_KEY:
        log.warning("COMPOSIO_API_KEY not set, skipping sheet write")
        return False

    # Discover Sheets endpoint via Composio
    import urllib.request

    # Use the same approach as the working Composio Gmail tools
    # GET spreadsheet to find first empty row
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/values/A:A"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {COMPOSIO_API_KEY}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
            rows = data.get("values", [])
            next_row = len(rows) + 1
    except Exception as e:
        log.warning("Failed to read sheet dimensions: %s, defaulting to A2", e)
        next_row = 2

    range_str = f"Sheet1!A{next_row}:F{next_row}"
    body = {"values": [row]}
    patch_url = (
        f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}"
        f"/values/{range_str}:append?valueInputOption=RAW"
    )
    req2 = urllib.request.Request(
        patch_url,
        data=json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {COMPOSIO_API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req2, timeout=15) as resp:
            return resp.status == 200
    except Exception as e:
        log.error("Failed to append row to sheet: %s", e)
        return False


# ─── Telegram alert ────────────────────────────────────────────────────────────

def send_telegram(message: str) -> bool:
    """Send alert via Telegram Bot API."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("Telegram credentials not set, skipping alert")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": f"⚠️ *System Health Anomaly*\n\n{message}",
        "parse_mode": "Markdown",
    }
    for attempt in range(3):
        try:
            r = requests.post(url, json=payload, timeout=15)
            if r.status_code == 200:
                return True
            log.warning("Telegram attempt %d failed: %s", attempt + 1, r.text)
        except Exception as e:
            log.warning("Telegram attempt %d error: %s", attempt + 1, e)
        time.sleep(2 * (attempt + 1))
    return False


# ─── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="System Health → Google Sheets")
    parser.add_argument("--dry-run", action="store_true", help="Collect only, no write")
    parser.add_argument("--no-sheet", action="store_true", help="Skip Google Sheets write")
    parser.add_argument("--no-alert", action="store_true", help="Skip Telegram alert")
    args = parser.parse_args()

    metrics = collect_all()

    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    row = [
        ts,
        round(metrics.ram_pct, 1),
        metrics.disk_gb,
        metrics.session_tokens,
        metrics.ollama_status,
        "YES" if metrics.anomaly else "no",
    ]

    log.info(
        "RAM=%.1f%% Disk=%.1fGB Session=%d tokens Ollama=%s Anomaly=%s",
        metrics.ram_pct, metrics.disk_gb, metrics.session_tokens,
        metrics.ollama_status, metrics.anomaly,
    )

    if metrics.anomaly:
        reasons = ", ".join(metrics.anomaly_reasons)
        log.warning("Anomaly detected: %s", reasons)
        if not args.no_alert:
            send_telegram(reasons)

    if args.dry_run:
        log.info("Dry run — row not written: %s", row)
        return 0

    if not args.no_sheet and GOOGLE_SHEETS_ID:
        ok = append_to_sheets(GOOGLE_SHEETS_ID, row)
        log.info("Sheet write: %s", "ok" if ok else "FAILED")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())