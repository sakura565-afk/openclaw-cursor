"""ReActor identity restore: put the ORIGINAL photo's face back onto the edit.

Qwen-Image-Edit at denoise 1.0 regenerates the whole scene, face included. Even when the
geometry roughly matches, the face comes out softer and smaller in frame, which reads as
"some other, older woman". ReActor (inswapper_128) transplants the real face from the
source photo, so identity is no longer left to the diffusion model's luck.

Graph: LoadImage(edit) + LoadImage(source) -> ReActorFaceSwap -> SaveImage
"""
from __future__ import annotations

import json
import os
import shutil
import time
import urllib.error
import urllib.request

SCRATCH = r"C:\Users\user\AppData\Local\hermes\cache\scratch"
PROD_DIR = r"E:\ComfyUI_windows_portable_nvidia_cu126\ComfyUI_windows_portable"
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


def run(tag: str, *, edit: str, source: str, restore: str = "GFPGANv1.4.pth",
        visibility: float = 0.9, codeformer_weight: float = 0.5):
    shutil.copy2(os.path.join(SCRATCH, edit), os.path.join(PROD_IN, f"ra_{tag}_edit.png"))
    shutil.copy2(os.path.join(SCRATCH, source), os.path.join(PROD_IN, f"ra_{tag}_src.png"))
    nodes = {
        "1": {"class_type": "LoadImage", "inputs": {"image": f"ra_{tag}_edit.png"}},
        "2": {"class_type": "LoadImage", "inputs": {"image": f"ra_{tag}_src.png"}},
        "3": {"class_type": "ReActorFaceSwap",
              "inputs": {"enabled": True,
                         "input_image": ["1", 0],
                         "source_image": ["2", 0],
                         "swap_model": "inswapper_128.onnx",
                         "facedetection": "retinaface_resnet50",
                         "face_restore_model": restore,
                         "face_restore_visibility": visibility,
                         "codeformer_weight": codeformer_weight,
                         "detect_gender_input": "no",
                         "detect_gender_source": "no",
                         "input_faces_index": "0",
                         "source_faces_index": "0",
                         "console_log_level": 1}},
        "4": {"class_type": "SaveImage",
              "inputs": {"images": ["3", 0], "filename_prefix": f"ra_{tag}"}},
    }
    try:
        resp = api("/prompt", {"prompt": nodes, "client_id": "reactor"}, timeout=240)
    except urllib.error.HTTPError as e:
        print(f"{tag}: HTTP {e.code} {e.read().decode('utf-8','replace')[:600]}", flush=True)
        return None
    pid = resp.get("prompt_id")
    if not pid:
        print(f"{tag}: нет prompt_id {json.dumps(resp)[:300]}", flush=True)
        return None
    print(f"{tag}: prompt_id={pid} restore={restore}", flush=True)
    t0 = time.time()
    while time.time() - t0 < 1200:
        time.sleep(8)
        try:
            hist = api(f"/history/{pid}", timeout=45)
        except Exception:
            continue
        e = hist.get(pid)
        if not e:
            continue
        if e.get("status", {}).get("status_str") == "error":
            print(f"{tag}: ОШИБКА {json.dumps(e.get('status',{}).get('messages',[]))[:600]}", flush=True)
            return None
        for o in e.get("outputs", {}).values():
            for im in o.get("images", []):
                p = os.path.join(PROD_OUT, im.get("subfolder", ""), im["filename"])
                if os.path.exists(p):
                    dst = os.path.join(SCRATCH, f"FINAL_{tag}.png")
                    shutil.copy2(p, dst)
                    print(f"{tag}: ГОТОВО за {int(time.time()-t0)} с -> {dst} "
                          f"({os.path.getsize(dst)//1024} КБ)", flush=True)
                    return dst
    print(f"{tag}: ТАЙМАУТ", flush=True)
    return None


if __name__ == "__main__":
    SRC_STAND = (r"C:\Users\user\AppData\Local\hermes\cache\documents\native-inputs"
                  r"\664dd4bff18f966d9f7f869d66cd50c647d0218edde2c9cf9823dc666bc502be\img_320df09f260d.jpg")
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "both":
        run("stand", edit="qe_undress_hi.png", source=SRC_STAND)
        run("recline", edit="qe_rec_2509_urp10.png", source=SRC_STAND)
    else:
        run("stand", edit="qe_undress_hi.png", source=SRC_STAND)
