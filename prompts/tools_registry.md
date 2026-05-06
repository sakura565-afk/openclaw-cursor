# OpenClaw tools registry

This file is generated for agent reference. Regenerate after adding or changing tools.

**Generated:** `2026-05-06T18:09:48+00:00` (UTC)

## Scanned locations

- `/workspace`

---

## CLI tools (argparse)

Typical usage: run from the repository root with `PYTHONPATH` set so `scripts` and `src` resolve (many environments do this automatically when executing from the project).

### `scripts/` — automation CLIs

#### `scripts.batch_image_optimizer`

- **Source:** `scripts/batch_image_optimizer.py`
- **Invoke:** `python -m scripts.batch_image_optimizer`
- **Module summary:** Bulk image optimizer with optional MiniMax enhancement.
- **CLI description:** Bulk image processor (resize/watermark/compress/enhance via MiniMax).
- **Subcommands / commands:** `run`, `self-test`
  - `run` — Process a directory of images.
  - `self-test` — Run a mock-image self test.

#### `scripts.comfy_auto_quality`

- **Source:** `scripts/comfy_auto_quality.py`
- **Invoke:** `python -m scripts.comfy_auto_quality`
- **Module summary:** Universal auto-quality processor for ComfyUI images.
- **CLI description:** ComfyUI auto-quality processor (face restore + denoise + upscale).

#### `scripts.comfy_video_pipeline`

- **Source:** `scripts/comfy_video_pipeline.py`
- **Invoke:** `python -m scripts.comfy_video_pipeline`
- **Module summary:** Универсальный пайплайн генерации видео мебели через ComfyUI API: 1) Генерация базовых кадров из фото товара (rotate/zoom/pan + изменение света) 2) Интерполяция кадров через RIFE (например 16 -> 60 FPS) 3) Финальный upscale через SUPIR (если ноды доступны)
- **CLI description:** Генерация и интерполяция видео мебели через ComfyUI (SD + RIFE + SUPIR).

#### `scripts.context_split`

- **Source:** `scripts/context_split.py`
- **Invoke:** `python -m scripts.context_split`
- **CLI description:** Split large contexts before querying MiniMax.

#### `scripts.face_swap_batch`

- **Source:** `scripts/face_swap_batch.py`
- **Invoke:** `python -m scripts.face_swap_batch`
- **Module summary:** Batch face swap processor for model photography.
- **CLI description:** Batch face swap processor

#### `scripts.memory_analytics`

- **Source:** `scripts/memory_analytics.py`
- **Invoke:** `python -m scripts.memory_analytics`
- **Module summary:** Memory health analytics for MEMORY.md files.
- **CLI description:** Analyze MEMORY.md health.

#### `scripts.memory_cleanup`

- **Source:** `scripts/memory_cleanup.py`
- **Invoke:** `python -m scripts.memory_cleanup`
- **CLI description:** Automated memory cleanup for OpenClaw.

#### `scripts.obsidian_dashboard`

- **Source:** `scripts/obsidian_dashboard.py`
- **Invoke:** `python -m scripts.obsidian_dashboard`
- **Module summary:** Obsidian Vault Dashboard
- **CLI description:** Obsidian Vault Dashboard

#### `scripts.ollama_batch`

- **Source:** `scripts/ollama_batch.py`
- **Invoke:** `python -m scripts.ollama_batch`
- **CLI description:** Run Ollama prompts in parallel and save responses to JSON.
- **Subcommands / commands:** `run`
  - `run` — Run prompts from a text file or JSON array.

#### `scripts.ollama_benchmark`

- **Source:** `scripts/ollama_benchmark.py`
- **Invoke:** `python -m scripts.ollama_benchmark`
- **CLI description:** Benchmark installed Ollama models.
- **Subcommands / commands:** `run`, `compare`, `history`
  - `run` — Run a benchmark across models.
  - `compare` — Compare two benchmark runs.
  - `history` — Show historical benchmark runs.

#### `scripts.ollama_manifest_fix`

