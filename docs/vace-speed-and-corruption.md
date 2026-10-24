# Wan2.2 VACE — checkpoint corruption, speed matrix, upstream notes

Companion to [`vace-banding-ab.md`](vace-banding-ab.md) (A/B harness and banding detector).

Hardware for the 2026-10-24 matrix: **RTX 4070 Ti SUPER** (16 GB VRAM, sm_89), 64 GB system RAM, ComfyUI with **ComfyUI-WanVideoWrapper** and the Wan2.2 **VACE v2v** graph from [`scripts/vace_ab.py`](../scripts/vace_ab.py). Banding scores used [`scripts/banding_detector.py`](../scripts/banding_detector.py).

## Root cause of horizontal green stripes

The horizontal green/teal stripes were **not** caused by quantization mode, attention backend, or other sampler/loader knobs.

The high-noise checkpoint file **`wan2.2_fun_vace_high_noise_14B_fp8_scaled.safetensors`** was **corrupt on disk**: a **duplicated ~20 MiB chunk** inside the safetensors container meant **422 of 2322 tensors** were read with a **shifted byte offset**, producing periodic garbage in the decoded video.

The file was **repaired on 2026-10-24 18:11:09** and is now **byte-identical** to the official Comfy-Org release:

| Field | Value |
|-------|-------|
| File | `wan2.2_fun_vace_high_noise_14B_fp8_scaled.safetensors` |
| Size | 17 346 056 104 bytes |
| SHA-256 | `23130f30207f6c8697a5bead113ddc9904cfbb5e8fa51a550df3cee4d21fb54c` |

Source: [Comfy-Org/Wan_2.2_ComfyUI_Repackaged](https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged) → `split_files/diffusion_models/`.

**After repair, every re-checked render was clean** — **43/43** matrix runs re-analysed from the `.mp4` files on disk. The earlier hypothesis that `quantization="fp8_e4m3fn_scaled"` (or switching to `fp8_e4m3fn_scaled_fast`) caused the stripes is **refuted**; `fp8_e4m3fn_scaled_fast` was clean in every run and is **not** flaky.

Verify local copies anytime:

```bash
python scripts/verify_wan_vace_checkpoints.py
```

Re-download with size + SHA-256 verification (same conventions as [`scripts/dl_verified.py`](../scripts/dl_verified.py)):

```bash
python scripts/verify_wan_vace_checkpoints.py --print-urls
```

## Recommended configuration (quality + speed)

**Keep the baseline loader settings:**

- `quantization="fp8_e4m3fn_scaled"` (not plain `fp8_e4m3fn`)
- `rope_function="comfy"` on both samplers

**Real speed levers** (all post-repair runs scored `clean`):

| Configuration | Wall time (s) | Notes |
|---------------|---------------|--------|
| `use_non_blocking=True` | 90 | Fastest measured lever |
| `use_non_blocking=True` + `prefetch_blocks=2` | 130 | Good block-swap overlap |
| `attention_mode="sdpa"` | 141 | PyTorch SDPA fallback |
| `fp8_e4m3fn_scaled_fast` + `prefetch_blocks=1` | 170 | Still clean |
| **BASELINE** (`fp8_e4m3fn_scaled` + sageattn) | 200 / 403 / 501 / 763 | Same config, huge spread (see below) |
| `fp8_e4m3fn_scaled_fast` | 201 / 393 / 422 | Clean; not a banding fix, only speed variant |
| `fp8_e4m3fn` + `sdpa` | 510 / 636 | Plain fp8 slower than scaled |
| `fp8_e4m3fn` (plain, not scaled) | 661 / 671 | ~1.5–3× slower than scaled |
| `rope_function="default"` | 671 | Works; slower than `comfy` |
| `riflex_freq_index=1` | 855 | |
| `rope_function="comfy_chunked"` | 2985 | **~6× slower — do not use** |
| Pure T2V, no VACE | 350 | Reference only (different graph) |

