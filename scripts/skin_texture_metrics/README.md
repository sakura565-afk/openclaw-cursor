# skin_texture_metrics

CPU-only metric for separating pore-band structure from digital sand and
pore-free plastic. It reuses:

- `probe_pore_spectrum.band_energy` and its ROI convention;
- `eval_judge.metrics.gaussian_blur` (the same CPU/scipy fallback used by the
  existing probe);
- `compare_video_texture.extract` and `canonical_sizes` for decoding and one
  canonical pixel geometry across clips.

No GPU, ComfyUI server, rendering, or network access is used.

## Measurements

For every ROI, in the canonical scale:

1. `pore = sum(abs(FFT(gray)) ** 2)` for spatial periods **3–12 px**.
2. Blur the same ROI with Gaussian **sigma=1.2**, then calculate `pore_blur`
   in exactly the same band.
3. `survival = pore_blur / pore`.
4. `pore_rms = sqrt(pore) / pixel_count` compensates the unnormalised FFT and
   ROI area. Unlike `pore/spec`, it is not driven by mean skin brightness.
5. `structure_score` is bounded to 0..1:

   ```text
   activity  = clip((pore_rms - 4.90) / (6.20 - 4.90), 0, 1)
   coherence = clip((survival - 0.45) / (0.80 - 0.45), 0, 1)
   score     = activity * coherence
   ```

The single-image classifier is deliberately simple and auditable:

- `pore_rms < 4.90` → **plastic**;
- otherwise `survival < 0.60` → **digital_sand**;
- otherwise → **pores**.

These thresholds are calibrated on our eye-labelled reference clips. They are
not universal constants and are not an independent validation on unseen skin.
The original FFT energies remain in the output so another calibration does not
require changing the spectral code.

## Rollback gate

```python
from scripts.skin_texture_metrics import is_pore_like

accepted, reason = is_pore_like(before_rgb, after_rgb)
```

The inputs must have identical shape. The gate requires:

- pore-band energy growth above 2%;
- **survival of the added component** `after - before` of at least 0.45;
- the finished image to land in the `pores` class.

Measuring the delta is essential. A BC result can inherit high survival from
the underlying smooth skin and look structured to the single-image classifier,
while the component BC actually added has noise-like survival.

## CLI

With real materials below `HERMES_SCRATCH_DIR` (or `HERMES_NATIVE_INPUTS`):

```bash
.venv/Scripts/python.exe -m scripts.skin_texture_metrics --real-set --frames 12
```

Arbitrary videos use the first video as the canonical scale reference:

```bash
.venv/Scripts/python.exe -m scripts.skin_texture_metrics \
  --video reference=path/to/reference.mp4 \
  --video candidate=path/to/candidate.mp4
```

A still-image gate and a JSON report:

```bash
.venv/Scripts/python.exe -m scripts.skin_texture_metrics \
  --before path/to/before.png --after path/to/after.png \
  --json-out path/to/report.json
```

The default skin ROIs are the established `thigh` and `chest` crops. Override
with repeatable `--roi name=x0,y0,x1,y1` in normalised coordinates.

## Tests

```bash
.venv/Scripts/python.exe -m pytest -q tests/test_skin_texture_metrics.py
```

Synthetic tests cover coherent Gaussian structure, white noise, flat plastic,
shape mismatch, and score bounds. Real-material tests skip cleanly when the
reference clips or BC pairs are unavailable. See `REPORT.md` for the measured
reference table and the known failure of the single-image classifier.
