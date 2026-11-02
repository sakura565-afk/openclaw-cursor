"""Final chain for the standing frame: hand-fix -> ReActor -> face-aware grade."""
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

base = sys.argv[1] if len(sys.argv) > 1 else "qfix_hand.png"
tag = sys.argv[2] if len(sys.argv) > 2 else "final_stand"

# 1) ReActor: возвращаем лицо из исходного фото
sys.path.insert(0, SCRATCH)
import reactor_restore as rr  # noqa: E402

out = rr.run(tag + "_ra", edit=base, source=SRC_PHOTO)
if not out:
    raise SystemExit("ReActor не отработал")
reactor_out = out

# 2) цветокоррекция: маска кожи + маска лица по diff pre/post ReActor
grade_out = os.path.join(SCRATCH, f"{tag}_graded.png")
r = subprocess.run([PY, os.path.join(SCRATCH, "skin_grade.py"),
                    "--src", reactor_out, "--out", grade_out,
                    "--warmth", "1.15", "--sat", "0.30", "--micro", "0.8",
                    "--face-a", os.path.join(SCRATCH, base),
                    "--face-b", reactor_out],
                   capture_output=True, text=True, timeout=600, cwd=ROOT)
print(r.stdout.strip() or r.stderr.strip()[:200])
print("ГОТОВО:", grade_out, os.path.getsize(grade_out) // 1024, "КБ")
