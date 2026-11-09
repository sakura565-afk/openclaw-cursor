# Independent ground truth for `skin_texture_metrics`

CPU-only synthetic oracle and blind visual pre-check for the existing pore /
plastic / digital-sand classifier. No GPU, ComfyUI, network, or video rendering
is used. Thresholds are measured from core controls, not fitted to blind labels.

## What is constructed

The default set contains **20 plates of 192 x 192 pixels**:

- 4 flat, pore-free `plastic` plates at different brightness levels;
- 4 sparse coarse-pore plates made by `pore_injector.field.build_pore_field`;
- 4 isotropic Gaussian-noise plates with requested sigma 12, 16, 20, 24 in
  8-bit intensity units;
- 4 pore+noise mixtures, constructively labelled `digital_sand` because noise
  is present;
- 4 independent diagnostics using the injector's normal fine-pore regime
  (`sigma=1.1`, rim and two-octave population). Their constructive label stays
  `pores` even when the legacy survival-only classifier disagrees.

Every case has a PNG and an NPZ with signed `pore_delta` and `noise_delta`.
The manifest records seeds, requested and realised noise sigma, nominal and
actual pit density, actual pit count, core sigma and FWHM diameter. Paths in
JSON are relative to the artefact root.

The four cases named `*_core` are the required acceptance controls. The other
cases are probes; fine-pore failures on survival alone are reported, not hidden.

## Generate

From the repository root:

```bash
.venv/Scripts/python.exe -m scripts.skin_metric_groundtruth
```

The default output is `scripts/skin_metric_groundtruth/artifacts/`.
A different output and reproducible seeds can be supplied:

```bash
.venv/Scripts/python.exe -m scripts.skin_metric_groundtruth \
  --out path/to/artefacts --seed 4102 --blind-seed 90210
```

Important outputs:

- `synthetic_manifest.json` — constructive labels and generator parameters;
- `calibration_report.json` and `.md` — label -> metric -> class tables,
  mismatches, and provisional threshold rationale;
- `blind/blind_sheet.png` — shuffled, numbered crops, made through
  `eval_judge.sheet.build_sheet`;
- `blind/judge_prompt.txt` — ready-made per-crop classification prompt;
- `blind/blind_key.json` — crop-to-case mapping; **withhold this from the judge**;
- `blind/visual_labels.json` — the recorded blind AI pre-check, not a human
  validation;
- `blind/blind_report.json` — blind-set and visual-label status.

## Human blind review

Give the reviewer only `blind_sheet.png` and `judge_prompt.txt`. Do not provide
the manifest, key, metric table, or case-named PNGs. Save the returned per-crop
JSON with `"judge"` and `"is_human": true`, then rerun:

```bash
.venv/Scripts/python.exe -m scripts.skin_metric_groundtruth \
  --visual-labels scripts/skin_metric_groundtruth/artifacts/blind/visual_labels.json
```

This preserves the same blind seed and adds visual labels as a separate column.
It does not fit thresholds to those labels.

## Measured default result

With the shape-augmented classifier in `scripts/skin_texture_metrics/metrics.py`
(`shape_component_density_per_10k` from `scripts/skin_metric_shape`):

- Core constructive controls: **4/4 correct**.
- All constructive labels: **20/20 correct** (was **16/20** under
  `legacy_classify` on survival and pore RMS alone).
- Blind human labels (`artifacts/blind/human_labels.json`): **20/20**
  human/constructive agreement; metric vs human: **20/20**.
- Blind AI visual pre-check (`artifacts/blind/visual_labels.json`):
  **18/20 metric/visual matches**, **18/20 visual/constructive matches**.
  This is not validation and is not a substitute for human review.
- Former survival-only fine-pore misses (now `pores` via component sparsity):
  crop 10 (RMS 5.0598, survival 0.2557, density 259.874),
  crop 11 (RMS 5.3331, survival 0.2690, density 134.006),
  crop 18 (RMS 3.4521, survival 0.2513, density 277.778),
  crop 20 (RMS 6.6245, survival 0.2705, density 250.380).
- Mixed / digital-sand component density stays well above the sparse pore band
  (for example core mixed 1099.447 comp/10k versus core coarse pores 50.998).
- Remaining metric vs blind-AI disagreements: mixed crops 2 and 4 still called
  `pores` by the blind AI judge while both construction and metric say
  `digital_sand`. These are judge/constructive disagreements, not metric errors.

## Threshold recommendation

Retain **plastic max pore RMS = 4.90** and **digital-sand max survival = 0.60**
for the legacy stage. They separate the core controls: core pore RMS is 5.8114,
core noise survival is 0.1162, core mixed survival is 0.3198, and core pore
survival is 0.6208.

Add **shape max component density / 10k = 575.222** (midpoint of core pore
maximum 50.998 and core sand minimum 1099.447), with minimum density
**25.499** and minimum pore RMS **2.906**, all from the four `*_core` cases only.
On the full 20-case constructive set, pore-class densities span
50.998–277.778 and sand-class densities span 1099.447–3345.920; separation
ratio **3.958×** vs survival **0.786×** (overlap).

Do not claim validation. Fine-pore survival (0.2513–0.2705) still overlaps the
mixed pore+noise range (0.2121–0.3198); lowering survival alone cannot repair
those cases honestly. Spatial sparsity of signed high-pass components is the
added discriminator. The final eye remains the human's.

## Tests

```bash
.venv/Scripts/python.exe -m pytest -q tests/test_skin_metric_groundtruth.py
.venv/Scripts/python.exe -m pytest -q
```

Tests live under `tests/`, so the repository `testpaths` configuration collects
them. The suite checks the four core classes, generator metadata, signed truth
reconstruction, blind ordering/prompt separation, calibration reporting, and
the committed 20/20 constructive regression on `artifacts/`.
