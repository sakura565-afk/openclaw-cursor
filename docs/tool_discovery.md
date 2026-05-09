# Tool Discovery Report

Auto-generated capability and dependency analysis for `scripts/` entrypoints and `src/` modules.

## Summary

- Total tools discovered: **50** (`scripts/`: 43, `src/`: 7)
- High-risk tools: **15**

## `ami_parser`

- Kind: Script
- Path: `scripts/ami_parser.py`
- Description: AMI.by price intelligence tracker for furniture categories.
- Risk profile: **medium**
- I/O behavior: filesystem, network
- Capabilities: General utility automation
- Dependencies: none

### Functions

- `check_updates()`
- `get_all_products()`
- `get_price_change()`
- `main()`
- `parse_category()`
- `print_report()`

### Commands

- _No subcommands discovered_

### Example usage

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

## `auto_memory_cleanup`

- Kind: Script
- Path: `scripts/auto_memory_cleanup.py`
- Description: Auto Memory Cleanup — clean and maintain MEMORY.md.
- Risk profile: **low**
- I/O behavior: filesystem
- Capabilities: Cleanup and maintenance
- Dependencies: none

### Functions

- `analyze()`
- `clean_old_daily_notes()`
- `find_sections()`
- `load()`
- `main()`
- `merge_duplicate_sections()`
- `print_analysis()`
- `run_cleanup()`
- `save()`

### Commands

- _No subcommands discovered_

### Example usage

```bash
python -m scripts.auto_memory_cleanup
```
```bash
# Filesystem workflow
python -m scripts.auto_memory_cleanup --help
```

## `auto_reflection`

- Kind: Script
- Path: `scripts/auto_reflection.py`
- Description: Cron-friendly self-reflection over recent agent-style logs and session artifacts.
- Risk profile: **medium**
- I/O behavior: filesystem, network, structured-data
- Capabilities: Messaging and notifications
- Dependencies: none

### Functions

- `build_parser()`
- `build_summary_markdown()`
- `collect_globs()`
- `dedupe_insights()`
- `extract_insights_from_json()`
- `extract_insights_from_text()`
- `insight_fingerprint()`
- `iter_session_files()`
- `load_state()`
- `main()`
- `maybe_post_results()`
- `normalize_insight_text()`
- `post_telegram_summary()`
- `post_webhook()`
- `read_and_extract()`
- `run_reflection()`
- `save_state()`
- `update_weekly_summary()`
- `utc_now()`
- `walk()`
- `weekly_report_path()`
- `write_insight_artifacts()`
- `write_latest_pointers()`

### Commands

- _No subcommands discovered_

### Example usage

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

## `batch_image_optimizer`

- Kind: Script
- Path: `scripts/batch_image_optimizer.py`
- Description: Bulk image optimizer with optional MiniMax enhancement.
- Risk profile: **high**
- I/O behavior: filesystem, network, structured-data
- Capabilities: General utility automation
- Dependencies: comfy_auto_quality, comfy_video_pipeline

### Functions

- `append_markdown_log()`
- `apply_resize()`
- `apply_watermark()`
- `load_dotenv()`
- `main()`
- `minimax_enhance_image()`
- `parse_args()`
- `parse_operations()`
- `parse_size()`
- `process_directory()`
- `run_self_test()`
- `setup_logging()`
- `single_image_process()`

### Commands

- `run`
- `self-test`

### Example usage

```bash
python -m scripts.batch_image_optimizer run
```
```bash
python -m scripts.batch_image_optimizer self-test
```
```bash
# Network-aware run
python -m scripts.batch_image_optimizer run --help
```
```bash
# Filesystem workflow
python -m scripts.batch_image_optimizer run --help
```

## `comfy_auto_quality`

- Kind: Script
- Path: `scripts/comfy_auto_quality.py`
- Description: Universal auto-quality processor for ComfyUI images.
- Risk profile: **medium**
- I/O behavior: filesystem, network
- Capabilities: Queue orchestration
- Dependencies: batch_image_optimizer, comfy_video_pipeline, face_clustering, face_swap_batch

### Functions

- `download_image()`
- `log()`
- `main()`
- `object_info()`
- `parse_args()`
- `ping()`
- `process()`
- `process_pil_image()`
- `queue_prompt()`
- `run_self_test()`
- `upload_image_bytes()`
- `wait_result()`

### Commands

- _No subcommands discovered_

### Example usage

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

## `comfy_video_pipeline`

- Kind: Script
- Path: `scripts/comfy_video_pipeline.py`
- Description: Универсальный пайплайн генерации видео мебели через ComfyUI API:
- Risk profile: **high**
- I/O behavior: filesystem, network, process, structured-data
- Capabilities: Queue orchestration
- Dependencies: batch_image_optimizer, comfy_auto_quality

### Functions

- `download_view_image()`
- `get_object_info()`
- `log()`
- `main()`
- `parse_args()`
- `ping()`
- `queue_prompt()`
- `retry()`
- `run()`
- `upload_image()`
- `wait_prompt()`
- `wrapper()`

### Commands

- _No subcommands discovered_

### Example usage

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

## `context_split`

- Kind: Script
- Path: `scripts/context_split.py`
- Description: No module docstring available.
- Risk profile: **medium**
- I/O behavior: filesystem, network, structured-data
- Capabilities: Context shaping and prompt preparation
- Dependencies: none

