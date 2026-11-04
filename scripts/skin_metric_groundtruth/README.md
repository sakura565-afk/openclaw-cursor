# Independent ground truth for `skin_texture_metrics`

CPU-only synthetic oracle and blind visual pre-check for the existing pore /
plastic / digital-sand classifier. No GPU, ComfyUI, network, or video rendering
is used. The existing thresholds are measured, not modified.

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
  `pores` even when the classifier disagrees.

Every case has a PNG and an NPZ with signed `pore_delta` and `noise_delta`.
The manifest records seeds, requested and realised noise sigma, nominal and
actual pit density, actual pit count, core sigma and FWHM diameter. Paths in
JSON are relative to the artefact root.

The four cases named `*_core` are the required acceptance controls. The other
cases are probes; fine-pore failures are deliberately not hidden or relabelled.

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

- Core constructive controls: **4/4 correct**.
- All constructive labels: **16/20 correct**; all four misses are fine-pore
  diagnostics.
- Blind AI visual pre-check: **14/20 metric/visual matches**, **18/20
  visual/constructive matches**. It is not a substitute for human review.
- Fine-pore misses: crop 10 (`digital_sand`, RMS 5.0598, survival 0.2557),
  crop 18 (`plastic`, RMS 3.4521, survival 0.2513), crop 20 (`digital_sand`,
  RMS 6.6245, survival 0.2705), and crop 11 (`digital_sand`, RMS 5.3331,
  survival 0.2690). The blind AI judge called all four `pores`.
- Additional metric/visual disagreements: mixed crops 2 and 4 were called
  `pores` by the blind AI judge, while both construction and metric say
  `digital_sand`. These are visual-judge/constructive-truth disagreements, not
  evidence that the metric misclassified those two mixtures.

## Threshold recommendation

Retain **plastic max pore RMS = 4.90** and **digital-sand max survival = 0.60**
only provisionally. They separate the core controls: core pore RMS is 5.8114,
core noise survival is 0.1162, core mixed survival is 0.3198, and core pore
survival is 0.6208. The margin above the pore-survival threshold is small.

Do not claim validation. Fine-pore survival (0.2513–0.2705) overlaps the mixed
pore+noise range (0.2121–0.3198), so lowering one survival threshold cannot
honestly repair all fine-pore misses without admitting mixtures. A shape or
sparsity feature and human blind labels are needed before changing the metric.
The final eye remains the human's.

## Tests

```bash
.venv/Scripts/python.exe -m pytest -q tests/test_skin_metric_groundtruth.py
.venv/Scripts/python.exe -m pytest -q
```

Tests live under `tests/`, so the repository `testpaths` configuration collects
them. The suite checks the four core classes, generator metadata, signed truth
reconstruction, blind ordering/prompt separation, and calibration reporting.
