# OpenClaw tool discovery index

Curated, searchable view of repository tools (scripts, skills, doc index) plus
how to interpret session-derived usage metrics.

## How to refresh

```bash
python -m scripts.tool_discovery scan --root .
python -m scripts.tool_discovery search "sync obsidian notes" --top 5 --root .
```

## Usage tips

- Prefer ``python -m scripts.<name> --help`` to confirm CLI flags; parameters below come from static analysis.
- ``success_rate`` / ``error_rate`` in JSON registries are **heuristic** signals from log adjacency, not ground truth.
- When logs are empty, run ``scan`` after collecting agent transcripts under ``logs/`` or ``memory/``.

## Session usage snapshot

- Files scanned: **3**
- Distinct tool-like symbols: **0**

## Tool capabilities

### `ami_parser` (script)

AMI.by price intelligence tracker for furniture categories.

**Use cases**
- When you need general utility automation, consider ``ami_parser``.

**Examples**
```bash
python -m scripts.ami_parser
```
```bash
# Network-aware run
python -m scripts.ami_parser --help
```
```bash
# Filesystem workflow
python -m scripts.ami_parser --help
```
```bash
python scripts/ami_parser.py --help
```

**Tags:** General utility automation

---

### `auto_memory_cleanup` (script)

Auto Memory Cleanup — clean and maintain MEMORY.md.

**Use cases**
- When you need cleanup and maintenance, consider ``auto_memory_cleanup``.

**Examples**
```bash
python -m scripts.auto_memory_cleanup
```
```bash
# Filesystem workflow
python -m scripts.auto_memory_cleanup --help
```
```bash
python scripts/auto_memory_cleanup.py --help
```
```bash
python scripts/auto_memory_cleanup.py
```

**Tags:** Cleanup and maintenance

---

### `auto_reflection` (script)

Cron-friendly self-reflection over recent agent-style logs and session artifacts.

**Use cases**
- When you need messaging and notifications, consider ``auto_reflection``.

**Examples**
```bash
python -m scripts.auto_reflection
```
```bash
# Network-aware run
python -m scripts.auto_reflection --help
```
```bash
# Filesystem workflow
python -m scripts.auto_reflection --help
```
```bash
python scripts/auto_reflection.py --help
```

**Tags:** Messaging and notifications

---

### `batch_image_optimizer` (script)

Bulk image optimizer with optional MiniMax enhancement.

**Use cases**
- When you need general utility automation, consider ``batch_image_optimizer``.
- Typical entry points: subcommands `run`, `self-test`.

**Examples**
```bash
python -m scripts.batch_image_optimizer run
```
```bash
python -m scripts.batch_image_optimizer self-test
```
```bash
# Network-aware run
python -m scripts.batch_image_optimizer --help
```
```bash
# Filesystem workflow
python -m scripts.batch_image_optimizer --help
```

**Tags:** General utility automation

---

### `comfy_auto_quality` (script)

Universal auto-quality processor for ComfyUI images.

**Use cases**
- When you need queue orchestration, consider ``comfy_auto_quality``.

**Examples**
```bash
python -m scripts.comfy_auto_quality
```
```bash
# Network-aware run
python -m scripts.comfy_auto_quality --help
```
```bash
# Filesystem workflow
python -m scripts.comfy_auto_quality --help
```
```bash
python scripts/comfy_auto_quality.py --help
```

**Tags:** Queue orchestration

---

### `comfy_video_pipeline` (script)

Универсальный пайплайн генерации видео мебели через ComfyUI API:

**Use cases**
- When you need queue orchestration, consider ``comfy_video_pipeline``.
- Some CLI arguments are required; run with ``--help`` for the full contract.

**Examples**
```bash
python -m scripts.comfy_video_pipeline
```
```bash
# Network-aware run
python -m scripts.comfy_video_pipeline --help
```
```bash
# Filesystem workflow
python -m scripts.comfy_video_pipeline --help
```
```bash
python scripts/comfy_video_pipeline.py --help
```

**Tags:** Queue orchestration

---

### `context_split` (script)

No module docstring available.

**Use cases**
- When you need context shaping and prompt preparation, consider ``context_split``.
- Some CLI arguments are required; run with ``--help`` for the full contract.

