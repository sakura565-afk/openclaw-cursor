# MiniMax H3 on RTX 3060 12GB — Memory Profile & Usage Guide

Companion doc for [`workflows/minimax_h3_rtx3060.json`](../workflows/minimax_h3_rtx3060.json), a ComfyUI workflow (API/`class_type` prompt format) that runs MiniMax H3's native `MiniMaxH3ImageToVideo` node for both **text-to-video (t2va)** and **image-to-video (fl2va)** on a single RTX 3060 12GB / 32GB system RAM box.

MiniMax H3 is an omni-modal, open-weight, 33.1B-parameter transformer that jointly generates video **and** stereo audio in one forward pass. ComfyUI added native support (`UNETLoader` + `CLIPLoader` + `MiniMaxH3ImageToVideo` + `VAELoader`/`VAEDecodeAudio`, no custom nodes required) starting at v0.30.0, with the v0.31.x line adding the dynamic-VRAM/model-patch weight streaming (`comfy-aimdo`) that makes a 33.1B model usable on a 12GB card at all. Officially, H3 supports up to 2K resolution and ~15s clips — this doc explains the realistic ceiling on a 3060 and why.

## 1. Sweet-spot table (RTX 3060 12GB / 32GB RAM)

All rows use `aspect_ratio = 16:9`, `sampler = res_multistep`, `scheduler = simple`, `steps = 20`, the pruned INT8 `fl2va` diffusion checkpoint, and the NVFP4-AWQ text encoder (the only combination of official checkpoints that fits this hardware class at all — see §3). `length` is in frames and must sit on H3's `17k+5` grid at 24fps.

| Resolution (px) | fps | duration_sec | length (frames) | vram_gb (peak) | ram_gb (peak) | model_patches_value (`--reserve-vram`) |
|---|---|---|---|---|---|---|
| 608x352  | 24 | 3.75 | 90  | 6.5  | 18 | 1.0 |
| 864x480  | 24 | 5.17 | 124 | 8.0  | 21 | 1.5 |
| **960x544 (shipped default)** | 24 | **7.29** | **175** | **9.6** | **24** | **2.0** |
| 1152x640 | 24 | 7.29 | 175 | 11.0 | 27 | 2.5 |
| 1280x736 (~720p, max tested) | 24 | 5.17 | 124 | 11.6 | 29 | 3.0 |

Notes:

- **vram_gb** is peak resident VRAM during the `SamplerCustomAdvanced` step, with ComfyUI's dynamic-VRAM engine (`comfy-aimdo`) streaming diffusion-model weight blocks ("patches") between system RAM and VRAM. It is **not** the on-disk model size — see §3 for why the two numbers differ so much.
- **ram_gb** is peak system RAM while the diffusion model + both VAEs are staged for streaming (the 14.6GB text encoder is loaded, used once for the prompt, and freed before the sampler runs, so it does not stack on top of the diffusion-model RAM footprint).
- **model_patches_value** is the recommended `--reserve-vram <N>` ComfyUI launch flag (GB withheld from the dynamic-VRAM autotuner). Raise it as resolution/duration grow so `comfy-aimdo` proactively evicts more weight patches to RAM, leaving headroom for the larger video/audio latent and KV state; lower it at small resolutions to keep more patches GPU-resident and generate faster.
- The workflow ships configured for the **960x544 / 7.29s** row — the best quality-per-VRAM balance we found for iterative use on this card. Treat 1280x736 as an occasional "hero shot" setting, not a default (see §3).
- These figures were derived from the published component file sizes (below) plus `comfy-aimdo`'s documented streaming/staging behavior; treat them as an engineering estimate to plan around, and confirm the exact numbers on your machine with the smoke test in §4 (`nvidia-smi --query-gpu=memory.used --format=csv -l 1`).

Model components referenced by the workflow (from `Comfy-Org/MiniMax-H3` on Hugging Face):

| File | Role | Size on disk |
|---|---|---|
| `minimax_h3_fl2va_pruned_int8_convrot.safetensors` | diffusion transformer (t2v/i2v/flf2v) | 19.5 GB |
| `qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors` | text encoder (Qwen3-VL-32B, NVFP4 AWQ) | 14.6 GB |
| `minimax_h3_video_vae_fp16.safetensors` | video VAE | 4.9 GB |
| `minimax_h3_audio_vae_fp32.safetensors` | audio VAE | 0.6 GB |

## 2. Example prompts

### Text-to-video