### Functions

- `build_chunk_messages()`
- `build_synthesis_messages()`
- `estimate_tokens()`
- `extract_message_text()`
- `is_header_block()`
- `load_context()`
- `main()`
- `normalize_api_url()`
- `normalize_text()`
- `openrouter_chat_completion()`
- `parse_args()`
- `query_with_retry()`
- `split_and_query_context()`
- `split_context()`
- `split_semantic_units()`

### Commands

- _No subcommands discovered_

### Example usage

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

## `conversation_extractor`

- Kind: Script
- Path: `scripts/conversation_extractor.py`
- Description: Extract decisions, learnings, and tool-usage highlights from OpenClaw session transcripts.
- Risk profile: **low**
- I/O behavior: filesystem, structured-data
- Capabilities: General utility automation
- Dependencies: none

### Functions

- `all_tools()`
- `analyze_segments()`
- `build_arg_parser()`
- `digest_to_dict()`
- `extract_tool_signals()`
- `main()`
- `match_patterns()`
- `normalize_ws()`
- `parse_json_session()`
- `parse_session_log()`
- `parse_text_session()`
- `render_markdown()`
- `run_extraction()`
- `utc_stamp()`
- `walk()`
- `write_digest()`

### Commands

- _No subcommands discovered_

### Example usage

```bash
python -m scripts.conversation_extractor
```
```bash
# Filesystem workflow
python -m scripts.conversation_extractor --help
```

## `doc_generator`

- Kind: Script
- Path: `scripts/doc_generator.py`
- Description: Automated markdown documentation generator for OpenClaw scripts.
- Risk profile: **medium**
- I/O behavior: filesystem
- Capabilities: General utility automation
- Dependencies: none

### Functions

- `build_parser()`
- `build_usage_examples()`
- `colorize()`
- `detail_text()`
- `detect_color_enabled()`
- `discover_scripts()`
- `display_name()`
- `expression_text()`
- `extract_leading_header()`
- `extract_target_names()`
- `generate_docs()`
- `log()`
- `main()`
- `markdown_escape()`
- `merge_generated_content()`
- `parse_script()`
- `qualified_name()`
- `render_argument_table()`
- `render_exit_codes()`
- `render_master_index()`
- `render_script_readme()`
- `safe_literal()`
- `summarize_description()`
- `usage_token()`
- `visit_AnnAssign()`
- `visit_Assign()`
- `visit_Call()`
- `visit_Import()`
- `visit_ImportFrom()`
- `visit_Raise()`
- `write_if_changed()`

### Commands

- _No subcommands discovered_

### Example usage

```bash
python -m scripts.doc_generator
```
```bash
# Filesystem workflow
python -m scripts.doc_generator --help
```

## `error_learning`

- Kind: Script
- Path: `scripts/error_learning.py`
- Description: Capture and learn from recurring OpenClaw session errors.
- Risk profile: **medium**
- I/O behavior: filesystem, structured-data
- Capabilities: General utility automation
- Dependencies: none

### Functions

- `add_entry()`
- `build_entry()`
- `canonical_payload()`
- `category_color()`
- `colorize()`
- `default_store()`
- `entries_match()`
- `format_entry()`
- `load_store()`
- `main()`
- `normalize_text()`
- `parse_args()`
- `print_entries()`
- `print_stats()`
- `save_store()`
- `search_entries()`
- `search_score()`
- `validate_entry()`

### Commands

- `add`
- `list`
- `search`
- `stats`

