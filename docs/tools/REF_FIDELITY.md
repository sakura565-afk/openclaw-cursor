# Reference fidelity (`tools/ref_fidelity.py`)

Numeric checks that a generated product render still shows the **same subject** as the reference still—not merely “a chair” in words, but the same silhouette and colours.

## Why this exists

Vision-model scores and textual descriptions of the reference are unreliable when the reviewer never sees reference and render together. A render that **replaces** the product (different chair, same category) can still read as excellent prose. This tool puts images side by side and emits hard metrics that **fail when the subject was replaced**.

**Rule:** A vision-model score computed without the reference image next to the render is **not evidence** of reference fidelity.

## Usage

```bash
python tools/ref_fidelity.py --reference ref.png --render render.png --out out/
```

Writes `out/reference.png`, `out/render.png`, and `out/collage.png`, then prints JSON metrics on stdout.

## Metrics

| Metric | Meaning |
|--------|---------|
| `edge_iou` | Intersection-over-union of binarised Sobel edge maps after foreground bounding-box alignment. High = similar silhouette. |
| `edge_dice` | Dice coefficient on the same edge maps (symmetric overlap measure). |
| `palette_distance` | Mean CIE76 ΔE in Lab between corresponding cells of a **4×4** spatial colour grid (smaller = closer palettes). |
| `bbox_ratio_match` | `1 −` relative error between foreground bounding-box aspect ratios (`1.0` = identical aspect). |
| `verdict` | `same`, `drifted`, or `replaced` (see thresholds below). |

### How edges are computed

1. Downscale both images to a common max edge (192 px) and convert to grayscale.
2. Apply 3×3 Sobel filters; take gradient magnitude.
3. Otsu threshold → binary edge map.
4. Foreground bounding box = tight box around edge pixels (+1 px pad).
5. Crop each map to its box, resize both to the same pixel grid (nearest neighbour), then compute IoU/Dice.

### Verdict thresholds (from `edge_iou` only)

| `edge_iou` | Verdict |
|------------|---------|
| ≥ 0.70 | `same` |
| ≥ 0.45 | `drifted` |
| &lt; 0.45 | `replaced` |

`replaced` means the silhouette no longer matches closely enough—the usual signal that the model swapped the product.

## Reading the collage

`collage.png` is reference on the **left**, render on the **right**, scaled to **equal height** on a **white** canvas with a small gap. Use it for human review alongside the numbers: if the shapes look unrelated but a text-only judge gave 9/10, trust the metrics and the collage, not the score.

## Programmatic API

```python
from tools.ref_fidelity import RefFidelity

result = RefFidelity().compare("ref.png", "render.png", "out/")
```

Missing files raise `FileNotFoundError` with an explicit message (CLI exits code 2).