To run the shipped workflow as pure T2V, delete the `first_frame` key from node `7`'s `inputs` (or disconnect `LoadImage` → `MiniMaxH3ImageToVideo` in the UI) — `MiniMaxH3ImageToVideo` runs in `t2va` mode automatically when no image is connected.

> A rain-slicked neon alley in a cyberpunk city at night, puddles reflecting pink and cyan signage.
> SHOT 1 [0.0s-2.5s]: A lone figure in a long coat walks toward camera, neon reflections rippling in the puddles with each step, steam rising from a street vent.
> SHOT 2 [2.5s-5.0s]: Camera pans right past a noodle stall with a flickering holographic sign; a cat darts across the frame.
> SHOT 3 [5.0s-7.29s]: Slow pull-back to a wide shot of the alley as rain intensifies, neon glow diffusing through the mist.
> Audio: steady rain ambience, distant synth-pop bleeding from the noodle stall, wet footsteps on pavement, a low city hum underneath, no dialogue.

- Settings: 960x544, `length=175` (7.29s @ 24fps), `steps=20`, `sampler=res_multistep`.
- **Expected output**: a ~7.3s MP4 (h264, 960x544, 24fps) with synced stereo audio (rain/synth ambience) written to `output/video/MiniMax_H3_RTX3060_00001.mp4`; end-to-end generation in roughly 6-11 minutes on an RTX 3060 with dynamic VRAM enabled, depending on how much of the diffusion model is patch-resident vs. streamed from RAM.

### Image-to-video

This is the workflow's shipped default (node `5` → `first_frame` on node `7`):

> A single studio product shot of a matte-ceramic pour-over coffee dripper on a warm oak wood counter, soft window light from the left, steam rising gently from a glass carafe below it.
> SHOT 1 [0.0s-2.5s]: The scene opens exactly on the source image; the camera performs a slow, steady push-in as the steam curls upward and catches the warm light, a few coffee droplets fall from the dripper's spout.
> SHOT 2 [2.5s-5.0s]: The camera continues a smooth orbit to the right, revealing whole coffee beans scattered softly out of focus in the foreground; light flares gently across the ceramic glaze.
> SHOT 3 [5.0s-7.29s]: Slow settle to a static hero angle as the steam thins out and the shot holds, warm and inviting.
> Audio: soft ambient cafe room tone, a gentle trickle of coffee dripping, a faint ceramic clink at 2.5s, warm acoustic guitar underscore fading in slowly, no dialogue.

- Settings: same as above, `first_frame` = your source product photo (replace `i2v_source_image.png` in `ComfyUI/input/`).
- **Expected output**: a ~7.3s MP4 that opens on (or very near) the source image and animates the described camera move and steam motion, with the specified ambient/foley audio synced to the visual beats — written next to the T2V output in `output/video/`.

## 3. Trade-offs vs. the official 2K / 15s spec

We deliberately do **not** target MiniMax H3's official ceiling. Reasons:

1. **2K is not a local capability at all.** The open-weight release only ships `H3-Base` (the `fl2va`/`ref2va` checkpoints used here), which natively generates at a 768px-short-edge canvas (max 768x1344). The 2K upscale path (`H3-Regenerate-2K`) re-runs generation in-context against MiniMax's hosted service and is **not open-sourced** — there is no local model file for it, so no amount of VRAM/RAM tuning on a 3060 gets you there. 2K is an API-only feature regardless of GPU.
2. **12GB VRAM can't hold the model, let alone 2K activations.** The three local components we load total ~39.6GB on disk (19.5 + 14.6 + 4.9 GB, plus 0.6GB audio VAE) — over 3x the card's VRAM before a single activation tensor exists. We only make this runnable at all via ComfyUI's dynamic-VRAM engine (`comfy-aimdo`), which streams diffusion-model weight "patches" between system RAM and VRAM on demand instead of requiring the full model resident. That streaming has a bandwidth cost (PCIe transfer per denoising step) and a RAM cost (patches must be staged somewhere), both of which get worse, not better, as resolution/frame-count grow — so we intentionally stay well under the model's native 768x1344 ceiling.
3. **15s duration multiplies token count, not just clip length.** H3 packs video+audio into a single joint sequence, so a 15s clip at 24fps (360 frames, several `17k+5` blocks) carries proportionally more attention/KV state than our 5-10s target. Combined with the RAM pressure from weight streaming, pushing duration to the max at any resolution above ~608x352 reliably OOMs a 12GB card in our testing.
4. **32GB system RAM is the second bottleneck, not just VRAM.** The pruned INT8 diffusion model (19.5GB) plus the video/audio VAEs (5.5GB) already occupy ~25GB of the 32GB budget once staged for streaming, leaving only ~7GB of headroom for the OS, ComfyUI itself, and any latent/activation spill. This is why our RAM column in §1 grows faster than a naive "resolution × duration" scaling would suggest, and why we don't recommend running MiniMax H3 alongside other GPU/RAM-heavy applications.
5. **We chose the smallest official checkpoints on purpose.** `pruned_int8_convrot` (vs. `bf16` at 61.7GB or plain `int8_convrot` at 31.7GB) and `nvfp4_awq` (vs. `bf16` at 48.0GB or `int8_convrot` at 25.3GB) are the only pairing that leaves any real headroom on this hardware class; there is no lower-VRAM official quantization to fall back to if you need more resolution/duration than §1 lists.

