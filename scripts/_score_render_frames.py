"""Score every frame of an H3 render with the pipeline validator.

Prints the visibility ranking so a wide "context" frame can be compared
against the macro crop the picker prefers.
"""
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from scripts.anatomy_still.metrics import validate_anatomy  # noqa: E402


def main() -> int:
    video = Path(sys.argv[1])
    mask_png = Path(sys.argv[2])
    limit = int(sys.argv[3]) if len(sys.argv) > 3 else 175

    work = video.parent / "_pick_scan"
    work.mkdir(exist_ok=True)
    for f in work.glob("f*.png"):
        f.unlink()
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(video),
                    "-frames:v", str(limit), str(work / "f%05d.png")],
                   capture_output=True)
    frames = sorted(work.glob("f*.png"))
    print("  кадров извлечено:", len(frames))

    mask = (np.asarray(Image.open(mask_png).convert("L")) > 127).astype(np.uint8) * 255
    print("  маска: %d пикселей (%.2f%%)" % ((mask > 127).sum(), (mask > 127).sum() / mask.size * 100))

    rows = []
    for f in frames:
        img = np.asarray(Image.open(f).convert("RGB"))
        res = validate_anatomy(img, mask)
        rows.append((int(f.stem[1:]), res.visibility, res.skin_fraction,
                     res.cloth_score, res.texture_score, res.passed))

    rows.sort(key=lambda r: -r[1])
    print("  топ-12 по visibility:")
    for r in rows[:12]:
        print(f"    кадр {r[0]:3d}: vis={r[1]:.3f} skin={r[2]:.3f} "
              f"cloth={r[3]:.3f} texture={r[4]:.3f} passed={r[5]}")
    print(f"  кадров с passed=True: {sum(1 for r in rows if r[5])} / {len(rows)}")
    print(f"  кадров с vis>0.9: {sum(1 for r in rows if r[1] > 0.9)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
