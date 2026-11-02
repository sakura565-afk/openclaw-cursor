"""BCNodes BC_SkinTexture on our frames (PROD :8188).

Why this node: after Qwen erases pores there is nothing left for an unsharp mask to
amplify, so the texture has to be ADDED. BC_SkinTexture multiplies in a synthetic pore
field (two band-pass octaves + rare dark pores) and boosts the image's own fine detail,
in linear light, inside a mask.

We connect OUR skin mask, so the SAM 3 detection (and its model download) is skipped.

Strength semantics (from the node's own tooltip): texture 1.0 = +/-6% luminance
modulation, so the practical range is 0.1-0.3.
"""
from __future__ import annotations

import json
import os
import shutil
import time
import urllib.error
import urllib.request

SCRATCH = os.environ.get(
    "HERMES_SCRATCH_DIR",
    os.path.join(os.path.expanduser("~"), "AppData", "Local", "hermes", "cache", "scratch"),
)
PROD_DIR = os.environ.get(
    "COMFYUI_PROD_DIR", r"E:\ComfyUI_windows_portable_nvidia_cu126\ComfyUI_windows_portable"
)
PROD_IN = os.path.join(PROD_DIR, "ComfyUI", "input")
PROD_OUT = os.path.join(PROD_DIR, "ComfyUI", "output")
BASE = "http://127.0.0.1:8188"


def api(path, payload=None, timeout=180):
    req = urllib.request.Request(BASE + path,
                                 data=json.dumps(payload).encode() if payload is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode("utf-8", "replace")
    return json.loads(raw) if raw.strip() else {}


def run(tag, *, img: str, mask: str, texture: float = 0.2, detail: float = 0.45,
        pore_scale: float = 1.0, feather: int = 8, seed: int = 5):
    shutil.copy2(os.path.join(SCRATCH, img), os.path.join(PROD_IN, f"bst_{tag}_img.png"))
    shutil.copy2(os.path.join(SCRATCH, mask), os.path.join(PROD_IN, f"bst_{tag}_mask.png"))
    nodes = {
        "1": {"class_type": "LoadImage", "inputs": {"image": f"bst_{tag}_img.png"}},
        "2": {"class_type": "LoadImage", "inputs": {"image": f"bst_{tag}_mask.png"}},
        "3": {"class_type": "ImageToMask", "inputs": {"image": ["2", 0], "channel": "red"}},
        "4": {"class_type": "BC_SkinTexture",
              "inputs": {"image": ["1", 0], "mask": ["3", 0],
                         "texture": texture, "detail": detail,
                         "pore_scale": pore_scale, "feather": feather,
                         "seed": seed, "threshold": 0.5,
                         "sam3_model": "sam3.1_multiplex_fp16.safetensors"}},
        "5": {"class_type": "SaveImage", "inputs": {"images": ["4", 0], "filename_prefix": "bst_" + tag}},
    }
    try:
        resp = api("/prompt", {"prompt": nodes, "client_id": "bst"}, timeout=240)
    except urllib.error.HTTPError as e:
        print(f"{tag}: HTTP {e.code} {e.read().decode('utf-8','replace')[:600]}", flush=True)
        return None
    pid = resp.get("prompt_id")
    if not pid:
        print(f"{tag}: нет prompt_id {json.dumps(resp)[:400]}", flush=True)
        return None
    print(f"{tag}: prompt_id={pid} texture={texture} detail={detail} pore_scale={pore_scale}", flush=True)
    t0 = time.time()
    while time.time() - t0 < 900:
        time.sleep(8)
        try:
            h = api(f"/history/{pid}", timeout=60)
        except Exception:
            continue
        e = h.get(pid)
        if not e:
            continue
        if e.get("status", {}).get("status_str") == "error":
            print(f"{tag}: ОШИБКА {json.dumps(e.get('status', {}).get('messages', []))[:500]}", flush=True)
            return None
        for o in e.get("outputs", {}).values():
            for im in o.get("images", []):
                p = os.path.join(PROD_OUT, im.get("subfolder", ""), im["filename"])
                if os.path.exists(p):
                    dst = os.path.join(SCRATCH, f"{tag}.png")
                    shutil.copy2(p, dst)
                    print(f"{tag}: ГОТОВО за {int(time.time()-t0)} с -> {dst}", flush=True)
                    return dst
    print(f"{tag}: ТАЙМАУТ", flush=True)
    return None


if __name__ == "__main__":
    import sys
    img = sys.argv[1] if len(sys.argv) > 1 else "FINAL_recline_graded2.png"
    mask = sys.argv[2] if len(sys.argv) > 2 else "mask_skin_recline.png"
    for t in (0.1, 0.2, 0.3):
        run(f"BST_{os.path.splitext(img)[0]}_{int(t*100)}", img=img, mask=mask, texture=t)
