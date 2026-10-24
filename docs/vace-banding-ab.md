# Wan2.2 VACE stripe A/B harness

Full write-up of the **2026-10-24 corruption root cause**, post-repair speed matrix,
upstream bugs, and checkpoint verification: [`vace-speed-and-corruption.md`](vace-speed-and-corruption.md).

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

### Root cause (2026-10-24)

Stripes were caused by a **corrupt** `wan2.2_fun_vace_high_noise_14B_fp8_scaled.safetensors`
(duplicated internal chunk → shifted tensor offsets), **not** by `fp8_e4m3fn_scaled` or other
matrix knobs. After replacing the file with the official Comfy-Org bytes (SHA-256
`23130f30207f6c8697a5bead113ddc9904cfbb5e8fa51a550df3cee4d21fb54c`), **43/43** renders
were `clean`. Details, timings, and `python scripts/verify_wan_vace_checkpoints.py` are in
[`vace-speed-and-corruption.md`](vace-speed-and-corruption.md).

## Measuring the output

### Banding (quality)

`scripts/vace_ab.py` may consult ComfyUI’s **preview** URL when building the report. That
URL **404s for some successful runs**, so the JSON can show a missing path or `None` for
banding even though `ComfyUI/output/` (or `output/vace_ab/`) contains a valid MP4. The
harness prefers a harness-named `vace_ab_*.mp4` and falls back to the history path, but
**always treat the file on disk as authoritative**: re-score with

```bash
python scripts/banding_detector.py path/to/render.mp4
```

### Speed (timing)

**Do not use wall-clock submit → complete while other jobs are queued.** The harness
records both wall `seconds` and ComfyUI **`exec_span`** (from `execution_start` /
`execution_success` timestamps in `status.messages`, in milliseconds). Compare **`exec_span`**
only. It also records **`execution_cached`** (pre-processing nodes 4–6, 9, 30–33 in this
graph; samplers 7–8 are never cached). Details and the 2026-10-24 paired matrix are in
[`vace-speed-and-corruption.md`](vace-speed-and-corruption.md#how-to-measure-speed-read-this-before-the-table).

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
| `control` | Recovered baseline settings (no overrides). With a **verified** checkpoint, expect `clean` (~**131 s** `exec_span` median at 6 steps on the 4070 Ti SUPER; corrupt high-noise weights produced `periodic` stripes). |
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
