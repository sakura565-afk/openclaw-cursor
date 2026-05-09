# Tool discovery report

_Generated for repository `workspace`._

## Catalog overview

- **Script tools** (`scripts/*.py`): 41
- **SKILL.md / skill.md** files: 0
- **Public `src/` entrypoints** (module-level functions): 26

## Session log usage

- Session-like files under log roots: **0**; files with parsed tool rows: **0**.

_No tool invocations parsed from session logs._
Ensure transcripts use `role: tool` lines or JSON exports compatible with `conversation_extractor.parse_session_log`.

## Best practices

- Prefer **narrow scripts** with clear `--help` and subcommands; match the task to capability labels in this report.
- For **high-risk** tools (subprocess, raw network), dry-run against sample data and keep working directories under version control.
- When logs show repeated failures for one tool, read the captured error line, fix inputs, then retry with a **smaller scope** (single file or single API call).
- Align long-running work with **dependencies** listed per script: chain tools in the suggested order instead of parallel shell hacks.

## Common failure patterns (heuristic)

- **Timeouts / refused connections**: retry with backoff; verify local daemons (`ollama`, bridges) are running.
- **Permission / path errors**: use absolute paths from repo root; check `logs/` is writable.
- **JSON / schema errors**: validate payloads with `python -m json.tool` before pasting into sessions.

## Recommended alternatives (scripts)

_No failed tool calls detected in scanned logs._

## Script reference (compact)

### `ami_parser`

- Path: `scripts/ami_parser.py` — AMI.by price intelligence tracker for furniture categories.
- Risk: **medium** | I/O: filesystem, network
- Capabilities: General utility automation
- Related scripts: `auto_memory_cleanup`, `auto_reflection`, `batch_image_optimizer`, `comfy_auto_quality`, `comfy_video_pipeline`, `context_split`, `conversation_extractor`, `doc_generator`, `error_learning`, `exif_date_normalizer`, `face_clustering`, `face_swap_batch` (+25 more)

### `auto_memory_cleanup`

- Path: `scripts/auto_memory_cleanup.py` — Auto Memory Cleanup — clean and maintain MEMORY.md.
- Risk: **low** | I/O: filesystem
- Capabilities: Cleanup and maintenance
- Related scripts: `ami_parser`, `auto_reflection`, `batch_image_optimizer`, `comfy_auto_quality`, `comfy_video_pipeline`, `context_split`, `conversation_extractor`, `doc_generator`, `error_learning`, `exif_date_normalizer`, `face_clustering`, `face_swap_batch` (+28 more)

### `auto_reflection`

- Path: `scripts/auto_reflection.py` — Cron-friendly self-reflection over recent agent-style logs and session artifacts.
- Risk: **medium** | I/O: filesystem, network, structured-data
- Capabilities: Messaging and notifications
- Related scripts: `ami_parser`, `auto_memory_cleanup`, `batch_image_optimizer`, `comfy_auto_quality`, `comfy_video_pipeline`, `context_split`, `conversation_extractor`, `doc_generator`, `error_learning`, `exif_date_normalizer`, `face_clustering`, `face_swap_batch` (+28 more)

### `batch_image_optimizer`

- Path: `scripts/batch_image_optimizer.py` — Bulk image optimizer with optional MiniMax enhancement.
- Risk: **high** | I/O: filesystem, network, structured-data
- Capabilities: General utility automation
- Related scripts: `ami_parser`, `auto_memory_cleanup`, `auto_reflection`, `comfy_auto_quality`, `comfy_video_pipeline`, `context_split`, `conversation_extractor`, `doc_generator`, `error_learning`, `exif_date_normalizer`, `face_clustering`, `face_swap_batch` (+28 more)
- Subcommands: `run`, `self-test`

### `comfy_auto_quality`

- Path: `scripts/comfy_auto_quality.py` — Universal auto-quality processor for ComfyUI images.
- Risk: **medium** | I/O: filesystem, network
- Capabilities: Queue orchestration
- Related scripts: `ami_parser`, `auto_memory_cleanup`, `auto_reflection`, `batch_image_optimizer`, `comfy_video_pipeline`, `context_split`, `conversation_extractor`, `doc_generator`, `error_learning`, `exif_date_normalizer`, `face_clustering`, `face_swap_batch` (+28 more)

