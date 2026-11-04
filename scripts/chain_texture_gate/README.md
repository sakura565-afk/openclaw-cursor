# chain_texture_gate

Survival-based texture acceptance for long H3 clips. This package wraps, rather than rewrites, `scripts/h3_chain_pipeline_kimi/`: its renderer invocation, fixed FL2VA flags, resume state, per-chunk seeds, exact frame joining, 40 ms equal-power audio joins, and mean/tail drift extraction remain in that package. FFT band energy, matched blur, survival, structural score, and classification come from `scripts/skin_texture_metrics/`.

No ComfyUI call is made by importing the module, planning, testing, or running the offline comparison.

## Why survival, not pore energy

Pore energy says how much activity is in the 3–12 px band, not whether that activity is pore-like. Measured `PORE_TIGHT_012` sand has **more** pore RMS than plastic (6.403 versus 4.750). BC output can also inherit convincing raw survival from smooth underlying skin while its actual added component is noise.

For every candidate frame this gate measures two different things through `skin_texture_metrics.measure_image`:

- finished texture: `pore`, `pore_rms`, `texture_survival`, and `structure_score`;
- added component: `candidate_frame - incoming_chunk_anchor`, then the same pore band before/after Gaussian blur with sigma 1.2. The report field **`survival` is added-component survival**.

Thus a noisy BC delta cannot hide behind the finished image. This is not a biological pore detector: motion inside fixed skin ROIs also enters the delta, and the available set has no independently eye-approved real-pore positive.

## Decision rule

A chunk is accepted only when:

1. the existing mean and contiguous-tail face drift are at most `--max-face-drift` (default 8.0), all measurements are finite, and pore energy is positive;
2. added-band survival is at least `max(--min-survival, baseline_survival * (1 - --max-survival-drop))`;
3. finished structure score is at least `max(--min-structure, baseline_structure * (1 - --max-structure-drop))`.

The **first accepted chunk** is the fixed baseline for all later chunks. It is not replaced by each preceding chunk, so small losses cannot ratchet downward unnoticed. A failed attempt is rerendered from the same incoming anchor with the existing per-attempt seed rule; accepted earlier chunks are untouched. Exhausted attempts retain the existing best-effort assembly behavior, but the chunk and final `quality_gate_passed` stay false and the CLI returns 3. Gate settings are included in the resume fingerprint.

Defaults and their measured basis:

| Setting | Default | Basis |
|---|---:|---|
| `min_survival` | **0.45** | Existing calibrated `MIN_ADDED_SURVIVAL`; real BC 0.10/0.20/0.30 added deltas measure 0.226/0.226/0.270, all below it. |
| `min_structure` | **0.18** | Reference scores: plastic 0.144, sand 0.093, flow 0.240. This is 25% below flow while remaining above both negative references. |
| `max_survival_drop` | **0.15** | MASTURB2 offline survival retention was 101.1% overall and 102.9% across the seam; 15% leaves room for ordinary variation without permitting a noise collapse. |
| `max_structure_drop` | **0.20** | The same artifact retained 87.1% structure overall and 89.9% across the seam. A 20% limit covers that measured 12.9% overall loss with margin; it does not claim zero loss. |

These are regression thresholds on the available artifacts, not universal constants or an independent generalization study.

## Real long-render command — documented, not run

The GPU/ComfyUI render was **not executed**. When the GPU is available, this is the actual entry point:

```bash
export COMFYUI_PROD_DIR="$COMFYUI_PORTABLE_ROOT"
export COMFYUI_PROD_URL=http://127.0.0.1:8188

.venv/Scripts/python.exe -m scripts.chain_texture_gate \
  --image "$INPUT_DIR/portrait.png" \
  --prompt-file "$INPUT_DIR/prompt.txt" \
  --audio "$INPUT_DIR/track.wav" \
  --output "$OUTPUT_DIR/long.mp4" \
  --chunk-frames 56 --total-frames 166 \
  --seed-base 42 --max-retries 2 --tail-frames 5 \
  --max-face-drift 8.0 \
  --min-survival 0.45 --min-structure 0.18 \
  --max-survival-drop 0.15 --max-structure-drop 0.20
```