### Example usage

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
python -m scripts.error_learning add --help
```

## `exif_date_normalizer`

- Kind: Script
- Path: `scripts/exif_date_normalizer.py`
- Description: Normalize photo filenames using EXIF DateTimeOriginal metadata.
- Risk profile: **low**
- I/O behavior: filesystem
- Capabilities: General utility automation
- Dependencies: photo_archive_report, photo_deduplication

### Functions

- `build_new_name()`
- `ensure_unique_target()`
- `iter_supported_files()`
- `main()`
- `parse_args()`
- `parse_exif_datetime()`
- `process_file()`
- `read_exif_datetime()`
- `read_folder_datetime()`
- `write_csv_log()`

### Commands

- _No subcommands discovered_

### Example usage

```bash
python -m scripts.exif_date_normalizer
```
```bash
# Filesystem workflow
python -m scripts.exif_date_normalizer --help
```

## `face_clustering`

- Kind: Script
- Path: `scripts/face_clustering.py`
- Description: No module docstring available.
- Risk profile: **medium**
- I/O behavior: filesystem, structured-data
- Capabilities: General utility automation
- Dependencies: comfy_auto_quality, face_swap_batch

### Functions

- `auto_cluster()`
- `build_catalog()`
- `candidate_eps_values()`
- `cluster_with_eps()`
- `connected_components()`
- `discover_images()`
- `encode()`
- `encode()`
- `encode()`
- `export_folders()`
- `extract_records()`
- `file_signature()`
- `load_cache()`
- `main()`
- `pairwise_distances()`
- `parse_args()`
- `pick_backend()`
- `positive_int()`
- `run()`
- `safe_link_name()`
- `save_cache()`

### Commands

- _No subcommands discovered_

### Example usage

```bash
python -m scripts.face_clustering
```
```bash
# Filesystem workflow
python -m scripts.face_clustering --help
```

## `face_swap_batch`

- Kind: Script
- Path: `scripts/face_swap_batch.py`
- Description: Batch face swap processor for model photography.
- Risk profile: **low**
- I/O behavior: filesystem
- Capabilities: Model lifecycle management
- Dependencies: comfy_auto_quality, face_clustering

### Functions

- `apply_gfpgan()`
- `apply_inswapper()`
- `batch_swap()`
- `detect_faces()`
- `log()`
- `main()`
- `single_swap()`

### Commands

- _No subcommands discovered_

### Example usage

```bash
python -m scripts.face_swap_batch
```
```bash
# Filesystem workflow
python -m scripts.face_swap_batch --help
```

## `goal_decomposer`

- Kind: Script
- Path: `scripts/goal_decomposer.py`
- Description: Goal Decomposer — break goals into actionable roadmaps.
- Risk profile: **low**
- I/O behavior: filesystem
- Capabilities: General utility automation
- Dependencies: none

### Functions

- `decompose_goal()`
- `estimate_task_hours()`
- `format_roadmap()`
- `main()`
- `parse_goal_text()`
- `prioritize()`
- `save_goals()`

### Commands

- _No subcommands discovered_

### Example usage

```bash
python -m scripts.goal_decomposer
```
```bash
# Filesystem workflow
python -m scripts.goal_decomposer --help
```

## `health_dashboard`

- Kind: Script
- Path: `scripts/health_dashboard.py`
- Description: System Health Dashboard.
- Risk profile: **high**
- I/O behavior: filesystem, network, process, structured-data
- Capabilities: General utility automation
- Dependencies: none

### Functions

- `check_disk_space()`
- `check_gpu()`
- `check_ollama()`
- `check_openclaw()`
- `format_console()`
- `main()`
- `run_all()`

### Commands

- _No subcommands discovered_

### Example usage

```bash
python -m scripts.health_dashboard
```
```bash
# Network-aware run
python -m scripts.health_dashboard --help
```
```bash
# Filesystem workflow
python -m scripts.health_dashboard --help
```

## `image_format_migrator`

- Kind: Script
- Path: `scripts/image_format_migrator.py`
- Description: Convert and compress image archives to JPEG.
- Risk profile: **medium**
- I/O behavior: filesystem
- Capabilities: General utility automation
- Dependencies: none

### Functions

- `convert_file()`
- `default_output_dir()`
- `destination_for_file()`
- `detect_mime()`
- `heic_to_image()`
- `is_supported_image()`
- `iter_images()`
- `main()`
- `open_image()`
- `parse_args()`
- `print_progress()`
- `process_many()`
- `setup_logger()`

### Commands

- _No subcommands discovered_

### Example usage

```bash
python -m scripts.image_format_migrator
```
```bash
# Filesystem workflow
python -m scripts.image_format_migrator --help
```

## `marketplace_dashboard`

- Kind: Script
- Path: `scripts/marketplace_dashboard.py`
- Description: Marketplace analytics dashboard for Amadey.ru, Wildberries, and Ozon.
- Risk profile: **low**
- I/O behavior: filesystem
- Capabilities: Analytics and reporting
- Dependencies: none

### Functions

- `bar_chart()`
- `channel_comparison()`
- `line()`
- `main()`
- `money()`
- `monthly_dynamics()`
- `parse_args()`
- `render_table()`
- `seed_if_empty()`
- `setup_database()`
- `summary_metrics()`
- `top_products()`

### Commands

- _No subcommands discovered_

### Example usage

```bash
python -m scripts.marketplace_dashboard
```
```bash
# Filesystem workflow
python -m scripts.marketplace_dashboard --help
```

## `media_tool`

- Kind: Script
- Path: `scripts/media_tool.py`
- Description: Utilities for preparing media files before upload.
- Risk profile: **high**
- I/O behavior: filesystem, process
- Capabilities: Cleanup and maintenance, Media processing
- Dependencies: none

### Functions

- `cleanup()`
- `ensure_photo_size_under_limit()`

### Commands

- _No subcommands discovered_

### Example usage

```bash
python -m scripts.media_tool
```
```bash
# Filesystem workflow
python -m scripts.media_tool --help
```

## `memory_analytics`

- Kind: Script
- Path: `scripts/memory_analytics.py`
- Description: Memory health analytics for MEMORY.md files.
- Risk profile: **medium**
- I/O behavior: filesystem, structured-data
- Capabilities: Analytics and reporting
- Dependencies: none

### Functions

- `analyze_memory()`
- `build_age_distribution()`
- `colorize()`
- `extract_dates()`
- `extract_internal_links()`
- `find_duplicate_entries()`
- `find_missing_cross_references()`
- `find_stale_entries()`
- `flush_entry()`
- `human_size()`
- `last_mention()`
- `main()`
- `normalize_anchor()`
- `normalize_text()`
- `parse_args()`
- `parse_date_string()`
- `parse_memory_file()`
- `print_console_summary()`
- `render_markdown_report()`
- `slugify_heading()`
- `to_dict()`
- `to_dict()`

### Commands

- _No subcommands discovered_

### Example usage

```bash
python -m scripts.memory_analytics
```
```bash
# Filesystem workflow
python -m scripts.memory_analytics --help
```

## `memory_cleanup`

- Kind: Script
- Path: `scripts/memory_cleanup.py`
- Description: No module docstring available.
- Risk profile: **low**
- I/O behavior: filesystem, structured-data
- Capabilities: Cleanup and maintenance
- Dependencies: none

### Functions

- `archive_path_for()`
- `backup_file()`
- `backup_path_for()`
- `build_parser()`
- `build_weekly_summary()`
- `colorize()`
- `date_from_stem()`
- `dates_from_text()`
- `discover_memory_files()`
- `ensure_unique_path()`
- `entry_id()`
- `human_bytes()`
- `infer_last_updated()`
- `main()`
- `merge_bodies()`
- `normalize_lines()`
- `parse_date_candidate()`
- `parse_file()`
- `print_report()`
- `rebuild_file()`
- `render()`
- `run_cleanup()`
- `semantic_text()`
- `split_heading_and_body()`
- `split_sections()`

### Commands

- _No subcommands discovered_

### Example usage

```bash
python -m scripts.memory_cleanup
```
```bash
# Filesystem workflow
python -m scripts.memory_cleanup --help
```

## `nightly_pipeline`

- Kind: Script
- Path: `scripts/nightly_pipeline.py`
- Description: Nightly Pipeline - Run during 1:00-8:00 AM
- Risk profile: **high**
- I/O behavior: filesystem, process, structured-data
- Capabilities: Cleanup and maintenance, Data synchronization, Messaging and notifications
- Dependencies: none

### Functions

- `generate_morning_brief()`
- `log()`
- `main()`
- `memory_cleanup()`
- `obsidian_sync()`
- `run_ollama()`
- `send_telegram_summary()`
- `step()`

### Commands

- _No subcommands discovered_

### Example usage

```bash
python -m scripts.nightly_pipeline
```
```bash
# Filesystem workflow
python -m scripts.nightly_pipeline --help
```

## `obsidian_dashboard`

- Kind: Script
- Path: `scripts/obsidian_dashboard.py`
- Description: Obsidian Vault Dashboard
- Risk profile: **medium**
- I/O behavior: filesystem, structured-data
- Capabilities: General utility automation
- Dependencies: none

### Functions

- `api_stats()`
- `bytes_to_human()`
- `collect_markdown_notes()`
- `create_app()`
- `dashboard()`
- `extract_tags()`
- `extract_wiki_links()`
- `find_broken_links()`
- `generate_report()`
- `get_project_stats()`
- `load_stats()`
- `log_event()`
- `main()`
- `normalize_note_name()`
- `now_iso()`
- `parse_args()`
- `parse_wiki_target()`
- `report_html()`
- `report_md()`
- `run_server()`
- `scan_vault()`

### Commands

- _No subcommands discovered_

### Example usage

```bash
python -m scripts.obsidian_dashboard
```
```bash
# Filesystem workflow
python -m scripts.obsidian_dashboard --help
```

## `obsidian_link_checker`

- Kind: Script
- Path: `scripts/obsidian_link_checker.py`
- Description: Scan an Obsidian vault for broken internal links and write a JSON report.
- Risk profile: **medium**
- I/O behavior: filesystem, network, structured-data
- Capabilities: General utility automation
- Dependencies: none

### Functions

- `anchor_exists_in_note()`
- `build()`
- `check_vault()`
- `collect_heading_slugs()`
- `default_case_sensitive()`
- `default_vault_path()`
- `iter_markdown_files()`
- `main()`
- `normalize_key()`
- `parse_md_link_target()`
- `resolve_markdown_href()`
- `resolve_wiki_path()`
- `slugify_heading()`
- `split_wiki_target()`
- `strip_code_fences()`
- `try_asset_bases()`

### Commands

- _No subcommands discovered_

### Example usage

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

## `obsidian_researcher`

- Kind: Script
- Path: `scripts/obsidian_researcher.py`
- Description: Obsidian Researcher.
- Risk profile: **low**
- I/O behavior: filesystem
- Capabilities: General utility automation
- Dependencies: none

### Functions

- `find_notes_by_content()`
- `main()`
- `summarize_topic()`

### Commands

- _No subcommands discovered_

### Example usage

```bash
python -m scripts.obsidian_researcher
```
```bash
# Filesystem workflow
python -m scripts.obsidian_researcher --help
```

## `ollama_batch`

- Kind: Script
- Path: `scripts/ollama_batch.py`
- Description: No module docstring available.
- Risk profile: **medium**
- I/O behavior: filesystem, process, structured-data
- Capabilities: General utility automation
- Dependencies: none

### Functions

- `build_output_payload()`
- `default_runner()`
- `format_duration()`
- `load_prompts()`
- `main()`
- `parse_args()`
- `positive_float()`
- `positive_int()`
- `print_progress()`
- `run_batch()`
- `run_command()`
- `run_prompt()`
- `to_dict()`
- `write_results()`

### Commands

- `run`

### Example usage

```bash
python -m scripts.ollama_batch run
```
```bash
# Filesystem workflow
python -m scripts.ollama_batch run --help
```

## `ollama_batch_download`

- Kind: Script
- Path: `scripts/ollama_batch_download.py`
- Description: Ollama Batch Model Downloader
- Risk profile: **medium**
- I/O behavior: filesystem, process
- Capabilities: Model lifecycle management
- Dependencies: none

### Functions

- `log()`
- `main()`
- `pull()`

### Commands

- _No subcommands discovered_

### Example usage

```bash
python -m scripts.ollama_batch_download
```
```bash
# Filesystem workflow
python -m scripts.ollama_batch_download --help
```

## `ollama_benchmark`

- Kind: Script
- Path: `scripts/ollama_benchmark.py`
- Description: No module docstring available.
- Risk profile: **medium**
- I/O behavior: filesystem, process, structured-data
- Capabilities: Model lifecycle management, Performance benchmarking
- Dependencies: none

### Functions

- `benchmark_log_path()`
- `benchmark_model()`
- `benchmark_prompt()`
- `benchmark_rows()`
- `build_history_rows()`
- `build_run_payload()`
- `compare_results()`
- `ensure_log_dir()`
- `estimate_token_count()`
- `format_number()`
- `latest_two_runs()`
- `list_models()`
- `load_history()`
- `load_runs_from_payload()`
- `main()`
- `normalize_text()`
- `parse_args()`
- `print_compare_report()`
- `print_history_report()`
- `print_run_report()`
- `read_json()`
- `read_ram_usage_mb()`
- `read_vram_usage_mb()`
- `render_markdown_table()`
- `resolve_run_identifier()`
- `run_benchmarks()`
- `run_command()`
- `run_sort_key()`
- `safe_delta()`
- `save_run()`
- `score_prompt_output()`
- `write_json()`

### Commands

- `compare`
- `history`
- `run`

### Example usage

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
python -m scripts.ollama_benchmark compare --help
```

