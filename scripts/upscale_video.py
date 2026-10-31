"""SeedVR2 upscale stage for a finished 24 fps clip.

Runs the learned upscaler alone (no re-generation, no interpolation) on PROD :8188.
Replaces the scratch-only copy of this script - the scratch directory is pruned after
24 h of inactivity and this stage is needed on every delivery.


SeedVR2 needs ~15 GB of REAL free VRAM. Two traps this script handles:
  1. the LAB ComfyUI's FreeVideo resident worker holds ~13.4 GB -> stop LAB (and relaunch
     it afterwards, detached, with the registry env);
  2. ComfyUI's /system_stats vram_free is the TORCH ALLOCATOR's view, not the driver's.
     After a RIFE job the allocator keeps a reserved pool, so vram_free reads ~0.5 GB
     even though the card is empty -> the repo's preflight then silently falls back to
     Lanczos. Fix: restart the PROD ComfyUI before the upscale so the pool starts clean.

    python upscale_seedvr2_only.py --src in.mp4 --out out.mp4 --height 1620
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from video_pipeline.vram_guard import (  # noqa: E402
    acquire_render_lock, pause_named_processes, release_render_lock, restore_processes,
)
from video_pipeline.upscale import upscale_video, free_vram_gb  # noqa: E402
from video_pipeline.config import UpscaleConfig  # noqa: E402

PROD = "http://127.0.0.1:8188"


def log(m):
    print(f"[up] {m}", flush=True)


def _ffprobe_path() -> str:
    """Locate ffprobe: the Hermes tools copy first, then whatever is on PATH."""
    candidates = [
        Path(r"C:\Users\user\AppData\Local\hermes\tools\ffmpeg-9.0.1-win32-x64\bin\ffprobe.exe"),
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    found = shutil.which("ffprobe")
    if found:
        return found
    raise FileNotFoundError("ffprobe не найден")


def gpu_mb():
    r = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                       capture_output=True, text=True)
    try:
        return int(r.stdout.strip().splitlines()[0])
    except Exception:
        return -1


def prod_pids():
    r = subprocess.run(["powershell", "-NoProfile", "-Command",
        "$c = Get-NetTCPConnection -LocalPort 8188 -State Listen -ErrorAction SilentlyContinue | "
        "Select-Object -Expand OwningProcess -Unique; foreach ($p in $c) { $p }"],
        capture_output=True, text=True, timeout=60)
    return [int(x) for x in (r.stdout or "").split() if x.strip().isdigit()]


def lab_pids():
    pids = []
    for port in (8189,):
        r = subprocess.run(["powershell", "-NoProfile", "-Command",
            f"$c = Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue | "
            "Select-Object -Expand OwningProcess -Unique; foreach ($p in $c) { $p }"],
            capture_output=True, text=True, timeout=60)
        pids += [int(x) for x in (r.stdout or "").split() if x.strip().isdigit()]
    w = subprocess.run(["powershell", "-NoProfile", "-Command",
        "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -match 'freevideo_engine' } | "
        "ForEach-Object { $_.ProcessId }"], capture_output=True, text=True, timeout=60)
    pids += [int(x) for x in (w.stdout or "").split() if x.strip().isdigit()]
    return sorted(set(pids))


def kill_pids(pids, label):
    if not pids:
        return
    for p in pids:
        subprocess.run(["taskkill", "/PID", str(p), "/T", "/F"], capture_output=True)
    log(f"{label} остановлен: {pids}")


def start_prod():
    LOGS = Path(r"C:\Users\user\AppData\Local\hermes\logs")
    LOGS.mkdir(parents=True, exist_ok=True)
    out = LOGS / "comfyui_prod_8188_manual2.log"
    err = LOGS / "comfyui_prod_8188_manual2.err.log"
    ps_cmd = ("$env:PYTHONIOENCODING='utf-8'; $env:PYTHONUTF8='1'; "
              "Start-Process -FilePath 'E:\\ComfyUI_windows_portable_nvidia_cu126\\ComfyUI_windows_portable\\python_embeded\\python.exe' "
              "-ArgumentList '-s','ComfyUI/main.py','--listen','127.0.0.1','--port','8188','--disable-auto-launch' "
              "-WorkingDirectory 'E:\\ComfyUI_windows_portable_nvidia_cu126\\ComfyUI_windows_portable' "
              f"-RedirectStandardOutput '{out}' -RedirectStandardError '{err}' -WindowStyle Hidden; 'started'")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps_cmd], capture_output=True, text=True, timeout=90)
    # ComfyUI needs ~5.5 min to boot on this box (2214 nodes + the manager), and each
    # failed probe costs its own timeout - so the old 60x5 s budget turned a slow boot
    # into what looked like a hung script. Log the progress and wait long enough.
    for attempt in range(1, 121):
        time.sleep(5)
        try:
            import urllib.request
            urllib.request.urlopen(f"{PROD}/system_stats", timeout=10).read()
            log(f"PROD поднялся (попытка {attempt})")
            return True
        except Exception:
            if attempt % 12 == 0:
                log(f"PROD ещё поднимается ({attempt * 5} с)")
    log("PROD не поднялся")
    return False


def start_lab():
    """Bring LAB back, but NEVER let it break the pipeline.

    The launcher can take longer than the wait budget (ComfyUI loads 1397 nodes), and a
    TimeoutError here used to kill the whole script AFTER the output was already written
    (the finally block raised) - which left LAB down for the next run.
    """
    starter = Path(r"C:\Users\user\AppData\Local\hermes\scripts\start_lab_freevideo.py")
    try:
        r = subprocess.run([sys.executable, str(starter), "--wait", "90"],
                           capture_output=True, text=True, timeout=150)
        log("LAB: " + (r.stdout or r.stderr or "").strip()[-200:])
    except subprocess.TimeoutExpired:
        log("подъём LAB не уложился в бюджет - запускаю в фоне")
        subprocess.Popen([sys.executable, str(starter), "--wait", "280"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception as e:
        log(f"подъём LAB: {e}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--height", type=int, default=1620)
    ap.add_argument("--width", type=int, default=0,
                    help="target width; 0 = derive from the source aspect ratio")
    ap.add_argument("--restart-prod", action="store_true")
    args = ap.parse_args()

    src, out = Path(args.src), Path(args.out)
    acquire_render_lock("seedvr2 upscale")
    paused = []
    try:
        if args.restart_prod:
            kill_pids(prod_pids(), "PROD")
            time.sleep(5)
            if not start_prod():
                return 7
        paused = pause_named_processes(["llama-server.exe"])
        if paused:
            log(f"приостановлено: {[p.name for p in paused]}")
        kill_pids(lab_pids(), "LAB")
        for _ in range(30):
            time.sleep(2)
            if gpu_mb() < 4000:
                break
        log(f"VRAM на карте: {gpu_mb()} МБ")

        import requests
        try:
            requests.post(f"{PROD}/free", json={"unload_models": True, "free_memory": True}, timeout=60)
        except Exception as e:
            log(f"/free: {e}")
        time.sleep(3)
        free = free_vram_gb(PROD)
        log(f"ComfyUI vram_free: {free if free is None else round(free,2)} ГБ, физически занято {gpu_mb()} МБ")
        if free is None or free < 13:
            log("Мало VRAM по данным ComfyUI - SeedVR2 будет пропущен")
            return 8

        w = args.width or 1088
        if not args.width:
            # derive the width from the SOURCE aspect ratio, otherwise SeedVR2
            # stretches the frame (1088x928 on a 1056x704 source = +28% vertical)
            try:
                import subprocess as _sp
                import json as _json
                _fp = str(_ffprobe_path())
                _d = _json.loads(_sp.run([_fp, "-v", "error", "-print_format", "json",
                                          "-show_streams", str(src)],
                                         capture_output=True, text=True).stdout)
                _v = next(s for s in _d["streams"] if s.get("codec_type") == "video")
                w = int(round(args.height * int(_v["width"]) / int(_v["height"]) / 16) * 16)
                log(f"ширина по AR источника: {w}x{args.height}")
            except Exception as e:
                log(f"не удалось вывести ширину: {e}")
        # Таймаут поллинга SeedVR2. Репо-дефолт 3600 с НЕ ХВАТАЕТ: на 173 кадрах
        # прогон занимает ~20-25 мин, а если карту делят другие процессы (llama-server
        # подхватил веса, висящие cursor-agent жгут CPU) - шаг декода растёт с 6 с до
        # 270 с и всё равно упирается в таймаут. Раньше скрипт МОЛЧА уходил в откат
        # на Lanczos и отдавал файл, который выглядит как апскейл, но деталей не даёт.
        # Теперь: ждём дольше и при таймауте падаем, а не подменяем бэкенд.
        import video_pipeline.upscale as _up
        _up.POLL_TIMEOUT_SEC = int(os.environ.get("UPSCALE_POLL_TIMEOUT", "14400"))
        log(f"таймаут поллинга: {_up.POLL_TIMEOUT_SEC} с")
        backend = upscale_video(
            src, out, w, args.height,
            UpscaleConfig(backend="seedvr2", comfyui_url=PROD),
        )
        log(f"бэкенд: {backend} -> {out}")
        if backend != "seedvr2":
            log("ВНИМАНИЕ: реального апскейла не было (откат на другой бэкенд) - файл НЕ пригоден как upscale")
            return 9
    finally:
        if paused:
            restore_processes(paused)
            log("llama-server возвращён")
        release_render_lock()
        start_lab()
    return 0


if __name__ == "__main__":
    sys.exit(main())