Use a new output/state path when changing inputs or take settings. The old `--min-texture` and `--max-texture-drop` energy-ratchet options are deliberately disabled in this entry point; positive pore energy remains only a fail-closed sanity check.

An offline plan performs no media, state, ffmpeg, renderer, or ComfyUI I/O:

```bash
.venv/Scripts/python.exe -m scripts.chain_texture_gate \
  --image portrait.png --prompt-file prompt.txt --audio track.wav \
  --output long.mp4 --chunk-frames 56 --total-frames 166 --plan
```

## Chunk report

The existing state/report attempt records now include:

- `face_drift`, `face_drift_tail`, `face_drift_last`, and `tail_frames`;
- `pore`, `pore_rms`, `texture_survival`, added-component `survival`, and `structure_score`;
- `accepted`/`gate_passed`, `reasons`, `penalty`, active survival/structure floors, and `baseline_chunk`.

The final summary adds mean/min added survival, raw survival, and structure. `make_chunk_report`/`format_chunk_report` expose the same fields for standalone CPU use.

## Offline one-chunk versus chained table

Run entirely on existing videos:

```bash
.venv/Scripts/python.exe -m scripts.chain_texture_gate.offline \
  --single "$HERMES_SCRATCH_DIR/MASTURB2_5s.mp4" \
  --assembled "$HERMES_SCRATCH_DIR/MASTURB2_V6.mp4" \
  --boundary 120 --frames 12 --seam-frames 6 \
  --work "$HERMES_SCRATCH_DIR/chain_texture_gate_offline/masturb2" \
  --json-out "$HERMES_SCRATCH_DIR/chain_texture_gate_offline/masturb2_report.json"
```

Actual result (12 uniform samples per video, six frames on each side of the boundary):

| Row | Frames | Pore RMS | Raw survival | Structure | Pore energy |
|---|---:|---:|---:|---:|---:|
| single chunk | 12 | 5.913 | 0.803 | 0.606 | 7.613e10 |
| assembled | 12 | 5.818 | 0.812 | 0.528 | 7.274e10 |
| seam before | 6 | 5.935 | 0.797 | 0.626 | 7.682e10 |
| seam after | 6 | 5.873 | 0.820 | 0.563 | 7.422e10 |
| source seam window | 12 | 5.904 | 0.808 | 0.595 | 7.552e10 |
| assembled seam window | 12 | 5.841 | 0.818 | 0.538 | 7.323e10 |

At the boundary itself, the added component has survival **0.563**, finished structure **0.539**, and the texture-only gate accepts it. Drift is not invented in this offline verdict. Assembled/source seam retention is 101.2% raw survival and 90.4% structure. Therefore the artifact shows no survival collapse at the join, but it does **not** show zero structural loss.

`MASTURB2_V6` has 175 frames and the single chunk has 120, consistent with a `120 + 55` chain. No original chain state was found, so boundary 120 is an artifact-based inference, not independently proven provenance. The first 120 decoded frame pairs have RGB mean absolute delta 4.342 (maximum 6.189), so they are similar but not byte-identical.

See `OFFLINE_REPORT.md` for the negative GOWN comparison and BC/reference numbers.

## Tests

```bash
.venv/Scripts/python.exe -m pytest -q tests/test_chain_texture_gate.py
.venv/Scripts/python.exe -m pytest -q tests/test_no_absolute_paths.py
.venv/Scripts/python.exe -m pytest -q
```

Tests cover coherent detail versus sand, the real BC delta, fail-closed metrics, fixed-baseline retention, retry/seed behavior, plan-only execution, report fields, and real offline seam measurements. The final full-repository run on 2026-11-04 returned **1344 passed, 8 subtests passed in 136.44 s**. Real-artifact tests skip when those files are unavailable. Not verified: actual GPU rendering, ComfyUI queue behavior, long-run AV sync, visual identity, motion robustness of anchor deltas, and generalization to unseen lighting/skin/resolutions.