## `ollama_bridge`

- Kind: Script
- Path: `scripts/ollama_bridge.py`
- Description: Ollama Bridge — HTTP proxy between OpenClaw and Ollama API.
- Risk profile: **low**
- I/O behavior: filesystem, network, structured-data
- Capabilities: Model lifecycle management
- Dependencies: none

### Functions

- `do_GET()`
- `do_POST()`
- `handle_chat()`
- `handle_health()`
- `handle_models()`
- `log_message()`
- `main()`
- `route()`
- `run_server()`
- `translate_request()`
- `translate_response()`

### Commands

- _No subcommands discovered_

### Example usage

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

## `ollama_manifest_fix`

- Kind: Script
- Path: `scripts/ollama_manifest_fix.py`
- Description: Repair Ollama on-disk manifests so current servers can list and load local models.
- Risk profile: **high**
- I/O behavior: filesystem, structured-data
- Capabilities: Model lifecycle management
- Dependencies: none

### Functions

- `blob_path_for_digest()`
- `build_arg_parser()`
- `canonicalize_digest()`
- `fix_manifest_obj()`
- `go_style_json_lines()`
- `main()`
- `manifest_paths_under()`
- `parse_manifest_rel_path()`
- `process_manifest_file()`
- `repair_layer()`
- `run_fix()`