**Examples**
```bash
python -m scripts.context_split
```
```bash
# Network-aware run
python -m scripts.context_split --help
```
```bash
# Filesystem workflow
python -m scripts.context_split --help
```
```bash
python scripts/context_split.py --help
```

**Tags:** Context shaping and prompt preparation

---

### `conversation_extractor` (script)

Extract decisions, learnings, and tool-usage highlights from OpenClaw session transcripts.

**Use cases**
- When you need general utility automation, consider ``conversation_extractor``.

**Examples**
```bash
python -m scripts.conversation_extractor
```
```bash
# Filesystem workflow
python -m scripts.conversation_extractor --help
```
```bash
python scripts/conversation_extractor.py --help
```
```bash
python scripts/conversation_extractor.py session_log
```

**Tags:** General utility automation

---

### `doc_generator` (script)

Automated markdown documentation generator for OpenClaw scripts.

**Use cases**
- When you need general utility automation, consider ``doc_generator``.

**Examples**
```bash
python -m scripts.doc_generator
```
```bash
# Filesystem workflow
python -m scripts.doc_generator --help
```
```bash
python scripts/doc_generator.py --help
```
```bash
python scripts/doc_generator.py filenames ...
```

**Tags:** General utility automation

---

### `error_learning` (script)

Capture and learn from recurring OpenClaw session errors.

**Use cases**
- When you need general utility automation, consider ``error_learning``.
- Typical entry points: subcommands `add`, `list`, `search`, `stats`.

**Examples**
```bash
python -m scripts.error_learning add
```
```bash
python -m scripts.error_learning list
```
```bash
python -m scripts.error_learning search
```
```bash
# Filesystem workflow
python -m scripts.error_learning --help
```

**Tags:** General utility automation

---

### `exif_date_normalizer` (script)

Normalize photo filenames using EXIF DateTimeOriginal metadata.

**Use cases**
- When you need general utility automation, consider ``exif_date_normalizer``.
- Some CLI arguments are required; run with ``--help`` for the full contract.

**Examples**
```bash
python -m scripts.exif_date_normalizer
```
```bash
# Filesystem workflow
python -m scripts.exif_date_normalizer --help
```
```bash
python scripts/exif_date_normalizer.py --help
```
```bash
python scripts/exif_date_normalizer.py --scan scan
```

**Tags:** General utility automation

---

### `face_swap_batch` (script)

Batch face swap processor for model photography.

**Use cases**
- When you need model lifecycle management, consider ``face_swap_batch``.

**Examples**
```bash
python -m scripts.face_swap_batch
```
```bash
# Filesystem workflow
python -m scripts.face_swap_batch --help
```
```bash
python scripts/face_swap_batch.py --help
```
```bash
python scripts/face_swap_batch.py
```

**Tags:** Model lifecycle management

---

### `goal_decomposer` (script)

Goal Decomposer — break goals into actionable roadmaps.

**Use cases**
- When you need general utility automation, consider ``goal_decomposer``.
- Some CLI arguments are required; run with ``--help`` for the full contract.

**Examples**
```bash
python -m scripts.goal_decomposer
```
```bash
# Filesystem workflow
python -m scripts.goal_decomposer --help
```
```bash
python scripts/goal_decomposer.py --help
```
```bash
python scripts/goal_decomposer.py command text
```

**Tags:** General utility automation

---

### `image_format_migrator` (script)

Convert and compress image archives to JPEG.

**Use cases**
- When you need general utility automation, consider ``image_format_migrator``.

**Examples**
```bash
python -m scripts.image_format_migrator
```
```bash
# Filesystem workflow
python -m scripts.image_format_migrator --help
```
```bash
python scripts/image_format_migrator.py --help
```
```bash
python scripts/image_format_migrator.py
```

**Tags:** General utility automation

---

### `kara_poll_iskra_results` (script)

Kara cron helper: drain Iskra shared-memory queue, else fall back to tasks/results.

**Use cases**
- When you need queue orchestration, consider ``kara_poll_iskra_results``.

**Examples**
```bash
python -m scripts.kara_poll_iskra_results
```
```bash
# Filesystem workflow
python -m scripts.kara_poll_iskra_results --help
```
```bash
python scripts/kara_poll_iskra_results.py --help
```
```bash
python scripts/kara_poll_iskra_results.py
```

