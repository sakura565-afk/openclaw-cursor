# Tool discovery index

Timestamps and metadata live in [`manifest.json`](manifest.json) (`generated_at`).
Regenerate after edits: `python3 src/skills/tool_discovery.py --write`.

## Modules

- [scripts/__init__.py](by_module/scripts____init__.py.md) — Utility scripts for OpenClaw automation.
- [scripts/ami_parser.py](by_module/scripts__ami_parser.py.md) — AMI.by price intelligence tracker for furniture categories.
- [scripts/batch_image_optimizer.py](by_module/scripts__batch_image_optimizer.py.md) — Bulk image optimizer with optional MiniMax enhancement.
- [scripts/comfy_auto_quality.py](by_module/scripts__comfy_auto_quality.py.md) — Universal auto-quality processor for ComfyUI images.
- [scripts/comfy_video_pipeline.py](by_module/scripts__comfy_video_pipeline.py.md) — Универсальный пайплайн генерации видео мебели через ComfyUI API:
- [scripts/context_split.py](by_module/scripts__context_split.py.md) — Split large contexts before querying MiniMax.
- [scripts/face_swap_batch.py](by_module/scripts__face_swap_batch.py.md) — Batch face swap processor for model photography.
- [scripts/marketplace_dashboard.py](by_module/scripts__marketplace_dashboard.py.md) — Marketplace analytics dashboard for Amadey.ru, Wildberries, and Ozon.
- [scripts/media_tool.py](by_module/scripts__media_tool.py.md) — Utilities for preparing media files before upload.
- [scripts/memory_analytics.py](by_module/scripts__memory_analytics.py.md) — Memory health analytics for MEMORY.md files.
- [scripts/memory_cleanup.py](by_module/scripts__memory_cleanup.py.md) — Automated memory cleanup for OpenClaw.
- [scripts/obsidian_dashboard.py](by_module/scripts__obsidian_dashboard.py.md) — Obsidian Vault Dashboard
- [scripts/ollama_batch.py](by_module/scripts__ollama_batch.py.md) — Run Ollama prompts in parallel and save responses to JSON.
- [scripts/ollama_benchmark.py](by_module/scripts__ollama_benchmark.py.md) — Benchmark installed Ollama models.
- [scripts/ollama_manifest_fix.py](by_module/scripts__ollama_manifest_fix.py.md) — Repair Ollama on-disk manifests so current servers can list and load local models.
- [scripts/ollama_model_manager.py](by_module/scripts__ollama_model_manager.py.md) — Manage local Ollama models for OpenClaw.
- [scripts/ollama_monitor.py](by_module/scripts__ollama_monitor.py.md) — Monitor and auto-restart Ollama for OpenClaw.
- [scripts/optimize_context.py](by_module/scripts__optimize_context.py.md) — Optimize OpenClaw session context and suggest reductions.
- [scripts/proactive_scout.py](by_module/scripts__proactive_scout.py.md) — Proactive scout for OpenClaw follow-up prediction.
- [scripts/queue_manager.py](by_module/scripts__queue_manager.py.md) — Cursor Cloud Agents Batch Queue Manager
- [scripts/sync_obsidian.py](by_module/scripts__sync_obsidian.py.md) — Bidirectional sync between MEMORY.md and an Obsidian vault.
- [scripts/telegram_sender.py](by_module/scripts__telegram_sender.py.md) — Telegram bot sender utility for OpenClaw.
- [scripts/video_thumbnail_generator.py](by_module/scripts__video_thumbnail_generator.py.md) — Thumbnail generator for furniture videos.
- [src/__init__.py](by_module/src____init__.py.md) — OpenClaw source package.
- [src/coordination/__init__.py](by_module/src__coordination____init__.py.md) — Coordination utilities for OpenClaw bots.
- [src/coordination/cross_bot_sync.py](by_module/src__coordination__cross_bot_sync.py.md) — Cross-bot coordination helpers for OpenClaw bots.
- [src/dreams/__init__.py](by_module/src__dreams____init__.py.md) — Library or helper module (no module docstring or ArgumentParser description found).
- [src/dreams/dream_tracker.py](by_module/src__dreams__dream_tracker.py.md) — Library or helper module (no module docstring or ArgumentParser description found).
- [src/ideation/__init__.py](by_module/src__ideation____init__.py.md) — Idea pipeline package for OpenClaw.
- [src/ideation/idea_pipeline.py](by_module/src__ideation__idea_pipeline.py.md) — Idea-to-PR pipeline utilities for OpenClaw.
- [src/monitoring/__init__.py](by_module/src__monitoring____init__.py.md) — OpenClaw Monitoring Package
- [src/monitoring/session_monitor.py](by_module/src__monitoring__session_monitor.py.md) — OpenClaw Session Monitor
- [src/openclaw_orchestration/__init__.py](by_module/src__openclaw_orchestration____init__.py.md) — Library or helper module (no module docstring or ArgumentParser description found).
- [src/openclaw_orchestration/task_runner.py](by_module/src__openclaw_orchestration__task_runner.py.md) — Run OpenClaw task workflows.
- [src/self_improvement/__init__.py](by_module/src__self_improvement____init__.py.md) — Self-improvement utilities for OpenClaw.
- [src/self_improvement/auto_engine.py](by_module/src__self_improvement__auto_engine.py.md) — OpenClaw autonomous self-improvement engine
- [src/skills/__init__.py](by_module/src__skills____init__.py.md) — Skill utilities for OpenClaw.
- [src/skills/proactive_watcher.py](by_module/src__skills__proactive_watcher.py.md) — Proactive skill watcher for OpenClaw.
- [src/skills/tool_discovery.py](by_module/src__skills__tool_discovery.py.md) — Static discovery for CLI scripts and library modules under ``scripts/`` and ``src/``.