### Commands

- _No subcommands discovered_

### Example usage

```bash
python -m scripts.ollama_manifest_fix
```
```bash
# Filesystem workflow
python -m scripts.ollama_manifest_fix --help
```

## `ollama_model_manager`

- Kind: Script
- Path: `scripts/ollama_model_manager.py`
- Description: No module docstring available.
- Risk profile: **high**
- I/O behavior: filesystem, process, structured-data
- Capabilities: Cleanup and maintenance, Model lifecycle management
- Dependencies: none

### Functions

- `cleanup_suggestions()`
- `colorize()`
- `ensure_ollama_available()`
- `format_bytes()`
- `format_duration()`
- `format_rate()`
- `get_disk_space()`
- `list_models()`
- `main()`
- `make_row()`
- `make_separator()`
- `parse_args()`
- `parse_eta_seconds()`
- `parse_human_bytes()`
- `parse_key_value_output()`
- `parse_model_list()`
- `parse_modelfile_output()`
- `parse_parameter_output()`
- `parse_pull_progress()`
- `parse_relative_age_days()`
- `parse_tabular_output()`
- `print_section()`
- `pull_model()`
- `read_stream_chunks()`
- `remove_model()`
- `render_pull_progress()`
- `render_table()`
- `run_ollama_command()`
- `search_models()`
- `show_model()`
- `strip_ansi()`
- `update_pull_metrics()`

### Commands

- `cleanup`
- `list`
- `pull`
- `remove`
- `search`
- `show`

### Example usage

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
python -m scripts.ollama_model_manager cleanup --help
```

## `ollama_monitor`

- Kind: Script
- Path: `scripts/ollama_monitor.py`
- Description: No module docstring available.
- Risk profile: **high**
- I/O behavior: filesystem, network, process, structured-data
- Capabilities: Data synchronization, Monitoring and observability
- Dependencies: none

### Functions

- `append_log_event()`
- `check_health()`
- `check_vram()`
- `daily_log_path()`
- `default_state()`
- `ensure_logs_dir()`
- `finalize_current_uptime()`
- `format_duration()`
- `gather_status()`
- `is_pid_running()`
- `isoformat_timestamp()`
- `latest_json_log()`
- `load_daily_log()`
- `load_json_file()`
- `load_state()`
- `logs_dir()`
- `main()`
- `mark_service_healthy()`
- `parse_args()`
- `parse_timestamp()`
- `print_json()`
- `print_logs()`
- `restart_ollama()`
- `run_monitor_loop()`
- `save_state()`
- `start_ollama_process()`
- `state_path()`
- `stderr_log_path()`
- `stop_managed_ollama()`
- `summarize_state()`
- `sync_stderr_to_json()`
- `utc_now()`
- `write_daily_log()`

### Commands

- `logs`
- `restart`
- `status`

### Example usage

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
python -m scripts.ollama_monitor logs --help
```
```bash
# Filesystem workflow
python -m scripts.ollama_monitor logs --help
```

