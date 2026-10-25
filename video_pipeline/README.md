# Video Pipeline

Declarative YAML-config-driven orchestrator for ComfyUI image-to-video renders.

Supported directors:

- **h3** (default) — MiniMax H3 on LAB or PROD ComfyUI (workflow JSON).
- **freevideo** — [FreeVideo](https://github.com/FlashML-org/FreeVideo) (`FreeVideoGenerate`, Video DeltaNet). **LAB only** (`http://127.0.0.1:8189`); the pack is not installed on PROD (`:8188`). Set `director_config.comfy_url` to LAB when using `director: freevideo`. The node writes outputs under `G:/FreeVideo_output/<date>/<hash>/` (not ComfyUI `output/`); the pipeline snapshots that tree and copies the newest new `.mp4` into `output_dir`. Finished videos embed the ComfyUI workflow in MP4 metadata (drag back onto the canvas).

## Quick start

```bash
pip install pydantic pyyaml pillow portalocker requests loguru

# Plan jobs without rendering
python -m video_pipeline.runner --config video_pipeline/configs/furniture_batch.yaml --dry-run

# Run pipeline (requires ComfyUI on localhost:8183)
export TELEGRAM_BOT_TOKEN=your_token
python -m video_pipeline.runner --config video_pipeline/configs/furniture_batch.yaml

# Resume after crash
python -m video_pipeline.runner --config video_pipeline/configs/furniture_batch.yaml --resume
```

## YAML config schema

| Field | Type | Description |
|-------|------|-------------|
| `project` | string | Project name for logging and state |
| `source_dir` | path | Directory with `*.jpg` / `*.png` inputs |
| `outputs` | list | Output specs: `format`, `motion`, `duration_sec`, `megapixels` |
| `quality_gates` | object | `max_ar_drift`, `max_render_sec`, `min_resolution_h`, `expected_codec` |
| `delivery` | object | `telegram_chat`, templates, `progress_every_min` |
| `recovery` | object | `max_retries`, `retry_backoff_sec`, `resume_from_state`, `state_file` |
| `upscale` | object | Post-render upscale: `backend`, `comfyui_url`, `resolution`, `batch_size`, … |
| `director` | string | Director key: `h3` (default) or `freevideo` (LAB only) |
| `director_config` | object | `comfy_url` (LAB `:8189` for FreeVideo), optional `workflow_path` (H3) |
| `freevideo` | object | FreeVideo settings (when `director: freevideo`): `width`, `height`, `seconds`, `seed`, `two_pass`, `base_steps`, `refine_steps`, `preview`, `output_root` (default `G:/FreeVideo_output`), `last_frame` (optional path pinned as the graph’s last frame), `frame_lock` (default `true`: keep the full subject in frame at constant distance) |
| `output_dir` | path | Output directory for rendered videos |

### Upscale (`upscale`)

After each H3 render, the runner upscales to the format’s delivery resolution.

| Key | Default | Description |
|-----|---------|-------------|
| `backend` | `auto` | `auto`, `seedvr2`, `esrgan`, or `lanczos` |
| `comfyui_url` | `http://127.0.0.1:8188` | PROD ComfyUI for learned backends (not the LAB render URL) |
| `resolution` | `1080` | SeedVR2 target **short edge** (pixels); aspect ratio preserved |
| `batch_size` | `5` | SeedVR2 temporal batch size |
| `temporal_overlap` | `3` | SeedVR2 overlap between batches |
| `blocks_to_swap` | `32` | SeedVR2 DiT VRAM trade-off |
| `free_vram_urls` | `[]` | ComfyUI base URLs to unload via `POST /free` before a learned upscale (see below) |
| `min_free_vram_gb` | `13.0` | Minimum free VRAM (GB) on `comfyui_url` before starting SeedVR2 / ESRGAN |

**Backend order for `auto`:** `seedvr2` → `esrgan` → `lanczos` (probed once via `/object_info` on `comfyui_url`).

- **seedvr2** — requires the `ComfyUI-SeedVR2_VideoUpscaler` custom node pack and model files under `models/SEEDVR2/` on that instance.
- **esrgan** — `UpscaleModelLoader` + `ImageUpscaleWithModel` with `4x-UltraSharp.pth`, then Lanczos scale+pad to the exact target box.
- **lanczos** — ffmpeg only; used when ComfyUI is unreachable or nodes are missing (logged warning).

Audio is preserved (VHS audio wiring for Comfy paths; ffmpeg copy/mux for Lanczos).

#### VRAM and scheduling (SeedVR2)

The SeedVR2 backend needs about **15 GB of free VRAM** on the GPU (measured peak **15.76 / 16.38 GB** on an RTX 4070 Ti SUPER 16 GB) because it runs a 3B DiT plus a tiled video VAE. On this machine the render ComfyUI instance (**LAB**, `director_config.comfy_url`, port **8189**) idles holding roughly **11 GB** of the card. Before each learned upscale on **PROD** (`upscale.comfyui_url`, port **8188**), the pipeline calls `POST {url}/free` with `{"unload_models": true, "free_memory": true}` on every URL in `free_vram_urls` (by default the LAB URL when `free_vram_urls` is empty), then reads `GET {comfyui_url}/system_stats` and checks `devices[0].vram_free`. If free VRAM is below `min_free_vram_gb` (default **13.0**), upscale **falls back to Lanczos** automatically with a logged warning that includes both the measured free GB and the required threshold. If stats are unavailable, the learned backend still runs (no hard block).

**Timing (SeedVR2, 1080×1880):** about **6.6 s per frame** (~**8 min** for a 73-frame clip, ~**19 min** for 175 frames). For faster turnaround, use a smaller `resolution` or the `esrgan` backend.

In the `upscale` config, `resolution` is the **target short edge** in pixels; aspect ratio is preserved.

See `video_pipeline/config.py` for the full Pydantic schema.

### Output formats

Supported aspect ratios: `1:1`, `9:16`, `16:9`, `3:4`, `4:3`, `2:3`.

### Motion presets (H3)

Built-in motion keys: `slow_push_in`, `static`, `slow_turn`, `pan_left`. Custom strings are used as raw prompt fragments.

### Product shots from photos

For furniture and other rotating product clips, pin **both** ends of the clip to real photos. The LAB `FreeVideoGenerate` node accepts optional `first` and `last` image inputs; when the last frame is left unpinned, a slow rotation tends to **invent** carved scroll volutes on armrest extremities and gilt crest details that are absent in the first frame, and the framing **crops** feet or other extremities out of shot. With first and last frames pinned to true product photos (and `frame_lock: true` so the camera distance stays constant), measured runs produced clean geometry at every sampled angle.

CLI helper (LAB ComfyUI must be running):

```bash
python scripts/product_shot.py \
  --image path/to/front.jpg \
  --last-image path/to/back.jpg \
  --width 1344 --height 768 \
  --prompt prompts/product_rotate.txt \
  --out media/out/product_rotate.mp4
```

`--last-image` maps to `freevideo.last_frame` in YAML configs. The script waits for `video.partial.mp4` to finish growing (then rename to `video.mp4`) before copying the final file.

## Adding a new director

```python
from video_pipeline.director_base import Director, JobSpec, VideoResult

class WanDirector(Director):
    def prepare(self, source_image, output_spec) -> JobSpec: ...
    def submit(self, job) -> str: ...
    def poll_until_done(self, prompt_id, timeout_sec) -> VideoResult: ...
    def upscale(self, video_path, target_format) -> Path: ...
```

Register in `runner.py` `_get_director()` factory and set `director: wan` in YAML.

## Quality gates

After each render, `QualityGate` checks via ffprobe:

- Aspect ratio drift vs source image (PIL)
- Minimum resolution height
- Expected codec (h264)
- Duration tolerance (optional)

Failed renders are retried up to `max_retries`; they are not sent to Telegram.

## Recovery workflow

State is persisted to `video_pipeline_state.json` (configurable via `recovery.state_file`).

Use `--resume` to skip items with `status: done` and retry `pending` / `failed` items.

SIGINT saves state before exit.

## Telegram delivery

Set `TELEGRAM_BOT_TOKEN` in the environment. Configure `delivery.telegram_chat` in YAML.

Captions are transliterated via `unidecode` for cp1251 safety. Progress messages throttle to `progress_every_min`.

## Troubleshooting

| Error | Fix |
|-------|-----|
| `TELEGRAM_BOT_TOKEN environment variable is required` | Export bot token or use `--dry-run` |
| `Config validation failed: field 'outputs'` | Check YAML output format literals |
| `ComfyUI POST /prompt failed` | Ensure ComfyUI runs on `http://127.0.0.1:8183` |
| `ffprobe failed` | Install ffmpeg/ffprobe |
| `Workflow not found` | Run from repo root; workflow at `workflows/minimax_h3_hero_short.json` |

## Backward compatibility

`scripts/h3_dining_table_v2_compat.py` wraps the runner for existing cron jobs. Env vars: `H3_SOURCE_DIR`, `H3_OUTPUT_DIR`, `H3_PROJECT`, `TELEGRAM_CHAT_ID`.
