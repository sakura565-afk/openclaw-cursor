"""FreeVideo (Video DeltaNet) generator with LoRA stack + VRAM guard, LAB ComfyUI :8189.

Paths come from the environment (FREEVIDEO_LAB_DIR, FREEVIDEO_OUT_DIR,
HERMES_SCRATCH_DIR, FFPROBE_BIN) with this box's values as defaults.

Original docstring follows.

    python fv_lora.py --image src.jpg --width 704 --height 1056 --seconds 7 \
        --prompt prompt.txt --out "C:/path/name.mp4" \
        --lora "Hand in Panties....safetensors:0.7" --lora "Moawxx....safetensors:0.6"

VRAM guard: pauses llama-server (it holds ~8 GB of the 16 GB card) for the render and
restores it afterwards. A render lock file keeps the local watchdog from resurrecting
llama-server in the middle of the render.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

LAB = Path(os.environ.get("FREEVIDEO_LAB_DIR", r"E:/ComfyUI_LAB_037/ComfyUI_windows_portable/ComfyUI"))
OUT_ROOT = Path(os.environ.get("FREEVIDEO_OUT_DIR", r"G:/FreeVideo_output"))
SCRATCH = Path(os.environ.get("HERMES_SCRATCH_DIR", r"C:/Users/user/AppData/Local/hermes/cache/scratch"))
LOCK = SCRATCH / "render_vram.lock"
LLAMA_CMD = SCRATCH / "llama_server_cmd.json"
FFPROBE = os.environ.get("FFPROBE_BIN", r"C:\Users\user\AppData\Local\hermes\tools\ffmpeg-9.0.1-win32-x64\bin\ffprobe.exe")
BASE = os.environ.get("COMFYUI_LAB_URL", "http://127.0.0.1:8189")


def log(m):
    print(f"[fv] {m}", flush=True)


def get(url, timeout=60):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode())


def post_json(url, payload, timeout=180):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        # ComfyUI answers 400 with the node's own validation message - that is where
        # "FreeVideo setup is missing" and the LoRA-name error come from. Surface it.
        body = e.read().decode("utf-8", "replace")
        raise RuntimeError(f"ComfyUI отклонил промт ({e.code}): {body[:600]}") from None


def free_vram(url):
    try:
        req = urllib.request.Request(f"{url.rstrip('/')}/free",
                                     data=json.dumps({"unload_models": True,
                                                      "free_memory": True}).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=60) as r:
            r.read()
        return True
    except Exception:
        return False


def snapshot(root: Path) -> dict:
    out = {}
    if root.is_dir():
        for p in root.rglob("*.mp4"):
            try:
                out[p] = p.stat().st_mtime
            except OSError:
                pass
    return out


def probe(p: Path) -> dict:
    d = json.loads(subprocess.run([FFPROBE, "-v", "error", "-print_format", "json",
                                   "-show_streams", "-show_format", str(p)],
                                  capture_output=True, text=True).stdout or "{}")
    v = next((s for s in d.get("streams", []) if s.get("codec_type") == "video"), {})
    a = [s for s in d.get("streams", []) if s.get("codec_type") == "audio"]
    return {"size": f"{v.get('width')}x{v.get('height')}", "fps": v.get("r_frame_rate"),
            "frames": v.get("nb_frames"), "dur": round(float(d.get("format", {}).get("duration", 0)), 2),
            "audio": len(a), "mb": round(p.stat().st_size / 2**20, 2)}


def llama_running():
    try:
        with urllib.request.urlopen("http://127.0.0.1:18434/v1/models", timeout=6) as r:
            r.read()
            return True
    except Exception:
        return False


def pause_llama():
    """Stop llama-server for the render; it holds ~8 GB of VRAM."""
    if not llama_running():
        return None
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    LOCK.write_text(json.dumps({"pid": os.getpid(), "ts": time.time()}), encoding="utf-8")
    subprocess.run(["taskkill", "/IM", "llama-server.exe", "/F"], capture_output=True)
    for _ in range(40):
        if not llama_running():
            break
        time.sleep(1)
    log("llama-server приостановлен, render-lock активен")
    return True


def restore_llama(paused):
    if not paused:
        return
    try:
        LOCK.unlink(missing_ok=True)
    except OSError:
        pass
    if LLAMA_CMD.exists():
        saved = json.loads(LLAMA_CMD.read_text(encoding="utf-8"))
        subprocess.Popen(saved["cmdline"], cwd=saved["cwd"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    log("llama-server возвращён")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True)
    ap.add_argument("--width", type=int, required=True)
    ap.add_argument("--height", type=int, required=True)
    ap.add_argument("--seconds", type=float, required=True)
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--base-steps", type=int, default=8)
    ap.add_argument("--refine-steps", type=int, default=3)
    ap.add_argument("--lora", action="append", default=[], help="name.safetensors:strength")
    ap.add_argument("--timeout", type=int, default=5400)
    ap.add_argument("--tier-high", action="store_true",
                    help="single sampling pass at the full resolution (tier high) "
                         "instead of the default two_pass light tier")
    ap.add_argument("--last", default=None,
                    help="optional LAST frame image: the clip will end on it "
                         "(node 3 LoadImage -> FreeVideoGenerate.last)")
    args = ap.parse_args()

    if "FreeVideoGenerate" not in get(f"{BASE}/object_info", timeout=90):
        log("FreeVideoGenerate не зарегистрирован на LAB")
        return 1

    # Preflight: LAB must be UP and it must see the FreeVideo installation.
    # A LAB started without FREEVIDEO_HOME accepts the prompt and then fails every
    # node with "FreeVideo setup is missing" - so check that too, and start LAB
    # detached (with the registry env) if it is down.
    import urllib.error
    try:
        get(f"{BASE}/object_info", timeout=20)
    except Exception as e:
        log(f"LAB не отвечает ({str(e)[:50]}) - поднимаю его")
        starter = r"C:\Users\user\AppData\Local\hermes\scripts\start_lab_freevideo.py"
        r = subprocess.run([sys.executable, starter, "--wait", "280"],
                           capture_output=True, text=True, timeout=320)
        log((r.stdout or r.stderr or "").strip()[-400:])
        try:
            get(f"{BASE}/object_info", timeout=20)
        except Exception as e2:
            log(f"LAB не поднялся: {str(e2)[:60]}")
            return 6
    # NOTE: do NOT name this variable "probe" - there is a probe() FUNCTION below and
    # the shadowing made the script die with "TypeError: 'dict' object is not callable"
    # right after the render, before the output was copied.
    info_probe = get(f"{BASE}/object_info", timeout=30)
    if "FreeVideoGenerate" not in info_probe:
        log("LAB поднялся, но FreeVideoGenerate не зарегистрирован")
        return 1

    prompt = Path(args.prompt).read_text(encoding="utf-8").strip()
    src = Path(args.image)
    if not src.exists():
        # a bare file name is looked up in the scratch dir - passing "photo.jpg"
        # instead of the full path used to die with a bare FileNotFoundError
        for base in (SCRATCH, LAB / "input", Path.cwd()):
            candidate = base / src.name
            if candidate.exists():
                src = candidate
                log(f"изображение найдено в {base}")
                break
        else:
            raise SystemExit(f"изображение не найдено: {args.image} "
                             f"(искал в {SCRATCH} и {LAB / 'input'})")
    name = "fvgen_" + src.stem.replace(" ", "_")[:40] + ".jpg"
    shutil.copy2(src, LAB / "input" / name)

    adapters = []
    # Resolve LoRA names against the LIVE ComfyUI list: a single wrong character
    # ("4-8steps with turbo lora" vs "4-8steps with turbo") kills the job with
    # "Select an available safetensors LoRA from your ComfyUI model folders".
    info = get(f"{BASE}/object_info", timeout=90)
    lnode = info.get("LoraLoaderModelOnly") or info.get("LoraLoader") or {}
    available = [str(x) for x in (lnode.get("input", {}).get("required", {})
                                  .get("lora_name", [[]])[0] or [])]
    for item in args.lora:
        lname, _, s = item.rpartition(":")
        lname = lname.strip()
        if lname not in available:
            # normalise spaces AND underscores: the same LoRA exists in the folder under
            # both "Passionate Kissing.safetensors" and "Passionate_Kissing.safetensors"
            def norm(x):
                return x.lower().replace(" ", "").replace("_", "").replace("-", "")
            key = norm(lname)
            match = next((a for a in available if norm(a).startswith(key[:40])), None)
            if not match:
                match = next((a for a in available if key[:25] in norm(a)), None)
            if not match:
                # try the other way round: the file name is longer than what was passed
                match = next((a for a in available if norm(a)[:40].startswith(key)), None)
            if not match:
                log(f"ЛORA НЕ НАЙДЕНА: {lname!r}")
                return 5
            log(f"имя уточнено: {lname[:50]!r} -> {match[:50]!r}")
            lname = match
        adapters.append({"name": lname, "strength": float(s or 1.0)})

    free_vram(BASE)
    time.sleep(2)

    before = snapshot(OUT_ROOT)
    fv_inputs = {
        "text": prompt, "width": args.width, "height": args.height,
        "seconds": args.seconds, "seed": args.seed, "first": ["1", 0],
        # FreeVideo tiers (freevideo_engine/tiers.py):
        #   light = two_pass + upscale_factor 2 -> first pass at 25% of the pixels
        #   high  = single pass, 16 steps, easycache-av -> full resolution
        # The micro-texture of the skin is born in the first pass, so the light tier
        # cannot carry pores no matter what the upscaler does afterwards.
        "two_pass": not args.tier_high, "base_steps": args.base_steps,
        "refine_steps": args.refine_steps,
    }
    if args.tier_high:
        fv_inputs["step_cache"] = "easycache-av"
    graph = {
        "1": {"class_type": "LoadImage", "inputs": {"image": name}},
        "2": {"class_type": "FreeVideoGenerate", "inputs": fv_inputs},
    }
    if args.last:
        last_src = Path(args.last)
        if not last_src.exists():
            log(f"LAST-кадр не найден: {last_src}")
            return 7
        last_name = "fvlast_" + last_src.stem.replace(" ", "_")[:40] + ".jpg"
        shutil.copy2(last_src, LAB / "input" / last_name)
        # node id 90: node "3" is used by the LoRA stack, never reuse it here
        graph["90"] = {"class_type": "LoadImage", "inputs": {"image": last_name}}
        fv_inputs["last"] = ["90", 0]
        log(f"last-кадр закреплён: {last_name}")
    if adapters:
        graph["3"] = {"class_type": "FreeVideoLoRAStack",
                      "inputs": {"adapters": json.dumps(adapters, ensure_ascii=False)}}
        fv_inputs["loras"] = ["3", 0]
        log("лоры: " + ", ".join(f"{a['name'][:40]}@{a['strength']}" for a in adapters))

    paused = pause_llama()
    try:
        pid = post_json(f"{BASE}/prompt", {"prompt": graph})["prompt_id"]
        log(f"prompt_id={pid} | {args.width}x{args.height} | {args.seconds}s | seed={args.seed}")
        log(f"промт: {prompt[:100]}...")

        t0 = time.time()
        produced = None
        while time.time() - t0 < args.timeout:
            time.sleep(15)
            h = get(f"{BASE}/history/{pid}", timeout=60)
            if pid in h and h[pid].get("status", {}).get("status_str") == "error":
                log("ОШИБКА: " + json.dumps(h[pid]["status"].get("messages"))[:900])
                return 2
            new = [p for p in snapshot(OUT_ROOT) if p not in before]
            if new:
                new.sort(key=lambda p: p.stat().st_mtime)
                produced = new[-1]
                last, stable = -1, 0
                while stable < 3:
                    time.sleep(3)
                    try:
                        cur = produced.stat().st_size
                    except OSError:
                        break
                    if cur == last and cur > 100_000:
                        stable += 1
                    else:
                        stable = 0
                        last = cur
                break
            if int(time.time() - t0) % 60 < 15:
                log(f"... {int(time.time() - t0)}s")
    finally:
        restore_llama(paused)

    if not produced:
        log("файл не появился")
        return 3

    log(f"готово за {int(time.time() - t0)}s")
    if not produced.exists():
        alt = produced.parent / "video.mp4"
        if alt.exists():
            log("partial исчез, беру video.mp4")
            produced = alt
        else:
            log("файл исчез, копировать нечего")
            return 4
    log("OUTPUT: " + json.dumps(probe(produced), ensure_ascii=False))
    dst = Path(args.out)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(produced, dst)
    log("сохранено: " + str(dst))
    return 0


if __name__ == "__main__":
    sys.exit(main())
