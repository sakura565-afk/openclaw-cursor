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
| `director` | string | Director key: `h3` (default) or `freevideo` (LAB only) |
| `director_config` | object | `comfy_url` (LAB `:8189` for FreeVideo), optional `workflow_path` (H3) |
| `freevideo` | object | FreeVideo settings (when `director: freevideo`): `width`, `height`, `seconds`, `seed`, `two_pass`, `base_steps`, `refine_steps`, `preview`, `output_root` (default `G:/FreeVideo_output`) |
| `output_dir` | path | Output directory for rendered videos |

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