**Tags:** Queue orchestration

---

### `marketplace_dashboard` (script)

Marketplace analytics dashboard for Amadey.ru, Wildberries, and Ozon.

**Use cases**
- When you need analytics and reporting, consider ``marketplace_dashboard``.

**Examples**
```bash
python -m scripts.marketplace_dashboard
```
```bash
# Filesystem workflow
python -m scripts.marketplace_dashboard --help
```
```bash
python scripts/marketplace_dashboard.py --help
```
```bash
python scripts/marketplace_dashboard.py
```

**Tags:** Analytics and reporting

---

### `media_tool` (script)

Utilities for preparing media files before upload.

**Use cases**
- When you need cleanup and maintenance, consider ``media_tool``.
- When you need media processing, consider ``media_tool``.

**Examples**
```bash
python -m scripts.media_tool
```
```bash
# Filesystem workflow
python -m scripts.media_tool --help
```
```bash
python scripts/media_tool.py --help
```
```bash
python scripts/media_tool.py
```

**Tags:** Cleanup and maintenance, Media processing

---

### `memory_analytics` (script)

Memory health analytics for MEMORY.md files.

**Use cases**
- When you need analytics and reporting, consider ``memory_analytics``.

**Examples**
```bash
python -m scripts.memory_analytics
```
```bash
# Filesystem workflow
python -m scripts.memory_analytics --help
```
```bash
python scripts/memory_analytics.py --help
```
```bash
python scripts/memory_analytics.py
```

**Tags:** Analytics and reporting

---

### `memory_cleanup` (script)

No module docstring available.

**Use cases**
- When you need cleanup and maintenance, consider ``memory_cleanup``.

**Examples**
```bash
python -m scripts.memory_cleanup
```
```bash
# Filesystem workflow
python -m scripts.memory_cleanup --help
```
```bash
python scripts/memory_cleanup.py --help
```
```bash
python scripts/memory_cleanup.py
```

**Tags:** Cleanup and maintenance

---

### `nightly_pipeline` (script)

Nightly Pipeline - Run during 1:00-8:00 AM

**Use cases**
- When you need cleanup and maintenance, consider ``nightly_pipeline``.
- When you need data synchronization, consider ``nightly_pipeline``.
- When you need messaging and notifications, consider ``nightly_pipeline``.

**Examples**
```bash
python -m scripts.nightly_pipeline
```
```bash
# Filesystem workflow
python -m scripts.nightly_pipeline --help
```
```bash
python scripts/nightly_pipeline.py --help
```
```bash
python scripts/nightly_pipeline.py
```

**Tags:** Cleanup and maintenance, Data synchronization, Messaging and notifications

---

### `nouz_common` (script)

Shared paths, markdown walking, and domain detection for NOUZ tooling.

**Use cases**
- When you need general utility automation, consider ``nouz_common``.

**Examples**
```bash
python -m scripts.nouz_common
```
```bash
# Filesystem workflow
python -m scripts.nouz_common --help
```
```bash
python scripts/nouz_common.py --help
```
```bash
python scripts/nouz_common.py
```

**Tags:** General utility automation

---

### `nouz_search` (script)

NOUZ MCP-style search over an Obsidian vault: SQLite index + numpy embeddings.

**Use cases**
- When you need context shaping and prompt preparation, consider ``nouz_search``.
- When you need data synchronization, consider ``nouz_search``.
- Typical entry points: subcommands `bundle`, `core-profile`, `find`, `near-core`, `semantic`, `sync`.

**Examples**
```bash
python -m scripts.nouz_search bundle
```
```bash
python -m scripts.nouz_search core-profile
```
```bash
python -m scripts.nouz_search find
```
```bash
# Network-aware run
python -m scripts.nouz_search --help
```

**Tags:** Context shaping and prompt preparation, Data synchronization

---

### `nouz_yaml_tagger` (script)

Scan an Obsidian vault and add NOUZ YAML frontmatter fields when missing.

**Use cases**
- When you need general utility automation, consider ``nouz_yaml_tagger``.

**Examples**
```bash
python -m scripts.nouz_yaml_tagger
```
```bash
# Filesystem workflow
python -m scripts.nouz_yaml_tagger --help
```
```bash
python scripts/nouz_yaml_tagger.py --help
```
```bash
python scripts/nouz_yaml_tagger.py
```