- **Source:** `scripts/ollama_manifest_fix.py`
- **Invoke:** `python -m scripts.ollama_manifest_fix`
- **Module summary:** Repair Ollama on-disk manifests so current servers can list and load local models.
- **CLI description:** Fix Ollama local manifest layout (discoverability + digest/size consistency).

#### `scripts.ollama_model_manager`

- **Source:** `scripts/ollama_model_manager.py`
- **Invoke:** `python -m scripts.ollama_model_manager`
- **CLI description:** Manage local Ollama models for OpenClaw.
- **Subcommands / commands:** `list`, `pull`, `remove`, `show`, `search`, `cleanup`
  - `list` — List local Ollama models.
  - `pull` — Pull an Ollama model.
  - `remove` — Remove a local Ollama model.
  - `show` — Show details for a local model.
  - `search` — Search for models via the Ollama CLI.
  - `cleanup` — Suggest stale models to remove.

#### `scripts.ollama_monitor`

- **Source:** `scripts/ollama_monitor.py`
- **Invoke:** `python -m scripts.ollama_monitor`
- **CLI description:** Monitor and auto-restart Ollama for OpenClaw.
- **Subcommands / commands:** `status`, `restart`, `logs`
  - `status` — Run a one-time health and VRAM status check.
  - `restart` — Restart managed Ollama if needed.
  - `logs` — Print the latest Ollama JSON log.

#### `scripts.optimize_context`

- **Source:** `scripts/optimize_context.py`
- **Invoke:** `python -m scripts.optimize_context`
- **Module summary:** Optimize OpenClaw session context and suggest reductions.
- **CLI description:** Optimize OpenClaw session context.

#### `scripts.proactive_scout`

- **Source:** `scripts/proactive_scout.py`
- **Invoke:** `python -m scripts.proactive_scout`
- **CLI description:** Proactive scout for OpenClaw follow-up prediction.
- **Subcommands / commands:** `check`, `status`, `clear`, `predict`, `_worker`
  - `check` — Return a prepared follow-up if it is already cached.
  - `status` — Show current scout cache and job state.
  - `clear` — Clear scout cache and job records.
  - `predict` — Predict follow-ups and optionally run them in the background.

#### `scripts.queue_manager`

- **Source:** `scripts/queue_manager.py`
- **Invoke:** `python -m scripts.queue_manager`
- **Module summary:** Cursor Cloud Agents Batch Queue Manager Automatically launches and monitors Cursor agents in batches.
- **CLI description:** Cursor Batch Queue Manager

#### `scripts.sync_obsidian`

- **Source:** `scripts/sync_obsidian.py`
- **Invoke:** `python -m scripts.sync_obsidian`
- **Module summary:** Bidirectional sync between MEMORY.md and an Obsidian vault.
- **CLI description:** Bidirectional sync between MEMORY.md and an Obsidian vault.

#### `scripts.telegram_sender`

- **Source:** `scripts/telegram_sender.py`
- **Invoke:** `python -m scripts.telegram_sender`
- **Module summary:** Telegram bot sender utility for OpenClaw.
- **CLI description:** Send media to Telegram via bot API.
- **Subcommands / commands:** `send-photo`, `send-group`, `send-document`
  - `send-photo` — Send a single photo.
  - `send-group` — Send a media group.
  - `send-document` — Send a document upload.

#### `scripts.tool_discovery`

- **Source:** `scripts/tool_discovery.py`
- **Invoke:** `python -m scripts.tool_discovery`
- **Module summary:** Scan OpenClaw workspace(s) for CLI tools, Python modules, APIs, and skills.
- **CLI description:** Scan OpenClaw workspace(s) for CLI tools, Python modules, APIs, and skills.

#### `scripts.video_thumbnail_generator`

- **Source:** `scripts/video_thumbnail_generator.py`
- **Invoke:** `python -m scripts.video_thumbnail_generator`
- **Module summary:** Thumbnail generator for furniture videos.
- **CLI description:** Generate furniture video thumbnails.


### `src/` — package CLIs

#### `src.coordination.cross_bot_sync`

