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

## How to measure speed (read this before the table)

**Never benchmark a ComfyUI job by wall clock while other prompts are queued.** An early version of [`scripts/vace_ab.py`](../scripts/vace_ab.py) timed submit → history complete; that span includes **queue wait** behind unrelated jobs, which is why the same configuration once looked like **90 s** and another time **1741 s**.

The correct metric is ComfyUI’s **execution span** from the history entry’s `status.messages`:

```text
exec_span_s = (execution_success.timestamp - execution_start.timestamp) / 1000
```

The harness now records **`exec_span`** (seconds) alongside wall-clock **`seconds`** and the **`execution_cached`** node list. Use **`exec_span`** for speed comparisons.

`execution_cached` lists nodes whose outputs were reused. In this VACE graph that is only deterministic pre-processing — nodes **4** (`LoadWanVideoT5TextEncoder`), **5** (`WanVideoTextEncode`), **6** (`WanVideoVACEEncode`), **9** (`WanVideoVAELoader`), **30**–**33** (video load/resize). Samplers **7** and **8** must not appear there on a valid benchmark run (see below).

### The seed trap (silent invalidation of a benchmark)

The recovered workflow uses **fixed seeds** (node **7** → 42, node **8** → 43, node **31** → 42). ComfyUI caches node outputs by input hash, so re-submitting the **same** graph can be served **entirely from cache** — the job finishes in ~**0.6 s** with the **samplers never executing**. One such run appeared in an early matrix as a bogus fast row.

[`scripts/vace_ab.py`](../scripts/vace_ab.py) now **bumps those seeds on every `run_case`** (module run counter: `base + seq` per node). Reports record **`run_seq`** and the applied **`seeds`**. When benchmarking by hand, **change the seed** between runs and confirm **`execution_cached` does not list nodes 7 or 8**.

**Banding verdicts** are independent of timing: re-analyse the saved **`.mp4` on disk** with [`scripts/banding_detector.py`](../scripts/banding_detector.py). The ComfyUI **preview URL can 404** while the render succeeded; do not treat a missing preview as a failed run.

### Output discovery

On this ComfyUI build, the WanVideoWrapper **`SaveVideo`** node reports finished files under the **`images`** key in history (not **`gifs`** or **`videos`**), with an **`animated`** sibling on the node output. The harness collects **`gifs`**, **`videos`**, and **`images`**, and prefers an entry whose **`type`** is **`output`** so the graph’s **`LoadVideo`** input (`type: input`) is never scored as the result.

If the `.mp4` is not present under a local **`ComfyUI/`** tree or **`--output-dir`**, the harness downloads it from the instance via **`GET /view?filename=…&subfolder=…&type=…`** into a temporary file before banding analysis. **`--output-dir`** is only a local mirror fallback, not the ComfyUI install path.

## Recommended configuration (quality + speed)

**Keep the baseline loader settings:**

- `quantization="fp8_e4m3fn_scaled"` (not plain `fp8_e4m3fn`)
- `rope_function="comfy"` on both samplers

### 3-round paired matrix (2026-10-24, execution span)

Hardware as above; **6-step** harness graph unless noted. **11/11** runs re-verified **`clean`** from on-disk MP4s.

| Configuration | Run 1 (s) | Run 2 (s) | Run 3 (s) | Median (s) |
|---------------|-----------|-----------|-----------|------------|
| `use_non_blocking=True` + `prefetch_blocks=2` | 72.1 | 72.3 | — | **72.3** |
| `use_non_blocking=True` | 79.0 | 80.9 | — | **80.9** |
| `prefetch_blocks=2` | 123.4 | 121.4 | — | 122.4 |
| Baseline (`fp8_e4m3fn_scaled` + sageattn) | 130.7 | 114.8 | 185.3 | 130.7 |
| `attention_mode="sdpa"` | 138.4 | 135.6 | — | 138.4 |

Reproduce the headline row and neighbors on this hardware:

```bash
python scripts/vace_ab.py --matrix --only control,nonblocking,prefetch2,nb_pf2
```

**Headline:** **1.8× faster than baseline median** (130.7 s → **72.3 s**) and **~2.0× versus baseline mean** (143.6 s → 72.3 s), with every run still clean.

Same sessions illustrate queue pollution on wall clock vs execution span: e.g. `control_sdpa` **260.3 s** wall vs **138.4 s** exec; `control_nonblocking` **190.9 s** wall vs **79.0 s** exec. Quote **execution span** in write-ups.

The live production script (`wv_vace_kj_*.mp4`, full **20-step** schedule) measured **324.4 s** execution span (different step count — not comparable to the 6-step rows above).

### How to reproduce

On the same RTX 4070 Ti SUPER box, run the harness subset that maps to the table rows above:

```bash
python scripts/vace_ab.py --matrix --only control,nonblocking,prefetch2,nb_pf2
```

Expect execution-span medians near **72.3 s** (`nb_pf2`), **80.9 s** (`nonblocking`), **122.4 s** (`prefetch2`), and **130.7 s** (`control` baseline). Each run should still score **`clean`** when re-analysed from the on-disk `.mp4`.

With `use_non_blocking=True`, a prefetch sweep found **prefetch_blocks 2–6** equivalent (~72 s) and **8** degrading (**128.3 s**); use **2–4** in production.

### What the levers mean

- **`use_non_blocking=True`** — input on **`WanVideoBlockSwap`**: “Use non blocking transfer to GPU. This will be faster but will use more VRAM.” Largest single lever on this card (median **80.9 s** vs baseline **130.7 s**). Non-blocking transfers keep swapped blocks in VRAM longer; measured peak **7.6 GB** of **16 GB** (~**8.4 GB** free at 480×832 / 29 frames). Comfortable here; at higher resolutions watch free VRAM.
- **`prefetch_blocks=2`** — with non-blocking, completes the headline config (~**72.3 s** median). Alone (~**122.4 s**) it does not beat baseline; pair it with `use_non_blocking`. Prefetch **2–6** is equivalent on this card; **8** regresses to **128.3 s** — prefer **2–4**.
- **`attention_mode="sdpa"`** — PyTorch SDPA; ~**1.05×** vs baseline median on this card (SageAttention is still the default in the baseline).
- Keep **`fp8_e4m3fn_scaled`** over plain **`fp8_e4m3fn`** (scaled is faster on this stack).
- **`fp8_e4m3fn_scaled_fast`** was **`clean` in every run** — not flaky. Apparent “errors” were harness preview **404**s, not render failures.
- **`rope_function="comfy_chunked"`** — ~**6×** slower than `comfy`; never use it.

GPU thermals were not limiting during the matrix: **41 °C**, **2940/3120 MHz**.

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

See [How to measure speed](#how-to-measure-speed-read-this-before-the-table) for **`exec_span`** vs wall clock. For quality, the matrix harness may read ComfyUI’s **preview** URL; that URL **404s on some runs** and the report can show **`None`** even when the render succeeded on disk. The harness prefers harness-named `vace_ab_*.mp4` and falls back to the history path, but **the authoritative banding check is always the `.mp4` on disk** via `banding_detector.py` (43/43 post-repair verification and 11/11 in the paired matrix). See also [`vace-banding-ab.md`](vace-banding-ab.md#measuring-the-output).