**Tags:** General utility automation

---

### `obsidian_dashboard` (script)

Obsidian Vault Dashboard

**Use cases**
- When you need general utility automation, consider ``obsidian_dashboard``.

**Examples**
```bash
python -m scripts.obsidian_dashboard
```
```bash
# Filesystem workflow
python -m scripts.obsidian_dashboard --help
```
```bash
python scripts/obsidian_dashboard.py --help
```
```bash
python scripts/obsidian_dashboard.py
```

**Tags:** General utility automation

---

### `obsidian_link_checker` (script)

Scan an Obsidian vault for broken internal links and write a JSON report.

**Use cases**
- When you need general utility automation, consider ``obsidian_link_checker``.

**Examples**
```bash
python -m scripts.obsidian_link_checker
```
```bash
# Network-aware run
python -m scripts.obsidian_link_checker --help
```
```bash
# Filesystem workflow
python -m scripts.obsidian_link_checker --help
```
```bash
python scripts/obsidian_link_checker.py --help
```

**Tags:** General utility automation

---

### `ollama_batch` (script)

No module docstring available.

**Use cases**
- When you need general utility automation, consider ``ollama_batch``.
- Typical entry points: subcommands `run`.

**Examples**
```bash
python -m scripts.ollama_batch run
```
```bash
# Filesystem workflow
python -m scripts.ollama_batch --help
```
```bash
python scripts/ollama_batch.py --help
```
```bash
python scripts/ollama_batch.py
```

**Tags:** General utility automation

---

### `ollama_batch_download` (script)

Ollama Batch Model Downloader

**Use cases**
- When you need model lifecycle management, consider ``ollama_batch_download``.

**Examples**
```bash
python -m scripts.ollama_batch_download
```
```bash
# Filesystem workflow
python -m scripts.ollama_batch_download --help
```
```bash
python scripts/ollama_batch_download.py --help
```
```bash
python scripts/ollama_batch_download.py
```

**Tags:** Model lifecycle management

---

### `ollama_benchmark` (script)

No module docstring available.

**Use cases**
- When you need model lifecycle management, consider ``ollama_benchmark``.
- When you need performance benchmarking, consider ``ollama_benchmark``.
- Typical entry points: subcommands `compare`, `history`, `run`.

**Examples**
```bash
python -m scripts.ollama_benchmark compare
```
```bash
python -m scripts.ollama_benchmark history
```
```bash
python -m scripts.ollama_benchmark run
```
```bash
# Filesystem workflow
python -m scripts.ollama_benchmark --help
```

**Tags:** Model lifecycle management, Performance benchmarking

---

### `ollama_bridge` (script)

Ollama Bridge — HTTP proxy between OpenClaw and Ollama API.

**Use cases**
- When you need model lifecycle management, consider ``ollama_bridge``.

**Examples**
```bash
python -m scripts.ollama_bridge
```
```bash
# Network-aware run
python -m scripts.ollama_bridge --help
```
```bash
# Filesystem workflow
python -m scripts.ollama_bridge --help
```
```bash
python scripts/ollama_bridge.py --help
```

**Tags:** Model lifecycle management

---

### `ollama_manifest_fix` (script)

Repair Ollama on-disk manifests so current servers can list and load local models.

**Use cases**
- When you need model lifecycle management, consider ``ollama_manifest_fix``.

**Examples**
```bash
python -m scripts.ollama_manifest_fix
```
```bash
# Filesystem workflow
python -m scripts.ollama_manifest_fix --help
```
```bash
python scripts/ollama_manifest_fix.py --help
```
```bash
python scripts/ollama_manifest_fix.py manifest_paths ...
```

**Tags:** Model lifecycle management

---

### `ollama_model_manager` (script)

No module docstring available.

**Use cases**
- When you need cleanup and maintenance, consider ``ollama_model_manager``.
- When you need model lifecycle management, consider ``ollama_model_manager``.
- Typical entry points: subcommands `cleanup`, `list`, `pull`, `remove`, `search`, `show`.

**Examples**
```bash
python -m scripts.ollama_model_manager cleanup
```
```bash
python -m scripts.ollama_model_manager list
```
```bash
python -m scripts.ollama_model_manager pull
```
```bash
# Filesystem workflow
python -m scripts.ollama_model_manager --help
```