### `comfy_video_pipeline`

- Path: `scripts/comfy_video_pipeline.py` — Универсальный пайплайн генерации видео мебели через ComfyUI API:
- Risk: **high** | I/O: filesystem, network, process, structured-data
- Capabilities: Queue orchestration
- Related scripts: `ami_parser`, `auto_memory_cleanup`, `auto_reflection`, `batch_image_optimizer`, `comfy_auto_quality`, `context_split`, `conversation_extractor`, `doc_generator`, `error_learning`, `exif_date_normalizer`, `face_clustering`, `face_swap_batch` (+28 more)

### `context_split`

- Path: `scripts/context_split.py` — No module docstring available.
- Risk: **medium** | I/O: filesystem, network, structured-data
- Capabilities: Context shaping and prompt preparation
- Related scripts: `ami_parser`, `auto_memory_cleanup`, `auto_reflection`, `batch_image_optimizer`, `comfy_auto_quality`, `comfy_video_pipeline`, `conversation_extractor`, `doc_generator`, `error_learning`, `exif_date_normalizer`, `face_clustering`, `face_swap_batch` (+28 more)

### `conversation_extractor`

- Path: `scripts/conversation_extractor.py` — Extract decisions, learnings, and tool-usage highlights from OpenClaw session transcripts.
- Risk: **low** | I/O: filesystem, structured-data
- Capabilities: General utility automation
- Related scripts: `ami_parser`, `auto_memory_cleanup`, `auto_reflection`, `batch_image_optimizer`, `comfy_auto_quality`, `comfy_video_pipeline`, `context_split`, `doc_generator`, `error_learning`, `exif_date_normalizer`, `face_clustering`, `face_swap_batch` (+28 more)

### `doc_generator`

- Path: `scripts/doc_generator.py` — Automated markdown documentation generator for OpenClaw scripts.
- Risk: **medium** | I/O: filesystem
- Capabilities: General utility automation
- Related scripts: `ami_parser`, `auto_memory_cleanup`, `auto_reflection`, `batch_image_optimizer`, `comfy_auto_quality`, `comfy_video_pipeline`, `context_split`, `conversation_extractor`, `error_learning`, `exif_date_normalizer`, `face_clustering`, `face_swap_batch` (+28 more)

### `error_learning`

- Path: `scripts/error_learning.py` — Capture and learn from recurring OpenClaw session errors.
- Risk: **medium** | I/O: filesystem, structured-data
- Capabilities: General utility automation
- Related scripts: `ami_parser`, `auto_memory_cleanup`, `auto_reflection`, `batch_image_optimizer`, `comfy_auto_quality`, `comfy_video_pipeline`, `context_split`, `conversation_extractor`, `doc_generator`, `exif_date_normalizer`, `face_clustering`, `face_swap_batch` (+28 more)
- Subcommands: `add`, `list`, `search`, `stats`

### `exif_date_normalizer`

- Path: `scripts/exif_date_normalizer.py` — Normalize photo filenames using EXIF DateTimeOriginal metadata.
- Risk: **low** | I/O: filesystem
- Capabilities: General utility automation
- Related scripts: `ami_parser`, `auto_memory_cleanup`, `auto_reflection`, `batch_image_optimizer`, `comfy_auto_quality`, `comfy_video_pipeline`, `context_split`, `conversation_extractor`, `doc_generator`, `error_learning`, `face_clustering`, `face_swap_batch` (+26 more)

### `face_clustering`

- Path: `scripts/face_clustering.py` — No module docstring available.
- Risk: **medium** | I/O: filesystem, structured-data
- Capabilities: General utility automation
- Related scripts: `ami_parser`, `auto_memory_cleanup`, `auto_reflection`, `batch_image_optimizer`, `comfy_auto_quality`, `comfy_video_pipeline`, `context_split`, `conversation_extractor`, `doc_generator`, `error_learning`, `exif_date_normalizer`, `face_swap_batch` (+28 more)

### `face_swap_batch`