**Timing caveats**

- Absolute wall times for the **same** configuration swung **200 → 763 s** on this box. Block-swap **PCIe traffic** and **~40 GB ComfyUI RSS** on a 64 GB machine dominate variance. **Only compare pairs measured in the same session/window.**
- GPU thermals were not limiting: **41 °C**, **2940/3120 MHz** during the matrix.

**Avoid**

- `rope_function="comfy_chunked"` (~6× slower than `comfy`).
- Plain `fp8_e4m3fn` when `fp8_e4m3fn_scaled` is available (~1.5–3× slower).

## Upstream defects (ComfyUI-WanVideoWrapper)

Repository: [github.com/kijai/ComfyUI-WanVideoWrapper](https://github.com/kijai/ComfyUI-WanVideoWrapper).

1. **`custom_linear.py` (lines 255–260) — LoRA attenuated ~3200× in one branch**  
   ```python
   if weight.numel() < input.numel():
       weight = weight * scale_weight
   else:
       input = input * scale_weight
   ```  
   For a scalar `scale_weight` this is algebraically harmless on a single matmul (measured relative error ~1.8e-07), but **LoRA is merged into `weight` after this branch**. In the “scale the input” path, the LoRA delta is applied to the **unscaled** weight while the input still carries the scale → LoRA contribution ~**3200× too weak**. Present on upstream `main` since commit `29ce253` (2025-07-21); no documented rationale. **Irrelevant for the LoRA-free VACE graph**; **critical for H3/LoRA pipelines**.

2. **`UnboundLocalError: cannot access local variable 'callback_latent'`** — intermittent in the **low-noise sampler** path (2 of 43 matrix runs).

3. **`ValueError: Invalid T5 text encoder model, fp8 scaled is not supported by this node`** — `umt5_xxl_fp8_e4m3fn_scaled.safetensors` cannot be loaded via `LoadWanVideoT5TextEncoder`.

4. **`sageattn_3` on Ada (sm_89)** — imports `sageattn3`, which is not installed in the typical `_qattn_sm80/_sm89/_sm90` wheel layout → routes to `attention_func_error`. Use `sageattn` / SDPA instead.

## Kijai upstream references

**Attention modes** — [upstream issue #1656](https://github.com/kijai/ComfyUI-WanVideoWrapper/issues/1656) (“Recommendation for attention mode in WanVideo Model Loader?”): SageAttention can be more than twice as fast as other options depending on GPU and resolution, with very slight quality loss; SDPA is the always-available PyTorch fallback.

**Block swap defaults** — `nodes_model_loading.py` (~lines 299–306): `blocks_to_swap` default **20** (14B model has 40 blocks), `vace_blocks_to_swap` default **0** (VACE model has 15 blocks), `prefetch_blocks` default **0** with guidance that **1** is usually enough to offset block-swap overhead.

**README** — less reliance on `torch.compile` for VRAM efficiency; `allow_unmerged_lora_compile` off by default.

**Sampler caches** — `cache_methods/cache_methods.py` (`CACHEARGS` on `WanVideoSampler`): TeaCache / MagCache / EasyCache (`rel_l1_thresh`, `start_step`, `end_step`, `use_coefficients`, `mode`, `magcache_thresh`, `magcache_K`, `easycache_thresh`, `cache_device` cpu|gpu). Meaningful only for **longer** step counts; a **6-step** VACE render gains nothing.

## Measuring the output

The matrix harness may read ComfyUI’s **preview** URL for quick feedback; that URL **404s on some runs** and the report then shows **`None`** even when the render succeeded on disk. The harness prefers harness-named `vace_ab_*.mp4` and falls back to the file path from history, but **the authoritative check is always to re-analyse the saved `.mp4` with `banding_detector.py`** (as done for the 43/43 post-repair verification). See also [`vace-banding-ab.md`](vace-banding-ab.md#measuring-the-output).