- **Source:** `src/coordination/cross_bot_sync.py`
- **Invoke:** `python -m src.coordination.cross_bot_sync`
- **Module summary:** Cross-bot coordination helpers for OpenClaw bots.
- **CLI description:** OpenClaw cross-bot sync coordinator
- **Subcommands / commands:** `sync`, `handoff`, `status`, `unlock`
  - `sync` — Sync a MEMORY.md file with shared state.
  - `handoff` — Claim, transfer, or release a task.
  - `status` — Write bot status to the shared status file.
  - `unlock` — Remove the shared lock file if it exists.

#### `src.monitoring.session_monitor`

- **Source:** `src/monitoring/session_monitor.py`
- **Invoke:** `python -m src.monitoring.session_monitor`
- **Module summary:** OpenClaw Session Monitor Monitors session sizes and alerts when threshold exceeded.
- **CLI description:** OpenClaw Session Monitor

#### `src.openclaw_orchestration.task_runner`

- **Source:** `src/openclaw_orchestration/task_runner.py`
- **Invoke:** `python -m src.openclaw_orchestration.task_runner`
- **CLI description:** Run OpenClaw task workflows.
- **Subcommands / commands:** `list-tasks`, `run-task`, `show-status`
  - `list-tasks` — List available tasks.
  - `run-task` — Run a single task.
  - `show-status` — Show latest task status.

#### `src.self_improvement.auto_engine`

- **Source:** `src/self_improvement/auto_engine.py`
- **Invoke:** `python -m src.self_improvement.auto_engine`
- **CLI description:** OpenClaw autonomous self-improvement engine

#### `src.skills.proactive_watcher`

- **Source:** `src/skills/proactive_watcher.py`
- **Invoke:** `python -m src.skills.proactive_watcher`
- **CLI description:** Proactive skill watcher for OpenClaw.


## Library-style `scripts/` modules (no argparse CLI)

- **`scripts.media_tool`** (`scripts/media_tool.py`): Utilities for preparing media files before upload.

## In-repo Python packages (`src/`)

- **`src.coordination`** — Coordination utilities for OpenClaw bots.
- **`src.dreams`**
- **`src.ideation`** — Idea pipeline package for OpenClaw.
- **`src.monitoring`** — OpenClaw Monitoring Package
- **`src.openclaw_orchestration`**
- **`src.self_improvement`** — Self-improvement utilities for OpenClaw.
- **`src.skills`** — Skill utilities for OpenClaw.
  - `proactive_watcher`

## HTTP / URL endpoints referenced in code

Constants and literals observed in scanned Python sources (not a live reachability test).

- **`scripts/batch_image_optimizer.py`**
  - `https://api.minimax.chat/v1`

- **`scripts/comfy_auto_quality.py`**
  - `http://127.0.0.1:8188`
  - `http://192.168.31.180:8188`

- **`scripts/comfy_video_pipeline.py`**
  - `http://127.0.0.1:8188`

- **`scripts/context_split.py`**
  - `https://openrouter.ai/api/v1/chat/completions`

- **`scripts/ollama_monitor.py`**
  - `http://localhost:11434/api/tags`

- **`scripts/queue_manager.py`**
  - `https://cursor.com/agents/{agent_id}`
  - `https://github.com/sakura565-afk/openclaw-cursor`

- **`scripts/telegram_sender.py`**
  - `https://api.telegram.org`

## OpenClaw skill directories

No filesystem skills found under `$OPENCLAW_HOME/skills` or `$OPENCLAW_HOME/workspace/skills`. Set `OPENCLAW_HOME` or create skill folders to populate this section.

## Example artifacts

- `examples/bot_status_example.json` — _{_
- `examples/pipeline_example.yaml` — _pipeline:_

## Usage pattern for agents

1. Prefer `python -m <module>` for packaged entry points listed above.
2. Check **subcommands** before inventing flags; many tools expose `run`, `scan`, multi-phase workflows, etc.
3. Use **skill directories** on disk for experiments and user-specific automations;
   packaged logic also lives under `src/skills/`.
4. Re-run discovery after substantive changes:

   ```bash
   python scripts/tool_discovery.py
   ```