- Path: `scripts/face_swap_batch.py` — Batch face swap processor for model photography.
- Risk: **low** | I/O: filesystem
- Capabilities: Model lifecycle management
- Related scripts: `ami_parser`, `auto_memory_cleanup`, `auto_reflection`, `batch_image_optimizer`, `comfy_auto_quality`, `comfy_video_pipeline`, `context_split`, `conversation_extractor`, `doc_generator`, `error_learning`, `exif_date_normalizer`, `face_clustering` (+26 more)

### `goal_decomposer`

- Path: `scripts/goal_decomposer.py` — Goal Decomposer — break goals into actionable roadmaps.
- Risk: **low** | I/O: filesystem
- Capabilities: General utility automation
- Related scripts: `ami_parser`, `auto_memory_cleanup`, `auto_reflection`, `batch_image_optimizer`, `comfy_auto_quality`, `comfy_video_pipeline`, `context_split`, `conversation_extractor`, `doc_generator`, `error_learning`, `exif_date_normalizer`, `face_clustering` (+28 more)

### `image_format_migrator`

- Path: `scripts/image_format_migrator.py` — Convert and compress image archives to JPEG.
- Risk: **medium** | I/O: filesystem
- Capabilities: General utility automation
- Related scripts: `ami_parser`, `auto_memory_cleanup`, `auto_reflection`, `batch_image_optimizer`, `comfy_auto_quality`, `comfy_video_pipeline`, `context_split`, `conversation_extractor`, `doc_generator`, `error_learning`, `exif_date_normalizer`, `face_clustering` (+28 more)

### `marketplace_dashboard`

- Path: `scripts/marketplace_dashboard.py` — Marketplace analytics dashboard for Amadey.ru, Wildberries, and Ozon.
- Risk: **low** | I/O: filesystem
- Capabilities: Analytics and reporting
- Related scripts: `ami_parser`, `auto_memory_cleanup`, `auto_reflection`, `batch_image_optimizer`, `comfy_auto_quality`, `comfy_video_pipeline`, `context_split`, `conversation_extractor`, `doc_generator`, `error_learning`, `exif_date_normalizer`, `face_clustering` (+25 more)

### `media_tool`

- Path: `scripts/media_tool.py` — Utilities for preparing media files before upload.
- Risk: **high** | I/O: filesystem, process
- Capabilities: Cleanup and maintenance, Media processing
- Related scripts: `ami_parser`, `auto_memory_cleanup`, `auto_reflection`, `batch_image_optimizer`, `comfy_auto_quality`, `comfy_video_pipeline`, `context_split`, `conversation_extractor`, `doc_generator`, `error_learning`, `exif_date_normalizer`, `face_clustering` (+28 more)

### `memory_analytics`

- Path: `scripts/memory_analytics.py` — Memory health analytics for MEMORY.md files.
- Risk: **medium** | I/O: filesystem, structured-data
- Capabilities: Analytics and reporting
- Related scripts: `ami_parser`, `auto_memory_cleanup`, `auto_reflection`, `batch_image_optimizer`, `comfy_auto_quality`, `comfy_video_pipeline`, `context_split`, `conversation_extractor`, `doc_generator`, `error_learning`, `exif_date_normalizer`, `face_clustering` (+28 more)

### `memory_cleanup`

- Path: `scripts/memory_cleanup.py` — No module docstring available.
- Risk: **low** | I/O: filesystem, structured-data
- Capabilities: Cleanup and maintenance
- Related scripts: `ami_parser`, `auto_memory_cleanup`, `auto_reflection`, `batch_image_optimizer`, `comfy_auto_quality`, `comfy_video_pipeline`, `context_split`, `conversation_extractor`, `doc_generator`, `error_learning`, `exif_date_normalizer`, `face_clustering` (+28 more)

### `nightly_pipeline`

- Path: `scripts/nightly_pipeline.py` — Nightly Pipeline - Run during 1:00-8:00 AM
- Risk: **high** | I/O: filesystem, process, structured-data
- Capabilities: Cleanup and maintenance, Data synchronization, Messaging and notifications
- Related scripts: `auto_memory_cleanup`, `auto_reflection`, `batch_image_optimizer`, `comfy_auto_quality`, `comfy_video_pipeline`, `context_split`, `conversation_extractor`, `doc_generator`, `error_learning`, `exif_date_normalizer`, `face_clustering`, `face_swap_batch` (+26 more)

