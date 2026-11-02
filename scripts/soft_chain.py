"""Full final chain with a SOFT face: Qwen edit -> ReActor (no GFPGAN) -> grade -> expert retouch.

The inserted face was the sharpest, most photographic element in the frame while the body
was smooth - that mismatch is what makes a composite look composited. Dropping the GFPGAN
face restore keeps the inswapper's natural softness, so the face sits closer to the body.
"""
from __future__ import annotations

import os
import subprocess
import sys

from pathlib import Path

ROOT = str(Path(__file__).resolve().parents[1])
SCRATCH = os.environ.get(
    "HERMES_SCRATCH_DIR",
    os.path.join(os.path.expanduser("~"), "AppData", "Local", "hermes", "cache", "scratch"),
)
PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
NATIVE_INPUTS = os.environ.get(
    "HERMES_NATIVE_INPUTS",
    os.path.join(
        os.path.expanduser("~"), "AppData", "Local", "hermes", "cache", "documents", "native-inputs"
    ),
)
SRC_PHOTO = os.path.join(
    NATIVE_INPUTS,
    "664dd4bff18f966d9f7f869d66cd50c647d0218edde2c9cf9823dc666bc502be",
    "img_320df09f260d.jpg",
)

sys.path.insert(0, SCRATCH)
import reactor_restore as rr  # noqa: E402

JOBS = [
    # (базовый рендер, тег)
    ("qe_rec_2509_urp10.png", "recline"),
    ("qfix_hand.png", "stand"),
]

for base, tag in JOBS:
    print(f"\n=== {tag}: {base} ===", flush=True)
    out = rr.run(f"{tag}_soft", edit=base, source=SRC_PHOTO, restore="none")
    if not out:
        print("ReActor не отработал", flush=True)
        continue
    graded = os.path.join(SCRATCH, f"{tag}_soft_graded.png")
    r = subprocess.run([PY, os.path.join(SCRATCH, "skin_grade.py"),
                        "--src", out, "--out", graded,
                        "--warmth", "1.15", "--sat", "0.30", "--micro", "0.8",
                        "--face-a", os.path.join(SCRATCH, base),
                        "--face-b", out],
                       capture_output=True, text=True, timeout=600, cwd=ROOT)
    print(r.stdout.strip() or r.stderr.strip()[:200], flush=True)
    final = os.path.join(SCRATCH, f"SOFT_{tag}_final.png")
    r2 = subprocess.run([PY, os.path.join(ROOT, "scripts", "skin_freq_retouch.py"),
                         "--src", graded, "--out", final,
                         "--lb-strength", "0.35", "--hf-strength", "0.35", "--grain", "0.010"],
                        capture_output=True, text=True, timeout=600, cwd=ROOT)
    print(r2.stdout.strip() or r2.stderr.strip()[:300], flush=True)
    print("ИТОГ:", final, os.path.getsize(final) // 1024, "КБ", flush=True)
