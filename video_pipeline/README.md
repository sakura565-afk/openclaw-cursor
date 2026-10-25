# Video Pipeline

Declarative YAML-config-driven orchestrator for ComfyUI MiniMax H3 image-to-video renders.

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
| `director` | string | Director key (`h3` default) |
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

**Backend order for `auto`:** `seedvr2` → `esrgan` → `lanczos` (probed once via `/object_info` on `comfyui_url`).

- **seedvr2** — requires the `ComfyUI-SeedVR2_VideoUpscaler` custom node pack and model files under `models/SEEDVR2/` on that instance.
- **esrgan** — `UpscaleModelLoader` + `ImageUpscaleWithModel` with `4x-UltraSharp.pth`, then Lanczos scale+pad to the exact target box.
- **lanczos** — ffmpeg only; used when ComfyUI is unreachable or nodes are missing (logged warning).

Audio is preserved (VHS audio wiring for Comfy paths; ffmpeg copy/mux for Lanczos).

#### VRAM and scheduling (SeedVR2)

The SeedVR2 backend needs about **15 GB of free VRAM** on the GPU (measured peak **15.7 / 16.4 GB** on an RTX 4070 Ti SUPER 16 GB) because it runs a 3B DiT plus a tiled video VAE. On this machine the other ComfyUI instance (**LAB**, port **8189**) idles holding roughly **11 GB** of the card, so SeedVR2 upscale must run either while LAB is idle or released, or in a **separate phase after** the H3 render phase—not at the same time as a live H3 render.

**Timing (SeedVR2, 1080×1880):** about **6.6 s per frame** (~**8 min** for a 73-frame clip, ~**19 min** for 175 frames). For faster turnaround, use a smaller `resolution` or the `esrgan` backend.

In the `upscale` config, `resolution` is the **target short edge** in pixels; aspect ratio is preserved.

See `video_pipeline/config.py` for the full Pydantic schema.

### Output formats

Supported aspect ratios: `1:1`, `9:16`, `16:9`, `3:4`, `4:3`, `2:3`.

### Motion presets (H3)

Built-in motion keys: `slow_push_in`, `static`, `slow_turn`, `pan_left`. Custom strings are used as raw prompt fragments.

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