### `obsidian_dashboard`

- Path: `scripts/obsidian_dashboard.py` — Obsidian Vault Dashboard
- Risk: **medium** | I/O: filesystem, structured-data
- Capabilities: General utility automation
- Related scripts: `ami_parser`, `auto_memory_cleanup`, `auto_reflection`, `batch_image_optimizer`, `comfy_auto_quality`, `comfy_video_pipeline`, `context_split`, `conversation_extractor`, `doc_generator`, `error_learning`, `exif_date_normalizer`, `face_clustering` (+27 more)

### `obsidian_link_checker`

- Path: `scripts/obsidian_link_checker.py` — Scan an Obsidian vault for broken internal links and write a JSON report.
- Risk: **medium** | I/O: filesystem, network, structured-data
- Capabilities: General utility automation
- Related scripts: `ami_parser`, `auto_memory_cleanup`, `auto_reflection`, `batch_image_optimizer`, `comfy_auto_quality`, `comfy_video_pipeline`, `context_split`, `conversation_extractor`, `doc_generator`, `error_learning`, `exif_date_normalizer`, `face_clustering` (+28 more)

### `ollama_batch`

- Path: `scripts/ollama_batch.py` — No module docstring available.
- Risk: **medium** | I/O: filesystem, process, structured-data
- Capabilities: General utility automation
- Related scripts: `ami_parser`, `auto_memory_cleanup`, `auto_reflection`, `batch_image_optimizer`, `comfy_auto_quality`, `comfy_video_pipeline`, `context_split`, `conversation_extractor`, `doc_generator`, `error_learning`, `exif_date_normalizer`, `face_clustering` (+28 more)
- Subcommands: `run`

### `ollama_batch_download`

- Path: `scripts/ollama_batch_download.py` — Ollama Batch Model Downloader
- Risk: **medium** | I/O: filesystem, process
- Capabilities: Model lifecycle management
- Related scripts: `auto_memory_cleanup`, `auto_reflection`, `batch_image_optimizer`, `comfy_auto_quality`, `comfy_video_pipeline`, `context_split`, `conversation_extractor`, `doc_generator`, `error_learning`, `face_clustering`, `goal_decomposer`, `image_format_migrator` (+21 more)

### `ollama_benchmark`

- Path: `scripts/ollama_benchmark.py` — No module docstring available.
- Risk: **medium** | I/O: filesystem, process, structured-data
- Capabilities: Model lifecycle management, Performance benchmarking
- Related scripts: `ami_parser`, `auto_memory_cleanup`, `auto_reflection`, `batch_image_optimizer`, `comfy_auto_quality`, `comfy_video_pipeline`, `context_split`, `conversation_extractor`, `doc_generator`, `error_learning`, `exif_date_normalizer`, `face_clustering` (+28 more)
- Subcommands: `compare`, `history`, `run`

### `ollama_bridge`

- Path: `scripts/ollama_bridge.py` — Ollama Bridge — HTTP proxy between OpenClaw and Ollama API.
- Risk: **low** | I/O: filesystem, network, structured-data
- Capabilities: Model lifecycle management
- Related scripts: `ami_parser`, `auto_memory_cleanup`, `auto_reflection`, `batch_image_optimizer`, `comfy_auto_quality`, `comfy_video_pipeline`, `context_split`, `conversation_extractor`, `doc_generator`, `error_learning`, `exif_date_normalizer`, `face_clustering` (+28 more)

### `ollama_manifest_fix`

- Path: `scripts/ollama_manifest_fix.py` — Repair Ollama on-disk manifests so current servers can list and load local models.
- Risk: **high** | I/O: filesystem, structured-data
- Capabilities: Model lifecycle management
- Related scripts: `ami_parser`, `auto_memory_cleanup`, `auto_reflection`, `batch_image_optimizer`, `comfy_auto_quality`, `comfy_video_pipeline`, `context_split`, `conversation_extractor`, `doc_generator`, `error_learning`, `exif_date_normalizer`, `face_clustering` (+28 more)