**Tags:** Cleanup and maintenance, Model lifecycle management

---

### `ollama_monitor` (script)

No module docstring available.

**Use cases**
- When you need data synchronization, consider ``ollama_monitor``.
- When you need monitoring and observability, consider ``ollama_monitor``.
- Typical entry points: subcommands `logs`, `restart`, `status`.

**Examples**
```bash
python -m scripts.ollama_monitor logs
```
```bash
python -m scripts.ollama_monitor restart
```
```bash
python -m scripts.ollama_monitor status
```
```bash
# Network-aware run
python -m scripts.ollama_monitor --help
```

**Tags:** Data synchronization, Monitoring and observability

---

### `ollama_queue_monitor` (script)

Ollama Queue Monitor - runs every hour, checks progress and launches next model.

**Use cases**
- When you need model lifecycle management, consider ``ollama_queue_monitor``.
- When you need monitoring and observability, consider ``ollama_queue_monitor``.
- When you need queue orchestration, consider ``ollama_queue_monitor``.

**Examples**
```bash
python -m scripts.ollama_queue_monitor
```
```bash
# Network-aware run
python -m scripts.ollama_queue_monitor --help
```
```bash
# Filesystem workflow
python -m scripts.ollama_queue_monitor --help
```
```bash
python scripts/ollama_queue_monitor.py --help
```

**Tags:** Model lifecycle management, Monitoring and observability, Queue orchestration

---

### `optimize_context` (script)

Optimize OpenClaw session context and suggest reductions.

**Use cases**
- When you need context shaping and prompt preparation, consider ``optimize_context``.

**Examples**
```bash
python -m scripts.optimize_context
```
```bash
# Network-aware run
python -m scripts.optimize_context --help
```
```bash
# Filesystem workflow
python -m scripts.optimize_context --help
```
```bash
python scripts/optimize_context.py --help
```

**Tags:** Context shaping and prompt preparation

---

### `photo_archive_report` (script)

Photo archive analytics and integrity report generator.

**Use cases**
- When you need analytics and reporting, consider ``photo_archive_report``.
- Some CLI arguments are required; run with ``--help`` for the full contract.

**Examples**
```bash
python -m scripts.photo_archive_report
```
```bash
# Filesystem workflow
python -m scripts.photo_archive_report --help
```
```bash
python scripts/photo_archive_report.py --help
```
```bash
python scripts/photo_archive_report.py --scan scan
```

**Tags:** Analytics and reporting

---

### `photo_deduplication` (script)

Photo archive deduplication with perceptual and average hashes.

**Use cases**
- When you need general utility automation, consider ``photo_deduplication``.
- Some CLI arguments are required; run with ``--help`` for the full contract.

**Examples**
```bash
python -m scripts.photo_deduplication
```
```bash
# Filesystem workflow
python -m scripts.photo_deduplication --help
```
```bash
python scripts/photo_deduplication.py --help
```
```bash
python scripts/photo_deduplication.py --scan scan
```

**Tags:** General utility automation

---

### `proactive_scout` (script)

No module docstring available.

**Use cases**
- When you need general utility automation, consider ``proactive_scout``.
- Typical entry points: subcommands `_worker`, `check`, `clear`, `predict`, `status`.

**Examples**
```bash
python -m scripts.proactive_scout _worker
```
```bash
python -m scripts.proactive_scout check
```
```bash
python -m scripts.proactive_scout clear
```
```bash
# Filesystem workflow
python -m scripts.proactive_scout --help
```

**Tags:** General utility automation

---

### `process_images` (script)

Batch image processing utility for OpenClaw orchestration.

**Use cases**
- When you need task orchestration, consider ``process_images``.
- Some CLI arguments are required; run with ``--help`` for the full contract.

**Examples**
```bash
python -m scripts.process_images
```
```bash
# Filesystem workflow
python -m scripts.process_images --help
```
```bash
python scripts/process_images.py --help
```
```bash
python scripts/process_images.py --input input --output output
```

**Tags:** Task orchestration

---

### `queue_manager` (script)

Cursor Cloud Agents Batch Queue Manager

**Use cases**
- When you need queue orchestration, consider ``queue_manager``.
- Some CLI arguments are required; run with ``--help`` for the full contract.

