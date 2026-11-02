"""Reclining photo: exact copy of the APPROVED recipe (2509 + colour grade).

The user reviewed a comparison sheet and picked the LEFT panel - the plain
Qwen-Image-Edit-2509 render with the colour grade - as the acceptable one; the Qwen-2.1
variants were rejected because they do not match the reference photo.

So for the reclining photo the priority is REFERENCE FIDELITY (pose, framing, face,
background) over skin micro-texture. Recipe = production default:
  UNet  qwen_image_edit_2509_fp8_e4m3fn + qwen_image_edit_plus_nsfw_lora (strength 1.0),
  denoise 1.0, cfg 1.0, prompt with body-proportion lock, then scripts/skin_grade.py.

Two prompts are tried: the body-lock one (same as the approved frame) and a
garment-focused one for the reclining pose.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time

import local_paths

ROOT = str(local_paths.REPO_ROOT)
SCRATCH = str(local_paths.SCRATCH)
PROD_IN = str(local_paths.PROD_IN)
PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
shutil.copy2(os.path.join(SCRATCH, "qe_recline_src.png"), os.path.join(PROD_IN, "recline_src.png"))

KEEP = ("Keep her exact pose: reclining on the pale sand-like surface, propped on one elbow, "
        "knees bent, legs extending to the left. Keep her face, hair, expression, the surface, "
        "the lighting, the framing and the camera angle exactly as they are.")
PROP = ("Preserve her original body shape exactly as in the source photo: identical waist width, "
        "identical hip width, identical leg proportions and body weight. Do not beautify, slim, "
        "exaggerate or idealize the body, keep natural real-human proportions.")

P1 = ("The woman is now completely nude - the white sheer bodysuit is entirely gone: no clothing, "
      "no bodysuit, no sheer fabric, no sleeves, no straps, no underwear, no translucent "
      "material, nothing covering any part of her body. " + KEEP + " " + PROP +
      " Real human skin with natural skin tone. no plastic skin, no airbrushing, no smooth skin, "
      "no mannequin, no waxy skin, no body hair, no fuzz on the skin, no sheer fabric, no white "
      "fabric, no cloth, no nylon, no stretched fabric over the body, "
      "no distortions, no extra fingers, no extra limbs")

P2 = ("Remove the white sheer bodysuit completely: the woman is fully naked, every part of her "
      "body and skin visible, no fabric of any kind left on her. " + KEEP + " " + PROP +
      " Realistic natural human skin, natural skin tone. no sheer fabric, no white fabric, "
      "no bodysuit, no sleeves, no straps, no nylon, no translucent fabric, no stretched fabric, "
      "no plastic skin, no airbrushing, no smooth skin, no waxy skin, no body hair, "
      "no distortions, no extra fingers, no extra limbs")

VARIANTS = [
    ("rec_plain_p1", P1, "88231"),
    ("rec_plain_p2", P2, "88231"),
]

for tag, prompt, seed in VARIANTS:
    pf = os.path.join(SCRATCH, f"prompt_{tag}.txt")
    open(pf, "w", encoding="utf-8").write(prompt)
    env = dict(os.environ, PYTHONPATH=ROOT,
               QWEN_EDIT_CFG="1.0",
               QWEN_EDIT_MODEL="qwen_image_edit_2509_fp8_e4m3fn.safetensors",
               QWEN_NSFW_LORA="qwen_image_edit_plus_nsfw_lora_safetensors.safetensors",
               QWEN_NSFW_LORA_STRENGTH="1.0")
    cmd = [PY, "-u", "scripts/qwen_edit_run.py", tag, "recline_src.png", seed,
           "1.0", pf, "30", "1280", "832"]
    print(f"\n=== {tag}: 2509 + NSFW-LoRA, denoise 1.0, 1280x832 ===", flush=True)
    t0 = time.time()
    try:
        r = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True, timeout=3600)
        print(f"rc={r.returncode} за {time.time()-t0:.0f} с", flush=True)
        for line in (r.stdout or "").splitlines()[-3:]:
            print("   ", line[:180], flush=True)
        if r.returncode != 0:
            print("   stderr:", (r.stderr or "")[-300:], flush=True)
    except subprocess.TimeoutExpired:
        print("   ТАЙМАУТ", flush=True)

print("\nГОТОВО", flush=True)