## `ollama_queue_monitor`

- Kind: Script
- Path: `scripts/ollama_queue_monitor.py`
- Description: Ollama Queue Monitor - runs every hour, checks progress and launches next model.
- Risk profile: **medium**
- I/O behavior: filesystem, network, process, structured-data
- Capabilities: Model lifecycle management, Monitoring and observability, Queue orchestration
- Dependencies: none

### Functions

- `get_ollama_models()`
- `is_model_downloading()`
- `launch_pull()`
- `log()`
- `main()`

### Commands

- _No subcommands discovered_

### Example usage

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

## `optimize_context`

- Kind: Script
- Path: `scripts/optimize_context.py`
- Description: Optimize OpenClaw session context and suggest reductions.
- Risk profile: **low**
- I/O behavior: filesystem, network, structured-data
- Capabilities: Context shaping and prompt preparation
- Dependencies: none

### Functions

- `analyze_session()`
- `build_file_metrics()`
- `color()`
- `content_fingerprint()`
- `detect_memory_bloat()`
- `estimate_tokens()`
- `extract_from_json()`
- `extract_paths_from_text()`
- `main()`
- `normalize_content()`
- `parse_session_log()`
- `parse_text_session_log()`
- `priority_label()`
- `read_text()`
- `relative_display()`
- `render_markdown()`
- `render_summary()`
- `repo_root()`
- `resolve_candidate_path()`
- `summarize_content()`
- `truncate()`
- `update_ref()`
- `write_report()`

### Commands

- _No subcommands discovered_

### Example usage

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

## `photo_archive_report`

- Kind: Script
- Path: `scripts/photo_archive_report.py`
- Description: Photo archive analytics and integrity report generator.
- Risk profile: **low**
- I/O behavior: filesystem, structured-data
- Capabilities: Analytics and reporting
- Dependencies: exif_date_normalizer, photo_deduplication

### Functions

- `build_report()`
- `check_integrity()`
- `main()`
- `parse_args()`
- `render_markdown()`
- `scan_files()`
- `setup_logger()`
- `summarize_sizes()`

### Commands

- _No subcommands discovered_

### Example usage

```bash
python -m scripts.photo_archive_report
```
```bash
# Filesystem workflow
python -m scripts.photo_archive_report --help
```

## `photo_deduplication`

- Kind: Script
- Path: `scripts/photo_deduplication.py`
- Description: Photo archive deduplication with perceptual and average hashes.
- Risk profile: **high**
- I/O behavior: filesystem, structured-data
- Capabilities: General utility automation
- Dependencies: exif_date_normalizer, photo_archive_report

### Functions

- `build_duplicate_groups()`
- `build_report()`
- `hash_image()`
- `iter_image_paths()`
- `main()`
- `parse_args()`
- `process_duplicates()`
- `similarity()`
- `write_csv()`

### Commands

- _No subcommands discovered_

### Example usage

```bash
python -m scripts.photo_deduplication
```
```bash
# Filesystem workflow
python -m scripts.photo_deduplication --help
```

## `proactive_scout`

- Kind: Script
- Path: `scripts/proactive_scout.py`
- Description: No module docstring available.
- Risk profile: **high**
- I/O behavior: filesystem, process, structured-data
- Capabilities: General utility automation
- Dependencies: none

### Functions

- `build_parser()`
- `main()`
- `scout_check()`
- `scout_clear()`
- `scout_predict()`
- `scout_run_background()`
- `scout_status()`
- `to_dict()`

### Commands

- `_worker`
- `check`
- `clear`
- `predict`
- `status`

