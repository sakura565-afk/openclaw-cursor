# Still detail ceiling

CPU/offline comparison of still-image detail candidates. The evaluator does not
call a resizer a detail-restoration model and does not turn unavailable learned
backends into synthetic success rows.

## Candidates

- `bicubic`, `lanczos`: OpenCV resampling baselines.
- `pore_minimax`: the still equivalent of `scripts/pore_injector`, using its
  video-default pore field, mask, structure gate and strength.
- `pore_kimi`: the still equivalent of `scripts/pore_injector_kimi`, likewise
  using its video defaults.
- `seedvr2`: an explicitly supplied cached output, or the existing
  `scripts/upscale_video.py --backend seedvr2` adapter when `--run-seedvr2` is
  explicitly requested. The adapter checks both the output file and the
  reported backend; a fallback is never accepted as SeedVR2.
- `real_esrgan`, `codeformer`: measured only from an explicitly supplied cached
  output. Otherwise the row is `unavailable` with the real reason. CodeFormer
  is face restoration, not a general body-pore detail lifter.

No network download, GPU render or ComfyUI call is needed for the normal run.

## Run

From the repository root, with the four approved images in the repository root
or `HERMES_SCRATCH_DIR`:

```bash
./.venv/Scripts/python.exe -m scripts.still_detail_ceiling \
  --seedvr2-output qe_graded_w0.9="$HERMES_SCRATCH_DIR/seedvr2_hi.png" \
  --review-roi qe_graded_w0.9=432,320,688,576 \
  --review-roi KATYA_UNDDRESSED=272,152,320,200
```

The two manual ROIs are intentional. The qe ROI includes facial contours so
that sharpening halos are visible. KATYA has no clean 256-pixel pore patch in
the reviewed source; its 48-pixel ROI is only an artifact check and is marked
with a warning in the report.

To evaluate another offline learned result:

```bash
./.venv/Scripts/python.exe -m scripts.still_detail_ceiling \
  --cached-output real_esrgan:style_name=relative/output.png
```

Use `codeformer:style_name` or `seedvr2:style_name` for those backends.
`--image` can be repeated to replace the default four-style discovery.

## Geometry contract

The default requested width is 1072, which is not divisible by 32. The CLI
normalizes it to the nearest aligned width, 1088, and records
`requested_width=1072` and `width_adjustment_px=16`. Height is derived from the
source aspect ratio and rounded to the same grid. It is never independently
stretched. The signed height error, absolute AR rounding error and any
violation of the 1% tolerance are explicit in JSON and stdout.

## Metrics

All available candidates in one style use the same normalized review ROI and
the same target geometry.

- `pore_energy`: FFT energy in the canonical 3--12 px band.
- `pore_survival`: Task 1's `survival`, the pore-band energy after sigma=1.2
  Gaussian blur divided by the energy before blur.
- `survE_delta`: after-blur pore energy compared with Lanczos. A positive total
  does not prove that the added component is coherent.
- `pore_gate`: Task 1's `is_pore_like(Lanczos, candidate)`, including the
  survival of the actual added component. This rejects energy that behaves
  like noise even when the finished image has a positive surviving-energy gain.
- `structure_score`, `pore_rms`, Task 1 classification and Laplacian HF survival
  are also stored.
- `canonical_mae_255`: RGB MAE after resizing the candidate back to the source
  geometry and cropping the same source-pixel ROI. It is a difference metric,
  not a perceptual quality score.

`OVERSHARP` requires more than 5% extra Laplacian energy and reduced pore
survival. It is not applied to intentional pore injectors; their added
component is judged by the Task 1 gate instead.

## Outputs

By default the package writes `results/report.json` and one native-target
`review_sheet.png` per style. Sheet crops are not resized again to fit a
window. The console prints the complete table, the selected source-pixel crop,
scaling violations and small-ROI warnings.

The automatic recommendation only considers an injector when its surviving
energy increases **and** the Task 1 pore gate accepts it. A resizer or an
injector can therefore never be described as recovered ground-truth detail.
The final choice remains a same-crop human eye check.

## Tests

```bash
./.venv/Scripts/python.exe -m pytest scripts/still_detail_ceiling/tests -q
```