**Examples**
```bash
python -m scripts.queue_manager
```
```bash
# Network-aware run
python -m scripts.queue_manager --help
```
```bash
# Filesystem workflow
python -m scripts.queue_manager --help
```
```bash
python scripts/queue_manager.py --help
```

**Tags:** Queue orchestration

---

### `raw_pipeline` (script)

RAW camera file decoding (CR2, NEF, ARW, DNG, RAF, ORF) via rawpy.

**Use cases**
- When you need general utility automation, consider ``raw_pipeline``.

**Examples**
```bash
python -m scripts.raw_pipeline
```
```bash
# Filesystem workflow
python -m scripts.raw_pipeline --help
```
```bash
python scripts/raw_pipeline.py --help
```
```bash
python scripts/raw_pipeline.py
```

**Tags:** General utility automation

---

### `run_task` (script)

Run OpenClaw task definitions from YAML specs.

**Use cases**
- When you need general utility automation, consider ``run_task``.
- Some CLI arguments are required; run with ``--help`` for the full contract.

**Examples**
```bash
python -m scripts.run_task
```
```bash
# Filesystem workflow
python -m scripts.run_task --help
```
```bash
python scripts/run_task.py --help
```
```bash
python scripts/run_task.py --task task
```

**Tags:** General utility automation

---

### `sqlite_helper` (script)

Small SQLite helpers (connection + row factory) for scripts.

**Use cases**
- When you need general utility automation, consider ``sqlite_helper``.

**Examples**
```bash
python -m scripts.sqlite_helper
```
```bash
# Filesystem workflow
python -m scripts.sqlite_helper --help
```
```bash
python scripts/sqlite_helper.py --help
```
```bash
python scripts/sqlite_helper.py
```

**Tags:** General utility automation

---

### `sync_obsidian` (script)

Bidirectional sync between MEMORY.md and an Obsidian vault.

**Use cases**
- When you need data synchronization, consider ``sync_obsidian``.

**Examples**
```bash
python -m scripts.sync_obsidian
```
```bash
# Filesystem workflow
python -m scripts.sync_obsidian --help
```
```bash
python scripts/sync_obsidian.py --help
```
```bash
python scripts/sync_obsidian.py
```

**Tags:** Data synchronization

---

### `telegram_sender` (script)

Telegram bot sender utility for OpenClaw.

**Use cases**
- When you need messaging and notifications, consider ``telegram_sender``.
- Typical entry points: subcommands `send-document`, `send-group`, `send-photo`.

**Examples**
```bash
python -m scripts.telegram_sender send-document
```
```bash
python -m scripts.telegram_sender send-group
```
```bash
python -m scripts.telegram_sender send-photo
```
```bash
# Network-aware run
python -m scripts.telegram_sender --help
```

**Tags:** Messaging and notifications

---

### `tool_discovery` (script)

Discover OpenClaw-related tools from code, docs, and session logs.

**Use cases**
- When you need general utility automation, consider ``tool_discovery``.
- Typical entry points: subcommands `analyze`, `docs`, `patterns`, `registry`, `scan`, `search`.

**Examples**
```bash
python -m scripts.tool_discovery analyze
```
```bash
python -m scripts.tool_discovery docs
```
```bash
python -m scripts.tool_discovery patterns
```
```bash
# Network-aware run
python -m scripts.tool_discovery --help
```

**Tags:** General utility automation

---

### `video_thumbnail_generator` (script)

Thumbnail generator for furniture videos.

**Use cases**
- When you need general utility automation, consider ``video_thumbnail_generator``.

**Examples**
```bash
python -m scripts.video_thumbnail_generator
```
```bash
# Filesystem workflow
python -m scripts.video_thumbnail_generator --help
```
```bash
python scripts/video_thumbnail_generator.py --help
```
```bash
python scripts/video_thumbnail_generator.py
```

**Tags:** General utility automation

---

### `skill:proactive_watcher` (skill)

No module docstring available.

**Use cases**
- When you need general utility automation, consider ``skill:proactive_watcher``.
- When you need openclaw skill module, consider ``skill:proactive_watcher``.

**Examples**
```bash
python -c "import importlib; importlib.import_module('src.skills.proactive_watcher')"
```

**Tags:** General utility automation, OpenClaw skill module

---
