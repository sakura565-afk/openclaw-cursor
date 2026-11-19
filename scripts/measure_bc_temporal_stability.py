# -*- coding: utf-8 -*-
"""Manual measurement: temporal stability of the BC pore pass per video frame.

Not a pytest module (run explicitly). Pytest does not collect scripts/ (testpaths = tests/).

Two ways to call BC_SkinTexture on a video:
  A) one call per frame, SAME seed  -> the pore field is identical in screen space
     for every frame, so the injected layer is temporally stable (no flicker),
     but the pores do not stick to the moving subject (they swim).
  B) one call per frame, different seed -> the pore field differs per frame,
     which is exactly the flicker the community reports.

This script measures both on REAL consecutive frames:
  temporal_delta = mean |frame[i+1] - frame[i]| of the injected layer
A stable injection keeps the delta of the *output* close to the delta of the
*input*; an unstable one inflates it.
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

SCRATCH = os.environ.get(
    "HERMES_SCRATCH_DIR",
    os.path.join(os.path.expanduser("~"), "AppData", "Local", "hermes", "cache", "scratch"),
)
# NB: os.path.join("E:", "x") yields "E:x" - a drive-relative path. Keep the
# drive letter and the rest of the path together as one string.
# The ComfyUI install dir is machine-specific, so it comes from the environment.
# NB: os.path.join("E:", "x") yields "E:x" - a drive-relative path - so the
# drive letter and the rest of the path must stay together in one string.
PROD_DIR = os.environ.get(
    "COMFYUI_PROD_DIR",
    "E:" + chr(92) + "ComfyUI_windows_portable_nvidia_cu126" + chr(92) + "ComfyUI_windows_portable",
)
PROD_IN = os.path.join(PROD_DIR, "ComfyUI", "input")
PROD_OUT = os.path.join(PROD_DIR, "ComfyUI", "output")
BASE = "http://127.0.0.1:8188"


def api(path, payload=None, timeout=240):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode("utf-8", "replace")
    return json.loads(raw) if raw.strip() else {}


def bc_apply(img_path, mask_path, seed, texture=0.10, detail=0.45, tag="t"):
    """Run BC_SkinTexture once on a single frame and return the output path."""
    in_img = os.path.join(PROD_IN, f"bct_{tag}_in.png")
    in_mask = os.path.join(PROD_IN, f"bct_{tag}_mask.png")
    import shutil
    shutil.copy2(img_path, in_img)
    shutil.copy2(mask_path, in_mask)
    nodes = {
        "1": {"class_type": "LoadImage", "inputs": {"image": os.path.basename(in_img)}},
        "2": {"class_type": "LoadImage", "inputs": {"image": os.path.basename(in_mask)}},
        "3": {"class_type": "ImageToMask",
              "inputs": {"image": ["2", 0], "channel": "red"}},
        "4": {"class_type": "BC_SkinTexture",
              "inputs": {"image": ["1", 0], "mask": ["3", 0],
                         "texture": texture, "detail": detail, "pore_scale": 1.0,
                         "feather": 8, "seed": seed, "threshold": 0.5,
                         "sam3_model": "sam3.1_multiplex_fp16.safetensors"}},
        "5": {"class_type": "SaveImage",
              "inputs": {"images": ["4", 0], "filename_prefix": f"bct_{tag}"}},
    }
    try:
        resp = api("/prompt", {"prompt": nodes, "client_id": "bcT"})
    except Exception as e:
        print("  prompt error:", str(e)[:100])
        return None
    pid = resp.get("prompt_id")
    if not pid:
        return None
    t0 = time.time()
    while time.time() - t0 < 240:
        time.sleep(2)
        try:
            h = api(f"/history/{pid}", timeout=60)
        except Exception:
            continue
        e = h.get(pid)
        if not e:
            continue
        if e.get("status", {}).get("status_str") == "error":
            print("  node error:", json.dumps(e.get("status", {}).get("messages", []))[:160])
            return None
        for o in e.get("outputs", {}).values():
            for im in o.get("images", []):
                p = os.path.join(PROD_OUT, im.get("subfolder", ""), im["filename"])
                if os.path.exists(p):
                    return p
    return None


def main():
    import numpy as np
    from PIL import Image

    video = os.path.join(SCRATCH, "bathe_full_raw.mp4")
    if not os.path.exists(video):
        print("нет видео")
        return 1
    frames_dir = os.path.join(SCRATCH, "tst_frames")
    os.makedirs(frames_dir, exist_ok=True)
    for f in os.listdir(frames_dir):
        os.remove(os.path.join(frames_dir, f))
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", video,
                    "-vf", "select='between(n,60,64)'", "-fps_mode", "passthrough",
                    os.path.join(frames_dir, "f_%03d.png")], check=True, timeout=300)
    frames = sorted(f for f in os.listdir(frames_dir)
                     if f.endswith(".png") and not f.startswith("m_"))
    print("кадров:", len(frames))

    # маска кожи: person-aware v2, иначе хроматический fallback
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
    try:
        from skin_mask_v2 import skin_mask_v2
    except Exception:
        skin_mask_v2 = None
    from skin_detail_shade import skin_mask

    masks = []
    for i, f in enumerate(frames):
        src = os.path.join(frames_dir, f)
        pil = Image.open(src).convert("RGB")
        if skin_mask_v2 is not None:
            m = np.asarray(skin_mask_v2(pil, use_comfy=False))
        else:
            arr = np.asarray(pil).astype(np.float32) / 255
            m = (skin_mask(arr) > 0.45).astype(np.float32)
        mp = os.path.join(frames_dir, f"m_{i:03d}.png")
        Image.fromarray((np.clip(m, 0, 1) * 255).astype(np.uint8)).save(mp)
        masks.append(mp)
        print(f"  маска {i}: {float((np.asarray(Image.open(mp))>0).mean())*100:.1f}%")

    results = {}
    for label, seed_fn in (("fixed_seed", lambda i: 12345), ("varying_seed", lambda i: 12345 + i)):
        outs = []
        for i, f in enumerate(frames):
            src = os.path.join(frames_dir, f)
            out = bc_apply(src, masks[i], seed_fn(i), tag=f"{label}_{i}")
            outs.append(out)
        print(f"  {label}: {sum(1 for o in outs if o)}/{len(outs)} кадров")
        if not all(outs):
            continue
        imgs = [np.asarray(Image.open(o).convert("RGB")).astype(np.float32) for o in outs]
        srcs = [np.asarray(Image.open(os.path.join(frames_dir, f)).convert("RGB")).astype(np.float32)
                for f in frames]
        d_out = float(np.mean([np.abs(imgs[i + 1] - imgs[i]).mean() for i in range(len(imgs) - 1)]))
        d_in = float(np.mean([np.abs(srcs[i + 1] - srcs[i]).mean() for i in range(len(srcs) - 1)]))
        inj = [imgs[i] - srcs[i] for i in range(len(imgs))]
        d_inj = float(np.mean([np.abs(inj[i + 1] - inj[i]).mean() for i in range(len(inj) - 1)]))
        mag = float(np.mean([np.abs(x).mean() for x in inj]))
        results[label] = {"delta_output": d_out, "delta_input": d_in,
                          "delta_injected": d_inj, "injected_magnitude": mag}
        print(f"    дельта выхода {d_out:.3f} | входа {d_in:.3f} | инжекта {d_inj:.4f} | амплитуда {mag:.3f}")

    print("\nИТОГ:")
    print(json.dumps(results, indent=2, ensure_ascii=False))
    print("Критерий: delta_injected у fixed_seed должна быть ~= 0 (вjection не меняется "
          "между кадрами => нет мерцания).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