### `ollama_model_manager`

- Path: `scripts/ollama_model_manager.py` — No module docstring available.
- Risk: **high** | I/O: filesystem, process, structured-data
- Capabilities: Cleanup and maintenance, Model lifecycle management
- Related scripts: `ami_parser`, `auto_memory_cleanup`, `auto_reflection`, `batch_image_optimizer`, `comfy_auto_quality`, `comfy_video_pipeline`, `context_split`, `conversation_extractor`, `doc_generator`, `error_learning`, `exif_date_normalizer`, `face_clustering` (+28 more)
- Subcommands: `cleanup`, `list`, `pull`, `remove`, `search`, `show`

### `ollama_monitor`

- Path: `scripts/ollama_monitor.py` — No module docstring available.
- Risk: **high** | I/O: filesystem, network, process, structured-data
- Capabilities: Data synchronization, Monitoring and observability
- Related scripts: `ami_parser`, `auto_memory_cleanup`, `auto_reflection`, `batch_image_optimizer`, `comfy_auto_quality`, `comfy_video_pipeline`, `context_split`, `conversation_extractor`, `doc_generator`, `error_learning`, `exif_date_normalizer`, `face_clustering` (+28 more)
- Subcommands: `logs`, `restart`, `status`

### `ollama_queue_monitor`

- Path: `scripts/ollama_queue_monitor.py` — Ollama Queue Monitor - runs every hour, checks progress and launches next model.
- Risk: **medium** | I/O: filesystem, network, process, structured-data
- Capabilities: Model lifecycle management, Monitoring and observability, Queue orchestration
- Related scripts: `auto_memory_cleanup`, `auto_reflection`, `batch_image_optimizer`, `comfy_auto_quality`, `comfy_video_pipeline`, `context_split`, `conversation_extractor`, `doc_generator`, `error_learning`, `face_clustering`, `goal_decomposer`, `image_format_migrator` (+24 more)

### `optimize_context`

- Path: `scripts/optimize_context.py` — Optimize OpenClaw session context and suggest reductions.
- Risk: **low** | I/O: filesystem, network, structured-data
- Capabilities: Context shaping and prompt preparation
- Related scripts: `ami_parser`, `auto_memory_cleanup`, `auto_reflection`, `batch_image_optimizer`, `comfy_auto_quality`, `comfy_video_pipeline`, `context_split`, `conversation_extractor`, `doc_generator`, `error_learning`, `exif_date_normalizer`, `face_clustering` (+28 more)

### `photo_archive_report`

- Path: `scripts/photo_archive_report.py` — Photo archive analytics and integrity report generator.
- Risk: **low** | I/O: filesystem, structured-data
- Capabilities: Analytics and reporting
- Related scripts: `ami_parser`, `auto_memory_cleanup`, `auto_reflection`, `batch_image_optimizer`, `comfy_auto_quality`, `comfy_video_pipeline`, `context_split`, `conversation_extractor`, `doc_generator`, `error_learning`, `exif_date_normalizer`, `face_clustering` (+27 more)

### `photo_deduplication`

- Path: `scripts/photo_deduplication.py` — Photo archive deduplication with perceptual and average hashes.
- Risk: **high** | I/O: filesystem, structured-data
- Capabilities: General utility automation
- Related scripts: `ami_parser`, `auto_memory_cleanup`, `auto_reflection`, `batch_image_optimizer`, `comfy_auto_quality`, `comfy_video_pipeline`, `context_split`, `conversation_extractor`, `doc_generator`, `error_learning`, `exif_date_normalizer`, `face_clustering` (+27 more)

### `proactive_scout`

- Path: `scripts/proactive_scout.py` — No module docstring available.
- Risk: **high** | I/O: filesystem, process, structured-data
- Capabilities: General utility automation
- Related scripts: `ami_parser`, `auto_memory_cleanup`, `auto_reflection`, `batch_image_optimizer`, `comfy_auto_quality`, `comfy_video_pipeline`, `context_split`, `conversation_extractor`, `doc_generator`, `error_learning`, `exif_date_normalizer`, `face_clustering` (+28 more)
- Subcommands: `_worker`, `check`, `clear`, `predict`, `status`