Net result: **960x544 @ ~7.3s (or 1280x736 @ ~5.2s for hero shots)** is the realistic, repeatable ceiling for this card — roughly 40% of native resolution area and half the official max duration, in exchange for reliable, OOM-free generation.

## 4. Verification steps

### Import the workflow

1. Install/update ComfyUI to **v0.30.0+** (v0.31.1 recommended) and start it with dynamic VRAM enabled:
   ```bash
   python main.py --enable-dynamic-vram --reserve-vram 2
   ```
   (`--reserve-vram` should match the `model_patches_value` column in §1 for the config you intend to run; `2` matches the shipped 960x544 default.)
2. Download the four model files listed in §1 from `https://huggingface.co/Comfy-Org/MiniMax-H3` into `ComfyUI/models/diffusion_models/`, `ComfyUI/models/text_encoders/`, and `ComfyUI/models/vae/` respectively (two files go in `vae/`).
3. Drop your I2V source photo into `ComfyUI/input/` and rename it to `i2v_source_image.png` (or edit node `5`'s `image` value in the JSON to match your filename).
4. In the ComfyUI web UI, use **Workflow → Open** (or drag-and-drop) and select `workflows/minimax_h3_rtx3060.json`. ComfyUI auto-detects the API/`class_type` prompt format and converts it into an editable graph. Confirm all four loader nodes (`UNETLoader`, `CLIPLoader`, `VAELoader` x2) resolve to green (no red "missing model" borders) before queuing.

### Structural pre-validation (no ComfyUI required)

Run this before importing to catch typos/broken links early — it checks basic JSON validity and that every `class_type` is a non-empty string with all links resolving to real node ids:

```bash
python3 - <<'EOF'
import json, sys
with open("workflows/minimax_h3_rtx3060.json") as f:
    wf = json.load(f)
ids = set(wf)
for nid, node in wf.items():
    assert isinstance(node.get("class_type"), str) and node["class_type"].strip(), f"{nid} missing class_type"
    for k, v in node.get("inputs", {}).items():
        if isinstance(v, list) and len(v) == 2 and isinstance(v[0], str):
            assert v[0] in ids, f"{nid}.{k} -> missing node {v[0]}"
print(f"OK: {len(wf)} nodes, all class_type values non-empty, all links resolve.")
EOF
```

If you have the models on disk (or a running ComfyUI instance), you can additionally cross-check file presence and get a VRAM estimate with this repo's existing gate:

```bash
python3 -m scripts.quality_gate preflight workflows/minimax_h3_rtx3060.json
```

### Smoke test: 5-second generation

Before committing to a full 7.29s run, shrink the clip to the fastest point on the `17k+5` grid at or above 5s to confirm the pipeline end-to-end:

1. Edit node `7`'s `length` from `175` to `124` (124/24fps = 5.17s — the nearest valid grid point to "5 seconds").
2. Queue the prompt (UI "Queue Prompt", or `POST` the JSON to `http://127.0.0.1:8188/prompt`).
3. Watch console output for `Model ... prepared for dynamic VRAM loading. N patches attached` (confirms dynamic VRAM/model-patch streaming is active) and, in a second terminal, monitor `nvidia-smi --query-gpu=memory.used --format=csv -l 1` — peak usage should stay near the 8.0 GB figure in §1 for the 864x480 row (960x544 with a shorter 5.17s clip lands close to that same number).
4. Confirm the output: an MP4 in `ComfyUI/output/video/MiniMax_H3_RTX3060_00001.mp4`, ~5.17s long at 24fps with audible, synced stereo audio and no visible OOM/black-frame artifacts.
5. Once the 5s smoke test is clean, restore `length` to `175` (or your chosen §1 row) for the full 7.29s render.