### Example usage

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
python -m scripts.proactive_scout _worker --help
```

## `process_images`

- Kind: Script
- Path: `scripts/process_images.py`
- Description: Batch image processing utility for OpenClaw orchestration.
- Risk profile: **low**
- I/O behavior: filesystem
- Capabilities: Task orchestration
- Dependencies: none

### Functions

- `iter_images()`
- `main()`
- `process_image()`

### Commands

- _No subcommands discovered_

### Example usage

```bash
python -m scripts.process_images
```
```bash
# Filesystem workflow
python -m scripts.process_images --help
```

## `queue_manager`

- Kind: Script
- Path: `scripts/queue_manager.py`
- Description: Cursor Cloud Agents Batch Queue Manager
- Risk profile: **high**
- I/O behavior: filesystem, network, process, structured-data
- Capabilities: Queue orchestration
- Dependencies: none

### Functions

- `get_active_agents()`
- `launch_agent()`
- `log_queue_status()`
- `main()`
- `merge_pull_request()`
- `poll_agent_once()`
- `poll_agent_with_retry()`
- `run_queue()`

### Commands

- _No subcommands discovered_

### Example usage

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

## `run_task`

- Kind: Script
- Path: `scripts/run_task.py`
- Description: Run OpenClaw task definitions from YAML specs.
- Risk profile: **low**
- I/O behavior: filesystem
- Capabilities: General utility automation
- Dependencies: openclaw_orchestration.task_runner

### Functions

- `main()`
- `parse_args()`

### Commands

- _No subcommands discovered_

### Example usage

```bash
python -m scripts.run_task
```
```bash
# Filesystem workflow
python -m scripts.run_task --help
```

## `sync_obsidian`

- Kind: Script
- Path: `scripts/sync_obsidian.py`
- Description: Bidirectional sync between MEMORY.md and an Obsidian vault.
- Risk profile: **medium**
- I/O behavior: filesystem, structured-data
- Capabilities: Data synchronization
- Dependencies: none

### Functions

- `add_daily_notes_entries()`
- `apply_stale_marker()`
- `build_daily_note_entry()`
- `build_diff()`
- `build_parser()`
- `build_reference_note()`
- `build_wikilink()`
- `colorize()`
- `isoformat_timestamp()`
- `iter_vault_notes()`
- `main()`
- `parse_memory_sections()`
- `print_report()`
- `read_text()`
- `resolve_path()`
- `slug()`
- `slugify()`
- `stem_slug()`
- `sync_memory_and_vault()`
- `write_text()`

### Commands

- _No subcommands discovered_

### Example usage

```bash
python -m scripts.sync_obsidian
```
```bash
# Filesystem workflow
python -m scripts.sync_obsidian --help
```

## `telegram_sender`

- Kind: Script
- Path: `scripts/telegram_sender.py`
- Description: Telegram bot sender utility for OpenClaw.
- Risk profile: **high**
- I/O behavior: filesystem, network, structured-data
- Capabilities: Messaging and notifications
- Dependencies: none

### Functions

- `build_parser()`
- `close()`
- `from_env()`
- `main()`
- `name()`
- `read()`
- `send_document()`
- `send_group()`
- `send_photo()`

### Commands

- `send-document`
- `send-group`
- `send-photo`

### Example usage

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
python -m scripts.telegram_sender send-document --help
```
```bash
# Filesystem workflow
python -m scripts.telegram_sender send-document --help
```

## `tool_discovery`

- Kind: Script
- Path: `scripts/tool_discovery.py`
- Description: No module docstring available.
- Risk profile: **low**
- I/O behavior: filesystem, network, process, structured-data
- Capabilities: General utility automation
- Dependencies: none

### Functions

- `analyze_scripts()`
- `build_examples()`
- `discover_python_paths()`
- `enrich_dependency_graph()`
- `extract_cli_commands()`
- `extract_description()`
- `extract_functions()`
- `extract_imports()`
- `generate_markdown()`
- `import_matches_profile()`
- `infer_capabilities()`
- `infer_io_profile()`
- `infer_risk_level()`
- `main()`
- `parse_args()`
- `score_tool_for_goal()`
- `suggest_tools()`
- `to_dict()`
- `tool_name_for_path()`

### Commands

- `analyze`
- `docs`
- `suggest`

### Example usage

```bash
python -m scripts.tool_discovery analyze
```
```bash
python -m scripts.tool_discovery docs
```
```bash
python -m scripts.tool_discovery suggest
```
```bash
# Network-aware run
python -m scripts.tool_discovery analyze --help
```
```bash
# Filesystem workflow
python -m scripts.tool_discovery analyze --help
```

## `video_thumbnail_generator`

- Kind: Script
- Path: `scripts/video_thumbnail_generator.py`
- Description: Thumbnail generator for furniture videos.
- Risk profile: **medium**
- I/O behavior: filesystem, process
- Capabilities: General utility automation
- Dependencies: none

### Functions

- `apply_branding()`
- `batch_process()`
- `enhance_thumbnail()`
- `extract_frames()`
- `generate_all_sizes()`
- `main()`
- `parse_args()`
- `select_best_frame()`
- `setup_logger()`

### Commands

- _No subcommands discovered_

### Example usage

```bash
python -m scripts.video_thumbnail_generator
```
```bash
# Filesystem workflow
python -m scripts.video_thumbnail_generator --help
```

## `coordination.cross_bot_sync`

- Kind: Source module
- Path: `src/coordination/cross_bot_sync.py`
- Description: Cross-bot coordination helpers for OpenClaw bots.
- Risk profile: **medium**
- I/O behavior: filesystem, structured-data
- Capabilities: Data synchronization
- Dependencies: none

### Functions

- `acquire()`
- `acquire()`
- `atomic_write_json()`
- `atomic_write_text()`
- `build_lock()`
- `build_parser()`
- `claim_task()`
- `handoff_task()`
- `json_dumps()`
- `main()`
- `normalize_memory_key()`
- `normalize_task()`
- `parse_memory_entries()`
- `read_json()`
- `release()`
- `release()`
- `release_task()`
- `render_memory()`
- `run_cli()`
- `sync_memory()`
- `unlock()`
- `utc_now()`
- `write_status()`

### Commands

- `handoff`
- `status`
- `sync`
- `unlock`

### Example usage

```bash
PYTHONPATH=src python -m coordination.cross_bot_sync handoff
```
```bash
PYTHONPATH=src python -m coordination.cross_bot_sync status
```
```bash
PYTHONPATH=src python -m coordination.cross_bot_sync sync
```
```bash
# Filesystem workflow
PYTHONPATH=src python -m coordination.cross_bot_sync handoff --help
```

