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
python scripts/vace_ab.py --run control
python scripts/vace_ab.py --matrix
python scripts/vace_ab.py --report
```

`--matrix` runs every case in `CASES` against the same seed and writes
`memory/vace_ab_report.json`. `--report` prints a compact table from that file.

CI only exercises the **pure Python** workflow graph logic (`tests/test_vace_ab.py`);
it does not start ComfyUI or touch GPUs.

## Baseline and matrix design

The baseline is the ComfyUI **API prompt** recovered from the history entry that
produced the striped `wv_vace_final_*.mp4` clips. `scripts/vace_ab.py` loads it
from `tests/fixtures/vace_defective_workflow.json` (single source of truth). Tests
pin both the raw file SHA-256 and the canonical JSON hash so the fixture cannot
drift silently.

Recovered graph (node ids). The fixture file is checksum-pinned in
`tests/test_vace_ab.py` (raw file SHA-256 and canonical JSON hash).

| Node | Role |
|------|------|
| `1` / `2` | `WanVideoModelLoader` — high/low `wan2.2_fun_vace_*_noise_14B_fp8_scaled`, `block_swap_args` → `3`, sageattn, fp8 scaled quant |
| `3` | `WanVideoBlockSwap` — `blocks_to_swap` 30, `vace_blocks_to_swap` 8 |
| `4` / `5` | `LoadWanVideoT5TextEncoder` → `WanVideoTextEncode` |
| `6` | `WanVideoVACEEncode` — 480×832, 29 frames, strength 1.0, VACE 0–100%, VAE from node `9` |
| `7` / `8` | `WanVideoSampler` — 6 steps, CFG 1.0, shift 5.0, `dpm++_sde`, split at step 3 (seeds 42 / 43), `rope_function` comfy |
| `9` / `10` | `WanVideoVAELoader` (`Wan2_1_VAE_bf16.safetensors`) → `WanVideoDecode` |
| `30` → `31` → `32` → `33` | `LoadVideo` (`vace_test.mp4`) → `VideoFrameSample` (`head`, seed 42) → `GetVideoComponents` → `ImageResizeKJ` (lanczos, `divisible_by` 16) |
| `40` / `41` | `CreateVideo` (16 fps, 8-bit) → `SaveVideo` (`wv_vace_final`, h264 mp4) |

No `WanVideoImageToVideoEncode` and no `VHS_*` nodes (native ComfyUI video I/O).

Each matrix case changes **one** override relative to that baseline, except `combo`
(which stacks the main sampler knobs). Overrides touch CFG, steps/split, scheduler,
shift, VACE strength/end, resize `divisible_by`, attention mode, quantization, or
output prefix.

| Case | Isolates |
|------|----------|
| `control` | Recovered defective settings (no overrides). Reproduces the defect in ~200 s at 6 steps (score ~71). |
| `cfg5` | CFG 5.0 on both samplers. |
| `steps20` | 20 steps, high/low split at 10. |
| `unipc` | UniPC scheduler + shift 8.0. |
| `strength07` | VACE strength 0.7. |
| `vace_end08` | VACE end percent 0.8. |
| `div32` | Resize `divisible_by` 32. |
| `sdpa` | SDPA attention on both loaders. |
| `noquant` | Quantization disabled on both loaders. |
| `combo` | CFG 5.0 + 20 steps / split 10 + UniPC + shift 8.0. |

**Interpretation:** treat a case as a fix only when the banding verdict becomes
`clean` or `geometry` **and** green dominance drops below about **+5**. A lower
score alone is not enough if the verdict stays `periodic`.
