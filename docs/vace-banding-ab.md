# Wan2.2 VACE stripe A/B harness

## The defect and detector

Wan2.2 VACE outputs on this machine can show **horizontal green/teal stripes** across
frames. `scripts/banding_detector.py` scores a clip numerically instead of
eyeballing frames.

Measured on real outputs:

| Kind | Verdict | Periodic lag | Autocorr. peak | Green dominance |
|------|---------|--------------|----------------|-----------------|
| Defective | `periodic` | 16–64 px | ~1.0 | +6 … +20 |
| Clean | `clean` or `geometry` | — | ≤ 0.19 | ≤ +1 |

The harness runs ComfyUI with one knob changed at a time, then runs the detector
on the saved MP4 so regressions show up as numbers.

## Running the harness

Requires a local ComfyUI with **ComfyUI-WanVideoWrapper** (WanVideo nodes) and
ffmpeg on `PATH` for scoring.

```bash
python scripts/vace_ab.py --list
python scripts/vace_ab.py --run baseline
python scripts/vace_ab.py --matrix
python scripts/vace_ab.py --report
```

`--matrix` runs every case in `CASES` against the same seed and writes
`memory/vace_ab_report.json`. `--report` prints a compact table from that file.

CI only exercises the **pure Python** workflow graph logic (`tests/test_vace_ab.py`);
it does not start ComfyUI or touch GPUs.

## Baseline and matrix design

The baseline prompt (`vace_ab.BASELINE`) is the ComfyUI **API prompt** recovered
from the history entry that produced the striped `wv_vace_final_*.mp4` clips. The
frozen copy lives in `tests/fixtures/vace_defective_workflow.json`.

Each matrix case changes **one** override relative to that baseline (CFG, steps,
high/low split, scheduler, shift, VACE strength/end, resize `divisible_by`,
attention mode, or quantization on both loaders).

| Case | Isolates |
|------|----------|
| `baseline` | Recovered defective settings (control). |
| `cfg_1` | CFG = 1.0 on both samplers. |
| `cfg_3_5` | Higher CFG on both samplers. |
| `steps_8` | More denoise steps. |
| `split_4` | Earlier handoff from high to low sampler. |
| `scheduler_euler` | Euler scheduler. |
| `shift_5` | Lower flow shift. |
| `vace_strength_0_75` | Weaker VACE conditioning. |
| `vace_end_0_8` | Shorter VACE active window. |
| `divisible_32` | Coarser resize divisibility. |
| `attention_sdpa` | SDPA instead of sageattn on loaders. |
| `quant_fp8_e4m3fn` | Different fp8 quantization on loaders. |

**Interpretation:** treat a case as a fix only when the banding verdict becomes
`clean` or `geometry` **and** green dominance drops below about **+5**. A lower
score alone is not enough if the verdict stays `periodic`.