## `dreams.dream_tracker`

- Kind: Source module
- Path: `src/dreams/dream_tracker.py`
- Description: No module docstring available.
- Risk profile: **low**
- I/O behavior: filesystem, structured-data
- Capabilities: Memory and ideation workflows
- Dependencies: none

### Functions

- `archive_dream()`
- `auto_generate_dreams()`
- `create_dream()`
- `get_dream()`
- `implement_dream()`
- `list_dreams()`
- `main()`
- `research_dream()`
- `status()`

### Commands

- _No subcommands discovered_

### Example usage

```bash
PYTHONPATH=src python -m dreams.dream_tracker
```
```bash
# Filesystem workflow
PYTHONPATH=src python -m dreams.dream_tracker --help
```

## `ideation.idea_pipeline`

- Kind: Source module
- Path: `src/ideation/idea_pipeline.py`
- Description: Idea-to-PR pipeline utilities for OpenClaw.
- Risk profile: **medium**
- I/O behavior: filesystem, process, structured-data
- Capabilities: General utility automation
- Dependencies: none

### Functions

- `main()`
- `run_phase()`

### Commands

- _No subcommands discovered_

### Example usage

```bash
PYTHONPATH=src python -m ideation.idea_pipeline
```
```bash
# Filesystem workflow
PYTHONPATH=src python -m ideation.idea_pipeline --help
```

## `monitoring.session_monitor`

- Kind: Source module
- Path: `src/monitoring/session_monitor.py`
- Description: OpenClaw Session Monitor
- Risk profile: **medium**
- I/O behavior: filesystem, process, structured-data
- Capabilities: Monitoring and observability
- Dependencies: none

### Functions

- `check_sessions()`
- `get_session_sizes()`
- `log_warning()`
- `main()`

### Commands

- _No subcommands discovered_

### Example usage

```bash
PYTHONPATH=src python -m monitoring.session_monitor
```
```bash
# Filesystem workflow
PYTHONPATH=src python -m monitoring.session_monitor --help
```

## `openclaw_orchestration.task_runner`

- Kind: Source module
- Path: `src/openclaw_orchestration/task_runner.py`
- Description: No module docstring available.
- Risk profile: **high**
- I/O behavior: filesystem, network, process, structured-data
- Capabilities: Task orchestration
- Dependencies: none

### Functions

- `build_parser()`
- `from_mapping()`
- `get_task()`
- `list_tasks()`
- `load_tasks()`
- `main()`
- `retries()`
- `run_task()`
- `show_status()`
- `timeout()`
- `to_dict()`
- `with_timeout()`

### Commands

- `list-tasks`
- `run-task`
- `show-status`

### Example usage

```bash
PYTHONPATH=src python -m openclaw_orchestration.task_runner list-tasks
```
```bash
PYTHONPATH=src python -m openclaw_orchestration.task_runner run-task
```
```bash
PYTHONPATH=src python -m openclaw_orchestration.task_runner show-status
```
```bash
# Network-aware run
PYTHONPATH=src python -m openclaw_orchestration.task_runner list-tasks --help
```
```bash
# Filesystem workflow
PYTHONPATH=src python -m openclaw_orchestration.task_runner list-tasks --help
```

## `self_improvement.auto_engine`

- Kind: Source module
- Path: `src/self_improvement/auto_engine.py`
- Description: No module docstring available.
- Risk profile: **high**
- I/O behavior: filesystem, process, structured-data
- Capabilities: General utility automation
- Dependencies: none

### Functions

- `as_dict()`
- `as_dict()`
- `auto_fix()`
- `build_parser()`
- `check_disk_space()`
- `check_gpu_health()`
- `check_memory_usage()`
- `check_ollama_status()`
- `clear_temp_files()`
- `colorize()`
- `format_action()`
- `format_check_result()`
- `generate_weekly_digest()`
- `iter_log_entries()`
- `log_warning()`
- `main()`
- `restart_ollama()`
- `run_health_checks()`
- `status_report()`

### Commands

- _No subcommands discovered_

### Example usage

```bash
PYTHONPATH=src python -m self_improvement.auto_engine
```
```bash
# Filesystem workflow
PYTHONPATH=src python -m self_improvement.auto_engine --help
```

## `skills.proactive_watcher`

- Kind: Source module
- Path: `src/skills/proactive_watcher.py`
- Description: No module docstring available.
- Risk profile: **high**
- I/O behavior: filesystem, process, structured-data
- Capabilities: General utility automation
- Dependencies: none

### Functions

- `add_file()`
- `add_location()`
- `analyze_errors()`
- `analyze_usage()`
- `build_parser()`
- `build_suggestions()`
- `format_error_output()`
- `format_scan_output()`
- `format_suggestion_output()`
- `format_usage_output()`
- `has_docs()`
- `has_tests()`
- `main()`
- `python_files()`
- `render_report()`
- `scan_skills()`
- `write_report()`

### Commands

- _No subcommands discovered_

### Example usage

```bash
PYTHONPATH=src python -m skills.proactive_watcher
```
```bash
# Filesystem workflow
PYTHONPATH=src python -m skills.proactive_watcher --help
```
