# Pore injector (Kimi)

CPU-only MP4 post-pass. It adds sparse dark pits with a lighter rim, not
isotropic random noise. One seeded field is generated for the whole processed
clip, warped current-to-previous with Farneback flow, and the injected signal
is filtered by a recursive temporal low-pass.

The amplitude is `max(local_RMS_HF - threshold, 0)` from each current frame.
There is no detail floor, so smooth regions receive no injection. Without an
external PNG, the skin prior is computed in uint8 HSV, rejects `V > 0.95` or
`S < 0.10`, and retains only the largest connected component.

## Run

From the repository root:

```bash
PYTHONPATH=scripts .venv/Scripts/python.exe -m pore_injector_kimi \
  --video input.mp4 --out output.mp4 \
  --strength 2.0 --pit-density 0.003 --rim 0.35 \
  --pore-sigma 1.25 --threshold 0.008 --temporal-sigma 1.5 \
  --seed 42 --two-octave --max-frames 120
```

Add `--mask skin.png` for an external grayscale uint8 mask with the same
geometry as the video. `--no-two-octave` disables the coarse pore population.
`pit_density` is the Bernoulli probability of a pore centre per pixel.

The MP4 uses OpenCV's `mp4v` writer and preserves source fps and dimensions;
audio is not copied. The report is written to `output.json` (the output suffix
is replaced by `.json`).

## Report

All measurements are taken by decoding the source and the **encoded output**.
Pore energy uses the canonical 3--12 px FFT band from
`scripts/probe_pore_spectrum.py` inside a fixed ROI derived from the first
non-empty mask. Temporal RGB and HF deltas use consecutive full frames and
the same Laplacian definition as `compare_video_texture.py`.

Each metric has `before`, `after`, and `delta_pct`. The report also contains
mean mask coverage, processed frame count, ROI, fps, and all parameters.

## Tests

```bash
.venv/Scripts/python.exe -m pytest tests/test_pore_injector_kimi.py -q
```