### `process_images`

- Path: `scripts/process_images.py` — Batch image processing utility for OpenClaw orchestration.
- Risk: **low** | I/O: filesystem
- Capabilities: Task orchestration
- Related scripts: `ami_parser`, `auto_memory_cleanup`, `auto_reflection`, `batch_image_optimizer`, `comfy_auto_quality`, `comfy_video_pipeline`, `context_split`, `conversation_extractor`, `doc_generator`, `error_learning`, `exif_date_normalizer`, `face_clustering` (+28 more)

### `queue_manager`

- Path: `scripts/queue_manager.py` — Cursor Cloud Agents Batch Queue Manager
- Risk: **high** | I/O: filesystem, network, process, structured-data
- Capabilities: Queue orchestration
- Related scripts: `ami_parser`, `auto_memory_cleanup`, `auto_reflection`, `batch_image_optimizer`, `comfy_auto_quality`, `comfy_video_pipeline`, `context_split`, `conversation_extractor`, `doc_generator`, `error_learning`, `exif_date_normalizer`, `face_clustering` (+28 more)

### `run_task`

- Path: `scripts/run_task.py` — Run OpenClaw task definitions from YAML specs.
- Risk: **low** | I/O: filesystem
- Capabilities: General utility automation
- Related scripts: `ami_parser`, `auto_memory_cleanup`, `auto_reflection`, `batch_image_optimizer`, `comfy_auto_quality`, `comfy_video_pipeline`, `context_split`, `conversation_extractor`, `doc_generator`, `error_learning`, `exif_date_normalizer`, `face_clustering` (+28 more)

### `sync_obsidian`

- Path: `scripts/sync_obsidian.py` — Bidirectional sync between MEMORY.md and an Obsidian vault.
- Risk: **medium** | I/O: filesystem, structured-data
- Capabilities: Data synchronization
- Related scripts: `ami_parser`, `auto_memory_cleanup`, `auto_reflection`, `batch_image_optimizer`, `comfy_auto_quality`, `comfy_video_pipeline`, `context_split`, `conversation_extractor`, `doc_generator`, `error_learning`, `exif_date_normalizer`, `face_clustering` (+28 more)

### `telegram_sender`

- Path: `scripts/telegram_sender.py` — Telegram bot sender utility for OpenClaw.
- Risk: **high** | I/O: filesystem, network, structured-data
- Capabilities: Messaging and notifications
- Related scripts: `ami_parser`, `auto_memory_cleanup`, `auto_reflection`, `batch_image_optimizer`, `comfy_auto_quality`, `comfy_video_pipeline`, `context_split`, `conversation_extractor`, `doc_generator`, `error_learning`, `exif_date_normalizer`, `face_clustering` (+28 more)
- Subcommands: `send-document`, `send-group`, `send-photo`

### `tool_discovery`

- Path: `scripts/tool_discovery.py` — Catalog OpenClaw tools (scripts, skills, source APIs) and correlate usage from session logs.
- Risk: **low** | I/O: filesystem, network, process, structured-data
- Capabilities: General utility automation
- Related scripts: `ami_parser`, `auto_memory_cleanup`, `auto_reflection`, `batch_image_optimizer`, `comfy_auto_quality`, `comfy_video_pipeline`, `context_split`, `conversation_extractor`, `doc_generator`, `error_learning`, `exif_date_normalizer`, `face_clustering` (+28 more)
- Subcommands: `discover`, `health-check`, `report`

### `video_thumbnail_generator`

- Path: `scripts/video_thumbnail_generator.py` — Thumbnail generator for furniture videos.
- Risk: **medium** | I/O: filesystem, process
- Capabilities: General utility automation
- Related scripts: `ami_parser`, `auto_memory_cleanup`, `auto_reflection`, `batch_image_optimizer`, `comfy_auto_quality`, `comfy_video_pipeline`, `context_split`, `conversation_extractor`, `doc_generator`, `error_learning`, `exif_date_normalizer`, `face_clustering` (+28 more)
