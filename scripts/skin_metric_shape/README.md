# skin_metric_shape

CPU-only shape/sparsity extension for `skin_texture_metrics`. It closes the
blind spot where resolved fine Gaussian pores have the same 3–12 px FFT blur
survival as isotropic grain.

## Selected statistic

`component_density_per_10k` counts signed high-pass components separately above
and below `max(1.0, std(highpass))`, using 8-connectivity. High-pass is
`gray - gaussian_filter(gray, sigma=3)`. Normalising by pixel count keeps the
statistic comparable across still crops and canonical video ROIs.

On the 20 human-labelled blind crops:

- old survival separation ratio: **0.7858** (ranges overlap);
- local-extremum peak area fraction: **2.2489×**;
- signed connected-component density: **3.9580×**;
- median equivalent component width: **2.2361×**;
- pit/non-peak active area ratio: **0.7983** (overlap);
- absolute-Laplacian/local-extremum correlation: **1.1644×**.

Component density was therefore selected. Its upper bound is the midpoint of
core-control class bounds, not of the four failing fine-pore cases. Activity and
minimum-density guards stop flat plastic and isolated shading blobs from being
promoted to pores. Full numbers are in `artifacts/blind_calibration.json` and
`.md`; eight additional seeds are reported in `artifacts/synthetic_validation.*`.

## Still and video use

The existing still API now includes `shape_*` fields:

```python
from scripts.skin_texture_metrics import measure_image
result = measure_image("crop.png")
print(result["classification"], result["shape_component_density_per_10k"])
```

For decoded frames, `measure_frames` adds `temporal_shape` per ROI and overall.
It compares adjacent high-pass fields and component densities. Correlation is
unregistered: motion can reduce it, so it is evidence, not an automatic reject.
Standalone arrays use `measure_shape` and `measure_shape_sequence`.

## Reports

```bash
python -m scripts.skin_metric_shape calibrate
python -m scripts.skin_metric_shape validate
python -m scripts.skin_metric_shape recheck --render-root path/to/renders
```

`recheck` reuses `compare_video_texture.extract` and `canonical_sizes`, and the
same measurement path as `eval_detail_candidates.py`. It never calls ComfyUI or
the GPU. Missing archives are reported as missing rather than substituted.

## Compatibility and limits

`classify(mapping)` without `shape_component_density_per_10k` follows the legacy
rule exactly. Existing FFT, survival, structure, rollback, and before/after gate
fields are unchanged. A `pores` label is not proof of biological skin texture;
real renders with changed verdicts still need human visual confirmation.
