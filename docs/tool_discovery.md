# Tool Discovery Report

Auto-generated capability, I/O, safety, and dependency analysis for Python modules in the workspace.

Repository root: `/workspace`

## Summary

- Total tools discovered: **81**
- High-risk tools: **24**
- Medium-risk tools: **32**

## Registry overview

| Name | Path | Risk | I/O | Safety constraints |
| --- | --- | --- | --- | --- |
| `business_dashboard` | `business_dashboard.py` | low | filesystem, database | database_access, filesystem_read |
| `furniture_sales_database.category_detector` | `furniture_sales_database/category_detector.py` | low | in-memory | pure_or_unknown_side_effects |
| `furniture_sales_database.import_sales` | `furniture_sales_database/import_sales.py` | low | filesystem, database | database_access, filesystem_read |
| `furniture_sales_database.query_sales` | `furniture_sales_database/query_sales.py` | low | filesystem, database | database_access |
| `ami_parser` | `scripts/ami_parser.py` | medium | filesystem, network, database | database_access, network_egress |
| `auto_memory_cleanup` | `scripts/auto_memory_cleanup.py` | low | filesystem | pure_or_unknown_side_effects |
| `auto_reflection` | `scripts/auto_reflection.py` | medium | filesystem, network, structured-data | filesystem_read, network_egress |
| `batch_image_optimizer` | `scripts/batch_image_optimizer.py` | high | filesystem, network, structured-data | filesystem_write, network_egress |
| `comfy_auto_quality` | `scripts/comfy_auto_quality.py` | medium | filesystem, network | filesystem_write, network_egress |
| `comfy_video_pipeline` | `scripts/comfy_video_pipeline.py` | high | filesystem, network, process, structured-data | filesystem_destructive, filesystem_write, network_egress, subprocess_execution |
| `context_split` | `scripts/context_split.py` | medium | filesystem, network, structured-data | filesystem_read, network_egress |
| `conversation_extractor` | `scripts/conversation_extractor.py` | low | filesystem, structured-data | filesystem_read |
| `doc_generator` | `scripts/doc_generator.py` | medium | filesystem | pure_or_unknown_side_effects |
| `error_learning` | `scripts/error_learning.py` | medium | filesystem, structured-data | filesystem_read |
| `exif_date_normalizer` | `scripts/exif_date_normalizer.py` | low | filesystem | filesystem_write |
| `face_clustering` | `scripts/face_clustering.py` | medium | filesystem, structured-data | filesystem_read |
| `face_swap_batch` | `scripts/face_swap_batch.py` | low | filesystem | filesystem_write |
| `goal_decomposer` | `scripts/goal_decomposer.py` | low | filesystem | pure_or_unknown_side_effects |
| `health_dashboard` | `scripts/health_dashboard.py` | high | filesystem, network, process, structured-data | network_egress, subprocess_execution |
| `image_format_migrator` | `scripts/image_format_migrator.py` | medium | filesystem | filesystem_destructive, filesystem_read |
| `marketplace_dashboard` | `scripts/marketplace_dashboard.py` | low | filesystem, database | database_access |
| `media_tool` | `scripts/media_tool.py` | high | filesystem, process | filesystem_read, subprocess_execution |
| `memory_analytics` | `scripts/memory_analytics.py` | medium | filesystem, structured-data | filesystem_read |
| `memory_cleanup` | `scripts/memory_cleanup.py` | low | filesystem, structured-data | pure_or_unknown_side_effects |
| `nightly_pipeline` | `scripts/nightly_pipeline.py` | high | filesystem, process, structured-data | filesystem_write, subprocess_execution |
| `obsidian_dashboard` | `scripts/obsidian_dashboard.py` | medium | filesystem, structured-data | filesystem_write |
| `obsidian_link_checker` | `scripts/obsidian_link_checker.py` | medium | filesystem, network, structured-data | filesystem_read, network_egress |
| `obsidian_researcher` | `scripts/obsidian_researcher.py` | low | filesystem | pure_or_unknown_side_effects |
| `ollama_batch` | `scripts/ollama_batch.py` | medium | filesystem, process, structured-data | filesystem_read, subprocess_execution |
| `ollama_batch_download` | `scripts/ollama_batch_download.py` | medium | filesystem, process | filesystem_write, subprocess_execution |
| `ollama_benchmark` | `scripts/ollama_benchmark.py` | medium | filesystem, process, structured-data | filesystem_read, subprocess_execution |
| `ollama_bridge` | `scripts/ollama_bridge.py` | medium | filesystem, network, structured-data | filesystem_read, network_egress |
| `ollama_manifest_fix` | `scripts/ollama_manifest_fix.py` | high | filesystem, structured-data | filesystem_read |
| `ollama_model_manager` | `scripts/ollama_model_manager.py` | high | filesystem, process, structured-data | filesystem_destructive, filesystem_read, subprocess_execution |
| `ollama_monitor` | `scripts/ollama_monitor.py` | high | filesystem, network, process, structured-data | filesystem_write, network_egress, subprocess_execution |
| `ollama_queue_monitor` | `scripts/ollama_queue_monitor.py` | medium | filesystem, network, process, structured-data | filesystem_write, network_egress, subprocess_execution |
| `optimize_context` | `scripts/optimize_context.py` | low | filesystem, structured-data | filesystem_read |
| `photo_archive_report` | `scripts/photo_archive_report.py` | low | filesystem, structured-data | filesystem_read |
| `photo_deduplication` | `scripts/photo_deduplication.py` | high | filesystem, structured-data | filesystem_destructive, filesystem_write |
| `proactive_scout` | `scripts/proactive_scout.py` | high | filesystem, process, structured-data | filesystem_destructive, filesystem_read, subprocess_execution |
| `process_images` | `scripts/process_images.py` | low | filesystem | filesystem_read |
| `queue_manager` | `scripts/queue_manager.py` | high | filesystem, network, process, structured-data | filesystem_read, network_egress, subprocess_execution |
| `run_task` | `scripts/run_task.py` | low | filesystem | filesystem_read |
| `sync_obsidian` | `scripts/sync_obsidian.py` | medium | filesystem, structured-data | filesystem_read |
| `telegram_sender` | `scripts/telegram_sender.py` | high | filesystem, network, structured-data | filesystem_read, network_egress |
| `tool_discovery` | `scripts/tool_discovery.py` | medium | filesystem, process, structured-data | filesystem_read, subprocess_execution |
| `video_thumbnail_generator` | `scripts/video_thumbnail_generator.py` | medium | filesystem, process | filesystem_write, subprocess_execution |
| `src.coordination.cross_bot_sync` | `src/coordination/cross_bot_sync.py` | medium | filesystem, structured-data | filesystem_read |
| `src.dreams.dream_tracker` | `src/dreams/dream_tracker.py` | low | filesystem, structured-data | filesystem_read |
| `src.ideation.idea_pipeline` | `src/ideation/idea_pipeline.py` | medium | filesystem, process, structured-data | filesystem_write, subprocess_execution |
| `src.monitoring.session_monitor` | `src/monitoring/session_monitor.py` | medium | filesystem, process, structured-data | filesystem_read, subprocess_execution |
| `src.openclaw_orchestration.task_runner` | `src/openclaw_orchestration/task_runner.py` | high | filesystem, network, process, structured-data | filesystem_write, network_egress, subprocess_execution |
| `src.self_improvement.auto_engine` | `src/self_improvement/auto_engine.py` | high | filesystem, process, structured-data | filesystem_destructive, filesystem_read, subprocess_execution |
| `src.skills.proactive_watcher` | `src/skills/proactive_watcher.py` | high | filesystem, process, structured-data | filesystem_read, subprocess_execution |
| `tests.test_auto_engine` | `tests/test_auto_engine.py` | high | filesystem, process, structured-data | filesystem_read, subprocess_execution |
| `tests.test_auto_reflection` | `tests/test_auto_reflection.py` | medium | filesystem, network, structured-data | filesystem_read, network_egress |
| `tests.test_context_split` | `tests/test_context_split.py` | low | filesystem, structured-data | filesystem_read |
| `tests.test_cross_bot_sync` | `tests/test_cross_bot_sync.py` | high | filesystem, process, structured-data | filesystem_read, subprocess_execution |
| `tests.test_doc_generator` | `tests/test_doc_generator.py` | medium | filesystem, process | filesystem_read, subprocess_execution |
| `tests.test_dream_tracker` | `tests/test_dream_tracker.py` | high | filesystem, process, structured-data | filesystem_read, subprocess_execution |
| `tests.test_error_learning` | `tests/test_error_learning.py` | medium | filesystem, structured-data | filesystem_read |
| `tests.test_face_clustering` | `tests/test_face_clustering.py` | low | filesystem, structured-data | filesystem_read |
| `tests.test_idea_pipeline` | `tests/test_idea_pipeline.py` | medium | filesystem, structured-data | filesystem_read |
| `tests.test_image_format_migrator` | `tests/test_image_format_migrator.py` | low | filesystem | filesystem_read |
| `tests.test_memory_analytics` | `tests/test_memory_analytics.py` | medium | filesystem, structured-data | filesystem_read |
| `tests.test_memory_cleanup` | `tests/test_memory_cleanup.py` | low | filesystem, structured-data | filesystem_read |
| `tests.test_obsidian_link_checker` | `tests/test_obsidian_link_checker.py` | low | filesystem | filesystem_read |
| `tests.test_ollama_batch` | `tests/test_ollama_batch.py` | high | filesystem, process, structured-data | filesystem_read, subprocess_execution |
| `tests.test_ollama_benchmark` | `tests/test_ollama_benchmark.py` | high | filesystem, process, structured-data | filesystem_read, subprocess_execution |
| `tests.test_ollama_manifest_fix` | `tests/test_ollama_manifest_fix.py` | medium | filesystem, structured-data | filesystem_read |
| `tests.test_ollama_model_manager` | `tests/test_ollama_model_manager.py` | high | filesystem, process | filesystem_destructive, filesystem_read, subprocess_execution |
| `tests.test_ollama_monitor` | `tests/test_ollama_monitor.py` | medium | filesystem, network, structured-data | filesystem_read, network_egress |
| `tests.test_optimize_context` | `tests/test_optimize_context.py` | low | filesystem, structured-data | filesystem_read |
| `tests.test_photo_deduplication` | `tests/test_photo_deduplication.py` | low | filesystem, structured-data | filesystem_read |
| `tests.test_proactive_scout` | `tests/test_proactive_scout.py` | high | filesystem, process, structured-data | filesystem_read, subprocess_execution |
| `tests.test_proactive_watcher` | `tests/test_proactive_watcher.py` | high | filesystem, process | filesystem_read, subprocess_execution |
| `tests.test_sync_obsidian` | `tests/test_sync_obsidian.py` | medium | filesystem, structured-data | filesystem_read |
| `tests.test_task_runner` | `tests/test_task_runner.py` | medium | filesystem, network, structured-data | filesystem_read, network_egress |
| `tests.test_telegram_sender` | `tests/test_telegram_sender.py` | high | filesystem, network, structured-data | filesystem_read, network_egress |
| `tests.test_tool_discovery` | `tests/test_tool_discovery.py` | low | filesystem, structured-data | filesystem_read |
| `yandex_metrika` | `yandex_metrika.py` | medium | filesystem, network, structured-data, database | database_access, filesystem_read, network_egress |

---

## `business_dashboard`

- **tool_id**: `business_dashboard.py`
- **Path**: `business_dashboard.py`
- **Description**: Unified business dashboard for furniture sales data.
- **Risk level**: **low**
- **Capabilities**: Analytics and reporting, Filesystem-oriented API surface
- **I/O profile**: filesystem, database
- **Safety constraints**: database_access, filesystem_read
- **Decorator signals**: —
- **Dependencies**: ami_parser, auto_memory_cleanup, auto_reflection, batch_image_optimizer, comfy_auto_quality, comfy_video_pipeline, context_split, conversation_extractor, doc_generator, error_learning, exif_date_normalizer, face_clustering, face_swap_batch, furniture_sales_database.category_detector, furniture_sales_database.import_sales, furniture_sales_database.query_sales, goal_decomposer, health_dashboard, image_format_migrator, marketplace_dashboard, media_tool, memory_analytics, memory_cleanup, nightly_pipeline, obsidian_dashboard, obsidian_link_checker, obsidian_researcher, ollama_batch, ollama_benchmark, ollama_bridge, ollama_manifest_fix, ollama_model_manager, ollama_monitor, optimize_context, photo_archive_report, photo_deduplication, proactive_scout, process_images, queue_manager, run_task, src.coordination.cross_bot_sync, src.dreams.dream_tracker, src.ideation.idea_pipeline, src.monitoring.session_monitor, src.openclaw_orchestration.task_runner, src.self_improvement.auto_engine, src.skills.proactive_watcher, sync_obsidian, telegram_sender, tests.test_auto_engine, tests.test_auto_reflection, tests.test_context_split, tests.test_error_learning, tests.test_face_clustering, tests.test_idea_pipeline, tests.test_image_format_migrator, tests.test_memory_analytics, tests.test_memory_cleanup, tests.test_ollama_batch, tests.test_ollama_benchmark, tests.test_ollama_manifest_fix, tests.test_ollama_model_manager, tests.test_ollama_monitor, tests.test_proactive_scout, tests.test_proactive_watcher, tests.test_sync_obsidian, tests.test_tool_discovery, tool_discovery, video_thumbnail_generator, yandex_metrika

### Public functions (signatures & docstrings)

| Function | Parameters | Returns | Decorators | Docstring |
| --- | --- | --- | --- | --- |
| `parse_args` |  | `argparse.Namespace` | — | — |
| `table_columns` | conn: sqlite3.Connection, table_name: str | `set[str]` | — | — |
| `has_table` | conn: sqlite3.Connection, table_name: str | `bool` | — | — |
| `choose_first_available` | columns: set[str], names: Iterable[str] | `str | None` | — | — |
| `load_sales_from_db` | db_path: Path | `list[SaleRecord]` | — | — |
| `parse_month` | raw_date: str | `str` | — | — |
| `bar` | value: float, max_value: float, width: int | `str` | — | — |
| `format_money` | value: float | `str` | — | — |
| `print_table_with_chart` | title: str, rows: list[tuple[str, float]], width: int, value_label: str | `None` | — | — |
| `main` |  | `None` | — | — |

### CLI subcommands

- _No argparse subcommands discovered_

### Example usage

```bash
python -m business_dashboard
```
```bash
# Filesystem workflow
python -m business_dashboard --help
```

## `furniture_sales_database.category_detector`

- **tool_id**: `furniture_sales_database/category_detector.py`
- **Path**: `furniture_sales_database/category_detector.py`
- **Description**: Category auto-detection for furniture product names.
- **Risk level**: **low**
- **Capabilities**: General utility automation
- **I/O profile**: in-memory
- **Safety constraints**: pure_or_unknown_side_effects
- **Decorator signals**: —
- **Dependencies**: ami_parser, auto_reflection, batch_image_optimizer, business_dashboard, comfy_auto_quality, comfy_video_pipeline, context_split, conversation_extractor, doc_generator, exif_date_normalizer, face_clustering, face_swap_batch, furniture_sales_database.import_sales, furniture_sales_database.query_sales, image_format_migrator, marketplace_dashboard, media_tool, memory_analytics, memory_cleanup, obsidian_dashboard, obsidian_link_checker, ollama_batch, ollama_benchmark, ollama_manifest_fix, photo_archive_report, proactive_scout, process_images, src.coordination.cross_bot_sync, src.openclaw_orchestration.task_runner, src.self_improvement.auto_engine, sync_obsidian, telegram_sender, tool_discovery, video_thumbnail_generator, yandex_metrika

### Public functions (signatures & docstrings)

| Function | Parameters | Returns | Decorators | Docstring |
| --- | --- | --- | --- | --- |
| `detect_category` | product_name: str | `str` | — | Detect a furniture category from a product name. |

### CLI subcommands

- _No argparse subcommands discovered_

### Example usage

```bash
python -m furniture_sales_database.category_detector
```

## `furniture_sales_database.import_sales`

- **tool_id**: `furniture_sales_database/import_sales.py`
- **Path**: `furniture_sales_database/import_sales.py`
- **Description**: Import furniture sales data from XLS into SQLite.
- **Risk level**: **low**
- **Capabilities**: Filesystem-oriented API surface
- **I/O profile**: filesystem, database
- **Safety constraints**: database_access, filesystem_read
- **Decorator signals**: —
- **Dependencies**: ami_parser, auto_memory_cleanup, auto_reflection, batch_image_optimizer, business_dashboard, comfy_auto_quality, comfy_video_pipeline, context_split, conversation_extractor, doc_generator, error_learning, exif_date_normalizer, face_clustering, face_swap_batch, furniture_sales_database.category_detector, furniture_sales_database.query_sales, goal_decomposer, health_dashboard, image_format_migrator, marketplace_dashboard, media_tool, memory_analytics, memory_cleanup, nightly_pipeline, obsidian_dashboard, obsidian_link_checker, obsidian_researcher, ollama_batch, ollama_benchmark, ollama_bridge, ollama_manifest_fix, ollama_model_manager, ollama_monitor, optimize_context, photo_archive_report, photo_deduplication, proactive_scout, process_images, queue_manager, run_task, src.coordination.cross_bot_sync, src.dreams.dream_tracker, src.ideation.idea_pipeline, src.monitoring.session_monitor, src.openclaw_orchestration.task_runner, src.self_improvement.auto_engine, src.skills.proactive_watcher, sync_obsidian, telegram_sender, tests.test_auto_engine, tests.test_auto_reflection, tests.test_context_split, tests.test_error_learning, tests.test_face_clustering, tests.test_idea_pipeline, tests.test_image_format_migrator, tests.test_memory_analytics, tests.test_memory_cleanup, tests.test_ollama_batch, tests.test_ollama_benchmark, tests.test_ollama_manifest_fix, tests.test_ollama_model_manager, tests.test_ollama_monitor, tests.test_proactive_scout, tests.test_proactive_watcher, tests.test_sync_obsidian, tests.test_tool_discovery, tool_discovery, video_thumbnail_generator, yandex_metrika

### Public functions (signatures & docstrings)

| Function | Parameters | Returns | Decorators | Docstring |
| --- | --- | --- | --- | --- |
| `normalize_header` | value: object | `str` | — | — |
| `parse_date` | cell_value: object, datemode: int | `str` | — | — |
| `parse_int` | value: object, field: str | `int` | — | — |
| `parse_float` | value: object, field: str | `float` | — | — |
| `resolve_headers` | header_row: Iterable[object] | `Dict[str, int]` | — | — |
| `get_or_create` | cur: sqlite3.Cursor, table: str, name: str | `int` | — | — |
| `get_or_create_product` | cur: sqlite3.Cursor | `int` | — | — |
| `initialize_schema` | conn: sqlite3.Connection, schema_path: Path | `None` | — | — |
| `import_xls` | db_path: Path, xls_path: Path, schema_path: Path | `None` | — | — |
| `main` |  | `None` | — | — |

### CLI subcommands

- _No argparse subcommands discovered_

### Example usage

```bash
python -m furniture_sales_database.import_sales
```
```bash
# Filesystem workflow
python -m furniture_sales_database.import_sales --help
```

## `furniture_sales_database.query_sales`

- **tool_id**: `furniture_sales_database/query_sales.py`
- **Path**: `furniture_sales_database/query_sales.py`
- **Description**: Example analytics queries for furniture sales database.
- **Risk level**: **low**
- **Capabilities**: Analytics and reporting
- **I/O profile**: filesystem, database
- **Safety constraints**: database_access
- **Decorator signals**: —
- **Dependencies**: ami_parser, auto_memory_cleanup, auto_reflection, batch_image_optimizer, business_dashboard, comfy_auto_quality, comfy_video_pipeline, context_split, conversation_extractor, doc_generator, error_learning, exif_date_normalizer, face_clustering, face_swap_batch, furniture_sales_database.category_detector, furniture_sales_database.import_sales, goal_decomposer, image_format_migrator, marketplace_dashboard, media_tool, memory_analytics, memory_cleanup, obsidian_dashboard, obsidian_link_checker, ollama_batch, ollama_benchmark, ollama_bridge, ollama_manifest_fix, ollama_model_manager, ollama_monitor, optimize_context, photo_archive_report, photo_deduplication, proactive_scout, process_images, queue_manager, run_task, src.coordination.cross_bot_sync, src.openclaw_orchestration.task_runner, src.self_improvement.auto_engine, src.skills.proactive_watcher, sync_obsidian, telegram_sender, tests.test_context_split, tool_discovery, video_thumbnail_generator, yandex_metrika

### Public functions (signatures & docstrings)

| Function | Parameters | Returns | Decorators | Docstring |
| --- | --- | --- | --- | --- |
| `print_rows` | title: str, columns: Sequence[str], rows: Iterable[sqlite3.Row] | `None` | — | — |
| `main` |  | `None` | — | — |

### CLI subcommands

- _No argparse subcommands discovered_

### Example usage

```bash
python -m furniture_sales_database.query_sales
```
```bash
# Filesystem workflow
python -m furniture_sales_database.query_sales --help
```

## `ami_parser`

- **tool_id**: `scripts/ami_parser.py`
- **Path**: `scripts/ami_parser.py`
- **Description**: AMI.by price intelligence tracker for furniture categories.
- **Risk level**: **medium**
- **Capabilities**: General utility automation
- **I/O profile**: filesystem, network, database
- **Safety constraints**: database_access, network_egress
- **Decorator signals**: —
- **Dependencies**: auto_memory_cleanup, auto_reflection, batch_image_optimizer, business_dashboard, comfy_auto_quality, comfy_video_pipeline, context_split, conversation_extractor, doc_generator, error_learning, exif_date_normalizer, face_clustering, face_swap_batch, furniture_sales_database.category_detector, furniture_sales_database.import_sales, furniture_sales_database.query_sales, goal_decomposer, health_dashboard, image_format_migrator, marketplace_dashboard, media_tool, memory_analytics, memory_cleanup, obsidian_dashboard, obsidian_link_checker, obsidian_researcher, ollama_batch, ollama_benchmark, ollama_bridge, ollama_manifest_fix, ollama_model_manager, ollama_monitor, optimize_context, photo_archive_report, photo_deduplication, proactive_scout, process_images, queue_manager, run_task, src.coordination.cross_bot_sync, src.dreams.dream_tracker, src.ideation.idea_pipeline, src.monitoring.session_monitor, src.openclaw_orchestration.task_runner, src.self_improvement.auto_engine, src.skills.proactive_watcher, sync_obsidian, telegram_sender, tests.test_context_split, tests.test_ollama_benchmark, tests.test_ollama_model_manager, tests.test_ollama_monitor, tests.test_proactive_scout, tool_discovery, video_thumbnail_generator, yandex_metrika

### Public functions (signatures & docstrings)

| Function | Parameters | Returns | Decorators | Docstring |
| --- | --- | --- | --- | --- |
| `parse_category` | category_name: str | `list[Product]` | — | — |
| `get_all_products` |  | `list[Product]` | — | — |
| `get_price_change` | old_price: float | None, new_price: float | None | `str` | — | — |
| `check_updates` | products: Iterable[Product] | `dict[str, list[dict[str, str]]]` | — | — |
| `print_report` | report: dict[str, list[dict[str, str]]] | `None` | `staticmethod` | — |
| `main` |  | `None` | — | — |

### CLI subcommands

- _No argparse subcommands discovered_

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

- **tool_id**: `scripts/auto_memory_cleanup.py`
- **Path**: `scripts/auto_memory_cleanup.py`
- **Description**: Auto Memory Cleanup — clean and maintain MEMORY.md.
- **Risk level**: **low**
- **Capabilities**: Cleanup and maintenance, Memory and ideation workflows
- **I/O profile**: filesystem
- **Safety constraints**: pure_or_unknown_side_effects
- **Decorator signals**: —
- **Dependencies**: ami_parser, auto_reflection, batch_image_optimizer, business_dashboard, comfy_auto_quality, comfy_video_pipeline, context_split, conversation_extractor, doc_generator, error_learning, exif_date_normalizer, face_clustering, face_swap_batch, furniture_sales_database.import_sales, furniture_sales_database.query_sales, goal_decomposer, health_dashboard, image_format_migrator, marketplace_dashboard, media_tool, memory_analytics, memory_cleanup, nightly_pipeline, obsidian_dashboard, obsidian_link_checker, obsidian_researcher, ollama_batch, ollama_batch_download, ollama_benchmark, ollama_bridge, ollama_manifest_fix, ollama_model_manager, ollama_monitor, ollama_queue_monitor, optimize_context, photo_archive_report, photo_deduplication, proactive_scout, process_images, queue_manager, run_task, src.coordination.cross_bot_sync, src.dreams.dream_tracker, src.ideation.idea_pipeline, src.monitoring.session_monitor, src.openclaw_orchestration.task_runner, src.self_improvement.auto_engine, src.skills.proactive_watcher, sync_obsidian, telegram_sender, tests.test_auto_engine, tests.test_auto_reflection, tests.test_context_split, tests.test_cross_bot_sync, tests.test_doc_generator, tests.test_dream_tracker, tests.test_error_learning, tests.test_face_clustering, tests.test_idea_pipeline, tests.test_memory_analytics, tests.test_memory_cleanup, tests.test_obsidian_link_checker, tests.test_ollama_batch, tests.test_ollama_benchmark, tests.test_ollama_manifest_fix, tests.test_ollama_model_manager, tests.test_ollama_monitor, tests.test_proactive_scout, tests.test_proactive_watcher, tests.test_sync_obsidian, tests.test_tool_discovery, tool_discovery, video_thumbnail_generator, yandex_metrika

### Public functions (signatures & docstrings)

| Function | Parameters | Returns | Decorators | Docstring |
| --- | --- | --- | --- | --- |
| `load` |  | `bool` | — | Load MEMORY.md content. |
| `find_sections` |  | `list[tuple[int, str]]` | — | Find all ## sections with line numbers. |
| `analyze` |  | `dict` | — | Analyze MEMORY.md and return statistics. |
| `print_analysis` |  | `—` | — | Print memory analysis. |
| `clean_old_daily_notes` | cutoff_days: int | `int` | — | Remove daily notes older than cutoff_days. |
| `merge_duplicate_sections` |  | `int` | — | Merge sections with similar names. |
| `save` |  | `bool` | — | Save cleaned content. |
| `run_cleanup` | cutoff_days: int, merge_dups: bool | `dict` | — | Run full cleanup and return stats. |
| `main` |  | `—` | — | — |

### CLI subcommands

- _No argparse subcommands discovered_

### Example usage

```bash
python -m scripts.auto_memory_cleanup
```
```bash
# Filesystem workflow
python -m scripts.auto_memory_cleanup --help
```

## `auto_reflection`

- **tool_id**: `scripts/auto_reflection.py`
- **Path**: `scripts/auto_reflection.py`
- **Description**: Cron-friendly self-reflection over recent agent-style logs and session artifacts.
- **Risk level**: **medium**
- **Capabilities**: Deduplication and similarity, Filesystem-oriented API surface, General utility automation, Messaging and notifications, Monitoring and observability, Network-oriented API surface
- **I/O profile**: filesystem, network, structured-data
- **Safety constraints**: filesystem_read, network_egress
- **Decorator signals**: —
- **Dependencies**: ami_parser, auto_memory_cleanup, batch_image_optimizer, business_dashboard, comfy_auto_quality, comfy_video_pipeline, context_split, conversation_extractor, doc_generator, error_learning, exif_date_normalizer, face_clustering, face_swap_batch, furniture_sales_database.category_detector, furniture_sales_database.import_sales, furniture_sales_database.query_sales, goal_decomposer, health_dashboard, image_format_migrator, marketplace_dashboard, media_tool, memory_analytics, memory_cleanup, nightly_pipeline, obsidian_dashboard, obsidian_link_checker, obsidian_researcher, ollama_batch, ollama_batch_download, ollama_benchmark, ollama_bridge, ollama_manifest_fix, ollama_model_manager, ollama_monitor, ollama_queue_monitor, optimize_context, photo_archive_report, photo_deduplication, proactive_scout, process_images, queue_manager, run_task, src.coordination.cross_bot_sync, src.dreams.dream_tracker, src.ideation.idea_pipeline, src.monitoring.session_monitor, src.openclaw_orchestration.task_runner, src.self_improvement.auto_engine, src.skills.proactive_watcher, sync_obsidian, telegram_sender, tests.test_auto_engine, tests.test_auto_reflection, tests.test_context_split, tests.test_cross_bot_sync, tests.test_doc_generator, tests.test_dream_tracker, tests.test_error_learning, tests.test_face_clustering, tests.test_idea_pipeline, tests.test_image_format_migrator, tests.test_memory_analytics, tests.test_memory_cleanup, tests.test_obsidian_link_checker, tests.test_ollama_batch, tests.test_ollama_benchmark, tests.test_ollama_manifest_fix, tests.test_ollama_model_manager, tests.test_ollama_monitor, tests.test_optimize_context, tests.test_photo_deduplication, tests.test_proactive_scout, tests.test_proactive_watcher, tests.test_sync_obsidian, tests.test_task_runner, tests.test_telegram_sender, tests.test_tool_discovery, tool_discovery, video_thumbnail_generator, yandex_metrika

### Public functions (signatures & docstrings)

| Function | Parameters | Returns | Decorators | Docstring |
| --- | --- | --- | --- | --- |
| `utc_now` |  | `datetime` | — | — |
| `load_state` | root: Path | `dict[str, Any]` | — | — |
| `save_state` | root: Path, data: dict[str, Any] | `None` | — | — |
| `iter_session_files` | root: Path, globs: Sequence[str], cutoff: datetime | `list[Path]` | — | — |
| `normalize_insight_text` | line: str | `str` | — | — |
| `insight_fingerprint` | text: str | `str` | — | — |
| `extract_insights_from_text` | path: Path, root: Path, raw: str | `Iterator[Insight]` | — | — |
| `extract_insights_from_json` | path: Path, root: Path, raw: str | `Iterator[Insight]` | — | — |
| `walk` | obj: Any | `None` | — | — |
| `read_and_extract` | path: Path, root: Path | `list[Insight]` | — | — |
| `dedupe_insights` | insights: Iterable[Insight] | `list[Insight]` | — | — |
| `build_summary_markdown` | run_at: datetime, files_scanned: int, insights: Sequence[Insight], top_sessions: Sequence[str] | `str` | — | — |
| … | _11 more_ | | | |

### CLI subcommands

- _No argparse subcommands discovered_

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

- **tool_id**: `scripts/batch_image_optimizer.py`
- **Path**: `scripts/batch_image_optimizer.py`
- **Description**: Bulk image optimizer with optional MiniMax enhancement.
- **Risk level**: **high**
- **Capabilities**: Filesystem-oriented API surface, Media processing, Network-oriented API surface, Task orchestration
- **I/O profile**: filesystem, network, structured-data
- **Safety constraints**: filesystem_write, network_egress
- **Decorator signals**: —
- **Dependencies**: ami_parser, auto_memory_cleanup, auto_reflection, business_dashboard, comfy_auto_quality, comfy_video_pipeline, context_split, conversation_extractor, doc_generator, error_learning, exif_date_normalizer, face_clustering, face_swap_batch, furniture_sales_database.category_detector, furniture_sales_database.import_sales, furniture_sales_database.query_sales, goal_decomposer, health_dashboard, image_format_migrator, marketplace_dashboard, media_tool, memory_analytics, memory_cleanup, nightly_pipeline, obsidian_dashboard, obsidian_link_checker, obsidian_researcher, ollama_batch, ollama_batch_download, ollama_benchmark, ollama_bridge, ollama_manifest_fix, ollama_model_manager, ollama_monitor, ollama_queue_monitor, optimize_context, photo_archive_report, photo_deduplication, proactive_scout, process_images, queue_manager, run_task, src.coordination.cross_bot_sync, src.dreams.dream_tracker, src.ideation.idea_pipeline, src.monitoring.session_monitor, src.openclaw_orchestration.task_runner, src.self_improvement.auto_engine, src.skills.proactive_watcher, sync_obsidian, telegram_sender, tests.test_auto_engine, tests.test_auto_reflection, tests.test_context_split, tests.test_cross_bot_sync, tests.test_doc_generator, tests.test_dream_tracker, tests.test_error_learning, tests.test_face_clustering, tests.test_idea_pipeline, tests.test_image_format_migrator, tests.test_memory_analytics, tests.test_memory_cleanup, tests.test_obsidian_link_checker, tests.test_ollama_batch, tests.test_ollama_benchmark, tests.test_ollama_manifest_fix, tests.test_ollama_model_manager, tests.test_ollama_monitor, tests.test_optimize_context, tests.test_photo_deduplication, tests.test_proactive_scout, tests.test_proactive_watcher, tests.test_sync_obsidian, tests.test_task_runner, tests.test_telegram_sender, tests.test_tool_discovery, tool_discovery, video_thumbnail_generator, yandex_metrika

### Public functions (signatures & docstrings)

| Function | Parameters | Returns | Decorators | Docstring |
| --- | --- | --- | --- | --- |
| `load_dotenv` |  | `bool` | — | — |
| `parse_size` | value: str | `tuple[int, int]` | — | — |
| `parse_operations` | raw_value: str | `list[str]` | — | — |
| `setup_logging` | verbose: bool | `logging.Logger` | — | — |
| `append_markdown_log` | lines: Iterable[str] | `None` | — | — |
| `minimax_enhance_image` | image: Image.Image, api_key: str, base_url: str, model: str, timeout: int | `Image.Image | None` | — | — |
| `apply_resize` | image: Image.Image, target_size: tuple[int, int] | `Image.Image` | — | — |
| `apply_watermark` | image: Image.Image, text: str, opacity: int, margin: int | `Image.Image` | — | — |
| `single_image_process` | image_path: Path, operations: list[str], target_size: tuple[int, int], watermark_text: str, minimax_api_key: str | None, minimax_base_url: str, … | `tuple[Image.Image, list[str]]` | — | — |
| `process_directory` | input_dir: Path | str, output_dir: Path | str, operations: list[str] | None, target_size: tuple[int, int], watermark_text: str, minimax_api_key: str | None, … | `dict[str, int]` | — | — |
| `run_self_test` | logger: logging.Logger | `int` | — | — |
| `parse_args` | argv: list[str] | None | `argparse.Namespace` | — | — |
| … | _1 more_ | | | |

### CLI subcommands

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
python -m scripts.batch_image_optimizer --help
```
```bash
# Filesystem workflow
python -m scripts.batch_image_optimizer --help
```

## `comfy_auto_quality`

- **tool_id**: `scripts/comfy_auto_quality.py`
- **Path**: `scripts/comfy_auto_quality.py`
- **Description**: Universal auto-quality processor for ComfyUI images.
- **Risk level**: **medium**
- **Capabilities**: Media processing, Queue orchestration
- **I/O profile**: filesystem, network
- **Safety constraints**: filesystem_write, network_egress
- **Decorator signals**: —
- **Dependencies**: ami_parser, auto_memory_cleanup, auto_reflection, batch_image_optimizer, business_dashboard, comfy_video_pipeline, context_split, conversation_extractor, doc_generator, error_learning, exif_date_normalizer, face_clustering, face_swap_batch, furniture_sales_database.category_detector, furniture_sales_database.import_sales, furniture_sales_database.query_sales, goal_decomposer, health_dashboard, image_format_migrator, marketplace_dashboard, media_tool, memory_analytics, memory_cleanup, nightly_pipeline, obsidian_dashboard, obsidian_link_checker, obsidian_researcher, ollama_batch, ollama_batch_download, ollama_benchmark, ollama_bridge, ollama_manifest_fix, ollama_model_manager, ollama_monitor, ollama_queue_monitor, optimize_context, photo_archive_report, photo_deduplication, proactive_scout, process_images, queue_manager, run_task, src.coordination.cross_bot_sync, src.dreams.dream_tracker, src.ideation.idea_pipeline, src.monitoring.session_monitor, src.openclaw_orchestration.task_runner, src.self_improvement.auto_engine, src.skills.proactive_watcher, sync_obsidian, telegram_sender, tests.test_auto_engine, tests.test_auto_reflection, tests.test_context_split, tests.test_cross_bot_sync, tests.test_error_learning, tests.test_face_clustering, tests.test_idea_pipeline, tests.test_image_format_migrator, tests.test_memory_analytics, tests.test_memory_cleanup, tests.test_ollama_batch, tests.test_ollama_benchmark, tests.test_ollama_manifest_fix, tests.test_ollama_model_manager, tests.test_ollama_monitor, tests.test_photo_deduplication, tests.test_proactive_scout, tests.test_proactive_watcher, tests.test_sync_obsidian, tests.test_task_runner, tests.test_telegram_sender, tests.test_tool_discovery, tool_discovery, video_thumbnail_generator, yandex_metrika

### Public functions (signatures & docstrings)

| Function | Parameters | Returns | Decorators | Docstring |
| --- | --- | --- | --- | --- |
| `log` | title: str, message: str | `None` | — | — |
| `ping` |  | `None` | — | — |
| `object_info` |  | `Dict[str, Any]` | — | — |
| `upload_image_bytes` | image_bytes: bytes, filename: str | `Dict[str, Any]` | — | — |
| `queue_prompt` | workflow: Dict[str, Any] | `str` | — | — |
| `wait_result` | prompt_id: str, timeout_sec: int | `Dict[str, Any]` | — | — |
| `download_image` | image_ref: Dict[str, str] | `bytes` | — | — |
| `process` | pil_image: Optional[Image.Image] | `Path` | — | — |
| `parse_args` |  | `Config` | — | — |
| `run_self_test` | cfg: Config, logger: MarkdownLogger | `int` | — | — |
| `process_pil_image` | pil_image: Image.Image, output_dir: str, gpu: str | `Path` | — | Programmatic API entry point for PIL inputs. |
| `main` |  | `int` | — | — |

### CLI subcommands

- _No argparse subcommands discovered_

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

- **tool_id**: `scripts/comfy_video_pipeline.py`
- **Path**: `scripts/comfy_video_pipeline.py`
- **Description**: Универсальный пайплайн генерации видео мебели через ComfyUI API:
- **Risk level**: **high**
- **Capabilities**: Filesystem-oriented API surface, Media processing, Network-oriented API surface, Queue orchestration, Task orchestration
- **I/O profile**: filesystem, network, process, structured-data
- **Safety constraints**: filesystem_destructive, filesystem_write, network_egress, subprocess_execution
- **Decorator signals**: —
- **Dependencies**: ami_parser, auto_memory_cleanup, auto_reflection, batch_image_optimizer, business_dashboard, comfy_auto_quality, context_split, conversation_extractor, doc_generator, error_learning, exif_date_normalizer, face_clustering, face_swap_batch, furniture_sales_database.category_detector, furniture_sales_database.import_sales, furniture_sales_database.query_sales, goal_decomposer, health_dashboard, image_format_migrator, marketplace_dashboard, media_tool, memory_analytics, memory_cleanup, nightly_pipeline, obsidian_dashboard, obsidian_link_checker, obsidian_researcher, ollama_batch, ollama_batch_download, ollama_benchmark, ollama_bridge, ollama_manifest_fix, ollama_model_manager, ollama_monitor, ollama_queue_monitor, optimize_context, photo_archive_report, photo_deduplication, proactive_scout, process_images, queue_manager, run_task, src.coordination.cross_bot_sync, src.dreams.dream_tracker, src.ideation.idea_pipeline, src.monitoring.session_monitor, src.openclaw_orchestration.task_runner, src.self_improvement.auto_engine, src.skills.proactive_watcher, sync_obsidian, telegram_sender, tests.test_auto_engine, tests.test_auto_reflection, tests.test_context_split, tests.test_cross_bot_sync, tests.test_doc_generator, tests.test_dream_tracker, tests.test_error_learning, tests.test_face_clustering, tests.test_idea_pipeline, tests.test_image_format_migrator, tests.test_memory_analytics, tests.test_memory_cleanup, tests.test_obsidian_link_checker, tests.test_ollama_batch, tests.test_ollama_benchmark, tests.test_ollama_manifest_fix, tests.test_ollama_model_manager, tests.test_ollama_monitor, tests.test_optimize_context, tests.test_photo_deduplication, tests.test_proactive_scout, tests.test_proactive_watcher, tests.test_sync_obsidian, tests.test_task_runner, tests.test_telegram_sender, tests.test_tool_discovery, tool_discovery, video_thumbnail_generator, yandex_metrika

### Public functions (signatures & docstrings)

| Function | Parameters | Returns | Decorators | Docstring |
| --- | --- | --- | --- | --- |
| `log` | title: str, message: str | `None` | — | — |
| `ping` |  | `None` | — | — |
| `get_object_info` |  | `Dict[str, Any]` | — | — |
| `upload_image` | image_path: Path, subfolder: str | `Dict[str, Any]` | — | — |
| `queue_prompt` | prompt: Dict[str, Any] | `str` | — | — |
| `wait_prompt` | prompt_id: str, timeout_sec: int | `Dict[str, Any]` | — | — |
| `download_view_image` | filename: str, subfolder: str, folder_type: str, out_path: Path | `None` | — | — |
| `retry` | func | `—` | — | Декоратор retry для локальных операций (не HTTP). |
| `wrapper` |  | `—` | — | — |
| `run` |  | `None` | — | — |
| `parse_args` |  | `PipelineConfig` | — | — |
| `main` |  | `int` | — | — |

### CLI subcommands

- _No argparse subcommands discovered_

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

- **tool_id**: `scripts/context_split.py`
- **Path**: `scripts/context_split.py`
- **Description**: No module docstring available.
- **Risk level**: **medium**
- **Capabilities**: Context shaping and prompt preparation, General utility automation, Network-oriented API surface
- **I/O profile**: filesystem, network, structured-data
- **Safety constraints**: filesystem_read, network_egress
- **Decorator signals**: —
- **Dependencies**: ami_parser, auto_memory_cleanup, auto_reflection, batch_image_optimizer, business_dashboard, comfy_auto_quality, comfy_video_pipeline, conversation_extractor, doc_generator, error_learning, exif_date_normalizer, face_clustering, face_swap_batch, furniture_sales_database.category_detector, furniture_sales_database.import_sales, furniture_sales_database.query_sales, goal_decomposer, health_dashboard, image_format_migrator, marketplace_dashboard, media_tool, memory_analytics, memory_cleanup, nightly_pipeline, obsidian_dashboard, obsidian_link_checker, obsidian_researcher, ollama_batch, ollama_batch_download, ollama_benchmark, ollama_bridge, ollama_manifest_fix, ollama_model_manager, ollama_monitor, ollama_queue_monitor, optimize_context, photo_archive_report, photo_deduplication, proactive_scout, process_images, queue_manager, run_task, src.coordination.cross_bot_sync, src.dreams.dream_tracker, src.ideation.idea_pipeline, src.monitoring.session_monitor, src.openclaw_orchestration.task_runner, src.self_improvement.auto_engine, src.skills.proactive_watcher, sync_obsidian, telegram_sender, tests.test_auto_engine, tests.test_auto_reflection, tests.test_context_split, tests.test_cross_bot_sync, tests.test_doc_generator, tests.test_dream_tracker, tests.test_error_learning, tests.test_face_clustering, tests.test_idea_pipeline, tests.test_image_format_migrator, tests.test_memory_analytics, tests.test_memory_cleanup, tests.test_obsidian_link_checker, tests.test_ollama_batch, tests.test_ollama_benchmark, tests.test_ollama_manifest_fix, tests.test_ollama_model_manager, tests.test_ollama_monitor, tests.test_optimize_context, tests.test_photo_deduplication, tests.test_proactive_scout, tests.test_proactive_watcher, tests.test_sync_obsidian, tests.test_task_runner, tests.test_telegram_sender, tests.test_tool_discovery, tool_discovery, video_thumbnail_generator, yandex_metrika

### Public functions (signatures & docstrings)

| Function | Parameters | Returns | Decorators | Docstring |
| --- | --- | --- | --- | --- |
| `normalize_text` | text: str | `str` | — | — |
| `estimate_tokens` | text: str | `int` | — | — |
| `normalize_api_url` | api_url: str | `str` | — | — |
| `is_header_block` | block: str | `bool` | — | — |
| `split_semantic_units` | text: str | `list[str]` | — | — |
| `split_context` | text: str, chunk_size: int, overlap_tokens: int, split_threshold: int, recursive_limit: int, token_counter: Callable[[str], int] | `list[Chunk]` | — | — |
| `build_chunk_messages` | question: str, chunk: Chunk, total_chunks: int | `MessageList` | — | — |
| `build_synthesis_messages` | question: str, chunk_answers: Sequence[dict[str, object]] | `MessageList` | — | — |
| `extract_message_text` | payload: dict | `str` | — | — |
| `openrouter_chat_completion` | messages: MessageList, timeout: int | `str` | — | — |
| `query_with_retry` | requester: Requester, messages: MessageList, timeout: int, retry_attempts: int | `tuple[str, int]` | — | — |
| `split_and_query_context` | question: str, context: str | `dict[str, object]` | — | — |
| … | _3 more_ | | | |

### CLI subcommands

- _No argparse subcommands discovered_

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

- **tool_id**: `scripts/conversation_extractor.py`
- **Path**: `scripts/conversation_extractor.py`
- **Description**: Extract decisions, learnings, and tool-usage highlights from OpenClaw session transcripts.
- **Risk level**: **low**
- **Capabilities**: Filesystem-oriented API surface, General utility automation, Monitoring and observability
- **I/O profile**: filesystem, structured-data
- **Safety constraints**: filesystem_read
- **Decorator signals**: —
- **Dependencies**: ami_parser, auto_memory_cleanup, auto_reflection, batch_image_optimizer, business_dashboard, comfy_auto_quality, comfy_video_pipeline, context_split, doc_generator, error_learning, exif_date_normalizer, face_clustering, face_swap_batch, furniture_sales_database.category_detector, furniture_sales_database.import_sales, furniture_sales_database.query_sales, goal_decomposer, health_dashboard, image_format_migrator, marketplace_dashboard, media_tool, memory_analytics, memory_cleanup, nightly_pipeline, obsidian_dashboard, obsidian_link_checker, obsidian_researcher, ollama_batch, ollama_batch_download, ollama_benchmark, ollama_bridge, ollama_manifest_fix, ollama_model_manager, ollama_monitor, ollama_queue_monitor, optimize_context, photo_archive_report, photo_deduplication, proactive_scout, process_images, queue_manager, run_task, src.coordination.cross_bot_sync, src.dreams.dream_tracker, src.ideation.idea_pipeline, src.monitoring.session_monitor, src.openclaw_orchestration.task_runner, src.self_improvement.auto_engine, src.skills.proactive_watcher, sync_obsidian, telegram_sender, tests.test_auto_engine, tests.test_auto_reflection, tests.test_context_split, tests.test_cross_bot_sync, tests.test_doc_generator, tests.test_dream_tracker, tests.test_error_learning, tests.test_face_clustering, tests.test_idea_pipeline, tests.test_image_format_migrator, tests.test_memory_analytics, tests.test_memory_cleanup, tests.test_obsidian_link_checker, tests.test_ollama_batch, tests.test_ollama_benchmark, tests.test_ollama_manifest_fix, tests.test_ollama_model_manager, tests.test_ollama_monitor, tests.test_optimize_context, tests.test_photo_deduplication, tests.test_proactive_scout, tests.test_proactive_watcher, tests.test_sync_obsidian, tests.test_task_runner, tests.test_telegram_sender, tests.test_tool_discovery, tool_discovery, video_thumbnail_generator, yandex_metrika

### Public functions (signatures & docstrings)

| Function | Parameters | Returns | Decorators | Docstring |
| --- | --- | --- | --- | --- |
| `utc_stamp` |  | `str` | — | — |
| `walk` | node: Any, turn: int | `None` | — | — |
| `parse_json_session` | path: Path | `list[tuple[int, str | None, str]]` | — | — |
| `parse_text_session` | path: Path | `list[tuple[int, str | None, str]]` | — | Line-oriented logs with optional turn hints (compatible with optimize_context). |
| `parse_session_log` | path: Path | `list[tuple[int, str | None, str]]` | — | — |
| `match_patterns` | text: str, patterns: tuple[re.Pattern[str], ...] | `list[str]` | — | — |
| `normalize_ws` | s: str | `str` | — | — |
| `extract_tool_signals` | text: str | `Counter[str]` | — | — |
| `all_tools` |  | `Counter[str]` | — | — |
| `analyze_segments` | segments: list[tuple[int, str | None, str]], source_display: str | `ConversationDigest` | — | — |
| `render_markdown` | d: ConversationDigest | `str` | — | — |
| `digest_to_dict` | d: ConversationDigest | `dict[str, Any]` | — | — |
| … | _4 more_ | | | |

### CLI subcommands

- _No argparse subcommands discovered_

### Example usage

```bash
python -m scripts.conversation_extractor
```
```bash
# Filesystem workflow
python -m scripts.conversation_extractor --help
```

## `doc_generator`

- **tool_id**: `scripts/doc_generator.py`
- **Path**: `scripts/doc_generator.py`
- **Description**: Automated markdown documentation generator for OpenClaw scripts.
- **Risk level**: **medium**
- **Capabilities**: Filesystem-oriented API surface, General utility automation
- **I/O profile**: filesystem
- **Safety constraints**: pure_or_unknown_side_effects
- **Decorator signals**: —
- **Dependencies**: ami_parser, auto_memory_cleanup, auto_reflection, batch_image_optimizer, business_dashboard, comfy_auto_quality, comfy_video_pipeline, context_split, conversation_extractor, error_learning, exif_date_normalizer, face_clustering, face_swap_batch, furniture_sales_database.category_detector, furniture_sales_database.import_sales, furniture_sales_database.query_sales, goal_decomposer, health_dashboard, image_format_migrator, marketplace_dashboard, media_tool, memory_analytics, memory_cleanup, nightly_pipeline, obsidian_dashboard, obsidian_link_checker, obsidian_researcher, ollama_batch, ollama_batch_download, ollama_benchmark, ollama_bridge, ollama_manifest_fix, ollama_model_manager, ollama_monitor, ollama_queue_monitor, optimize_context, photo_archive_report, photo_deduplication, proactive_scout, process_images, queue_manager, run_task, src.coordination.cross_bot_sync, src.dreams.dream_tracker, src.ideation.idea_pipeline, src.monitoring.session_monitor, src.openclaw_orchestration.task_runner, src.self_improvement.auto_engine, src.skills.proactive_watcher, sync_obsidian, telegram_sender, tests.test_auto_reflection, tests.test_context_split, tests.test_cross_bot_sync, tests.test_doc_generator, tests.test_dream_tracker, tests.test_error_learning, tests.test_face_clustering, tests.test_idea_pipeline, tests.test_image_format_migrator, tests.test_memory_analytics, tests.test_memory_cleanup, tests.test_obsidian_link_checker, tests.test_ollama_batch, tests.test_ollama_benchmark, tests.test_ollama_manifest_fix, tests.test_ollama_model_manager, tests.test_ollama_monitor, tests.test_proactive_scout, tests.test_proactive_watcher, tests.test_sync_obsidian, tests.test_telegram_sender, tests.test_tool_discovery, tool_discovery, video_thumbnail_generator, yandex_metrika

### Public functions (signatures & docstrings)

| Function | Parameters | Returns | Decorators | Docstring |
| --- | --- | --- | --- | --- |
| `display_name` |  | `str` | `property` | — |
| `usage_token` |  | `str` | — | — |
| `detail_text` |  | `str` | — | — |
| `detect_color_enabled` | no_color: bool | `bool` | — | — |
| `colorize` | message: str, color: str, enabled: bool | `str` | — | — |
| `log` | message: str, color: str | `None` | — | — |
| `markdown_escape` | value: str | `str` | — | — |
| `extract_leading_header` | existing_text: str | `str` | — | — |
| `merge_generated_content` | existing_text: str, default_header: str, generated_body: str | `str` | — | — |
| `safe_literal` | node: ast.AST | None | `object | None` | — | — |
| `expression_text` | node: ast.AST | None | `str | None` | — | — |
| `qualified_name` | node: ast.AST | None | `str | None` | — | — |
| … | _19 more_ | | | |

### CLI subcommands

- _No argparse subcommands discovered_

### Example usage

```bash
python -m scripts.doc_generator
```
```bash
# Filesystem workflow
python -m scripts.doc_generator --help
```

## `error_learning`

- **tool_id**: `scripts/error_learning.py`
- **Path**: `scripts/error_learning.py`
- **Description**: Capture and learn from recurring OpenClaw session errors.
- **Risk level**: **medium**
- **Capabilities**: Filesystem-oriented API surface, General utility automation, Monitoring and observability
- **I/O profile**: filesystem, structured-data
- **Safety constraints**: filesystem_read
- **Decorator signals**: —
- **Dependencies**: ami_parser, auto_memory_cleanup, auto_reflection, batch_image_optimizer, business_dashboard, comfy_auto_quality, comfy_video_pipeline, context_split, conversation_extractor, doc_generator, exif_date_normalizer, face_clustering, face_swap_batch, furniture_sales_database.import_sales, furniture_sales_database.query_sales, goal_decomposer, health_dashboard, image_format_migrator, marketplace_dashboard, media_tool, memory_analytics, memory_cleanup, nightly_pipeline, obsidian_dashboard, obsidian_link_checker, obsidian_researcher, ollama_batch, ollama_batch_download, ollama_benchmark, ollama_bridge, ollama_manifest_fix, ollama_model_manager, ollama_monitor, ollama_queue_monitor, optimize_context, photo_archive_report, photo_deduplication, proactive_scout, process_images, queue_manager, run_task, src.coordination.cross_bot_sync, src.dreams.dream_tracker, src.ideation.idea_pipeline, src.monitoring.session_monitor, src.openclaw_orchestration.task_runner, src.self_improvement.auto_engine, src.skills.proactive_watcher, sync_obsidian, telegram_sender, tests.test_auto_engine, tests.test_auto_reflection, tests.test_context_split, tests.test_cross_bot_sync, tests.test_doc_generator, tests.test_dream_tracker, tests.test_error_learning, tests.test_face_clustering, tests.test_idea_pipeline, tests.test_image_format_migrator, tests.test_memory_analytics, tests.test_memory_cleanup, tests.test_obsidian_link_checker, tests.test_ollama_batch, tests.test_ollama_benchmark, tests.test_ollama_manifest_fix, tests.test_ollama_model_manager, tests.test_ollama_monitor, tests.test_optimize_context, tests.test_photo_deduplication, tests.test_proactive_scout, tests.test_proactive_watcher, tests.test_sync_obsidian, tests.test_task_runner, tests.test_telegram_sender, tests.test_tool_discovery, tool_discovery, video_thumbnail_generator, yandex_metrika

### Public functions (signatures & docstrings)

| Function | Parameters | Returns | Decorators | Docstring |
| --- | --- | --- | --- | --- |
| `colorize` | text: str, color: str | `str` | — | Wrap text in ANSI color codes unless the user disabled them. |
| `normalize_text` | text: str | `str` | — | Normalize free-form text for comparisons and search. |
| `category_color` | category: str | `str` | — | Choose a stable display color for a category name. |
| `canonical_payload` | category: str, error: str, lesson: str, resolved: bool | `dict[str, object]` | — | Return a normalized payload used for IDs and deduplication. |
| `build_entry` | category: str, error: str, lesson: str | `dict[str, object]` | — | Create a log entry that matches the JSON schema. |
| `default_store` |  | `dict[str, object]` | — | Return an empty log document. |
| `validate_entry` | raw_entry: object | `dict[str, object]` | — | Validate a single persisted entry and normalize minor omissions. |
| `load_store` | log_path: Path | `dict[str, object]` | — | Load the persisted error log from disk. |
| `save_store` | log_path: Path, store: dict[str, object] | `None` | — | Persist the error log to disk. |
| `entries_match` | left: dict[str, object], right: dict[str, object] | `bool` | — | Return True when two entries are the same learning. |
| `add_entry` | log_path: Path, category: str, error: str, lesson: str | `tuple[dict[str, object], bool]` | — | Add an error learning entry unless it already exists. |
| `format_entry` | entry: dict[str, object] | `str` | — | Render a single entry for console output. |
| … | _6 more_ | | | |

### CLI subcommands

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
python -m scripts.error_learning --help
```

## `exif_date_normalizer`

- **tool_id**: `scripts/exif_date_normalizer.py`
- **Path**: `scripts/exif_date_normalizer.py`
- **Description**: Normalize photo filenames using EXIF DateTimeOriginal metadata.
- **Risk level**: **low**
- **Capabilities**: Filesystem-oriented API surface, Media processing
- **I/O profile**: filesystem
- **Safety constraints**: filesystem_write
- **Decorator signals**: —
- **Dependencies**: ami_parser, auto_memory_cleanup, auto_reflection, batch_image_optimizer, business_dashboard, comfy_auto_quality, comfy_video_pipeline, context_split, conversation_extractor, doc_generator, error_learning, face_clustering, face_swap_batch, furniture_sales_database.category_detector, furniture_sales_database.import_sales, furniture_sales_database.query_sales, goal_decomposer, health_dashboard, image_format_migrator, marketplace_dashboard, media_tool, memory_analytics, memory_cleanup, nightly_pipeline, obsidian_dashboard, obsidian_link_checker, obsidian_researcher, ollama_batch, ollama_benchmark, ollama_bridge, ollama_manifest_fix, ollama_model_manager, ollama_monitor, optimize_context, photo_archive_report, photo_deduplication, proactive_scout, process_images, queue_manager, run_task, src.coordination.cross_bot_sync, src.dreams.dream_tracker, src.ideation.idea_pipeline, src.monitoring.session_monitor, src.openclaw_orchestration.task_runner, src.self_improvement.auto_engine, src.skills.proactive_watcher, sync_obsidian, telegram_sender, tests.test_auto_engine, tests.test_auto_reflection, tests.test_context_split, tests.test_error_learning, tests.test_face_clustering, tests.test_idea_pipeline, tests.test_image_format_migrator, tests.test_memory_analytics, tests.test_memory_cleanup, tests.test_ollama_batch, tests.test_ollama_benchmark, tests.test_ollama_manifest_fix, tests.test_ollama_model_manager, tests.test_ollama_monitor, tests.test_photo_deduplication, tests.test_proactive_scout, tests.test_proactive_watcher, tests.test_sync_obsidian, tests.test_telegram_sender, tests.test_tool_discovery, tool_discovery, video_thumbnail_generator, yandex_metrika

### Public functions (signatures & docstrings)

| Function | Parameters | Returns | Decorators | Docstring |
| --- | --- | --- | --- | --- |
| `parse_args` | argv: Optional[list[str]] | `argparse.Namespace` | — | — |
| `iter_supported_files` | root: Path | `Iterable[Path]` | — | — |
| `parse_exif_datetime` | raw_value: str, tz_name: str | `Optional[datetime]` | — | — |
| `read_exif_datetime` | path: Path, tz_name: str | `Optional[datetime]` | — | — |
| `read_folder_datetime` | path: Path, tz_name: str | `Optional[datetime]` | — | — |
| `build_new_name` | path: Path, shot_at: datetime | `str` | — | — |
| `ensure_unique_target` | path: Path, new_name: str | `Path` | — | — |
| `process_file` | path: Path, apply_fix: bool, use_folder_date: bool, tz_name: str | `RenameDecision` | — | — |
| `write_csv_log` | log_path: Path, decisions: list[RenameDecision] | `None` | — | — |
| `main` | argv: Optional[list[str]] | `int` | — | — |

### CLI subcommands

- _No argparse subcommands discovered_

### Example usage

```bash
python -m scripts.exif_date_normalizer
```
```bash
# Filesystem workflow
python -m scripts.exif_date_normalizer --help
```

## `face_clustering`

- **tool_id**: `scripts/face_clustering.py`
- **Path**: `scripts/face_clustering.py`
- **Description**: No module docstring available.
- **Risk level**: **medium**
- **Capabilities**: Filesystem-oriented API surface, General utility automation, Media processing
- **I/O profile**: filesystem, structured-data
- **Safety constraints**: filesystem_read
- **Decorator signals**: —
- **Dependencies**: ami_parser, auto_memory_cleanup, auto_reflection, batch_image_optimizer, business_dashboard, comfy_auto_quality, comfy_video_pipeline, context_split, conversation_extractor, doc_generator, error_learning, exif_date_normalizer, face_swap_batch, furniture_sales_database.category_detector, furniture_sales_database.import_sales, furniture_sales_database.query_sales, goal_decomposer, health_dashboard, image_format_migrator, marketplace_dashboard, media_tool, memory_analytics, memory_cleanup, nightly_pipeline, obsidian_dashboard, obsidian_link_checker, obsidian_researcher, ollama_batch, ollama_batch_download, ollama_benchmark, ollama_bridge, ollama_manifest_fix, ollama_model_manager, ollama_monitor, ollama_queue_monitor, optimize_context, photo_archive_report, photo_deduplication, proactive_scout, process_images, queue_manager, run_task, src.coordination.cross_bot_sync, src.dreams.dream_tracker, src.ideation.idea_pipeline, src.monitoring.session_monitor, src.openclaw_orchestration.task_runner, src.self_improvement.auto_engine, src.skills.proactive_watcher, sync_obsidian, telegram_sender, tests.test_auto_engine, tests.test_auto_reflection, tests.test_context_split, tests.test_cross_bot_sync, tests.test_doc_generator, tests.test_dream_tracker, tests.test_error_learning, tests.test_face_clustering, tests.test_idea_pipeline, tests.test_image_format_migrator, tests.test_memory_analytics, tests.test_memory_cleanup, tests.test_obsidian_link_checker, tests.test_ollama_batch, tests.test_ollama_benchmark, tests.test_ollama_manifest_fix, tests.test_ollama_model_manager, tests.test_ollama_monitor, tests.test_optimize_context, tests.test_photo_deduplication, tests.test_proactive_scout, tests.test_proactive_watcher, tests.test_sync_obsidian, tests.test_task_runner, tests.test_telegram_sender, tests.test_tool_discovery, tool_discovery, video_thumbnail_generator, yandex_metrika

### Public functions (signatures & docstrings)

| Function | Parameters | Returns | Decorators | Docstring |
| --- | --- | --- | --- | --- |
| `encode` | image_path: Path | `list[np.ndarray]` | — | — |
| `encode` | image_path: Path | `list[np.ndarray]` | — | — |
| `encode` | image_path: Path | `list[np.ndarray]` | — | — |
| `parse_args` | argv: list[str] | None | `argparse.Namespace` | — | — |
| `positive_int` | value: str | `int` | — | — |
| `pick_backend` | name: str | `FaceBackend` | — | — |
| `discover_images` | scan_path: Path | `list[Path]` | — | — |
| `file_signature` | path: Path | `dict[str, Any]` | — | — |
| `load_cache` | path: Path | `dict[str, Any]` | — | — |
| `save_cache` | path: Path, payload: dict[str, Any] | `None` | — | — |
| `extract_records` | image_paths: Iterable[Path] | `list[FaceRecord]` | — | — |
| `pairwise_distances` | records: list[FaceRecord] | `np.ndarray` | — | — |
| … | _9 more_ | | | |

### CLI subcommands

- _No argparse subcommands discovered_

### Example usage

```bash
python -m scripts.face_clustering
```
```bash
# Filesystem workflow
python -m scripts.face_clustering --help
```

## `face_swap_batch`

- **tool_id**: `scripts/face_swap_batch.py`
- **Path**: `scripts/face_swap_batch.py`
- **Description**: Batch face swap processor for model photography.
- **Risk level**: **low**
- **Capabilities**: Filesystem-oriented API surface, Media processing, Model lifecycle management, Task orchestration
- **I/O profile**: filesystem
- **Safety constraints**: filesystem_write
- **Decorator signals**: —
- **Dependencies**: ami_parser, auto_memory_cleanup, auto_reflection, batch_image_optimizer, business_dashboard, comfy_auto_quality, comfy_video_pipeline, context_split, conversation_extractor, doc_generator, error_learning, exif_date_normalizer, face_clustering, furniture_sales_database.category_detector, furniture_sales_database.import_sales, furniture_sales_database.query_sales, goal_decomposer, health_dashboard, image_format_migrator, marketplace_dashboard, media_tool, memory_analytics, memory_cleanup, nightly_pipeline, obsidian_dashboard, obsidian_link_checker, obsidian_researcher, ollama_batch, ollama_batch_download, ollama_benchmark, ollama_bridge, ollama_manifest_fix, ollama_model_manager, ollama_monitor, optimize_context, photo_archive_report, photo_deduplication, proactive_scout, process_images, queue_manager, run_task, src.coordination.cross_bot_sync, src.dreams.dream_tracker, src.ideation.idea_pipeline, src.monitoring.session_monitor, src.openclaw_orchestration.task_runner, src.self_improvement.auto_engine, src.skills.proactive_watcher, sync_obsidian, telegram_sender, tests.test_auto_engine, tests.test_auto_reflection, tests.test_context_split, tests.test_error_learning, tests.test_face_clustering, tests.test_idea_pipeline, tests.test_image_format_migrator, tests.test_memory_analytics, tests.test_memory_cleanup, tests.test_ollama_batch, tests.test_ollama_benchmark, tests.test_ollama_manifest_fix, tests.test_ollama_model_manager, tests.test_ollama_monitor, tests.test_photo_deduplication, tests.test_proactive_scout, tests.test_proactive_watcher, tests.test_sync_obsidian, tests.test_telegram_sender, tests.test_tool_discovery, tool_discovery, video_thumbnail_generator, yandex_metrika

### Public functions (signatures & docstrings)

| Function | Parameters | Returns | Decorators | Docstring |
| --- | --- | --- | --- | --- |
| `log` | title: str, message: str | `None` | — | — |
| `detect_faces` | image_path: str | Path | `List[Tuple[int, int, int, int]]` | — | Detect face rectangles in an image. |
| `apply_inswapper` | image: np.ndarray, face_rect: Sequence[int], model_path: str | Path | `np.ndarray` | — | Apply face swap on one rectangle. |
| `apply_gfpgan` | image: np.ndarray, model_path: str | Path | `np.ndarray` | — | Enhance swapped image using GFPGAN when available. |
| `single_swap` | target_path: str | Path, source_face_path: str | Path, enhance: bool | `Optional[np.ndarray]` | — | Swap all faces in one target image. |
| `batch_swap` | target_dir: str | Path, source_face: str | Path, output_dir: str | Path, enhance: bool | `Dict[str, int]` | — | Batch face swap over all jpg/png images in target_dir. |
| `main` |  | `None` | — | — |

### CLI subcommands

- _No argparse subcommands discovered_

### Example usage

```bash
python -m scripts.face_swap_batch
```
```bash
# Filesystem workflow
python -m scripts.face_swap_batch --help
```

## `goal_decomposer`

- **tool_id**: `scripts/goal_decomposer.py`
- **Path**: `scripts/goal_decomposer.py`
- **Description**: Goal Decomposer — break goals into actionable roadmaps.
- **Risk level**: **low**
- **Capabilities**: General utility automation
- **I/O profile**: filesystem
- **Safety constraints**: pure_or_unknown_side_effects
- **Decorator signals**: —
- **Dependencies**: ami_parser, auto_memory_cleanup, auto_reflection, batch_image_optimizer, business_dashboard, comfy_auto_quality, comfy_video_pipeline, context_split, conversation_extractor, doc_generator, error_learning, exif_date_normalizer, face_clustering, face_swap_batch, furniture_sales_database.import_sales, furniture_sales_database.query_sales, health_dashboard, image_format_migrator, marketplace_dashboard, media_tool, memory_analytics, memory_cleanup, nightly_pipeline, obsidian_dashboard, obsidian_link_checker, obsidian_researcher, ollama_batch, ollama_batch_download, ollama_benchmark, ollama_bridge, ollama_manifest_fix, ollama_model_manager, ollama_monitor, ollama_queue_monitor, optimize_context, photo_archive_report, photo_deduplication, proactive_scout, process_images, queue_manager, run_task, src.coordination.cross_bot_sync, src.dreams.dream_tracker, src.ideation.idea_pipeline, src.monitoring.session_monitor, src.openclaw_orchestration.task_runner, src.self_improvement.auto_engine, src.skills.proactive_watcher, sync_obsidian, telegram_sender, tests.test_auto_engine, tests.test_auto_reflection, tests.test_context_split, tests.test_cross_bot_sync, tests.test_doc_generator, tests.test_dream_tracker, tests.test_error_learning, tests.test_face_clustering, tests.test_idea_pipeline, tests.test_memory_analytics, tests.test_memory_cleanup, tests.test_obsidian_link_checker, tests.test_ollama_batch, tests.test_ollama_benchmark, tests.test_ollama_manifest_fix, tests.test_ollama_model_manager, tests.test_ollama_monitor, tests.test_proactive_scout, tests.test_proactive_watcher, tests.test_sync_obsidian, tests.test_tool_discovery, tool_discovery, video_thumbnail_generator, yandex_metrika

### Public functions (signatures & docstrings)

| Function | Parameters | Returns | Decorators | Docstring |
| --- | --- | --- | --- | --- |
| `parse_goal_text` | text: str | `list[str]` | — | Extract key objectives from goal text. |
| `estimate_task_hours` | text: str | `float` | — | Rough estimate based on keywords. |
| `prioritize` | text: str | `str` | — | Determine priority based on keywords. |
| `decompose_goal` | goal_text: str, days: int | `list[Epic]` | — | Decompose goal into epics, tasks, subtasks. |
| `format_roadmap` | epics: list[Epic], goal: str, days: int | `str` | — | Format roadmaps as Obsidian-compatible markdown. |
| `save_goals` | epics: list[Epic], goal: str, days: int | `—` | — | Save goals to file. |
| `main` |  | `—` | — | — |

### CLI subcommands

- _No argparse subcommands discovered_

### Example usage

```bash
python -m scripts.goal_decomposer
```
```bash
# Filesystem workflow
python -m scripts.goal_decomposer --help
```

## `health_dashboard`

- **tool_id**: `scripts/health_dashboard.py`
- **Path**: `scripts/health_dashboard.py`
- **Description**: System Health Dashboard.
- **Risk level**: **high**
- **Capabilities**: Analytics and reporting, Model lifecycle management, Monitoring and observability
- **I/O profile**: filesystem, network, process, structured-data
- **Safety constraints**: network_egress, subprocess_execution
- **Decorator signals**: —
- **Dependencies**: ami_parser, auto_memory_cleanup, auto_reflection, batch_image_optimizer, business_dashboard, comfy_auto_quality, comfy_video_pipeline, context_split, conversation_extractor, doc_generator, error_learning, exif_date_normalizer, face_clustering, face_swap_batch, furniture_sales_database.import_sales, goal_decomposer, image_format_migrator, marketplace_dashboard, media_tool, memory_analytics, memory_cleanup, nightly_pipeline, obsidian_dashboard, obsidian_link_checker, obsidian_researcher, ollama_batch, ollama_batch_download, ollama_benchmark, ollama_bridge, ollama_manifest_fix, ollama_model_manager, ollama_monitor, ollama_queue_monitor, optimize_context, photo_archive_report, photo_deduplication, proactive_scout, process_images, queue_manager, run_task, src.coordination.cross_bot_sync, src.dreams.dream_tracker, src.ideation.idea_pipeline, src.monitoring.session_monitor, src.openclaw_orchestration.task_runner, src.self_improvement.auto_engine, src.skills.proactive_watcher, sync_obsidian, telegram_sender, tests.test_auto_engine, tests.test_auto_reflection, tests.test_context_split, tests.test_cross_bot_sync, tests.test_doc_generator, tests.test_dream_tracker, tests.test_error_learning, tests.test_face_clustering, tests.test_idea_pipeline, tests.test_memory_analytics, tests.test_memory_cleanup, tests.test_obsidian_link_checker, tests.test_ollama_batch, tests.test_ollama_benchmark, tests.test_ollama_manifest_fix, tests.test_ollama_model_manager, tests.test_ollama_monitor, tests.test_optimize_context, tests.test_photo_deduplication, tests.test_proactive_scout, tests.test_proactive_watcher, tests.test_sync_obsidian, tests.test_task_runner, tests.test_telegram_sender, tests.test_tool_discovery, tool_discovery, video_thumbnail_generator, yandex_metrika

### Public functions (signatures & docstrings)

| Function | Parameters | Returns | Decorators | Docstring |
| --- | --- | --- | --- | --- |
| `check_disk_space` |  | `—` | — | — |
| `check_ollama` |  | `—` | — | — |
| `check_gpu` |  | `—` | — | — |
| `check_openclaw` |  | `—` | — | — |
| `run_all` |  | `—` | — | — |
| `format_console` | checks | `—` | — | — |
| `main` |  | `—` | — | — |

### CLI subcommands

- _No argparse subcommands discovered_

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

- **tool_id**: `scripts/image_format_migrator.py`
- **Path**: `scripts/image_format_migrator.py`
- **Description**: Convert and compress image archives to JPEG.
- **Risk level**: **medium**
- **Capabilities**: Filesystem-oriented API surface, Media processing
- **I/O profile**: filesystem
- **Safety constraints**: filesystem_destructive, filesystem_read
- **Decorator signals**: —
- **Dependencies**: ami_parser, auto_memory_cleanup, auto_reflection, batch_image_optimizer, business_dashboard, comfy_auto_quality, comfy_video_pipeline, context_split, conversation_extractor, doc_generator, error_learning, exif_date_normalizer, face_clustering, face_swap_batch, furniture_sales_database.category_detector, furniture_sales_database.import_sales, furniture_sales_database.query_sales, goal_decomposer, health_dashboard, marketplace_dashboard, media_tool, memory_analytics, memory_cleanup, nightly_pipeline, obsidian_dashboard, obsidian_link_checker, obsidian_researcher, ollama_batch, ollama_batch_download, ollama_benchmark, ollama_bridge, ollama_manifest_fix, ollama_model_manager, ollama_monitor, ollama_queue_monitor, optimize_context, photo_archive_report, photo_deduplication, proactive_scout, process_images, queue_manager, run_task, src.coordination.cross_bot_sync, src.dreams.dream_tracker, src.ideation.idea_pipeline, src.monitoring.session_monitor, src.openclaw_orchestration.task_runner, src.self_improvement.auto_engine, src.skills.proactive_watcher, sync_obsidian, telegram_sender, tests.test_auto_engine, tests.test_auto_reflection, tests.test_context_split, tests.test_cross_bot_sync, tests.test_doc_generator, tests.test_dream_tracker, tests.test_error_learning, tests.test_face_clustering, tests.test_idea_pipeline, tests.test_image_format_migrator, tests.test_memory_analytics, tests.test_memory_cleanup, tests.test_obsidian_link_checker, tests.test_ollama_batch, tests.test_ollama_benchmark, tests.test_ollama_manifest_fix, tests.test_ollama_model_manager, tests.test_ollama_monitor, tests.test_photo_deduplication, tests.test_proactive_scout, tests.test_proactive_watcher, tests.test_sync_obsidian, tests.test_task_runner, tests.test_telegram_sender, tests.test_tool_discovery, tool_discovery, video_thumbnail_generator, yandex_metrika

### Public functions (signatures & docstrings)

| Function | Parameters | Returns | Decorators | Docstring |
| --- | --- | --- | --- | --- |
| `setup_logger` |  | `logging.Logger` | — | — |
| `detect_mime` | path: Path | `str | None` | — | — |
| `is_supported_image` | path: Path | `bool` | — | — |
| `iter_images` | root: Path | `Iterable[Path]` | — | — |
| `heic_to_image` | path: Path | `Image.Image` | — | — |
| `open_image` | path: Path | `tuple[Image.Image, bytes | None]` | — | — |
| `destination_for_file` | source: Path, overwrite: bool, output_root: Path | None | `Path` | — | — |
| `convert_file` | source: Path | `FileResult` | — | — |
| `default_output_dir` | scan_path: Path | `Path` | — | — |
| `print_progress` | current: int, total: int | `None` | — | — |
| `process_many` | files: list[Path] | `int` | — | — |
| `parse_args` | argv: list[str] | None | `argparse.Namespace` | — | — |
| … | _1 more_ | | | |

### CLI subcommands

- _No argparse subcommands discovered_

### Example usage

```bash
python -m scripts.image_format_migrator
```
```bash
# Filesystem workflow
python -m scripts.image_format_migrator --help
```

## `marketplace_dashboard`

- **tool_id**: `scripts/marketplace_dashboard.py`
- **Path**: `scripts/marketplace_dashboard.py`
- **Description**: Marketplace analytics dashboard for Amadey.ru, Wildberries, and Ozon.
- **Risk level**: **low**
- **Capabilities**: Analytics and reporting
- **I/O profile**: filesystem, database
- **Safety constraints**: database_access
- **Decorator signals**: —
- **Dependencies**: ami_parser, auto_memory_cleanup, auto_reflection, batch_image_optimizer, business_dashboard, comfy_auto_quality, comfy_video_pipeline, context_split, conversation_extractor, doc_generator, error_learning, exif_date_normalizer, face_clustering, face_swap_batch, furniture_sales_database.category_detector, furniture_sales_database.import_sales, furniture_sales_database.query_sales, goal_decomposer, health_dashboard, image_format_migrator, media_tool, memory_analytics, memory_cleanup, obsidian_dashboard, obsidian_link_checker, obsidian_researcher, ollama_batch, ollama_benchmark, ollama_bridge, ollama_manifest_fix, ollama_model_manager, ollama_monitor, optimize_context, photo_archive_report, photo_deduplication, proactive_scout, process_images, queue_manager, run_task, src.coordination.cross_bot_sync, src.ideation.idea_pipeline, src.monitoring.session_monitor, src.openclaw_orchestration.task_runner, src.self_improvement.auto_engine, src.skills.proactive_watcher, sync_obsidian, telegram_sender, tests.test_context_split, tests.test_ollama_benchmark, tests.test_ollama_model_manager, tool_discovery, video_thumbnail_generator, yandex_metrika

### Public functions (signatures & docstrings)

| Function | Parameters | Returns | Decorators | Docstring |
| --- | --- | --- | --- | --- |
| `setup_database` | connection: sqlite3.Connection | `None` | — | Create required marketplace_sales table. |
| `seed_if_empty` | connection: sqlite3.Connection | `None` | — | Insert demo data for dashboard display when table is empty. |
| `money` | value: float | `str` | — | — |
| `render_table` | title: str, headers: Sequence[str], rows: Iterable[Sequence[str]] | `str` | — | — |
| `line` | cells: Sequence[str] | `str` | — | — |
| `bar_chart` | title: str, datapoints: Sequence[tuple[str, float]], width: int | `str` | — | — |
| `summary_metrics` | connection: sqlite3.Connection | `str` | — | — |
| `top_products` | connection: sqlite3.Connection, limit: int | `str` | — | — |
| `monthly_dynamics` | connection: sqlite3.Connection | `str` | — | — |
| `channel_comparison` | connection: sqlite3.Connection | `str` | — | — |
| `parse_args` |  | `argparse.Namespace` | — | — |
| `main` |  | `None` | — | — |

### CLI subcommands

- _No argparse subcommands discovered_

### Example usage

```bash
python -m scripts.marketplace_dashboard
```
```bash
# Filesystem workflow
python -m scripts.marketplace_dashboard --help
```

## `media_tool`

- **tool_id**: `scripts/media_tool.py`
- **Path**: `scripts/media_tool.py`
- **Description**: Utilities for preparing media files before upload.
- **Risk level**: **high**
- **Capabilities**: Cleanup and maintenance, Filesystem-oriented API surface, Media processing
- **I/O profile**: filesystem, process
- **Safety constraints**: filesystem_read, subprocess_execution
- **Decorator signals**: —
- **Dependencies**: ami_parser, auto_memory_cleanup, auto_reflection, batch_image_optimizer, business_dashboard, comfy_auto_quality, comfy_video_pipeline, context_split, conversation_extractor, doc_generator, error_learning, exif_date_normalizer, face_clustering, face_swap_batch, furniture_sales_database.category_detector, furniture_sales_database.import_sales, furniture_sales_database.query_sales, goal_decomposer, health_dashboard, image_format_migrator, marketplace_dashboard, memory_analytics, memory_cleanup, nightly_pipeline, obsidian_dashboard, obsidian_link_checker, ollama_batch, ollama_batch_download, ollama_benchmark, ollama_bridge, ollama_manifest_fix, ollama_model_manager, ollama_monitor, ollama_queue_monitor, optimize_context, photo_archive_report, photo_deduplication, proactive_scout, process_images, queue_manager, run_task, src.coordination.cross_bot_sync, src.ideation.idea_pipeline, src.monitoring.session_monitor, src.openclaw_orchestration.task_runner, src.self_improvement.auto_engine, src.skills.proactive_watcher, sync_obsidian, telegram_sender, tests.test_auto_engine, tests.test_auto_reflection, tests.test_context_split, tests.test_cross_bot_sync, tests.test_doc_generator, tests.test_dream_tracker, tests.test_error_learning, tests.test_face_clustering, tests.test_idea_pipeline, tests.test_image_format_migrator, tests.test_memory_analytics, tests.test_memory_cleanup, tests.test_obsidian_link_checker, tests.test_ollama_batch, tests.test_ollama_benchmark, tests.test_ollama_manifest_fix, tests.test_ollama_model_manager, tests.test_ollama_monitor, tests.test_optimize_context, tests.test_photo_deduplication, tests.test_proactive_scout, tests.test_proactive_watcher, tests.test_sync_obsidian, tests.test_task_runner, tests.test_telegram_sender, tests.test_tool_discovery, tool_discovery, video_thumbnail_generator, yandex_metrika

### Public functions (signatures & docstrings)

| Function | Parameters | Returns | Decorators | Docstring |
| --- | --- | --- | --- | --- |
| `cleanup` |  | `None` | — | Remove any temporary file created during preparation. |
| `ensure_photo_size_under_limit` | input_path: os.PathLike[str] | str, limit_bytes: int, runner: ResizeRunner, reporter: Optional[Callable[[str], None]] | `PreparedFile` | — | Return a file path suitable for Telegram photo uploads. |

### CLI subcommands

- _No argparse subcommands discovered_

### Example usage

```bash
python -m scripts.media_tool
```
```bash
# Filesystem workflow
python -m scripts.media_tool --help
```

## `memory_analytics`

- **tool_id**: `scripts/memory_analytics.py`
- **Path**: `scripts/memory_analytics.py`
- **Description**: Memory health analytics for MEMORY.md files.
- **Risk level**: **medium**
- **Capabilities**: Analytics and reporting, Filesystem-oriented API surface, General utility automation, Memory and ideation workflows, Monitoring and observability
- **I/O profile**: filesystem, structured-data
- **Safety constraints**: filesystem_read
- **Decorator signals**: —
- **Dependencies**: ami_parser, auto_memory_cleanup, auto_reflection, batch_image_optimizer, business_dashboard, comfy_auto_quality, comfy_video_pipeline, context_split, conversation_extractor, doc_generator, error_learning, exif_date_normalizer, face_clustering, face_swap_batch, furniture_sales_database.category_detector, furniture_sales_database.import_sales, furniture_sales_database.query_sales, goal_decomposer, health_dashboard, image_format_migrator, marketplace_dashboard, media_tool, memory_cleanup, nightly_pipeline, obsidian_dashboard, obsidian_link_checker, obsidian_researcher, ollama_batch, ollama_batch_download, ollama_benchmark, ollama_bridge, ollama_manifest_fix, ollama_model_manager, ollama_monitor, ollama_queue_monitor, optimize_context, photo_archive_report, photo_deduplication, proactive_scout, process_images, queue_manager, run_task, src.coordination.cross_bot_sync, src.dreams.dream_tracker, src.ideation.idea_pipeline, src.monitoring.session_monitor, src.openclaw_orchestration.task_runner, src.self_improvement.auto_engine, src.skills.proactive_watcher, sync_obsidian, telegram_sender, tests.test_auto_engine, tests.test_auto_reflection, tests.test_context_split, tests.test_cross_bot_sync, tests.test_doc_generator, tests.test_dream_tracker, tests.test_error_learning, tests.test_face_clustering, tests.test_idea_pipeline, tests.test_image_format_migrator, tests.test_memory_analytics, tests.test_memory_cleanup, tests.test_obsidian_link_checker, tests.test_ollama_batch, tests.test_ollama_benchmark, tests.test_ollama_manifest_fix, tests.test_ollama_model_manager, tests.test_ollama_monitor, tests.test_optimize_context, tests.test_photo_deduplication, tests.test_proactive_scout, tests.test_proactive_watcher, tests.test_sync_obsidian, tests.test_task_runner, tests.test_telegram_sender, tests.test_tool_discovery, tool_discovery, video_thumbnail_generator, yandex_metrika

### Public functions (signatures & docstrings)

| Function | Parameters | Returns | Decorators | Docstring |
| --- | --- | --- | --- | --- |
| `last_mention` |  | `date | None` | `property` | — |
| `to_dict` | reference_date: date | `dict[str, object]` | — | — |
| `to_dict` | reference_date: date | `dict[str, object]` | — | — |
| `colorize` | text: str, color: str | `str` | — | Wrap text in ANSI color codes unless disabled. |
| `human_size` | size_bytes: int | `str` | — | Format bytes into a compact human-readable string. |
| `slugify_heading` | text: str | `str` | — | Create a markdown-friendly anchor from a heading. |
| `normalize_anchor` | anchor: str | `str` | — | Normalize anchor references for comparisons. |
| `normalize_text` | text: str | `str` | — | Normalize markdown text for duplicate detection. |
| `parse_date_string` | raw_value: str | `date | None` | — | Parse a single date-like string into a date. |
| `extract_dates` | text: str | `list[date]` | — | Extract unique dates found within a block of text. |
| `extract_internal_links` | text: str | `list[dict[str, object]]` | — | Extract internal anchor references from markdown and HTML links. |
| `parse_memory_file` | path: Path | `dict[str, object]` | — | Parse the memory file into sections, entries, anchors, and links. |
| … | _10 more_ | | | |

### CLI subcommands

- _No argparse subcommands discovered_

### Example usage

```bash
python -m scripts.memory_analytics
```
```bash
# Filesystem workflow
python -m scripts.memory_analytics --help
```

## `memory_cleanup`

- **tool_id**: `scripts/memory_cleanup.py`
- **Path**: `scripts/memory_cleanup.py`
- **Description**: No module docstring available.
- **Risk level**: **low**
- **Capabilities**: Cleanup and maintenance, Filesystem-oriented API surface, Memory and ideation workflows
- **I/O profile**: filesystem, structured-data
- **Safety constraints**: pure_or_unknown_side_effects
- **Decorator signals**: —
- **Dependencies**: ami_parser, auto_memory_cleanup, auto_reflection, batch_image_optimizer, business_dashboard, comfy_auto_quality, comfy_video_pipeline, context_split, conversation_extractor, doc_generator, error_learning, exif_date_normalizer, face_clustering, face_swap_batch, furniture_sales_database.category_detector, furniture_sales_database.import_sales, furniture_sales_database.query_sales, goal_decomposer, health_dashboard, image_format_migrator, marketplace_dashboard, media_tool, memory_analytics, nightly_pipeline, obsidian_dashboard, obsidian_link_checker, obsidian_researcher, ollama_batch, ollama_batch_download, ollama_benchmark, ollama_bridge, ollama_manifest_fix, ollama_model_manager, ollama_monitor, ollama_queue_monitor, optimize_context, photo_archive_report, photo_deduplication, proactive_scout, process_images, queue_manager, run_task, src.coordination.cross_bot_sync, src.dreams.dream_tracker, src.ideation.idea_pipeline, src.monitoring.session_monitor, src.openclaw_orchestration.task_runner, src.self_improvement.auto_engine, src.skills.proactive_watcher, sync_obsidian, telegram_sender, tests.test_auto_engine, tests.test_auto_reflection, tests.test_context_split, tests.test_cross_bot_sync, tests.test_doc_generator, tests.test_dream_tracker, tests.test_error_learning, tests.test_face_clustering, tests.test_idea_pipeline, tests.test_image_format_migrator, tests.test_memory_analytics, tests.test_memory_cleanup, tests.test_obsidian_link_checker, tests.test_ollama_batch, tests.test_ollama_benchmark, tests.test_ollama_manifest_fix, tests.test_ollama_model_manager, tests.test_ollama_monitor, tests.test_optimize_context, tests.test_photo_deduplication, tests.test_proactive_scout, tests.test_proactive_watcher, tests.test_sync_obsidian, tests.test_task_runner, tests.test_telegram_sender, tests.test_tool_discovery, tool_discovery, video_thumbnail_generator, yandex_metrika

### Public functions (signatures & docstrings)

| Function | Parameters | Returns | Decorators | Docstring |
| --- | --- | --- | --- | --- |
| `entry_id` |  | `str` | `property` | — |
| `render` |  | `str` | — | — |
| `colorize` | color: str, text: str | `str` | — | — |
| `human_bytes` | size: int | `str` | — | — |
| `normalize_lines` | text: str | `list[str]` | — | — |
| `semantic_text` | text: str | `str` | — | — |
| `merge_bodies` | primary: str, secondary: str | `str` | — | — |
| `parse_date_candidate` | value: str | `date | None` | — | — |
| `dates_from_text` | text: str | `list[date]` | — | — |
| `date_from_stem` | path: Path | `date | None` | — | — |
| `infer_last_updated` | section_text: str, path: Path | `date` | — | — |
| `split_sections` | text: str | `tuple[str, list[str]]` | — | — |
| … | _13 more_ | | | |

### CLI subcommands

- _No argparse subcommands discovered_

### Example usage

```bash
python -m scripts.memory_cleanup
```
```bash
# Filesystem workflow
python -m scripts.memory_cleanup --help
```

## `nightly_pipeline`

- **tool_id**: `scripts/nightly_pipeline.py`
- **Path**: `scripts/nightly_pipeline.py`
- **Description**: Nightly Pipeline - Run during 1:00-8:00 AM
- **Risk level**: **high**
- **Capabilities**: Cleanup and maintenance, Data synchronization, Memory and ideation workflows, Messaging and notifications, Model lifecycle management, Task orchestration
- **I/O profile**: filesystem, process, structured-data
- **Safety constraints**: filesystem_write, subprocess_execution
- **Decorator signals**: —
- **Dependencies**: auto_memory_cleanup, auto_reflection, batch_image_optimizer, business_dashboard, comfy_auto_quality, comfy_video_pipeline, context_split, conversation_extractor, doc_generator, error_learning, exif_date_normalizer, face_clustering, face_swap_batch, furniture_sales_database.import_sales, goal_decomposer, health_dashboard, image_format_migrator, media_tool, memory_analytics, memory_cleanup, obsidian_dashboard, obsidian_link_checker, obsidian_researcher, ollama_batch, ollama_batch_download, ollama_benchmark, ollama_bridge, ollama_manifest_fix, ollama_model_manager, ollama_monitor, ollama_queue_monitor, optimize_context, photo_archive_report, photo_deduplication, proactive_scout, process_images, queue_manager, run_task, src.coordination.cross_bot_sync, src.dreams.dream_tracker, src.ideation.idea_pipeline, src.monitoring.session_monitor, src.openclaw_orchestration.task_runner, src.self_improvement.auto_engine, src.skills.proactive_watcher, sync_obsidian, telegram_sender, tests.test_auto_engine, tests.test_auto_reflection, tests.test_context_split, tests.test_cross_bot_sync, tests.test_doc_generator, tests.test_dream_tracker, tests.test_error_learning, tests.test_face_clustering, tests.test_idea_pipeline, tests.test_memory_analytics, tests.test_memory_cleanup, tests.test_obsidian_link_checker, tests.test_ollama_batch, tests.test_ollama_benchmark, tests.test_ollama_manifest_fix, tests.test_ollama_model_manager, tests.test_ollama_monitor, tests.test_optimize_context, tests.test_photo_deduplication, tests.test_proactive_scout, tests.test_proactive_watcher, tests.test_sync_obsidian, tests.test_task_runner, tests.test_telegram_sender, tests.test_tool_discovery, tool_discovery, video_thumbnail_generator, yandex_metrika

### Public functions (signatures & docstrings)

| Function | Parameters | Returns | Decorators | Docstring |
| --- | --- | --- | --- | --- |
| `log` | msg: str | `—` | — | — |
| `run_ollama` | model: str, prompt: str, timeout: int | `str` | — | Run ollama with given model and prompt |
| `step` | name: str, func | `—` | — | Run a pipeline step with logging |
| `memory_cleanup` |  | `—` | — | Clean old sessions and temp files |
| `obsidian_sync` |  | `—` | — | Sync and check Obsidian vault |
| `generate_morning_brief` |  | `—` | — | Generate morning brief using local model |
| `send_telegram_summary` | brief: str | `—` | — | Send morning brief to Telegram |
| `main` |  | `—` | — | — |

### CLI subcommands

- _No argparse subcommands discovered_

### Example usage

```bash
python -m scripts.nightly_pipeline
```
```bash
# Filesystem workflow
python -m scripts.nightly_pipeline --help
```

## `obsidian_dashboard`

- **tool_id**: `scripts/obsidian_dashboard.py`
- **Path**: `scripts/obsidian_dashboard.py`
- **Description**: Obsidian Vault Dashboard
- **Risk level**: **medium**
- **Capabilities**: Analytics and reporting, Data synchronization, Filesystem-oriented API surface, General utility automation, HTTP service surface
- **I/O profile**: filesystem, structured-data
- **Safety constraints**: filesystem_write
- **Decorator signals**: http_route
- **Dependencies**: ami_parser, auto_memory_cleanup, auto_reflection, batch_image_optimizer, business_dashboard, comfy_auto_quality, comfy_video_pipeline, context_split, conversation_extractor, doc_generator, error_learning, exif_date_normalizer, face_clustering, face_swap_batch, furniture_sales_database.category_detector, furniture_sales_database.import_sales, furniture_sales_database.query_sales, goal_decomposer, health_dashboard, image_format_migrator, marketplace_dashboard, media_tool, memory_analytics, memory_cleanup, nightly_pipeline, obsidian_link_checker, obsidian_researcher, ollama_batch, ollama_benchmark, ollama_bridge, ollama_manifest_fix, ollama_model_manager, ollama_monitor, ollama_queue_monitor, optimize_context, photo_archive_report, photo_deduplication, proactive_scout, process_images, queue_manager, run_task, src.coordination.cross_bot_sync, src.dreams.dream_tracker, src.ideation.idea_pipeline, src.monitoring.session_monitor, src.openclaw_orchestration.task_runner, src.self_improvement.auto_engine, src.skills.proactive_watcher, sync_obsidian, telegram_sender, tests.test_auto_engine, tests.test_auto_reflection, tests.test_context_split, tests.test_cross_bot_sync, tests.test_doc_generator, tests.test_dream_tracker, tests.test_error_learning, tests.test_face_clustering, tests.test_idea_pipeline, tests.test_image_format_migrator, tests.test_memory_analytics, tests.test_memory_cleanup, tests.test_ollama_batch, tests.test_ollama_benchmark, tests.test_ollama_manifest_fix, tests.test_ollama_model_manager, tests.test_ollama_monitor, tests.test_optimize_context, tests.test_photo_deduplication, tests.test_proactive_scout, tests.test_proactive_watcher, tests.test_sync_obsidian, tests.test_task_runner, tests.test_telegram_sender, tests.test_tool_discovery, tool_discovery, video_thumbnail_generator, yandex_metrika

### Public functions (signatures & docstrings)

| Function | Parameters | Returns | Decorators | Docstring |
| --- | --- | --- | --- | --- |
| `bytes_to_human` | size: int | `str` | — | Convert bytes to human-readable value. |
| `now_iso` |  | `str` | — | Current timestamp in ISO format. |
| `log_event` | title: str, details: str | `None` | — | Append markdown log entry to memory/obsidian_dashboard_log.md. |
| `normalize_note_name` | path: Path | `str` | — | Normalize note path to Obsidian note key without extension. |
| `parse_wiki_target` | raw_target: str | `str` | — | Parse wiki link target: |
| `extract_wiki_links` | content: str | `list[str]` | — | Extract raw wiki-link targets from markdown content. |
| `extract_tags` | content: str, frontmatter_tags: Any | `list[str]` | — | Extract hashtags from content + tags from frontmatter. |
| `get_project_stats` | project_dir: str | Path | `dict[str, Any]` | — | Return project statistics: |
| `collect_markdown_notes` | vault_root: Path | `dict[str, Path]` | — | Build an index of markdown notes by possible wiki-link keys. |
| `find_broken_links` | vault_path: str | Path | `list[dict[str, str]]` | — | Find broken internal wiki links for markdown files. |
| `scan_vault` | vault_path: str | Path | `dict[str, Any]` | — | Scan Obsidian vault and return dashboard data dictionary. |
| `generate_report` | vault_stats: dict[str, Any], output_format: str | `str` | — | Generate dashboard report in markdown or html. |
| … | _9 more_ | | | |

### CLI subcommands

- _No argparse subcommands discovered_

### Example usage

```bash
python -m scripts.obsidian_dashboard
```
```bash
# Filesystem workflow
python -m scripts.obsidian_dashboard --help
```

## `obsidian_link_checker`

- **tool_id**: `scripts/obsidian_link_checker.py`
- **Path**: `scripts/obsidian_link_checker.py`
- **Description**: Scan an Obsidian vault for broken internal links and write a JSON report.
- **Risk level**: **medium**
- **Capabilities**: Data synchronization, Filesystem-oriented API surface
- **I/O profile**: filesystem, network, structured-data
- **Safety constraints**: filesystem_read, network_egress
- **Decorator signals**: —
- **Dependencies**: ami_parser, auto_memory_cleanup, auto_reflection, batch_image_optimizer, business_dashboard, comfy_auto_quality, comfy_video_pipeline, context_split, conversation_extractor, doc_generator, error_learning, exif_date_normalizer, face_clustering, face_swap_batch, furniture_sales_database.category_detector, furniture_sales_database.import_sales, furniture_sales_database.query_sales, goal_decomposer, health_dashboard, image_format_migrator, marketplace_dashboard, media_tool, memory_analytics, memory_cleanup, nightly_pipeline, obsidian_dashboard, obsidian_researcher, ollama_batch, ollama_batch_download, ollama_benchmark, ollama_bridge, ollama_manifest_fix, ollama_model_manager, ollama_monitor, ollama_queue_monitor, optimize_context, photo_archive_report, photo_deduplication, proactive_scout, process_images, queue_manager, run_task, src.coordination.cross_bot_sync, src.dreams.dream_tracker, src.ideation.idea_pipeline, src.monitoring.session_monitor, src.openclaw_orchestration.task_runner, src.self_improvement.auto_engine, src.skills.proactive_watcher, sync_obsidian, telegram_sender, tests.test_auto_engine, tests.test_auto_reflection, tests.test_context_split, tests.test_cross_bot_sync, tests.test_doc_generator, tests.test_dream_tracker, tests.test_error_learning, tests.test_face_clustering, tests.test_idea_pipeline, tests.test_image_format_migrator, tests.test_memory_analytics, tests.test_memory_cleanup, tests.test_obsidian_link_checker, tests.test_ollama_batch, tests.test_ollama_benchmark, tests.test_ollama_manifest_fix, tests.test_ollama_model_manager, tests.test_ollama_monitor, tests.test_optimize_context, tests.test_photo_deduplication, tests.test_proactive_scout, tests.test_proactive_watcher, tests.test_sync_obsidian, tests.test_task_runner, tests.test_telegram_sender, tests.test_tool_discovery, tool_discovery, video_thumbnail_generator, yandex_metrika

### Public functions (signatures & docstrings)

| Function | Parameters | Returns | Decorators | Docstring |
| --- | --- | --- | --- | --- |
| `default_vault_path` |  | `Path` | — | — |
| `default_case_sensitive` |  | `bool` | — | POSIX defaults to case-sensitive matching; Windows to case-insensitive. |
| `slugify_heading` | value: str | `str` | — | — |
| `iter_markdown_files` | vault: Path | `Iterator[Path]` | — | — |
| `strip_code_fences` | text: str | `str` | — | Drop fenced code blocks so we do not match links inside them. |
| `split_wiki_target` | raw: str | `tuple[str, str | None]` | — | Split `path\|alias` then path vs anchor (first `#` that starts an anchor section). |
| `normalize_key` | path_posix: str, case_sensitive: bool | `str` | — | — |
| `build` | vault: Path, case_sensitive: bool | `VaultIndex` | `classmethod` | — |
| `resolve_wiki_path` | source: Path, path_part: str | `Path | None` | — | Resolve wiki link path (no anchor) to an existing path, or None. |
| `try_asset_bases` | rel: str | `Path | None` | — | — |
| `collect_heading_slugs` | text: str | `set[str]` | — | — |
| `anchor_exists_in_note` | note_path: Path, anchor: str | `bool` | — | — |
| … | _4 more_ | | | |

### CLI subcommands

- _No argparse subcommands discovered_

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

- **tool_id**: `scripts/obsidian_researcher.py`
- **Path**: `scripts/obsidian_researcher.py`
- **Description**: Obsidian Researcher.
- **Risk level**: **low**
- **Capabilities**: Data synchronization
- **I/O profile**: filesystem
- **Safety constraints**: pure_or_unknown_side_effects
- **Decorator signals**: —
- **Dependencies**: ami_parser, auto_memory_cleanup, auto_reflection, batch_image_optimizer, business_dashboard, comfy_auto_quality, comfy_video_pipeline, context_split, conversation_extractor, doc_generator, error_learning, exif_date_normalizer, face_clustering, face_swap_batch, furniture_sales_database.import_sales, goal_decomposer, health_dashboard, image_format_migrator, marketplace_dashboard, memory_analytics, memory_cleanup, nightly_pipeline, obsidian_dashboard, obsidian_link_checker, ollama_batch, ollama_batch_download, ollama_benchmark, ollama_bridge, ollama_manifest_fix, ollama_model_manager, ollama_monitor, ollama_queue_monitor, optimize_context, photo_archive_report, photo_deduplication, proactive_scout, process_images, queue_manager, run_task, src.coordination.cross_bot_sync, src.dreams.dream_tracker, src.ideation.idea_pipeline, src.monitoring.session_monitor, src.openclaw_orchestration.task_runner, src.self_improvement.auto_engine, src.skills.proactive_watcher, sync_obsidian, telegram_sender, tests.test_auto_engine, tests.test_auto_reflection, tests.test_context_split, tests.test_cross_bot_sync, tests.test_doc_generator, tests.test_dream_tracker, tests.test_error_learning, tests.test_face_clustering, tests.test_idea_pipeline, tests.test_memory_analytics, tests.test_memory_cleanup, tests.test_obsidian_link_checker, tests.test_ollama_batch, tests.test_ollama_benchmark, tests.test_ollama_manifest_fix, tests.test_ollama_model_manager, tests.test_ollama_monitor, tests.test_proactive_scout, tests.test_proactive_watcher, tests.test_sync_obsidian, tests.test_task_runner, tests.test_telegram_sender, tests.test_tool_discovery, tool_discovery, video_thumbnail_generator, yandex_metrika

### Public functions (signatures & docstrings)

| Function | Parameters | Returns | Decorators | Docstring |
| --- | --- | --- | --- | --- |
| `find_notes_by_content` | vault, query, limit | `—` | — | — |
| `summarize_topic` | vault, topic, limit | `—` | — | — |
| `main` |  | `—` | — | — |

### CLI subcommands

- _No argparse subcommands discovered_

### Example usage

```bash
python -m scripts.obsidian_researcher
```
```bash
# Filesystem workflow
python -m scripts.obsidian_researcher --help
```

## `ollama_batch`

- **tool_id**: `scripts/ollama_batch.py`
- **Path**: `scripts/ollama_batch.py`
- **Description**: No module docstring available.
- **Risk level**: **medium**
- **Capabilities**: Filesystem-oriented API surface, Model lifecycle management, Task orchestration
- **I/O profile**: filesystem, process, structured-data
- **Safety constraints**: filesystem_read, subprocess_execution
- **Decorator signals**: —
- **Dependencies**: ami_parser, auto_memory_cleanup, auto_reflection, batch_image_optimizer, business_dashboard, comfy_auto_quality, comfy_video_pipeline, context_split, conversation_extractor, doc_generator, error_learning, exif_date_normalizer, face_clustering, face_swap_batch, furniture_sales_database.category_detector, furniture_sales_database.import_sales, furniture_sales_database.query_sales, goal_decomposer, health_dashboard, image_format_migrator, marketplace_dashboard, media_tool, memory_analytics, memory_cleanup, nightly_pipeline, obsidian_dashboard, obsidian_link_checker, obsidian_researcher, ollama_batch_download, ollama_benchmark, ollama_bridge, ollama_manifest_fix, ollama_model_manager, ollama_monitor, ollama_queue_monitor, optimize_context, photo_archive_report, photo_deduplication, proactive_scout, process_images, queue_manager, run_task, src.coordination.cross_bot_sync, src.dreams.dream_tracker, src.ideation.idea_pipeline, src.monitoring.session_monitor, src.openclaw_orchestration.task_runner, src.self_improvement.auto_engine, src.skills.proactive_watcher, sync_obsidian, telegram_sender, tests.test_auto_engine, tests.test_auto_reflection, tests.test_context_split, tests.test_cross_bot_sync, tests.test_doc_generator, tests.test_dream_tracker, tests.test_error_learning, tests.test_face_clustering, tests.test_idea_pipeline, tests.test_image_format_migrator, tests.test_memory_analytics, tests.test_memory_cleanup, tests.test_obsidian_link_checker, tests.test_ollama_batch, tests.test_ollama_benchmark, tests.test_ollama_manifest_fix, tests.test_ollama_model_manager, tests.test_ollama_monitor, tests.test_optimize_context, tests.test_photo_deduplication, tests.test_proactive_scout, tests.test_proactive_watcher, tests.test_sync_obsidian, tests.test_task_runner, tests.test_telegram_sender, tests.test_tool_discovery, tool_discovery, video_thumbnail_generator, yandex_metrika

### Public functions (signatures & docstrings)

| Function | Parameters | Returns | Decorators | Docstring |
| --- | --- | --- | --- | --- |
| `to_dict` |  | `dict[str, Any]` | — | — |
| `positive_int` | value: str | `int` | — | — |
| `positive_float` | value: str | `float` | — | — |
| `parse_args` | argv: list[str] | None | `argparse.Namespace` | — | — |
| `load_prompts` | path: Path | `list[str]` | — | — |
| `format_duration` | seconds: float | None | `str` | — | — |
| `print_progress` |  | `None` | — | — |
| `run_prompt` | prompt: str | `PromptResult` | — | — |
| `run_batch` | prompts: list[str] | `list[PromptResult]` | — | — |
| `default_runner` | prompt: str | `PromptResult` | — | — |
| `build_output_payload` |  | `dict[str, Any]` | — | — |
| `write_results` | path: Path, payload: dict[str, Any] | `None` | — | — |
| … | _2 more_ | | | |

### CLI subcommands

- `run`

### Example usage

```bash
python -m scripts.ollama_batch run
```
```bash
# Filesystem workflow
python -m scripts.ollama_batch --help
```

## `ollama_batch_download`

- **tool_id**: `scripts/ollama_batch_download.py`
- **Path**: `scripts/ollama_batch_download.py`
- **Description**: Ollama Batch Model Downloader
- **Risk level**: **medium**
- **Capabilities**: Model lifecycle management, Task orchestration
- **I/O profile**: filesystem, process
- **Safety constraints**: filesystem_write, subprocess_execution
- **Decorator signals**: —
- **Dependencies**: auto_memory_cleanup, auto_reflection, batch_image_optimizer, comfy_auto_quality, comfy_video_pipeline, context_split, conversation_extractor, doc_generator, error_learning, face_clustering, face_swap_batch, goal_decomposer, health_dashboard, image_format_migrator, media_tool, memory_analytics, memory_cleanup, nightly_pipeline, obsidian_link_checker, obsidian_researcher, ollama_batch, ollama_benchmark, ollama_bridge, ollama_manifest_fix, ollama_model_manager, ollama_monitor, ollama_queue_monitor, optimize_context, proactive_scout, process_images, queue_manager, run_task, src.coordination.cross_bot_sync, src.dreams.dream_tracker, src.ideation.idea_pipeline, src.monitoring.session_monitor, src.openclaw_orchestration.task_runner, src.self_improvement.auto_engine, src.skills.proactive_watcher, sync_obsidian, telegram_sender, tests.test_auto_engine, tests.test_auto_reflection, tests.test_context_split, tests.test_cross_bot_sync, tests.test_doc_generator, tests.test_dream_tracker, tests.test_error_learning, tests.test_face_clustering, tests.test_memory_analytics, tests.test_memory_cleanup, tests.test_obsidian_link_checker, tests.test_ollama_batch, tests.test_ollama_benchmark, tests.test_ollama_manifest_fix, tests.test_ollama_model_manager, tests.test_ollama_monitor, tests.test_proactive_scout, tests.test_proactive_watcher, tests.test_sync_obsidian, tests.test_tool_discovery, tool_discovery, video_thumbnail_generator

### Public functions (signatures & docstrings)

| Function | Parameters | Returns | Decorators | Docstring |
| --- | --- | --- | --- | --- |
| `log` | msg: str | `—` | — | — |
| `pull` | model: str | `bool` | — | — |
| `main` |  | `—` | — | — |

### CLI subcommands

- _No argparse subcommands discovered_

### Example usage

```bash
python -m scripts.ollama_batch_download
```
```bash
# Filesystem workflow
python -m scripts.ollama_batch_download --help
```

## `ollama_benchmark`

- **tool_id**: `scripts/ollama_benchmark.py`
- **Path**: `scripts/ollama_benchmark.py`
- **Description**: No module docstring available.
- **Risk level**: **medium**
- **Capabilities**: Filesystem-oriented API surface, Model lifecycle management, Performance benchmarking
- **I/O profile**: filesystem, process, structured-data
- **Safety constraints**: filesystem_read, subprocess_execution
- **Decorator signals**: —
- **Dependencies**: ami_parser, auto_memory_cleanup, auto_reflection, batch_image_optimizer, business_dashboard, comfy_auto_quality, comfy_video_pipeline, context_split, conversation_extractor, doc_generator, error_learning, exif_date_normalizer, face_clustering, face_swap_batch, furniture_sales_database.category_detector, furniture_sales_database.import_sales, furniture_sales_database.query_sales, goal_decomposer, health_dashboard, image_format_migrator, marketplace_dashboard, media_tool, memory_analytics, memory_cleanup, nightly_pipeline, obsidian_dashboard, obsidian_link_checker, obsidian_researcher, ollama_batch, ollama_batch_download, ollama_bridge, ollama_manifest_fix, ollama_model_manager, ollama_monitor, ollama_queue_monitor, optimize_context, photo_archive_report, photo_deduplication, proactive_scout, process_images, queue_manager, run_task, src.coordination.cross_bot_sync, src.dreams.dream_tracker, src.ideation.idea_pipeline, src.monitoring.session_monitor, src.openclaw_orchestration.task_runner, src.self_improvement.auto_engine, src.skills.proactive_watcher, sync_obsidian, telegram_sender, tests.test_auto_engine, tests.test_auto_reflection, tests.test_context_split, tests.test_cross_bot_sync, tests.test_doc_generator, tests.test_dream_tracker, tests.test_error_learning, tests.test_face_clustering, tests.test_idea_pipeline, tests.test_image_format_migrator, tests.test_memory_analytics, tests.test_memory_cleanup, tests.test_obsidian_link_checker, tests.test_ollama_batch, tests.test_ollama_benchmark, tests.test_ollama_manifest_fix, tests.test_ollama_model_manager, tests.test_ollama_monitor, tests.test_optimize_context, tests.test_photo_deduplication, tests.test_proactive_scout, tests.test_proactive_watcher, tests.test_sync_obsidian, tests.test_task_runner, tests.test_telegram_sender, tests.test_tool_discovery, tool_discovery, video_thumbnail_generator, yandex_metrika

### Public functions (signatures & docstrings)

| Function | Parameters | Returns | Decorators | Docstring |
| --- | --- | --- | --- | --- |
| `parse_args` | argv: list[str] | None | `argparse.Namespace` | — | — |
| `run_command` | args: list[str] | `subprocess.CompletedProcess[str]` | — | — |
| `estimate_token_count` | text: str | `int` | — | — |
| `normalize_text` | text: str | `str` | — | — |
| `score_prompt_output` | output: str, prompt: BenchmarkPrompt | `float` | — | — |
| `format_number` | value: float | int | None, digits: int | `str` | — | — |
| `render_markdown_table` | rows: list[dict[str, Any]], columns: list[tuple[str, str]] | `str` | — | — |
| `list_models` |  | `list[str]` | — | — |
| `read_vram_usage_mb` |  | `int | None` | — | — |
| `read_ram_usage_mb` |  | `int | None` | — | — |
| `safe_delta` | after: int | None, before: int | None | `int | None` | — | — |
| `benchmark_prompt` | model_name: str, prompt: BenchmarkPrompt | `dict[str, Any]` | — | — |
| … | _20 more_ | | | |

### CLI subcommands

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
python -m scripts.ollama_benchmark --help
```

## `ollama_bridge`

- **tool_id**: `scripts/ollama_bridge.py`
- **Path**: `scripts/ollama_bridge.py`
- **Description**: Ollama Bridge — HTTP proxy between OpenClaw and Ollama API.
- **Risk level**: **medium**
- **Capabilities**: Event or callback handling, Filesystem-oriented API surface, Model lifecycle management, Monitoring and observability, Network-oriented API surface
- **I/O profile**: filesystem, network, structured-data
- **Safety constraints**: filesystem_read, network_egress
- **Decorator signals**: —
- **Dependencies**: ami_parser, auto_memory_cleanup, auto_reflection, batch_image_optimizer, business_dashboard, comfy_auto_quality, comfy_video_pipeline, context_split, conversation_extractor, doc_generator, error_learning, exif_date_normalizer, face_clustering, face_swap_batch, furniture_sales_database.import_sales, furniture_sales_database.query_sales, goal_decomposer, health_dashboard, image_format_migrator, marketplace_dashboard, media_tool, memory_analytics, memory_cleanup, nightly_pipeline, obsidian_dashboard, obsidian_link_checker, obsidian_researcher, ollama_batch, ollama_batch_download, ollama_benchmark, ollama_manifest_fix, ollama_model_manager, ollama_monitor, ollama_queue_monitor, optimize_context, photo_archive_report, photo_deduplication, proactive_scout, process_images, queue_manager, run_task, src.coordination.cross_bot_sync, src.dreams.dream_tracker, src.ideation.idea_pipeline, src.monitoring.session_monitor, src.openclaw_orchestration.task_runner, src.self_improvement.auto_engine, src.skills.proactive_watcher, sync_obsidian, telegram_sender, tests.test_auto_engine, tests.test_auto_reflection, tests.test_context_split, tests.test_cross_bot_sync, tests.test_doc_generator, tests.test_dream_tracker, tests.test_error_learning, tests.test_face_clustering, tests.test_idea_pipeline, tests.test_memory_analytics, tests.test_memory_cleanup, tests.test_obsidian_link_checker, tests.test_ollama_batch, tests.test_ollama_benchmark, tests.test_ollama_manifest_fix, tests.test_ollama_model_manager, tests.test_ollama_monitor, tests.test_optimize_context, tests.test_photo_deduplication, tests.test_proactive_scout, tests.test_proactive_watcher, tests.test_sync_obsidian, tests.test_task_runner, tests.test_telegram_sender, tests.test_tool_discovery, tool_discovery, video_thumbnail_generator, yandex_metrika

### Public functions (signatures & docstrings)

| Function | Parameters | Returns | Decorators | Docstring |
| --- | --- | --- | --- | --- |
| `translate_request` | body: dict | `dict` | — | Convert OpenAI request format to Ollama format. |
| `translate_response` | ollama_response: dict, model: str | `dict` | — | Convert Ollama response to OpenAI-compatible format. |
| `handle_chat` | body: dict | `tuple[dict, int]` | — | Handle /v1/chat/completions request. |
| `handle_models` |  | `tuple[dict, int]` | — | Handle /v1/models request — list available models. |
| `handle_health` |  | `tuple[dict, int]` | — | Health check endpoint. |
| `route` | path: str, method: str, body: dict | None | `tuple[dict, int]` | — | Route request to appropriate handler. |
| `run_server` | port: int, ollama_url: str | `—` | — | Simple HTTP server using stdlib. |
| `log_message` | format | `—` | — | — |
| `do_POST` |  | `—` | — | — |
| `do_GET` |  | `—` | — | — |
| `main` |  | `—` | — | — |

### CLI subcommands

- _No argparse subcommands discovered_

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

- **tool_id**: `scripts/ollama_manifest_fix.py`
- **Path**: `scripts/ollama_manifest_fix.py`
- **Description**: Repair Ollama on-disk manifests so current servers can list and load local models.
- **Risk level**: **high**
- **Capabilities**: Filesystem-oriented API surface, Model lifecycle management
- **I/O profile**: filesystem, structured-data
- **Safety constraints**: filesystem_read
- **Decorator signals**: —
- **Dependencies**: ami_parser, auto_memory_cleanup, auto_reflection, batch_image_optimizer, business_dashboard, comfy_auto_quality, comfy_video_pipeline, context_split, conversation_extractor, doc_generator, error_learning, exif_date_normalizer, face_clustering, face_swap_batch, furniture_sales_database.category_detector, furniture_sales_database.import_sales, furniture_sales_database.query_sales, goal_decomposer, health_dashboard, image_format_migrator, marketplace_dashboard, media_tool, memory_analytics, memory_cleanup, nightly_pipeline, obsidian_dashboard, obsidian_link_checker, obsidian_researcher, ollama_batch, ollama_batch_download, ollama_benchmark, ollama_bridge, ollama_model_manager, ollama_monitor, ollama_queue_monitor, optimize_context, photo_archive_report, photo_deduplication, proactive_scout, process_images, queue_manager, run_task, src.coordination.cross_bot_sync, src.dreams.dream_tracker, src.ideation.idea_pipeline, src.monitoring.session_monitor, src.openclaw_orchestration.task_runner, src.self_improvement.auto_engine, src.skills.proactive_watcher, sync_obsidian, telegram_sender, tests.test_auto_engine, tests.test_auto_reflection, tests.test_context_split, tests.test_cross_bot_sync, tests.test_doc_generator, tests.test_dream_tracker, tests.test_error_learning, tests.test_face_clustering, tests.test_idea_pipeline, tests.test_image_format_migrator, tests.test_memory_analytics, tests.test_memory_cleanup, tests.test_obsidian_link_checker, tests.test_ollama_batch, tests.test_ollama_benchmark, tests.test_ollama_manifest_fix, tests.test_ollama_model_manager, tests.test_ollama_monitor, tests.test_optimize_context, tests.test_photo_deduplication, tests.test_proactive_scout, tests.test_proactive_watcher, tests.test_sync_obsidian, tests.test_task_runner, tests.test_telegram_sender, tests.test_tool_discovery, tool_discovery, video_thumbnail_generator, yandex_metrika

### Public functions (signatures & docstrings)

| Function | Parameters | Returns | Decorators | Docstring |
| --- | --- | --- | --- | --- |
| `manifest_paths_under` | root: Path | `list[Path]` | — | Match Ollama's manifest.Manifests glob: manifests/*/*/*/* (files only). |
| `parse_manifest_rel_path` | manifest_file: Path, manifests_dir: Path | `tuple[str, str, str, str] | None` | — | — |
| `canonicalize_digest` | digest: str | `str | None` | — | — |
| `blob_path_for_digest` | models_root: Path, digest: str | `Path` | — | — |
| `fix_manifest_obj` | data: dict[str, Any], models_root: Path | `tuple[dict[str, Any], list[str]]` | — | Normalize config + layers digests and sizes against files under models_root/blobs. |
| `repair_layer` | kind: str, layer: dict[str, Any] | `None` | — | — |
| `go_style_json_lines` | obj: dict[str, Any] | `bytes` | — | Match encoding/json.Encoder.Encode: one JSON object + trailing newline. |
| `process_manifest_file` | path: Path, models_root: Path | `dict[str, Any]` | — | Returns a report dict for one file. |
| `run_fix` | models_root: Path | `int` | — | — |
| `build_arg_parser` |  | `argparse.ArgumentParser` | — | — |
| `main` | argv: list[str] | None | `int` | — | — |

### CLI subcommands

- _No argparse subcommands discovered_

### Example usage

```bash
python -m scripts.ollama_manifest_fix
```
```bash
# Filesystem workflow
python -m scripts.ollama_manifest_fix --help
```

## `ollama_model_manager`

- **tool_id**: `scripts/ollama_model_manager.py`
- **Path**: `scripts/ollama_model_manager.py`
- **Description**: No module docstring available.
- **Risk level**: **high**
- **Capabilities**: Cleanup and maintenance, Filesystem-oriented API surface, Model lifecycle management
- **I/O profile**: filesystem, process, structured-data
- **Safety constraints**: filesystem_destructive, filesystem_read, subprocess_execution
- **Decorator signals**: —
- **Dependencies**: ami_parser, auto_memory_cleanup, auto_reflection, batch_image_optimizer, business_dashboard, comfy_auto_quality, comfy_video_pipeline, context_split, conversation_extractor, doc_generator, error_learning, exif_date_normalizer, face_clustering, face_swap_batch, furniture_sales_database.import_sales, furniture_sales_database.query_sales, goal_decomposer, health_dashboard, image_format_migrator, marketplace_dashboard, media_tool, memory_analytics, memory_cleanup, nightly_pipeline, obsidian_dashboard, obsidian_link_checker, obsidian_researcher, ollama_batch, ollama_batch_download, ollama_benchmark, ollama_bridge, ollama_manifest_fix, ollama_monitor, ollama_queue_monitor, optimize_context, photo_archive_report, photo_deduplication, proactive_scout, process_images, queue_manager, run_task, src.coordination.cross_bot_sync, src.dreams.dream_tracker, src.ideation.idea_pipeline, src.monitoring.session_monitor, src.openclaw_orchestration.task_runner, src.self_improvement.auto_engine, src.skills.proactive_watcher, sync_obsidian, telegram_sender, tests.test_auto_engine, tests.test_auto_reflection, tests.test_context_split, tests.test_cross_bot_sync, tests.test_doc_generator, tests.test_dream_tracker, tests.test_error_learning, tests.test_face_clustering, tests.test_idea_pipeline, tests.test_image_format_migrator, tests.test_memory_analytics, tests.test_memory_cleanup, tests.test_obsidian_link_checker, tests.test_ollama_batch, tests.test_ollama_benchmark, tests.test_ollama_manifest_fix, tests.test_ollama_model_manager, tests.test_ollama_monitor, tests.test_optimize_context, tests.test_photo_deduplication, tests.test_proactive_scout, tests.test_proactive_watcher, tests.test_sync_obsidian, tests.test_task_runner, tests.test_telegram_sender, tests.test_tool_discovery, tool_discovery, video_thumbnail_generator, yandex_metrika

### Public functions (signatures & docstrings)

| Function | Parameters | Returns | Decorators | Docstring |
| --- | --- | --- | --- | --- |
| `colorize` | text: str, color: str | `str` | — | — |
| `strip_ansi` | text: str | `str` | — | — |
| `format_bytes` | size_bytes: int | float | `str` | — | — |
| `parse_human_bytes` | value: str | `int` | — | — |
| `format_rate` | bytes_per_second: float | None | `str` | — | — |
| `format_duration` | seconds: float | None | `str` | — | — |
| `render_table` | headers: list[str], rows: list[list[str]] | `str` | — | — |
| `make_separator` |  | `str` | — | — |
| `make_row` | cells: list[str] | `str` | — | — |
| `print_section` | title: str | `None` | — | — |
| `parse_relative_age_days` | value: str, now: datetime | None | `float | None` | — | — |
| `parse_tabular_output` | text: str | `tuple[list[str], list[dict[str, str]]]` | — | — |
| … | _20 more_ | | | |

### CLI subcommands

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
python -m scripts.ollama_model_manager --help
```

## `ollama_monitor`

- **tool_id**: `scripts/ollama_monitor.py`
- **Path**: `scripts/ollama_monitor.py`
- **Description**: No module docstring available.
- **Risk level**: **high**
- **Capabilities**: Data synchronization, Filesystem-oriented API surface, Model lifecycle management, Monitoring and observability, Network-oriented API surface
- **I/O profile**: filesystem, network, process, structured-data
- **Safety constraints**: filesystem_write, network_egress, subprocess_execution
- **Decorator signals**: —
- **Dependencies**: ami_parser, auto_memory_cleanup, auto_reflection, batch_image_optimizer, business_dashboard, comfy_auto_quality, comfy_video_pipeline, context_split, conversation_extractor, doc_generator, error_learning, exif_date_normalizer, face_clustering, face_swap_batch, furniture_sales_database.import_sales, furniture_sales_database.query_sales, goal_decomposer, health_dashboard, image_format_migrator, marketplace_dashboard, media_tool, memory_analytics, memory_cleanup, nightly_pipeline, obsidian_dashboard, obsidian_link_checker, obsidian_researcher, ollama_batch, ollama_batch_download, ollama_benchmark, ollama_bridge, ollama_manifest_fix, ollama_model_manager, ollama_queue_monitor, optimize_context, photo_archive_report, photo_deduplication, proactive_scout, process_images, queue_manager, run_task, src.coordination.cross_bot_sync, src.dreams.dream_tracker, src.ideation.idea_pipeline, src.monitoring.session_monitor, src.openclaw_orchestration.task_runner, src.self_improvement.auto_engine, src.skills.proactive_watcher, sync_obsidian, telegram_sender, tests.test_auto_engine, tests.test_auto_reflection, tests.test_context_split, tests.test_cross_bot_sync, tests.test_doc_generator, tests.test_dream_tracker, tests.test_error_learning, tests.test_face_clustering, tests.test_idea_pipeline, tests.test_image_format_migrator, tests.test_memory_analytics, tests.test_memory_cleanup, tests.test_obsidian_link_checker, tests.test_ollama_batch, tests.test_ollama_benchmark, tests.test_ollama_manifest_fix, tests.test_ollama_model_manager, tests.test_ollama_monitor, tests.test_optimize_context, tests.test_photo_deduplication, tests.test_proactive_scout, tests.test_proactive_watcher, tests.test_sync_obsidian, tests.test_task_runner, tests.test_telegram_sender, tests.test_tool_discovery, tool_discovery, video_thumbnail_generator, yandex_metrika

### Public functions (signatures & docstrings)

| Function | Parameters | Returns | Decorators | Docstring |
| --- | --- | --- | --- | --- |
| `utc_now` |  | `datetime` | — | — |
| `isoformat_timestamp` | value: datetime | `str` | — | — |
| `parse_timestamp` | value: str | None | `datetime | None` | — | — |
| `format_duration` | seconds: int | `str` | — | — |
| `logs_dir` | root: Path | `Path` | — | — |
| `state_path` | root: Path | `Path` | — | — |
| `stderr_log_path` | root: Path, now: datetime | `Path` | — | — |
| `daily_log_path` | root: Path, now: datetime | `Path` | — | — |
| `ensure_logs_dir` | root: Path | `Path` | — | — |
| `default_state` |  | `dict` | — | — |
| `load_json_file` | path: Path, fallback: dict | `dict` | — | — |
| `summarize_state` | state: dict, now: datetime | None | `dict` | — | — |
| … | _21 more_ | | | |

### CLI subcommands

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
python -m scripts.ollama_monitor --help
```
```bash
# Filesystem workflow
python -m scripts.ollama_monitor --help
```

## `ollama_queue_monitor`

- **tool_id**: `scripts/ollama_queue_monitor.py`
- **Path**: `scripts/ollama_queue_monitor.py`
- **Description**: Ollama Queue Monitor - runs every hour, checks progress and launches next model.
- **Risk level**: **medium**
- **Capabilities**: Model lifecycle management, Monitoring and observability, Queue orchestration
- **I/O profile**: filesystem, network, process, structured-data
- **Safety constraints**: filesystem_write, network_egress, subprocess_execution
- **Decorator signals**: —
- **Dependencies**: auto_memory_cleanup, auto_reflection, batch_image_optimizer, comfy_auto_quality, comfy_video_pipeline, context_split, conversation_extractor, doc_generator, error_learning, face_clustering, goal_decomposer, health_dashboard, image_format_migrator, media_tool, memory_analytics, memory_cleanup, nightly_pipeline, obsidian_dashboard, obsidian_link_checker, obsidian_researcher, ollama_batch, ollama_batch_download, ollama_benchmark, ollama_bridge, ollama_manifest_fix, ollama_model_manager, ollama_monitor, optimize_context, photo_archive_report, photo_deduplication, proactive_scout, process_images, queue_manager, run_task, src.coordination.cross_bot_sync, src.dreams.dream_tracker, src.ideation.idea_pipeline, src.monitoring.session_monitor, src.openclaw_orchestration.task_runner, src.self_improvement.auto_engine, src.skills.proactive_watcher, sync_obsidian, telegram_sender, tests.test_auto_engine, tests.test_auto_reflection, tests.test_context_split, tests.test_cross_bot_sync, tests.test_doc_generator, tests.test_dream_tracker, tests.test_error_learning, tests.test_face_clustering, tests.test_idea_pipeline, tests.test_memory_analytics, tests.test_memory_cleanup, tests.test_obsidian_link_checker, tests.test_ollama_batch, tests.test_ollama_benchmark, tests.test_ollama_manifest_fix, tests.test_ollama_model_manager, tests.test_ollama_monitor, tests.test_optimize_context, tests.test_photo_deduplication, tests.test_proactive_scout, tests.test_proactive_watcher, tests.test_sync_obsidian, tests.test_task_runner, tests.test_telegram_sender, tests.test_tool_discovery, tool_discovery, video_thumbnail_generator, yandex_metrika

### Public functions (signatures & docstrings)

| Function | Parameters | Returns | Decorators | Docstring |
| --- | --- | --- | --- | --- |
| `log` | msg: str | `—` | — | — |
| `get_ollama_models` |  | `list` | — | — |
| `is_model_downloading` | model_short: str | `bool` | — | Check if Ollama is currently downloading a model (running ollama pull). |
| `launch_pull` | model: str | `bool` | — | — |
| `main` |  | `—` | — | — |

### CLI subcommands

- _No argparse subcommands discovered_

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

- **tool_id**: `scripts/optimize_context.py`
- **Path**: `scripts/optimize_context.py`
- **Description**: Optimize OpenClaw session context and suggest reductions.
- **Risk level**: **low**
- **Capabilities**: Context shaping and prompt preparation, Filesystem-oriented API surface, General utility automation, Memory and ideation workflows, Monitoring and observability
- **I/O profile**: filesystem, structured-data
- **Safety constraints**: filesystem_read
- **Decorator signals**: —
- **Dependencies**: ami_parser, auto_memory_cleanup, auto_reflection, batch_image_optimizer, business_dashboard, comfy_auto_quality, comfy_video_pipeline, context_split, conversation_extractor, doc_generator, error_learning, exif_date_normalizer, face_clustering, face_swap_batch, furniture_sales_database.import_sales, furniture_sales_database.query_sales, goal_decomposer, health_dashboard, image_format_migrator, marketplace_dashboard, media_tool, memory_analytics, memory_cleanup, nightly_pipeline, obsidian_dashboard, obsidian_link_checker, obsidian_researcher, ollama_batch, ollama_batch_download, ollama_benchmark, ollama_bridge, ollama_manifest_fix, ollama_model_manager, ollama_monitor, ollama_queue_monitor, photo_archive_report, photo_deduplication, proactive_scout, process_images, queue_manager, run_task, src.coordination.cross_bot_sync, src.dreams.dream_tracker, src.ideation.idea_pipeline, src.monitoring.session_monitor, src.openclaw_orchestration.task_runner, src.self_improvement.auto_engine, src.skills.proactive_watcher, sync_obsidian, telegram_sender, tests.test_auto_engine, tests.test_auto_reflection, tests.test_context_split, tests.test_cross_bot_sync, tests.test_doc_generator, tests.test_dream_tracker, tests.test_error_learning, tests.test_face_clustering, tests.test_idea_pipeline, tests.test_image_format_migrator, tests.test_memory_analytics, tests.test_memory_cleanup, tests.test_obsidian_link_checker, tests.test_ollama_batch, tests.test_ollama_benchmark, tests.test_ollama_manifest_fix, tests.test_ollama_model_manager, tests.test_ollama_monitor, tests.test_optimize_context, tests.test_photo_deduplication, tests.test_proactive_scout, tests.test_proactive_watcher, tests.test_sync_obsidian, tests.test_task_runner, tests.test_telegram_sender, tests.test_tool_discovery, tool_discovery, video_thumbnail_generator, yandex_metrika

### Public functions (signatures & docstrings)

| Function | Parameters | Returns | Decorators | Docstring |
| --- | --- | --- | --- | --- |
| `estimate_tokens` | text: str | `int` | — | Estimate token count at roughly four characters per token. |
| `read_text` | path: Path | `str` | — | — |
| `normalize_content` | text: str | `str` | — | — |
| `truncate` | text: str, limit: int | `str` | — | — |
| `color` | text: str, name: str, use_color: bool | `str` | — | — |
| `repo_root` |  | `Path` | — | — |
| `resolve_candidate_path` | candidate: str, workspace_root: Path, session_log_path: Path | None | `Path | None` | — | — |
| `update_ref` | refs: dict[str, dict], path_text: str, turn: int, workspace_root: Path, session_log_path: Path | None, embedded_content: str | None | `None` | — | — |
| `extract_paths_from_text` | text: str, turn: int, refs: dict[str, dict], workspace_root: Path, session_log_path: Path | None | `None` | — | — |
| `extract_from_json` | value, refs: dict[str, dict], workspace_root: Path, session_log_path: Path | None, current_turn: int | `int` | — | — |
| `parse_text_session_log` | session_log_path: Path, workspace_root: Path | `tuple[dict[str, dict], int]` | — | — |
| `parse_session_log` | session_log_path: Path | None, workspace_root: Path | `tuple[dict[str, dict], int]` | — | — |
| … | _11 more_ | | | |

### CLI subcommands

- _No argparse subcommands discovered_

### Example usage

```bash
python -m scripts.optimize_context
```
```bash
# Filesystem workflow
python -m scripts.optimize_context --help
```

## `photo_archive_report`

- **tool_id**: `scripts/photo_archive_report.py`
- **Path**: `scripts/photo_archive_report.py`
- **Description**: Photo archive analytics and integrity report generator.
- **Risk level**: **low**
- **Capabilities**: Analytics and reporting, Filesystem-oriented API surface, Media processing
- **I/O profile**: filesystem, structured-data
- **Safety constraints**: filesystem_read
- **Decorator signals**: —
- **Dependencies**: ami_parser, auto_memory_cleanup, auto_reflection, batch_image_optimizer, business_dashboard, comfy_auto_quality, comfy_video_pipeline, context_split, conversation_extractor, doc_generator, error_learning, exif_date_normalizer, face_clustering, face_swap_batch, furniture_sales_database.category_detector, furniture_sales_database.import_sales, furniture_sales_database.query_sales, goal_decomposer, health_dashboard, image_format_migrator, marketplace_dashboard, media_tool, memory_analytics, memory_cleanup, nightly_pipeline, obsidian_dashboard, obsidian_link_checker, obsidian_researcher, ollama_batch, ollama_benchmark, ollama_bridge, ollama_manifest_fix, ollama_model_manager, ollama_monitor, ollama_queue_monitor, optimize_context, photo_deduplication, proactive_scout, process_images, queue_manager, run_task, src.coordination.cross_bot_sync, src.dreams.dream_tracker, src.ideation.idea_pipeline, src.monitoring.session_monitor, src.openclaw_orchestration.task_runner, src.self_improvement.auto_engine, src.skills.proactive_watcher, sync_obsidian, telegram_sender, tests.test_auto_engine, tests.test_auto_reflection, tests.test_context_split, tests.test_cross_bot_sync, tests.test_dream_tracker, tests.test_error_learning, tests.test_face_clustering, tests.test_idea_pipeline, tests.test_image_format_migrator, tests.test_memory_analytics, tests.test_memory_cleanup, tests.test_ollama_batch, tests.test_ollama_benchmark, tests.test_ollama_manifest_fix, tests.test_ollama_model_manager, tests.test_ollama_monitor, tests.test_optimize_context, tests.test_photo_deduplication, tests.test_proactive_scout, tests.test_proactive_watcher, tests.test_sync_obsidian, tests.test_task_runner, tests.test_telegram_sender, tests.test_tool_discovery, tool_discovery, video_thumbnail_generator, yandex_metrika

### Public functions (signatures & docstrings)

| Function | Parameters | Returns | Decorators | Docstring |
| --- | --- | --- | --- | --- |
| `setup_logger` | verbose: bool | `logging.Logger` | — | — |
| `scan_files` | scan_path: Path | `list[Path]` | — | — |
| `summarize_sizes` | sizes: list[int] | `dict[str, float | int]` | — | — |
| `check_integrity` | path: Path, extension: str, size_bytes: int | `IntegrityIssue | None` | — | — |
| `build_report` | scan_path: Path, check_file_integrity: bool, verbose: bool | `dict[str, Any]` | — | — |
| `render_markdown` | report: dict[str, Any] | `str` | — | — |
| `parse_args` | argv: list[str] | None | `argparse.Namespace` | — | — |
| `main` | argv: list[str] | None | `int` | — | — |

### CLI subcommands

- _No argparse subcommands discovered_

### Example usage

```bash
python -m scripts.photo_archive_report
```
```bash
# Filesystem workflow
python -m scripts.photo_archive_report --help
```

## `photo_deduplication`

- **tool_id**: `scripts/photo_deduplication.py`
- **Path**: `scripts/photo_deduplication.py`
- **Description**: Photo archive deduplication with perceptual and average hashes.
- **Risk level**: **high**
- **Capabilities**: Deduplication and similarity, Filesystem-oriented API surface, Media processing
- **I/O profile**: filesystem, structured-data
- **Safety constraints**: filesystem_destructive, filesystem_write
- **Decorator signals**: —
- **Dependencies**: ami_parser, auto_memory_cleanup, auto_reflection, batch_image_optimizer, business_dashboard, comfy_auto_quality, comfy_video_pipeline, context_split, conversation_extractor, doc_generator, error_learning, exif_date_normalizer, face_clustering, face_swap_batch, furniture_sales_database.import_sales, furniture_sales_database.query_sales, goal_decomposer, health_dashboard, image_format_migrator, marketplace_dashboard, media_tool, memory_analytics, memory_cleanup, nightly_pipeline, obsidian_dashboard, obsidian_link_checker, obsidian_researcher, ollama_batch, ollama_benchmark, ollama_bridge, ollama_manifest_fix, ollama_model_manager, ollama_monitor, ollama_queue_monitor, optimize_context, photo_archive_report, proactive_scout, process_images, queue_manager, run_task, src.coordination.cross_bot_sync, src.dreams.dream_tracker, src.ideation.idea_pipeline, src.monitoring.session_monitor, src.openclaw_orchestration.task_runner, src.self_improvement.auto_engine, src.skills.proactive_watcher, sync_obsidian, telegram_sender, tests.test_auto_engine, tests.test_auto_reflection, tests.test_context_split, tests.test_cross_bot_sync, tests.test_dream_tracker, tests.test_error_learning, tests.test_face_clustering, tests.test_idea_pipeline, tests.test_image_format_migrator, tests.test_memory_analytics, tests.test_memory_cleanup, tests.test_ollama_batch, tests.test_ollama_benchmark, tests.test_ollama_manifest_fix, tests.test_ollama_model_manager, tests.test_ollama_monitor, tests.test_optimize_context, tests.test_photo_deduplication, tests.test_proactive_scout, tests.test_proactive_watcher, tests.test_sync_obsidian, tests.test_task_runner, tests.test_telegram_sender, tests.test_tool_discovery, tool_discovery, video_thumbnail_generator, yandex_metrika

### Public functions (signatures & docstrings)

| Function | Parameters | Returns | Decorators | Docstring |
| --- | --- | --- | --- | --- |
| `parse_args` | argv: list[str] | None | `argparse.Namespace` | — | — |
| `iter_image_paths` | root: Path | `list[Path]` | — | — |
| `hash_image` | path: Path, hash_type: str | `HashRecord | None` | — | — |
| `similarity` | record_a: HashRecord, record_b: HashRecord, hash_type: str | `float` | — | — |
| `build_duplicate_groups` | records: list[HashRecord], hash_type: str, threshold: float | `list[list[HashRecord]]` | — | — |
| `process_duplicates` | scan_root: Path, groups: list[list[HashRecord]], dry_run: bool, move_mode: bool | `list[dict[str, str]]` | — | — |
| `build_report` | scan_root: Path, hash_type: str, threshold: float, scanned_files: int, hashed_files: int, groups: list[list[HashRecord]], … | `dict[str, object]` | — | — |
| `write_csv` | path: Path, groups: list[list[HashRecord]], hash_type: str | `None` | — | — |
| `main` | argv: list[str] | None | `int` | — | — |

### CLI subcommands

- _No argparse subcommands discovered_

### Example usage

```bash
python -m scripts.photo_deduplication
```
```bash
# Filesystem workflow
python -m scripts.photo_deduplication --help
```

## `proactive_scout`

- **tool_id**: `scripts/proactive_scout.py`
- **Path**: `scripts/proactive_scout.py`
- **Description**: No module docstring available.
- **Risk level**: **high**
- **Capabilities**: Filesystem-oriented API surface
- **I/O profile**: filesystem, process, structured-data
- **Safety constraints**: filesystem_destructive, filesystem_read, subprocess_execution
- **Decorator signals**: —
- **Dependencies**: ami_parser, auto_memory_cleanup, auto_reflection, batch_image_optimizer, business_dashboard, comfy_auto_quality, comfy_video_pipeline, context_split, conversation_extractor, doc_generator, error_learning, exif_date_normalizer, face_clustering, face_swap_batch, furniture_sales_database.category_detector, furniture_sales_database.import_sales, furniture_sales_database.query_sales, goal_decomposer, health_dashboard, image_format_migrator, marketplace_dashboard, media_tool, memory_analytics, memory_cleanup, nightly_pipeline, obsidian_dashboard, obsidian_link_checker, obsidian_researcher, ollama_batch, ollama_batch_download, ollama_benchmark, ollama_bridge, ollama_manifest_fix, ollama_model_manager, ollama_monitor, ollama_queue_monitor, optimize_context, photo_archive_report, photo_deduplication, process_images, queue_manager, run_task, src.coordination.cross_bot_sync, src.dreams.dream_tracker, src.ideation.idea_pipeline, src.monitoring.session_monitor, src.openclaw_orchestration.task_runner, src.self_improvement.auto_engine, src.skills.proactive_watcher, sync_obsidian, telegram_sender, tests.test_auto_engine, tests.test_auto_reflection, tests.test_context_split, tests.test_cross_bot_sync, tests.test_doc_generator, tests.test_dream_tracker, tests.test_error_learning, tests.test_face_clustering, tests.test_idea_pipeline, tests.test_image_format_migrator, tests.test_memory_analytics, tests.test_memory_cleanup, tests.test_obsidian_link_checker, tests.test_ollama_batch, tests.test_ollama_benchmark, tests.test_ollama_manifest_fix, tests.test_ollama_model_manager, tests.test_ollama_monitor, tests.test_optimize_context, tests.test_photo_deduplication, tests.test_proactive_scout, tests.test_proactive_watcher, tests.test_sync_obsidian, tests.test_task_runner, tests.test_telegram_sender, tests.test_tool_discovery, tool_discovery, video_thumbnail_generator, yandex_metrika

### Public functions (signatures & docstrings)

| Function | Parameters | Returns | Decorators | Docstring |
| --- | --- | --- | --- | --- |
| `to_dict` |  | `dict[str, Any]` | — | — |
| `scout_predict` | task_type: str, result: str | `list[dict[str, Any]]` | — | — |
| `scout_check` | question: str, scout_dir: Path | None | `dict[str, Any] | None` | — | — |
| `scout_run_background` | predictions: list[dict[str, Any] | str], idle_seconds: float | None, scout_dir: Path | None | `dict[str, Any]` | — | — |
| `scout_status` | scout_dir: Path | None | `dict[str, Any]` | — | — |
| `scout_clear` | scout_dir: Path | None | `dict[str, Any]` | — | — |
| `build_parser` |  | `argparse.ArgumentParser` | — | — |
| `main` | argv: list[str] | None | `int` | — | — |

### CLI subcommands

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
python -m scripts.proactive_scout --help
```

## `process_images`

- **tool_id**: `scripts/process_images.py`
- **Path**: `scripts/process_images.py`
- **Description**: Batch image processing utility for OpenClaw orchestration.
- **Risk level**: **low**
- **Capabilities**: Filesystem-oriented API surface, Media processing, Task orchestration
- **I/O profile**: filesystem
- **Safety constraints**: filesystem_read
- **Decorator signals**: —
- **Dependencies**: ami_parser, auto_memory_cleanup, auto_reflection, batch_image_optimizer, business_dashboard, comfy_auto_quality, comfy_video_pipeline, context_split, conversation_extractor, doc_generator, error_learning, exif_date_normalizer, face_clustering, face_swap_batch, furniture_sales_database.category_detector, furniture_sales_database.import_sales, furniture_sales_database.query_sales, goal_decomposer, health_dashboard, image_format_migrator, marketplace_dashboard, media_tool, memory_analytics, memory_cleanup, nightly_pipeline, obsidian_dashboard, obsidian_link_checker, obsidian_researcher, ollama_batch, ollama_batch_download, ollama_benchmark, ollama_bridge, ollama_manifest_fix, ollama_model_manager, ollama_monitor, ollama_queue_monitor, optimize_context, photo_archive_report, photo_deduplication, proactive_scout, queue_manager, run_task, src.coordination.cross_bot_sync, src.dreams.dream_tracker, src.ideation.idea_pipeline, src.monitoring.session_monitor, src.openclaw_orchestration.task_runner, src.self_improvement.auto_engine, src.skills.proactive_watcher, sync_obsidian, telegram_sender, tests.test_auto_reflection, tests.test_context_split, tests.test_cross_bot_sync, tests.test_doc_generator, tests.test_dream_tracker, tests.test_error_learning, tests.test_face_clustering, tests.test_image_format_migrator, tests.test_memory_analytics, tests.test_memory_cleanup, tests.test_obsidian_link_checker, tests.test_ollama_batch, tests.test_ollama_benchmark, tests.test_ollama_manifest_fix, tests.test_ollama_model_manager, tests.test_ollama_monitor, tests.test_photo_deduplication, tests.test_proactive_scout, tests.test_proactive_watcher, tests.test_sync_obsidian, tests.test_telegram_sender, tests.test_tool_discovery, tool_discovery, video_thumbnail_generator, yandex_metrika

### Public functions (signatures & docstrings)

| Function | Parameters | Returns | Decorators | Docstring |
| --- | --- | --- | --- | --- |
| `iter_images` | input_dir: pathlib.Path | `Iterable[pathlib.Path]` | — | — |
| `process_image` | image_path: pathlib.Path, output_path: pathlib.Path | `None` | — | — |
| `main` |  | `int` | — | — |

### CLI subcommands

- _No argparse subcommands discovered_

### Example usage

```bash
python -m scripts.process_images
```
```bash
# Filesystem workflow
python -m scripts.process_images --help
```

## `queue_manager`

- **tool_id**: `scripts/queue_manager.py`
- **Path**: `scripts/queue_manager.py`
- **Description**: Cursor Cloud Agents Batch Queue Manager
- **Risk level**: **high**
- **Capabilities**: Network-oriented API surface, Queue orchestration, Task orchestration
- **I/O profile**: filesystem, network, process, structured-data
- **Safety constraints**: filesystem_read, network_egress, subprocess_execution
- **Decorator signals**: —
- **Dependencies**: ami_parser, auto_memory_cleanup, auto_reflection, batch_image_optimizer, business_dashboard, comfy_auto_quality, comfy_video_pipeline, context_split, conversation_extractor, doc_generator, error_learning, exif_date_normalizer, face_clustering, face_swap_batch, furniture_sales_database.import_sales, furniture_sales_database.query_sales, goal_decomposer, health_dashboard, image_format_migrator, marketplace_dashboard, media_tool, memory_analytics, memory_cleanup, nightly_pipeline, obsidian_dashboard, obsidian_link_checker, obsidian_researcher, ollama_batch, ollama_batch_download, ollama_benchmark, ollama_bridge, ollama_manifest_fix, ollama_model_manager, ollama_monitor, ollama_queue_monitor, optimize_context, photo_archive_report, photo_deduplication, proactive_scout, process_images, run_task, src.coordination.cross_bot_sync, src.dreams.dream_tracker, src.ideation.idea_pipeline, src.monitoring.session_monitor, src.openclaw_orchestration.task_runner, src.self_improvement.auto_engine, src.skills.proactive_watcher, sync_obsidian, telegram_sender, tests.test_auto_engine, tests.test_auto_reflection, tests.test_context_split, tests.test_cross_bot_sync, tests.test_doc_generator, tests.test_dream_tracker, tests.test_error_learning, tests.test_face_clustering, tests.test_idea_pipeline, tests.test_memory_analytics, tests.test_memory_cleanup, tests.test_ollama_batch, tests.test_ollama_benchmark, tests.test_ollama_manifest_fix, tests.test_ollama_model_manager, tests.test_ollama_monitor, tests.test_optimize_context, tests.test_photo_deduplication, tests.test_proactive_scout, tests.test_proactive_watcher, tests.test_sync_obsidian, tests.test_task_runner, tests.test_telegram_sender, tests.test_tool_discovery, tool_discovery, video_thumbnail_generator, yandex_metrika

### Public functions (signatures & docstrings)

| Function | Parameters | Returns | Decorators | Docstring |
| --- | --- | --- | --- | --- |
| `get_active_agents` |  | `int` | — | Count currently running agents via API. |
| `launch_agent` | branch: str, description: str | `Dict` | — | Launch a single Cursor agent. |
| `poll_agent_once` | agent_id: str | `PollResult` | — | Single poll attempt (no retry). Maps CLI output to PollResult. |
| `poll_agent_with_retry` | agent_id: str | `PollResult` | — | Poll agent status with exponential backoff between subprocess retries |
| `merge_pull_request` | pr_url: str | `Tuple[bool, str]` | — | Auto-merge a GitHub PR using `gh` (merge commit). |
| `log_queue_status` | pending: List[str], running: Dict, completed: List[Dict], poll_interval: float | `—` | — | Log queue status to file. |
| `run_queue` | branches: List[str], auto_merge: bool | `—` | — | Main queue runner. |
| `main` |  | `—` | — | — |

### CLI subcommands

- _No argparse subcommands discovered_

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

- **tool_id**: `scripts/run_task.py`
- **Path**: `scripts/run_task.py`
- **Description**: Run OpenClaw task definitions from YAML specs.
- **Risk level**: **low**
- **Capabilities**: General utility automation
- **I/O profile**: filesystem
- **Safety constraints**: filesystem_read
- **Decorator signals**: —
- **Dependencies**: ami_parser, auto_memory_cleanup, auto_reflection, batch_image_optimizer, business_dashboard, comfy_auto_quality, comfy_video_pipeline, context_split, conversation_extractor, doc_generator, error_learning, exif_date_normalizer, face_clustering, face_swap_batch, furniture_sales_database.import_sales, furniture_sales_database.query_sales, goal_decomposer, health_dashboard, image_format_migrator, marketplace_dashboard, media_tool, memory_analytics, memory_cleanup, nightly_pipeline, obsidian_dashboard, obsidian_link_checker, obsidian_researcher, ollama_batch, ollama_batch_download, ollama_benchmark, ollama_bridge, ollama_manifest_fix, ollama_model_manager, ollama_monitor, ollama_queue_monitor, optimize_context, photo_archive_report, photo_deduplication, proactive_scout, process_images, queue_manager, src.coordination.cross_bot_sync, src.dreams.dream_tracker, src.ideation.idea_pipeline, src.monitoring.session_monitor, src.openclaw_orchestration.task_runner, src.self_improvement.auto_engine, src.skills.proactive_watcher, sync_obsidian, telegram_sender, tests.test_auto_reflection, tests.test_context_split, tests.test_cross_bot_sync, tests.test_doc_generator, tests.test_dream_tracker, tests.test_error_learning, tests.test_face_clustering, tests.test_image_format_migrator, tests.test_memory_analytics, tests.test_memory_cleanup, tests.test_obsidian_link_checker, tests.test_ollama_batch, tests.test_ollama_benchmark, tests.test_ollama_manifest_fix, tests.test_ollama_model_manager, tests.test_ollama_monitor, tests.test_proactive_scout, tests.test_proactive_watcher, tests.test_sync_obsidian, tests.test_tool_discovery, tool_discovery, video_thumbnail_generator, yandex_metrika

### Public functions (signatures & docstrings)

| Function | Parameters | Returns | Decorators | Docstring |
| --- | --- | --- | --- | --- |
| `parse_args` |  | `argparse.Namespace` | — | — |
| `main` |  | `int` | — | — |

### CLI subcommands

- _No argparse subcommands discovered_

### Example usage

```bash
python -m scripts.run_task
```
```bash
# Filesystem workflow
python -m scripts.run_task --help
```

## `sync_obsidian`

- **tool_id**: `scripts/sync_obsidian.py`
- **Path**: `scripts/sync_obsidian.py`
- **Description**: Bidirectional sync between MEMORY.md and an Obsidian vault.
- **Risk level**: **medium**
- **Capabilities**: Data synchronization, Filesystem-oriented API surface, Memory and ideation workflows
- **I/O profile**: filesystem, structured-data
- **Safety constraints**: filesystem_read
- **Decorator signals**: —
- **Dependencies**: ami_parser, auto_memory_cleanup, auto_reflection, batch_image_optimizer, business_dashboard, comfy_auto_quality, comfy_video_pipeline, context_split, conversation_extractor, doc_generator, error_learning, exif_date_normalizer, face_clustering, face_swap_batch, furniture_sales_database.category_detector, furniture_sales_database.import_sales, furniture_sales_database.query_sales, goal_decomposer, health_dashboard, image_format_migrator, marketplace_dashboard, media_tool, memory_analytics, memory_cleanup, nightly_pipeline, obsidian_dashboard, obsidian_link_checker, obsidian_researcher, ollama_batch, ollama_batch_download, ollama_benchmark, ollama_bridge, ollama_manifest_fix, ollama_model_manager, ollama_monitor, ollama_queue_monitor, optimize_context, photo_archive_report, photo_deduplication, proactive_scout, process_images, queue_manager, run_task, src.coordination.cross_bot_sync, src.dreams.dream_tracker, src.ideation.idea_pipeline, src.monitoring.session_monitor, src.openclaw_orchestration.task_runner, src.self_improvement.auto_engine, src.skills.proactive_watcher, telegram_sender, tests.test_auto_engine, tests.test_auto_reflection, tests.test_context_split, tests.test_cross_bot_sync, tests.test_doc_generator, tests.test_dream_tracker, tests.test_error_learning, tests.test_face_clustering, tests.test_idea_pipeline, tests.test_image_format_migrator, tests.test_memory_analytics, tests.test_memory_cleanup, tests.test_obsidian_link_checker, tests.test_ollama_batch, tests.test_ollama_benchmark, tests.test_ollama_manifest_fix, tests.test_ollama_model_manager, tests.test_ollama_monitor, tests.test_optimize_context, tests.test_photo_deduplication, tests.test_proactive_scout, tests.test_proactive_watcher, tests.test_sync_obsidian, tests.test_task_runner, tests.test_telegram_sender, tests.test_tool_discovery, tool_discovery, video_thumbnail_generator, yandex_metrika

### Public functions (signatures & docstrings)

| Function | Parameters | Returns | Decorators | Docstring |
| --- | --- | --- | --- | --- |
| `slug` |  | `str` | `property` | — |
| `stem_slug` |  | `str` | `property` | — |
| `colorize` | text: str, color: str, enabled: bool | `str` | — | — |
| `slugify` | value: str | `str` | — | — |
| `isoformat_timestamp` | timestamp: float | `str` | — | — |
| `read_text` | path: Path | `str` | — | — |
| `write_text` | path: Path, text: str | `None` | — | — |
| `parse_memory_sections` | text: str | `list[MemorySection]` | — | — |
| `iter_vault_notes` | vault_path: Path | `list[VaultNote]` | — | — |
| `build_reference_note` | memory_path: Path, sections: Iterable[MemorySection] | `str` | — | — |
| `build_wikilink` | rel_path: str | `str` | — | — |
| `build_daily_note_entry` | rel_path: str, now: datetime | `str` | — | — |
| … | _8 more_ | | | |

### CLI subcommands

- _No argparse subcommands discovered_

### Example usage

```bash
python -m scripts.sync_obsidian
```
```bash
# Filesystem workflow
python -m scripts.sync_obsidian --help
```

## `telegram_sender`

- **tool_id**: `scripts/telegram_sender.py`
- **Path**: `scripts/telegram_sender.py`
- **Description**: Telegram bot sender utility for OpenClaw.
- **Risk level**: **high**
- **Capabilities**: Filesystem-oriented API surface, Media processing, Messaging and notifications
- **I/O profile**: filesystem, network, structured-data
- **Safety constraints**: filesystem_read, network_egress
- **Decorator signals**: —
- **Dependencies**: ami_parser, auto_memory_cleanup, auto_reflection, batch_image_optimizer, business_dashboard, comfy_auto_quality, comfy_video_pipeline, context_split, conversation_extractor, doc_generator, error_learning, exif_date_normalizer, face_clustering, face_swap_batch, furniture_sales_database.category_detector, furniture_sales_database.import_sales, furniture_sales_database.query_sales, goal_decomposer, health_dashboard, image_format_migrator, marketplace_dashboard, media_tool, memory_analytics, memory_cleanup, nightly_pipeline, obsidian_dashboard, obsidian_link_checker, obsidian_researcher, ollama_batch, ollama_batch_download, ollama_benchmark, ollama_bridge, ollama_manifest_fix, ollama_model_manager, ollama_monitor, ollama_queue_monitor, optimize_context, photo_archive_report, photo_deduplication, proactive_scout, process_images, queue_manager, run_task, src.coordination.cross_bot_sync, src.dreams.dream_tracker, src.ideation.idea_pipeline, src.monitoring.session_monitor, src.openclaw_orchestration.task_runner, src.self_improvement.auto_engine, src.skills.proactive_watcher, sync_obsidian, tests.test_auto_engine, tests.test_auto_reflection, tests.test_context_split, tests.test_cross_bot_sync, tests.test_doc_generator, tests.test_dream_tracker, tests.test_error_learning, tests.test_face_clustering, tests.test_idea_pipeline, tests.test_image_format_migrator, tests.test_memory_analytics, tests.test_memory_cleanup, tests.test_obsidian_link_checker, tests.test_ollama_batch, tests.test_ollama_benchmark, tests.test_ollama_manifest_fix, tests.test_ollama_model_manager, tests.test_ollama_monitor, tests.test_optimize_context, tests.test_photo_deduplication, tests.test_proactive_scout, tests.test_proactive_watcher, tests.test_sync_obsidian, tests.test_task_runner, tests.test_telegram_sender, tests.test_tool_discovery, tool_discovery, video_thumbnail_generator, yandex_metrika

### Public functions (signatures & docstrings)

| Function | Parameters | Returns | Decorators | Docstring |
| --- | --- | --- | --- | --- |
| `name` |  | `str` | `property` | — |
| `read` | size: int | `bytes` | — | — |
| `close` |  | `None` | — | — |
| `from_env` | timeout_seconds: int, retries: int | `'TelegramConfig'` | `classmethod` | — |
| `send_photo` | image_path: os.PathLike[str] | str, caption: str | `dict` | — | — |
| `send_document` | file_path: os.PathLike[str] | str, caption: str | `dict` | — | — |
| `send_group` | image_paths: Sequence[os.PathLike[str] | str], caption: str | `dict` | — | — |
| `build_parser` |  | `argparse.ArgumentParser` | — | — |
| `main` | argv: Optional[Sequence[str]] | `int` | — | — |

### CLI subcommands

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
python -m scripts.telegram_sender --help
```
```bash
# Filesystem workflow
python -m scripts.telegram_sender --help
```

## `tool_discovery`

- **tool_id**: `scripts/tool_discovery.py`
- **Path**: `scripts/tool_discovery.py`
- **Description**: Discover Python tools across the workspace: capabilities, I/O, safety, and suggestions.
- **Risk level**: **medium**
- **Capabilities**: Filesystem-oriented API surface, General utility automation
- **I/O profile**: filesystem, process, structured-data
- **Safety constraints**: filesystem_read, subprocess_execution
- **Decorator signals**: —
- **Dependencies**: ami_parser, auto_memory_cleanup, auto_reflection, batch_image_optimizer, business_dashboard, comfy_auto_quality, comfy_video_pipeline, context_split, conversation_extractor, doc_generator, error_learning, exif_date_normalizer, face_clustering, face_swap_batch, furniture_sales_database.category_detector, furniture_sales_database.import_sales, furniture_sales_database.query_sales, goal_decomposer, health_dashboard, image_format_migrator, marketplace_dashboard, media_tool, memory_analytics, memory_cleanup, nightly_pipeline, obsidian_dashboard, obsidian_link_checker, obsidian_researcher, ollama_batch, ollama_batch_download, ollama_benchmark, ollama_bridge, ollama_manifest_fix, ollama_model_manager, ollama_monitor, ollama_queue_monitor, optimize_context, photo_archive_report, photo_deduplication, proactive_scout, process_images, queue_manager, run_task, src.coordination.cross_bot_sync, src.dreams.dream_tracker, src.ideation.idea_pipeline, src.monitoring.session_monitor, src.openclaw_orchestration.task_runner, src.self_improvement.auto_engine, src.skills.proactive_watcher, sync_obsidian, telegram_sender, tests.test_auto_engine, tests.test_auto_reflection, tests.test_context_split, tests.test_cross_bot_sync, tests.test_doc_generator, tests.test_dream_tracker, tests.test_error_learning, tests.test_face_clustering, tests.test_idea_pipeline, tests.test_image_format_migrator, tests.test_memory_analytics, tests.test_memory_cleanup, tests.test_obsidian_link_checker, tests.test_ollama_batch, tests.test_ollama_benchmark, tests.test_ollama_manifest_fix, tests.test_ollama_model_manager, tests.test_ollama_monitor, tests.test_optimize_context, tests.test_photo_deduplication, tests.test_proactive_scout, tests.test_proactive_watcher, tests.test_sync_obsidian, tests.test_task_runner, tests.test_telegram_sender, tests.test_tool_discovery, video_thumbnail_generator, yandex_metrika

### Public functions (signatures & docstrings)

| Function | Parameters | Returns | Decorators | Docstring |
| --- | --- | --- | --- | --- |
| `to_dict` |  | `dict[str, object]` | — | — |
| `to_dict` |  | `dict[str, object]` | — | — |
| `by_name` |  | `dict[str, ToolProfile]` | — | — |
| `by_id` |  | `dict[str, ToolProfile]` | — | — |
| `to_dict` |  | `dict[str, object]` | — | — |
| `discover_python_files` | root: Path | `list[Path]` | — | All ``.py`` files under root, excluding common cache/vendor trees. |
| `extract_cli_commands` | tree: ast.AST | `list[str]` | — | — |
| `extract_imports` | tree: ast.AST | `set[str]` | — | — |
| `extract_decorator_signals` | tree: ast.AST | `list[str]` | — | — |
| `extract_public_functions` | tree: ast.AST | `list[FunctionToolInfo]` | — | — |
| `extract_functions` | tree: ast.AST | `list[str]` | — | — |
| `extract_description` | tree: ast.AST | `str` | — | — |
| … | _14 more_ | | | |

### CLI subcommands

- `analyze`
- `docs`
- `registry`
- `suggest`

### Example usage

```bash
python -m scripts.tool_discovery analyze
```
```bash
python -m scripts.tool_discovery docs
```
```bash
python -m scripts.tool_discovery registry
```
```bash
# Filesystem workflow
python -m scripts.tool_discovery --help
```

## `video_thumbnail_generator`

- **tool_id**: `scripts/video_thumbnail_generator.py`
- **Path**: `scripts/video_thumbnail_generator.py`
- **Description**: Thumbnail generator for furniture videos.
- **Risk level**: **medium**
- **Capabilities**: Filesystem-oriented API surface, General utility automation, Media processing, Network-oriented API surface, Task orchestration
- **I/O profile**: filesystem, process
- **Safety constraints**: filesystem_write, subprocess_execution
- **Decorator signals**: —
- **Dependencies**: ami_parser, auto_memory_cleanup, auto_reflection, batch_image_optimizer, business_dashboard, comfy_auto_quality, comfy_video_pipeline, context_split, conversation_extractor, doc_generator, error_learning, exif_date_normalizer, face_clustering, face_swap_batch, furniture_sales_database.category_detector, furniture_sales_database.import_sales, furniture_sales_database.query_sales, goal_decomposer, health_dashboard, image_format_migrator, marketplace_dashboard, media_tool, memory_analytics, memory_cleanup, nightly_pipeline, obsidian_dashboard, obsidian_link_checker, obsidian_researcher, ollama_batch, ollama_batch_download, ollama_benchmark, ollama_bridge, ollama_manifest_fix, ollama_model_manager, ollama_monitor, ollama_queue_monitor, optimize_context, photo_archive_report, photo_deduplication, proactive_scout, process_images, queue_manager, run_task, src.coordination.cross_bot_sync, src.dreams.dream_tracker, src.ideation.idea_pipeline, src.monitoring.session_monitor, src.openclaw_orchestration.task_runner, src.self_improvement.auto_engine, src.skills.proactive_watcher, sync_obsidian, telegram_sender, tests.test_auto_engine, tests.test_auto_reflection, tests.test_context_split, tests.test_cross_bot_sync, tests.test_doc_generator, tests.test_dream_tracker, tests.test_error_learning, tests.test_face_clustering, tests.test_idea_pipeline, tests.test_image_format_migrator, tests.test_memory_analytics, tests.test_memory_cleanup, tests.test_obsidian_link_checker, tests.test_ollama_batch, tests.test_ollama_benchmark, tests.test_ollama_manifest_fix, tests.test_ollama_model_manager, tests.test_ollama_monitor, tests.test_optimize_context, tests.test_photo_deduplication, tests.test_proactive_scout, tests.test_proactive_watcher, tests.test_sync_obsidian, tests.test_task_runner, tests.test_telegram_sender, tests.test_tool_discovery, tool_discovery, yandex_metrika

### Public functions (signatures & docstrings)

| Function | Parameters | Returns | Decorators | Docstring |
| --- | --- | --- | --- | --- |
| `setup_logger` | verbose: bool | `logging.Logger` | — | Build logger for console output. |
| `extract_frames` | video_path: Path, frame_count: int, logger: logging.Logger | `list[Path]` | — | Extract evenly sampled JPG frames from video into temp dir. |
| `select_best_frame` | frame_paths: Sequence[Path], logger: logging.Logger | `Path` | — | Select frame with highest center-biased Laplacian sharpness. |
| `enhance_thumbnail` | image: Image.Image, use_minimax: bool, saturation: float, contrast: float, sharpness: float | `Image.Image` | — | Enhance selected frame for thumbnail readability. |
| `apply_branding` | image: Image.Image, text: str, logger: logging.Logger | `Image.Image` | — | Overlay brand text in lower-left with a translucent rounded backdrop. |
| `generate_all_sizes` | image: Image.Image, base_name: str, output_dir: Path, sizes: Sequence[tuple[int, int]], logger: logging.Logger | `list[Path]` | — | Create thumbnails in requested resolutions. |
| `batch_process` | input_dir: Path, output_dir: Path, branding_text: str, use_minimax: bool, frame_count: int, logger: logging.Logger, … | `dict[str, list[Path]]` | — | Process all mp4 files in directory and export thumbnails. |
| `parse_args` |  | `argparse.Namespace` | — | Parse CLI arguments. |
| `main` |  | `int` | — | CLI entrypoint. |

### CLI subcommands

- _No argparse subcommands discovered_

### Example usage

```bash
python -m scripts.video_thumbnail_generator
```
```bash
# Filesystem workflow
python -m scripts.video_thumbnail_generator --help
```

## `src.coordination.cross_bot_sync`

- **tool_id**: `src/coordination/cross_bot_sync.py`
- **Path**: `src/coordination/cross_bot_sync.py`
- **Description**: Cross-bot coordination helpers for OpenClaw bots.
- **Risk level**: **medium**
- **Capabilities**: Data synchronization, Filesystem-oriented API surface, Memory and ideation workflows, Network-oriented API surface
- **I/O profile**: filesystem, structured-data
- **Safety constraints**: filesystem_read
- **Decorator signals**: —
- **Dependencies**: ami_parser, auto_memory_cleanup, auto_reflection, batch_image_optimizer, business_dashboard, comfy_auto_quality, comfy_video_pipeline, context_split, conversation_extractor, doc_generator, error_learning, exif_date_normalizer, face_clustering, face_swap_batch, furniture_sales_database.category_detector, furniture_sales_database.import_sales, furniture_sales_database.query_sales, goal_decomposer, health_dashboard, image_format_migrator, marketplace_dashboard, media_tool, memory_analytics, memory_cleanup, nightly_pipeline, obsidian_dashboard, obsidian_link_checker, obsidian_researcher, ollama_batch, ollama_batch_download, ollama_benchmark, ollama_bridge, ollama_manifest_fix, ollama_model_manager, ollama_monitor, ollama_queue_monitor, optimize_context, photo_archive_report, photo_deduplication, proactive_scout, process_images, queue_manager, run_task, src.dreams.dream_tracker, src.ideation.idea_pipeline, src.monitoring.session_monitor, src.openclaw_orchestration.task_runner, src.self_improvement.auto_engine, src.skills.proactive_watcher, sync_obsidian, telegram_sender, tests.test_auto_engine, tests.test_auto_reflection, tests.test_context_split, tests.test_cross_bot_sync, tests.test_doc_generator, tests.test_dream_tracker, tests.test_error_learning, tests.test_face_clustering, tests.test_idea_pipeline, tests.test_image_format_migrator, tests.test_memory_analytics, tests.test_memory_cleanup, tests.test_obsidian_link_checker, tests.test_ollama_batch, tests.test_ollama_benchmark, tests.test_ollama_manifest_fix, tests.test_ollama_model_manager, tests.test_ollama_monitor, tests.test_optimize_context, tests.test_photo_deduplication, tests.test_proactive_scout, tests.test_proactive_watcher, tests.test_sync_obsidian, tests.test_task_runner, tests.test_telegram_sender, tests.test_tool_discovery, tool_discovery, video_thumbnail_generator, yandex_metrika

### Public functions (signatures & docstrings)

| Function | Parameters | Returns | Decorators | Docstring |
| --- | --- | --- | --- | --- |
| `utc_now` |  | `str` | — | Return an ISO 8601 timestamp in UTC. |
| `normalize_task` | task: str | `str` | — | Normalize task descriptions so equivalent claims share a key. |
| `normalize_memory_key` | line: str | `str` | — | Normalize a memory line for de-duplication and conflict resolution. |
| `parse_memory_entries` | text: str | `Dict[str, str]` | — | Convert markdown text into normalized memory entries. |
| `render_memory` | entries: Dict[str, Dict[str, Any]] | `str` | — | Render normalized memory entries back to a deterministic markdown file. |
| `read_json` | path: Path, default: Optional[Dict[str, Any]] | `Dict[str, Any]` | — | Read a JSON file and return a mapping, or a default on missing files. |
| `atomic_write_text` | path: Path, content: str | `None` | — | Atomically write text content to a file. |
| `atomic_write_json` | path: Path, payload: Dict[str, Any] | `None` | — | Atomically write JSON content to a file. |
| `acquire` |  | `None` | — | — |
| `release` |  | `None` | — | — |
| `acquire` |  | `None` | — | — |
| `release` |  | `None` | — | — |
| … | _11 more_ | | | |

### CLI subcommands

- `handoff`
- `status`
- `sync`
- `unlock`

### Example usage

```bash
python -m src.coordination.cross_bot_sync handoff
```
```bash
python -m src.coordination.cross_bot_sync status
```
```bash
python -m src.coordination.cross_bot_sync sync
```
```bash
# Filesystem workflow
python -m src.coordination.cross_bot_sync --help
```

## `src.dreams.dream_tracker`

- **tool_id**: `src/dreams/dream_tracker.py`
- **Path**: `src/dreams/dream_tracker.py`
- **Description**: No module docstring available.
- **Risk level**: **low**
- **Capabilities**: Memory and ideation workflows
- **I/O profile**: filesystem, structured-data
- **Safety constraints**: filesystem_read
- **Decorator signals**: —
- **Dependencies**: ami_parser, auto_memory_cleanup, auto_reflection, batch_image_optimizer, business_dashboard, comfy_auto_quality, comfy_video_pipeline, context_split, conversation_extractor, doc_generator, error_learning, exif_date_normalizer, face_clustering, face_swap_batch, furniture_sales_database.import_sales, goal_decomposer, health_dashboard, image_format_migrator, memory_analytics, memory_cleanup, nightly_pipeline, obsidian_dashboard, obsidian_link_checker, obsidian_researcher, ollama_batch, ollama_batch_download, ollama_benchmark, ollama_bridge, ollama_manifest_fix, ollama_model_manager, ollama_monitor, ollama_queue_monitor, optimize_context, photo_archive_report, photo_deduplication, proactive_scout, process_images, queue_manager, run_task, src.coordination.cross_bot_sync, src.ideation.idea_pipeline, src.monitoring.session_monitor, src.openclaw_orchestration.task_runner, src.self_improvement.auto_engine, src.skills.proactive_watcher, sync_obsidian, telegram_sender, tests.test_auto_engine, tests.test_auto_reflection, tests.test_context_split, tests.test_cross_bot_sync, tests.test_doc_generator, tests.test_dream_tracker, tests.test_error_learning, tests.test_face_clustering, tests.test_idea_pipeline, tests.test_memory_analytics, tests.test_memory_cleanup, tests.test_obsidian_link_checker, tests.test_ollama_batch, tests.test_ollama_benchmark, tests.test_ollama_manifest_fix, tests.test_ollama_model_manager, tests.test_ollama_monitor, tests.test_optimize_context, tests.test_photo_deduplication, tests.test_proactive_scout, tests.test_proactive_watcher, tests.test_sync_obsidian, tests.test_task_runner, tests.test_telegram_sender, tests.test_tool_discovery, tool_discovery, video_thumbnail_generator, yandex_metrika

### Public functions (signatures & docstrings)

| Function | Parameters | Returns | Decorators | Docstring |
| --- | --- | --- | --- | --- |
| `create_dream` | title, description, source | `—` | — | — |
| `list_dreams` |  | `—` | — | — |
| `get_dream` | dream_id | `—` | — | — |
| `status` | dream_id | `—` | — | — |
| `research_dream` | dream_id | `—` | — | — |
| `implement_dream` | dream_id | `—` | — | — |
| `archive_dream` | dream_id, reason | `—` | — | — |
| `auto_generate_dreams` |  | `—` | — | — |
| `main` | argv | `—` | — | — |

### CLI subcommands

- _No argparse subcommands discovered_

### Example usage

```bash
python -m src.dreams.dream_tracker
```
```bash
# Filesystem workflow
python -m src.dreams.dream_tracker --help
```

## `src.ideation.idea_pipeline`

- **tool_id**: `src/ideation/idea_pipeline.py`
- **Path**: `src/ideation/idea_pipeline.py`
- **Description**: Idea-to-PR pipeline utilities for OpenClaw.
- **Risk level**: **medium**
- **Capabilities**: Memory and ideation workflows, Task orchestration
- **I/O profile**: filesystem, process, structured-data
- **Safety constraints**: filesystem_write, subprocess_execution
- **Decorator signals**: —
- **Dependencies**: ami_parser, auto_memory_cleanup, auto_reflection, batch_image_optimizer, business_dashboard, comfy_auto_quality, comfy_video_pipeline, context_split, conversation_extractor, doc_generator, error_learning, exif_date_normalizer, face_clustering, face_swap_batch, furniture_sales_database.import_sales, goal_decomposer, health_dashboard, image_format_migrator, marketplace_dashboard, media_tool, memory_analytics, memory_cleanup, nightly_pipeline, obsidian_dashboard, obsidian_link_checker, obsidian_researcher, ollama_batch, ollama_batch_download, ollama_benchmark, ollama_bridge, ollama_manifest_fix, ollama_model_manager, ollama_monitor, ollama_queue_monitor, optimize_context, photo_archive_report, photo_deduplication, proactive_scout, process_images, queue_manager, run_task, src.coordination.cross_bot_sync, src.dreams.dream_tracker, src.monitoring.session_monitor, src.openclaw_orchestration.task_runner, src.self_improvement.auto_engine, src.skills.proactive_watcher, sync_obsidian, telegram_sender, tests.test_auto_engine, tests.test_auto_reflection, tests.test_context_split, tests.test_cross_bot_sync, tests.test_doc_generator, tests.test_dream_tracker, tests.test_error_learning, tests.test_face_clustering, tests.test_idea_pipeline, tests.test_image_format_migrator, tests.test_memory_analytics, tests.test_memory_cleanup, tests.test_obsidian_link_checker, tests.test_ollama_batch, tests.test_ollama_benchmark, tests.test_ollama_manifest_fix, tests.test_ollama_model_manager, tests.test_ollama_monitor, tests.test_optimize_context, tests.test_photo_deduplication, tests.test_proactive_scout, tests.test_proactive_watcher, tests.test_sync_obsidian, tests.test_task_runner, tests.test_telegram_sender, tests.test_tool_discovery, tool_discovery, video_thumbnail_generator, yandex_metrika

### Public functions (signatures & docstrings)

| Function | Parameters | Returns | Decorators | Docstring |
| --- | --- | --- | --- | --- |
| `run_phase` | phase: str, topic: str | `dict` | — | Execute a pipeline phase for the supplied topic. |
| `main` | argv: list[str] | None | `int` | — | CLI entry point for the idea pipeline. |

### CLI subcommands

- _No argparse subcommands discovered_

### Example usage

```bash
python -m src.ideation.idea_pipeline
```
```bash
# Filesystem workflow
python -m src.ideation.idea_pipeline --help
```

## `src.monitoring.session_monitor`

- **tool_id**: `src/monitoring/session_monitor.py`
- **Path**: `src/monitoring/session_monitor.py`
- **Description**: OpenClaw Session Monitor
- **Risk level**: **medium**
- **Capabilities**: Monitoring and observability
- **I/O profile**: filesystem, process, structured-data
- **Safety constraints**: filesystem_read, subprocess_execution
- **Decorator signals**: —
- **Dependencies**: ami_parser, auto_memory_cleanup, auto_reflection, batch_image_optimizer, business_dashboard, comfy_auto_quality, comfy_video_pipeline, context_split, conversation_extractor, doc_generator, error_learning, exif_date_normalizer, face_clustering, face_swap_batch, furniture_sales_database.import_sales, goal_decomposer, health_dashboard, image_format_migrator, marketplace_dashboard, media_tool, memory_analytics, memory_cleanup, nightly_pipeline, obsidian_dashboard, obsidian_link_checker, obsidian_researcher, ollama_batch, ollama_batch_download, ollama_benchmark, ollama_bridge, ollama_manifest_fix, ollama_model_manager, ollama_monitor, ollama_queue_monitor, optimize_context, photo_archive_report, photo_deduplication, proactive_scout, process_images, queue_manager, run_task, src.coordination.cross_bot_sync, src.dreams.dream_tracker, src.ideation.idea_pipeline, src.openclaw_orchestration.task_runner, src.self_improvement.auto_engine, src.skills.proactive_watcher, sync_obsidian, telegram_sender, tests.test_auto_engine, tests.test_auto_reflection, tests.test_context_split, tests.test_cross_bot_sync, tests.test_doc_generator, tests.test_dream_tracker, tests.test_error_learning, tests.test_face_clustering, tests.test_idea_pipeline, tests.test_memory_analytics, tests.test_memory_cleanup, tests.test_obsidian_link_checker, tests.test_ollama_batch, tests.test_ollama_benchmark, tests.test_ollama_manifest_fix, tests.test_ollama_model_manager, tests.test_ollama_monitor, tests.test_optimize_context, tests.test_photo_deduplication, tests.test_proactive_scout, tests.test_proactive_watcher, tests.test_sync_obsidian, tests.test_task_runner, tests.test_telegram_sender, tests.test_tool_discovery, tool_discovery, video_thumbnail_generator, yandex_metrika

### Public functions (signatures & docstrings)

| Function | Parameters | Returns | Decorators | Docstring |
| --- | --- | --- | --- | --- |
| `get_session_sizes` |  | `—` | — | Parse openclaw status JSON to get session info. |
| `log_warning` | message: str | `—` | — | Log warning to daily memory file. |
| `check_sessions` | threshold_pct: float | `—` | — | Check all sessions and alert on large ones. |
| `main` |  | `—` | — | — |

### CLI subcommands

- _No argparse subcommands discovered_

### Example usage

```bash
python -m src.monitoring.session_monitor
```
```bash
# Filesystem workflow
python -m src.monitoring.session_monitor --help
```

## `src.openclaw_orchestration.task_runner`

- **tool_id**: `src/openclaw_orchestration/task_runner.py`
- **Path**: `src/openclaw_orchestration/task_runner.py`
- **Description**: No module docstring available.
- **Risk level**: **high**
- **Capabilities**: Task orchestration
- **I/O profile**: filesystem, network, process, structured-data
- **Safety constraints**: filesystem_write, network_egress, subprocess_execution
- **Decorator signals**: —
- **Dependencies**: ami_parser, auto_memory_cleanup, auto_reflection, batch_image_optimizer, business_dashboard, comfy_auto_quality, comfy_video_pipeline, context_split, conversation_extractor, doc_generator, error_learning, exif_date_normalizer, face_clustering, face_swap_batch, furniture_sales_database.category_detector, furniture_sales_database.import_sales, furniture_sales_database.query_sales, goal_decomposer, health_dashboard, image_format_migrator, marketplace_dashboard, media_tool, memory_analytics, memory_cleanup, nightly_pipeline, obsidian_dashboard, obsidian_link_checker, obsidian_researcher, ollama_batch, ollama_batch_download, ollama_benchmark, ollama_bridge, ollama_manifest_fix, ollama_model_manager, ollama_monitor, ollama_queue_monitor, optimize_context, photo_archive_report, photo_deduplication, proactive_scout, process_images, queue_manager, run_task, src.coordination.cross_bot_sync, src.dreams.dream_tracker, src.ideation.idea_pipeline, src.monitoring.session_monitor, src.self_improvement.auto_engine, src.skills.proactive_watcher, sync_obsidian, telegram_sender, tests.test_auto_engine, tests.test_auto_reflection, tests.test_context_split, tests.test_cross_bot_sync, tests.test_doc_generator, tests.test_dream_tracker, tests.test_error_learning, tests.test_face_clustering, tests.test_idea_pipeline, tests.test_image_format_migrator, tests.test_memory_analytics, tests.test_memory_cleanup, tests.test_obsidian_link_checker, tests.test_ollama_batch, tests.test_ollama_benchmark, tests.test_ollama_manifest_fix, tests.test_ollama_model_manager, tests.test_ollama_monitor, tests.test_optimize_context, tests.test_photo_deduplication, tests.test_proactive_scout, tests.test_proactive_watcher, tests.test_sync_obsidian, tests.test_task_runner, tests.test_telegram_sender, tests.test_tool_discovery, tool_discovery, video_thumbnail_generator, yandex_metrika

### Public functions (signatures & docstrings)

| Function | Parameters | Returns | Decorators | Docstring |
| --- | --- | --- | --- | --- |
| `from_mapping` | data: dict[str, Any] | `'TaskDefinition'` | `classmethod` | — |
| `timeout` |  | `float | None` | `property` | — |
| `retries` |  | `int` | `property` | — |
| `with_timeout` | timeout: float | None | `'TaskDefinition'` | — | — |
| `to_dict` |  | `dict[str, Any]` | — | — |
| `load_tasks` |  | `dict[str, TaskDefinition]` | — | — |
| `list_tasks` |  | `list[TaskDefinition]` | — | — |
| `get_task` | task_name: str | `TaskDefinition` | — | — |
| `run_task` | task_name: str | `TaskResult` | — | — |
| `show_status` |  | `dict[str, dict[str, Any]]` | — | — |
| `build_parser` |  | `argparse.ArgumentParser` | — | — |
| `main` | argv: list[str] | None | `int` | — | — |

### CLI subcommands

- `list-tasks`
- `run-task`
- `show-status`

### Example usage

```bash
python -m src.openclaw_orchestration.task_runner list-tasks
```
```bash
python -m src.openclaw_orchestration.task_runner run-task
```
```bash
python -m src.openclaw_orchestration.task_runner show-status
```
```bash
# Network-aware run
python -m src.openclaw_orchestration.task_runner --help
```
```bash
# Filesystem workflow
python -m src.openclaw_orchestration.task_runner --help
```

## `src.self_improvement.auto_engine`

- **tool_id**: `src/self_improvement/auto_engine.py`
- **Path**: `src/self_improvement/auto_engine.py`
- **Description**: No module docstring available.
- **Risk level**: **high**
- **Capabilities**: General utility automation, Memory and ideation workflows, Model lifecycle management, Monitoring and observability
- **I/O profile**: filesystem, process, structured-data
- **Safety constraints**: filesystem_destructive, filesystem_read, subprocess_execution
- **Decorator signals**: —
- **Dependencies**: ami_parser, auto_memory_cleanup, auto_reflection, batch_image_optimizer, business_dashboard, comfy_auto_quality, comfy_video_pipeline, context_split, conversation_extractor, doc_generator, error_learning, exif_date_normalizer, face_clustering, face_swap_batch, furniture_sales_database.category_detector, furniture_sales_database.import_sales, furniture_sales_database.query_sales, goal_decomposer, health_dashboard, image_format_migrator, marketplace_dashboard, media_tool, memory_analytics, memory_cleanup, nightly_pipeline, obsidian_dashboard, obsidian_link_checker, obsidian_researcher, ollama_batch, ollama_batch_download, ollama_benchmark, ollama_bridge, ollama_manifest_fix, ollama_model_manager, ollama_monitor, ollama_queue_monitor, optimize_context, photo_archive_report, photo_deduplication, proactive_scout, process_images, queue_manager, run_task, src.coordination.cross_bot_sync, src.dreams.dream_tracker, src.ideation.idea_pipeline, src.monitoring.session_monitor, src.openclaw_orchestration.task_runner, src.skills.proactive_watcher, sync_obsidian, telegram_sender, tests.test_auto_engine, tests.test_auto_reflection, tests.test_context_split, tests.test_cross_bot_sync, tests.test_doc_generator, tests.test_dream_tracker, tests.test_error_learning, tests.test_face_clustering, tests.test_idea_pipeline, tests.test_image_format_migrator, tests.test_memory_analytics, tests.test_memory_cleanup, tests.test_obsidian_link_checker, tests.test_ollama_batch, tests.test_ollama_benchmark, tests.test_ollama_manifest_fix, tests.test_ollama_model_manager, tests.test_ollama_monitor, tests.test_optimize_context, tests.test_photo_deduplication, tests.test_proactive_scout, tests.test_proactive_watcher, tests.test_sync_obsidian, tests.test_task_runner, tests.test_telegram_sender, tests.test_tool_discovery, tool_discovery, video_thumbnail_generator, yandex_metrika

### Public functions (signatures & docstrings)

| Function | Parameters | Returns | Decorators | Docstring |
| --- | --- | --- | --- | --- |
| `colorize` | text: str, color: str, enabled: bool | `str` | — | — |
| `as_dict` |  | `Dict[str, Any]` | — | — |
| `as_dict` |  | `Dict[str, Any]` | — | — |
| `run_health_checks` |  | `List[CheckResult]` | — | — |
| `check_gpu_health` |  | `CheckResult` | — | — |
| `check_ollama_status` |  | `CheckResult` | — | — |
| `check_disk_space` |  | `CheckResult` | — | — |
| `check_memory_usage` |  | `CheckResult` | — | — |
| `log_warning` | message: str | `ImprovementAction` | — | — |
| `clear_temp_files` |  | `ImprovementAction` | — | — |
| `restart_ollama` |  | `ImprovementAction` | — | — |
| `auto_fix` |  | `List[ImprovementAction]` | — | — |
| … | _7 more_ | | | |

### CLI subcommands

- _No argparse subcommands discovered_

### Example usage

```bash
python -m src.self_improvement.auto_engine
```
```bash
# Filesystem workflow
python -m src.self_improvement.auto_engine --help
```

## `src.skills.proactive_watcher`

- **tool_id**: `src/skills/proactive_watcher.py`
- **Path**: `src/skills/proactive_watcher.py`
- **Description**: No module docstring available.
- **Risk level**: **high**
- **Capabilities**: Filesystem-oriented API surface, General utility automation, Task orchestration
- **I/O profile**: filesystem, process, structured-data
- **Safety constraints**: filesystem_read, subprocess_execution
- **Decorator signals**: —
- **Dependencies**: ami_parser, auto_memory_cleanup, auto_reflection, batch_image_optimizer, business_dashboard, comfy_auto_quality, comfy_video_pipeline, context_split, conversation_extractor, doc_generator, error_learning, exif_date_normalizer, face_clustering, face_swap_batch, furniture_sales_database.import_sales, furniture_sales_database.query_sales, goal_decomposer, health_dashboard, image_format_migrator, marketplace_dashboard, media_tool, memory_analytics, memory_cleanup, nightly_pipeline, obsidian_dashboard, obsidian_link_checker, obsidian_researcher, ollama_batch, ollama_batch_download, ollama_benchmark, ollama_bridge, ollama_manifest_fix, ollama_model_manager, ollama_monitor, ollama_queue_monitor, optimize_context, photo_archive_report, photo_deduplication, proactive_scout, process_images, queue_manager, run_task, src.coordination.cross_bot_sync, src.dreams.dream_tracker, src.ideation.idea_pipeline, src.monitoring.session_monitor, src.openclaw_orchestration.task_runner, src.self_improvement.auto_engine, sync_obsidian, telegram_sender, tests.test_auto_engine, tests.test_auto_reflection, tests.test_context_split, tests.test_cross_bot_sync, tests.test_doc_generator, tests.test_dream_tracker, tests.test_error_learning, tests.test_face_clustering, tests.test_idea_pipeline, tests.test_image_format_migrator, tests.test_memory_analytics, tests.test_memory_cleanup, tests.test_obsidian_link_checker, tests.test_ollama_batch, tests.test_ollama_benchmark, tests.test_ollama_manifest_fix, tests.test_ollama_model_manager, tests.test_ollama_monitor, tests.test_optimize_context, tests.test_photo_deduplication, tests.test_proactive_scout, tests.test_proactive_watcher, tests.test_sync_obsidian, tests.test_task_runner, tests.test_telegram_sender, tests.test_tool_discovery, tool_discovery, video_thumbnail_generator, yandex_metrika

### Public functions (signatures & docstrings)

| Function | Parameters | Returns | Decorators | Docstring |
| --- | --- | --- | --- | --- |
| `add_location` | path: Path | `None` | — | — |
| `add_file` | path: Path | `None` | — | — |
| `has_docs` |  | `bool` | `property` | — |
| `has_tests` |  | `bool` | `property` | — |
| `python_files` |  | `list[Path]` | `property` | — |
| `scan_skills` |  | `dict[str, SkillRecord]` | — | — |
| `analyze_usage` |  | `dict[str, SkillRecord]` | — | — |
| `analyze_errors` |  | `dict[str, SkillRecord]` | — | — |
| `build_suggestions` |  | `dict[str, SkillRecord]` | — | — |
| `write_report` |  | `Path` | — | — |
| `render_report` |  | `str` | — | — |
| `format_scan_output` |  | `str` | — | — |
| … | _5 more_ | | | |

### CLI subcommands

- _No argparse subcommands discovered_

### Example usage

```bash
python -m src.skills.proactive_watcher
```
```bash
# Filesystem workflow
python -m src.skills.proactive_watcher --help
```

## `tests.test_auto_engine`

- **tool_id**: `tests/test_auto_engine.py`
- **Path**: `tests/test_auto_engine.py`
- **Description**: No module docstring available.
- **Risk level**: **high**
- **Capabilities**: Model lifecycle management
- **I/O profile**: filesystem, process, structured-data
- **Safety constraints**: filesystem_read, subprocess_execution
- **Decorator signals**: —
- **Dependencies**: auto_memory_cleanup, auto_reflection, batch_image_optimizer, business_dashboard, comfy_auto_quality, comfy_video_pipeline, context_split, conversation_extractor, error_learning, exif_date_normalizer, face_clustering, face_swap_batch, furniture_sales_database.import_sales, goal_decomposer, health_dashboard, image_format_migrator, media_tool, memory_analytics, memory_cleanup, nightly_pipeline, obsidian_dashboard, obsidian_link_checker, obsidian_researcher, ollama_batch, ollama_batch_download, ollama_benchmark, ollama_bridge, ollama_manifest_fix, ollama_model_manager, ollama_monitor, ollama_queue_monitor, optimize_context, photo_archive_report, photo_deduplication, proactive_scout, queue_manager, src.coordination.cross_bot_sync, src.dreams.dream_tracker, src.ideation.idea_pipeline, src.monitoring.session_monitor, src.openclaw_orchestration.task_runner, src.self_improvement.auto_engine, src.skills.proactive_watcher, sync_obsidian, telegram_sender, tests.test_auto_reflection, tests.test_context_split, tests.test_cross_bot_sync, tests.test_doc_generator, tests.test_dream_tracker, tests.test_error_learning, tests.test_face_clustering, tests.test_idea_pipeline, tests.test_image_format_migrator, tests.test_memory_analytics, tests.test_memory_cleanup, tests.test_obsidian_link_checker, tests.test_ollama_batch, tests.test_ollama_benchmark, tests.test_ollama_manifest_fix, tests.test_ollama_model_manager, tests.test_ollama_monitor, tests.test_optimize_context, tests.test_photo_deduplication, tests.test_proactive_scout, tests.test_proactive_watcher, tests.test_sync_obsidian, tests.test_task_runner, tests.test_telegram_sender, tests.test_tool_discovery, tool_discovery, video_thumbnail_generator, yandex_metrika

### Public functions (signatures & docstrings)

| Function | Parameters | Returns | Decorators | Docstring |
| --- | --- | --- | --- | --- |
| `completed` | returncode, stdout, stderr | `—` | — | — |
| `setUp` |  | `—` | — | — |
| `make_engine` | runner | `—` | — | — |
| `test_log_warning_writes_daily_json` |  | `—` | — | — |
| `test_clear_temp_files_only_removes_owned_paths` |  | `—` | — | — |
| `test_auto_fix_restarts_ollama_and_logs_warnings` |  | `—` | — | — |
| `runner` | command | `—` | — | — |
| `test_generate_weekly_digest_summarizes_recent_entries` |  | `—` | — | — |
| `strftime` | fmt | `—` | — | — |
| `date` |  | `—` | — | — |

### CLI subcommands

- _No argparse subcommands discovered_

### Example usage

```bash
python -m tests.test_auto_engine
```
```bash
# Filesystem workflow
python -m tests.test_auto_engine --help
```

## `tests.test_auto_reflection`

- **tool_id**: `tests/test_auto_reflection.py`
- **Path**: `tests/test_auto_reflection.py`
- **Description**: Tests for scripts.auto_reflection.
- **Risk level**: **medium**
- **Capabilities**: Deduplication and similarity, General utility automation, Network-oriented API surface
- **I/O profile**: filesystem, network, structured-data
- **Safety constraints**: filesystem_read, network_egress
- **Decorator signals**: —
- **Dependencies**: auto_memory_cleanup, auto_reflection, batch_image_optimizer, business_dashboard, comfy_auto_quality, comfy_video_pipeline, context_split, conversation_extractor, doc_generator, error_learning, exif_date_normalizer, face_clustering, face_swap_batch, furniture_sales_database.import_sales, goal_decomposer, health_dashboard, image_format_migrator, media_tool, memory_analytics, memory_cleanup, nightly_pipeline, obsidian_dashboard, obsidian_link_checker, obsidian_researcher, ollama_batch, ollama_batch_download, ollama_benchmark, ollama_bridge, ollama_manifest_fix, ollama_model_manager, ollama_monitor, ollama_queue_monitor, optimize_context, photo_archive_report, photo_deduplication, proactive_scout, process_images, queue_manager, run_task, src.coordination.cross_bot_sync, src.dreams.dream_tracker, src.ideation.idea_pipeline, src.monitoring.session_monitor, src.openclaw_orchestration.task_runner, src.self_improvement.auto_engine, src.skills.proactive_watcher, sync_obsidian, telegram_sender, tests.test_auto_engine, tests.test_context_split, tests.test_cross_bot_sync, tests.test_doc_generator, tests.test_dream_tracker, tests.test_error_learning, tests.test_face_clustering, tests.test_idea_pipeline, tests.test_image_format_migrator, tests.test_memory_analytics, tests.test_memory_cleanup, tests.test_obsidian_link_checker, tests.test_ollama_batch, tests.test_ollama_benchmark, tests.test_ollama_manifest_fix, tests.test_ollama_model_manager, tests.test_ollama_monitor, tests.test_optimize_context, tests.test_photo_deduplication, tests.test_proactive_scout, tests.test_proactive_watcher, tests.test_sync_obsidian, tests.test_task_runner, tests.test_telegram_sender, tests.test_tool_discovery, tool_discovery, video_thumbnail_generator, yandex_metrika

### Public functions (signatures & docstrings)

| Function | Parameters | Returns | Decorators | Docstring |
| --- | --- | --- | --- | --- |
| `test_dedupe_insights_collapses_near_duplicates` |  | `—` | — | — |
| `test_extract_from_json_walks_nested_errors` |  | `—` | — | — |
| `test_run_reflection_writes_learnings_and_skips_writes_on_dry_run` |  | `—` | — | — |
| `test_post_webhook_uses_json_post` |  | `—` | — | — |
| `read` |  | `—` | — | — |
| `fake_urlopen` | req, timeout | `—` | — | — |
| `test_main_stderr_posts_log_without_network` |  | `—` | — | — |

### CLI subcommands

- _No argparse subcommands discovered_

### Example usage

```bash
python -m tests.test_auto_reflection
```
```bash
# Network-aware run
python -m tests.test_auto_reflection --help
```
```bash
# Filesystem workflow
python -m tests.test_auto_reflection --help
```

## `tests.test_context_split`

- **tool_id**: `tests/test_context_split.py`
- **Path**: `tests/test_context_split.py`
- **Description**: No module docstring available.
- **Risk level**: **low**
- **Capabilities**: Context shaping and prompt preparation, General utility automation, Network-oriented API surface
- **I/O profile**: filesystem, structured-data
- **Safety constraints**: filesystem_read
- **Decorator signals**: —
- **Dependencies**: ami_parser, auto_memory_cleanup, auto_reflection, batch_image_optimizer, business_dashboard, comfy_auto_quality, comfy_video_pipeline, context_split, conversation_extractor, doc_generator, error_learning, exif_date_normalizer, face_clustering, face_swap_batch, furniture_sales_database.import_sales, furniture_sales_database.query_sales, goal_decomposer, health_dashboard, image_format_migrator, marketplace_dashboard, media_tool, memory_analytics, memory_cleanup, nightly_pipeline, obsidian_dashboard, obsidian_link_checker, obsidian_researcher, ollama_batch, ollama_batch_download, ollama_benchmark, ollama_bridge, ollama_manifest_fix, ollama_model_manager, ollama_monitor, ollama_queue_monitor, optimize_context, photo_archive_report, photo_deduplication, proactive_scout, process_images, queue_manager, run_task, src.coordination.cross_bot_sync, src.dreams.dream_tracker, src.ideation.idea_pipeline, src.monitoring.session_monitor, src.openclaw_orchestration.task_runner, src.self_improvement.auto_engine, src.skills.proactive_watcher, sync_obsidian, telegram_sender, tests.test_auto_engine, tests.test_auto_reflection, tests.test_cross_bot_sync, tests.test_doc_generator, tests.test_dream_tracker, tests.test_error_learning, tests.test_face_clustering, tests.test_idea_pipeline, tests.test_image_format_migrator, tests.test_memory_analytics, tests.test_memory_cleanup, tests.test_obsidian_link_checker, tests.test_ollama_batch, tests.test_ollama_benchmark, tests.test_ollama_manifest_fix, tests.test_ollama_model_manager, tests.test_ollama_monitor, tests.test_optimize_context, tests.test_photo_deduplication, tests.test_proactive_scout, tests.test_proactive_watcher, tests.test_sync_obsidian, tests.test_task_runner, tests.test_telegram_sender, tests.test_tool_discovery, tool_discovery, video_thumbnail_generator, yandex_metrika

### Public functions (signatures & docstrings)

| Function | Parameters | Returns | Decorators | Docstring |
| --- | --- | --- | --- | --- |
| `fake_token_counter` | text: str | `int` | — | — |
| `test_split_context_skips_small_inputs` |  | `None` | — | — |
| `test_split_context_uses_headers_and_overlap` |  | `None` | — | — |
| `test_split_context_recursively_splits_large_chunk` |  | `None` | — | — |
| `test_query_with_retry_retries_once` |  | `None` | — | — |
| `flaky_requester` | messages: context_split.MessageList, timeout: int | `str` | — | — |
| `test_split_and_query_context_direct_mode` |  | `None` | — | — |
| `requester` | messages: context_split.MessageList, timeout: int | `str` | — | — |
| `test_split_and_query_context_parallel_and_synthesis` |  | `None` | — | — |
| `requester` | messages: context_split.MessageList, timeout: int | `str` | — | — |
| `test_extract_message_text_supports_text_parts` |  | `None` | — | — |
| `test_load_context_reads_file` |  | `None` | — | — |
| … | _2 more_ | | | |

### CLI subcommands

- _No argparse subcommands discovered_

### Example usage

```bash
python -m tests.test_context_split
```
```bash
# Filesystem workflow
python -m tests.test_context_split --help
```

## `tests.test_cross_bot_sync`

- **tool_id**: `tests/test_cross_bot_sync.py`
- **Path**: `tests/test_cross_bot_sync.py`
- **Description**: No module docstring available.
- **Risk level**: **high**
- **Capabilities**: Data synchronization, Memory and ideation workflows
- **I/O profile**: filesystem, process, structured-data
- **Safety constraints**: filesystem_read, subprocess_execution
- **Decorator signals**: —
- **Dependencies**: auto_memory_cleanup, auto_reflection, batch_image_optimizer, comfy_auto_quality, comfy_video_pipeline, context_split, conversation_extractor, doc_generator, error_learning, face_clustering, goal_decomposer, health_dashboard, image_format_migrator, media_tool, memory_analytics, memory_cleanup, nightly_pipeline, obsidian_dashboard, obsidian_link_checker, obsidian_researcher, ollama_batch, ollama_batch_download, ollama_benchmark, ollama_bridge, ollama_manifest_fix, ollama_model_manager, ollama_monitor, ollama_queue_monitor, optimize_context, photo_archive_report, photo_deduplication, proactive_scout, process_images, queue_manager, run_task, src.coordination.cross_bot_sync, src.dreams.dream_tracker, src.ideation.idea_pipeline, src.monitoring.session_monitor, src.openclaw_orchestration.task_runner, src.self_improvement.auto_engine, src.skills.proactive_watcher, sync_obsidian, telegram_sender, tests.test_auto_engine, tests.test_auto_reflection, tests.test_context_split, tests.test_doc_generator, tests.test_dream_tracker, tests.test_error_learning, tests.test_face_clustering, tests.test_idea_pipeline, tests.test_image_format_migrator, tests.test_memory_analytics, tests.test_memory_cleanup, tests.test_obsidian_link_checker, tests.test_ollama_batch, tests.test_ollama_benchmark, tests.test_ollama_manifest_fix, tests.test_ollama_model_manager, tests.test_ollama_monitor, tests.test_optimize_context, tests.test_photo_deduplication, tests.test_proactive_scout, tests.test_proactive_watcher, tests.test_sync_obsidian, tests.test_task_runner, tests.test_telegram_sender, tests.test_tool_discovery, tool_discovery, video_thumbnail_generator, yandex_metrika

### Public functions (signatures & docstrings)

| Function | Parameters | Returns | Decorators | Docstring |
| --- | --- | --- | --- | --- |
| `setUp` |  | `None` | — | — |
| `tearDown` |  | `None` | — | — |
| `test_sync_memory_merges_entries_across_bots` |  | `None` | — | — |
| `test_sync_memory_prefers_newer_entry_on_conflict` |  | `None` | — | — |
| `test_task_handoff_prevents_duplicate_claims` |  | `None` | — | — |
| `test_write_status_persists_shared_status_file` |  | `None` | — | — |
| `test_unlock_removes_lock_file` |  | `None` | — | — |
| `test_lock_timeout_raises_when_lock_is_held` |  | `None` | — | — |
| `test_cli_status_command_writes_status` |  | `None` | — | — |

### CLI subcommands

- _No argparse subcommands discovered_

### Example usage

```bash
python -m tests.test_cross_bot_sync
```
```bash
# Filesystem workflow
python -m tests.test_cross_bot_sync --help
```

## `tests.test_doc_generator`

- **tool_id**: `tests/test_doc_generator.py`
- **Path**: `tests/test_doc_generator.py`
- **Description**: No module docstring available.
- **Risk level**: **medium**
- **Capabilities**: Filesystem-oriented API surface, General utility automation
- **I/O profile**: filesystem, process
- **Safety constraints**: filesystem_read, subprocess_execution
- **Decorator signals**: —
- **Dependencies**: auto_memory_cleanup, auto_reflection, batch_image_optimizer, comfy_video_pipeline, context_split, conversation_extractor, doc_generator, error_learning, face_clustering, goal_decomposer, health_dashboard, image_format_migrator, media_tool, memory_analytics, memory_cleanup, nightly_pipeline, obsidian_dashboard, obsidian_link_checker, obsidian_researcher, ollama_batch, ollama_batch_download, ollama_benchmark, ollama_bridge, ollama_manifest_fix, ollama_model_manager, ollama_monitor, ollama_queue_monitor, optimize_context, proactive_scout, process_images, queue_manager, run_task, src.coordination.cross_bot_sync, src.dreams.dream_tracker, src.ideation.idea_pipeline, src.monitoring.session_monitor, src.openclaw_orchestration.task_runner, src.self_improvement.auto_engine, src.skills.proactive_watcher, sync_obsidian, telegram_sender, tests.test_auto_engine, tests.test_auto_reflection, tests.test_context_split, tests.test_cross_bot_sync, tests.test_dream_tracker, tests.test_error_learning, tests.test_face_clustering, tests.test_idea_pipeline, tests.test_image_format_migrator, tests.test_memory_analytics, tests.test_memory_cleanup, tests.test_obsidian_link_checker, tests.test_ollama_batch, tests.test_ollama_benchmark, tests.test_ollama_manifest_fix, tests.test_ollama_model_manager, tests.test_ollama_monitor, tests.test_optimize_context, tests.test_photo_deduplication, tests.test_proactive_scout, tests.test_proactive_watcher, tests.test_sync_obsidian, tests.test_task_runner, tests.test_telegram_sender, tests.test_tool_discovery, tool_discovery, video_thumbnail_generator

### Public functions (signatures & docstrings)

| Function | Parameters | Returns | Decorators | Docstring |
| --- | --- | --- | --- | --- |
| `write_file` | path: Path, content: str | `None` | — | — |
| `sample_script` |  | `str` | — | — |
| `test_parse_script_extracts_docstrings_arguments_and_exit_codes` |  | `None` | — | — |
| `test_generate_docs_writes_markdown_and_preserves_existing_header` |  | `None` | — | — |
| `test_check_mode_detects_stale_output_without_writing` |  | `None` | — | — |
| `test_cli_manual_and_check_modes` |  | `None` | — | — |

### CLI subcommands

- _No argparse subcommands discovered_

### Example usage

```bash
python -m tests.test_doc_generator
```
```bash
# Filesystem workflow
python -m tests.test_doc_generator --help
```

## `tests.test_dream_tracker`

- **tool_id**: `tests/test_dream_tracker.py`
- **Path**: `tests/test_dream_tracker.py`
- **Description**: No module docstring available.
- **Risk level**: **high**
- **Capabilities**: Memory and ideation workflows
- **I/O profile**: filesystem, process, structured-data
- **Safety constraints**: filesystem_read, subprocess_execution
- **Decorator signals**: —
- **Dependencies**: auto_memory_cleanup, auto_reflection, batch_image_optimizer, comfy_video_pipeline, context_split, conversation_extractor, doc_generator, error_learning, face_clustering, goal_decomposer, health_dashboard, image_format_migrator, media_tool, memory_analytics, memory_cleanup, nightly_pipeline, obsidian_dashboard, obsidian_link_checker, obsidian_researcher, ollama_batch, ollama_batch_download, ollama_benchmark, ollama_bridge, ollama_manifest_fix, ollama_model_manager, ollama_monitor, ollama_queue_monitor, optimize_context, photo_archive_report, photo_deduplication, proactive_scout, process_images, queue_manager, run_task, src.coordination.cross_bot_sync, src.dreams.dream_tracker, src.ideation.idea_pipeline, src.monitoring.session_monitor, src.openclaw_orchestration.task_runner, src.self_improvement.auto_engine, src.skills.proactive_watcher, sync_obsidian, telegram_sender, tests.test_auto_engine, tests.test_auto_reflection, tests.test_context_split, tests.test_cross_bot_sync, tests.test_doc_generator, tests.test_error_learning, tests.test_face_clustering, tests.test_idea_pipeline, tests.test_image_format_migrator, tests.test_memory_analytics, tests.test_memory_cleanup, tests.test_obsidian_link_checker, tests.test_ollama_batch, tests.test_ollama_benchmark, tests.test_ollama_manifest_fix, tests.test_ollama_model_manager, tests.test_ollama_monitor, tests.test_optimize_context, tests.test_photo_deduplication, tests.test_proactive_scout, tests.test_proactive_watcher, tests.test_sync_obsidian, tests.test_task_runner, tests.test_telegram_sender, tests.test_tool_discovery, tool_discovery, video_thumbnail_generator, yandex_metrika

### Public functions (signatures & docstrings)

| Function | Parameters | Returns | Decorators | Docstring |
| --- | --- | --- | --- | --- |
| `make_repo` |  | `—` | — | — |
| `test_create_dream_builds_index_and_markdown` |  | `—` | — | — |
| `test_auto_generation_uses_system_and_conversation_patterns` |  | `—` | — | — |
| `test_research_and_implement_move_dream_through_workflow` |  | `—` | — | — |
| `test_cli_create_and_status_commands_work` |  | `—` | — | — |

### CLI subcommands

- _No argparse subcommands discovered_

### Example usage

```bash
python -m tests.test_dream_tracker
```
```bash
# Filesystem workflow
python -m tests.test_dream_tracker --help
```

## `tests.test_error_learning`

- **tool_id**: `tests/test_error_learning.py`
- **Path**: `tests/test_error_learning.py`
- **Description**: No module docstring available.
- **Risk level**: **medium**
- **Capabilities**: Deduplication and similarity, General utility automation, Media processing
- **I/O profile**: filesystem, structured-data
- **Safety constraints**: filesystem_read
- **Decorator signals**: —
- **Dependencies**: auto_memory_cleanup, auto_reflection, batch_image_optimizer, business_dashboard, comfy_auto_quality, comfy_video_pipeline, context_split, conversation_extractor, doc_generator, error_learning, exif_date_normalizer, face_clustering, face_swap_batch, furniture_sales_database.import_sales, goal_decomposer, health_dashboard, image_format_migrator, media_tool, memory_analytics, memory_cleanup, nightly_pipeline, obsidian_dashboard, obsidian_link_checker, obsidian_researcher, ollama_batch, ollama_batch_download, ollama_benchmark, ollama_bridge, ollama_manifest_fix, ollama_model_manager, ollama_monitor, ollama_queue_monitor, optimize_context, photo_archive_report, photo_deduplication, proactive_scout, process_images, queue_manager, run_task, src.coordination.cross_bot_sync, src.dreams.dream_tracker, src.ideation.idea_pipeline, src.monitoring.session_monitor, src.openclaw_orchestration.task_runner, src.self_improvement.auto_engine, src.skills.proactive_watcher, sync_obsidian, telegram_sender, tests.test_auto_engine, tests.test_auto_reflection, tests.test_context_split, tests.test_cross_bot_sync, tests.test_doc_generator, tests.test_dream_tracker, tests.test_face_clustering, tests.test_idea_pipeline, tests.test_image_format_migrator, tests.test_memory_analytics, tests.test_memory_cleanup, tests.test_obsidian_link_checker, tests.test_ollama_batch, tests.test_ollama_benchmark, tests.test_ollama_manifest_fix, tests.test_ollama_model_manager, tests.test_ollama_monitor, tests.test_optimize_context, tests.test_photo_deduplication, tests.test_proactive_scout, tests.test_proactive_watcher, tests.test_sync_obsidian, tests.test_task_runner, tests.test_telegram_sender, tests.test_tool_discovery, tool_discovery, video_thumbnail_generator, yandex_metrika

### Public functions (signatures & docstrings)

| Function | Parameters | Returns | Decorators | Docstring |
| --- | --- | --- | --- | --- |
| `setUp` |  | `None` | — | — |
| `tearDown` |  | `None` | — | — |
| `read_store` |  | `dict[str, object]` | — | — |
| `run_cli` |  | `tuple[int, str, str]` | — | — |
| `test_add_command_persists_schema_and_deduplicates` |  | `None` | — | — |
| `test_list_command_outputs_colorized_entries_and_status` |  | `None` | — | — |
| `test_stats_and_search_surface_relevant_entries` |  | `None` | — | — |

### CLI subcommands

- _No argparse subcommands discovered_

### Example usage

```bash
python -m tests.test_error_learning
```
```bash
# Filesystem workflow
python -m tests.test_error_learning --help
```

## `tests.test_face_clustering`

- **tool_id**: `tests/test_face_clustering.py`
- **Path**: `tests/test_face_clustering.py`
- **Description**: No module docstring available.
- **Risk level**: **low**
- **Capabilities**: Filesystem-oriented API surface, Media processing
- **I/O profile**: filesystem, structured-data
- **Safety constraints**: filesystem_read
- **Decorator signals**: —
- **Dependencies**: auto_memory_cleanup, auto_reflection, batch_image_optimizer, business_dashboard, comfy_auto_quality, comfy_video_pipeline, context_split, conversation_extractor, doc_generator, error_learning, exif_date_normalizer, face_clustering, face_swap_batch, furniture_sales_database.import_sales, goal_decomposer, health_dashboard, image_format_migrator, media_tool, memory_analytics, memory_cleanup, nightly_pipeline, obsidian_dashboard, obsidian_link_checker, obsidian_researcher, ollama_batch, ollama_batch_download, ollama_benchmark, ollama_bridge, ollama_manifest_fix, ollama_model_manager, ollama_monitor, ollama_queue_monitor, optimize_context, photo_archive_report, photo_deduplication, proactive_scout, process_images, queue_manager, run_task, src.coordination.cross_bot_sync, src.dreams.dream_tracker, src.ideation.idea_pipeline, src.monitoring.session_monitor, src.openclaw_orchestration.task_runner, src.self_improvement.auto_engine, src.skills.proactive_watcher, sync_obsidian, telegram_sender, tests.test_auto_engine, tests.test_auto_reflection, tests.test_context_split, tests.test_cross_bot_sync, tests.test_doc_generator, tests.test_dream_tracker, tests.test_error_learning, tests.test_idea_pipeline, tests.test_image_format_migrator, tests.test_memory_analytics, tests.test_memory_cleanup, tests.test_obsidian_link_checker, tests.test_ollama_batch, tests.test_ollama_benchmark, tests.test_ollama_manifest_fix, tests.test_ollama_model_manager, tests.test_ollama_monitor, tests.test_optimize_context, tests.test_photo_deduplication, tests.test_proactive_scout, tests.test_proactive_watcher, tests.test_sync_obsidian, tests.test_task_runner, tests.test_telegram_sender, tests.test_tool_discovery, tool_discovery, video_thumbnail_generator, yandex_metrika

### Public functions (signatures & docstrings)

| Function | Parameters | Returns | Decorators | Docstring |
| --- | --- | --- | --- | --- |
| `encode` | image_path: Path | `list[np.ndarray]` | — | — |
| `setUp` |  | `None` | — | — |
| `tearDown` |  | `None` | — | — |
| `make_image` | relative_path: str | `Path` | — | — |
| `test_cluster_count_auto_assign_and_json_export` |  | `None` | — | — |
| `test_cached_encodings_skip_reprocessing` |  | `None` | — | — |
| `test_export_folders_creates_symlinks` |  | `None` | — | — |

### CLI subcommands

- _No argparse subcommands discovered_

### Example usage

```bash
python -m tests.test_face_clustering
```
```bash
# Filesystem workflow
python -m tests.test_face_clustering --help
```

## `tests.test_idea_pipeline`

- **tool_id**: `tests/test_idea_pipeline.py`
- **Path**: `tests/test_idea_pipeline.py`
- **Description**: No module docstring available.
- **Risk level**: **medium**
- **Capabilities**: General utility automation, Memory and ideation workflows, Task orchestration
- **I/O profile**: filesystem, structured-data
- **Safety constraints**: filesystem_read
- **Decorator signals**: —
- **Dependencies**: auto_memory_cleanup, auto_reflection, batch_image_optimizer, business_dashboard, comfy_auto_quality, comfy_video_pipeline, context_split, conversation_extractor, doc_generator, error_learning, exif_date_normalizer, face_clustering, face_swap_batch, furniture_sales_database.import_sales, goal_decomposer, health_dashboard, image_format_migrator, media_tool, memory_analytics, memory_cleanup, nightly_pipeline, obsidian_dashboard, obsidian_link_checker, obsidian_researcher, ollama_batch, ollama_benchmark, ollama_bridge, ollama_manifest_fix, ollama_model_manager, ollama_monitor, ollama_queue_monitor, optimize_context, photo_archive_report, photo_deduplication, proactive_scout, queue_manager, src.coordination.cross_bot_sync, src.dreams.dream_tracker, src.ideation.idea_pipeline, src.monitoring.session_monitor, src.openclaw_orchestration.task_runner, src.self_improvement.auto_engine, src.skills.proactive_watcher, sync_obsidian, telegram_sender, tests.test_auto_engine, tests.test_auto_reflection, tests.test_context_split, tests.test_cross_bot_sync, tests.test_doc_generator, tests.test_dream_tracker, tests.test_error_learning, tests.test_face_clustering, tests.test_image_format_migrator, tests.test_memory_analytics, tests.test_memory_cleanup, tests.test_obsidian_link_checker, tests.test_ollama_batch, tests.test_ollama_benchmark, tests.test_ollama_manifest_fix, tests.test_ollama_model_manager, tests.test_ollama_monitor, tests.test_optimize_context, tests.test_photo_deduplication, tests.test_proactive_scout, tests.test_proactive_watcher, tests.test_sync_obsidian, tests.test_task_runner, tests.test_telegram_sender, tests.test_tool_discovery, tool_discovery, video_thumbnail_generator, yandex_metrika

### Public functions (signatures & docstrings)

| Function | Parameters | Returns | Decorators | Docstring |
| --- | --- | --- | --- | --- |
| `setUp` |  | `None` | — | — |
| `tearDown` |  | `None` | — | — |
| `test_research_phase_creates_daily_log` |  | `None` | — | — |
| `test_later_phase_backfills_prerequisites` |  | `None` | — | — |
| `test_invalid_phase_returns_error_code` |  | `None` | — | — |
| `test_cli_prints_json_for_successful_run` |  | `None` | — | — |

### CLI subcommands

- _No argparse subcommands discovered_

### Example usage

```bash
python -m tests.test_idea_pipeline
```
```bash
# Filesystem workflow
python -m tests.test_idea_pipeline --help
```

## `tests.test_image_format_migrator`

- **tool_id**: `tests/test_image_format_migrator.py`
- **Path**: `tests/test_image_format_migrator.py`
- **Description**: No module docstring available.
- **Risk level**: **low**
- **Capabilities**: Media processing
- **I/O profile**: filesystem
- **Safety constraints**: filesystem_read
- **Decorator signals**: —
- **Dependencies**: auto_reflection, batch_image_optimizer, business_dashboard, comfy_auto_quality, comfy_video_pipeline, context_split, conversation_extractor, doc_generator, error_learning, exif_date_normalizer, face_clustering, face_swap_batch, furniture_sales_database.import_sales, image_format_migrator, media_tool, memory_analytics, memory_cleanup, obsidian_dashboard, obsidian_link_checker, ollama_batch, ollama_benchmark, ollama_manifest_fix, ollama_model_manager, ollama_monitor, optimize_context, photo_archive_report, photo_deduplication, proactive_scout, process_images, run_task, src.coordination.cross_bot_sync, src.ideation.idea_pipeline, src.openclaw_orchestration.task_runner, src.self_improvement.auto_engine, src.skills.proactive_watcher, sync_obsidian, telegram_sender, tests.test_auto_engine, tests.test_auto_reflection, tests.test_context_split, tests.test_cross_bot_sync, tests.test_doc_generator, tests.test_dream_tracker, tests.test_error_learning, tests.test_face_clustering, tests.test_idea_pipeline, tests.test_memory_analytics, tests.test_memory_cleanup, tests.test_obsidian_link_checker, tests.test_ollama_batch, tests.test_ollama_benchmark, tests.test_ollama_manifest_fix, tests.test_ollama_model_manager, tests.test_ollama_monitor, tests.test_optimize_context, tests.test_photo_deduplication, tests.test_proactive_scout, tests.test_proactive_watcher, tests.test_sync_obsidian, tests.test_task_runner, tests.test_telegram_sender, tests.test_tool_discovery, tool_discovery, video_thumbnail_generator

### Public functions (signatures & docstrings)

| Function | Parameters | Returns | Decorators | Docstring |
| --- | --- | --- | --- | --- |
| `setUp` |  | `None` | — | — |
| `tearDown` |  | `None` | — | — |
| `test_single_png_converts_next_to_source_by_default` |  | `None` | — | — |
| `test_scan_uses_default_output_suffix_directory` |  | `None` | — | — |
| `test_dry_run_creates_no_files` |  | `None` | — | — |
| `test_overwrite_replaces_source_with_jpeg` |  | `None` | — | — |
| `test_quality_is_applied_for_jpeg_compression` |  | `None` | — | — |
| `test_mutually_exclusive_mode_flags` |  | `None` | — | — |

### CLI subcommands

- _No argparse subcommands discovered_

### Example usage

```bash
python -m tests.test_image_format_migrator
```
```bash
# Filesystem workflow
python -m tests.test_image_format_migrator --help
```

## `tests.test_memory_analytics`

- **tool_id**: `tests/test_memory_analytics.py`
- **Path**: `tests/test_memory_analytics.py`
- **Description**: No module docstring available.
- **Risk level**: **medium**
- **Capabilities**: Analytics and reporting, Filesystem-oriented API surface, General utility automation, Memory and ideation workflows, Monitoring and observability
- **I/O profile**: filesystem, structured-data
- **Safety constraints**: filesystem_read
- **Decorator signals**: —
- **Dependencies**: auto_memory_cleanup, auto_reflection, batch_image_optimizer, business_dashboard, comfy_auto_quality, comfy_video_pipeline, context_split, conversation_extractor, doc_generator, error_learning, exif_date_normalizer, face_clustering, face_swap_batch, furniture_sales_database.import_sales, goal_decomposer, health_dashboard, image_format_migrator, media_tool, memory_analytics, memory_cleanup, nightly_pipeline, obsidian_dashboard, obsidian_link_checker, obsidian_researcher, ollama_batch, ollama_batch_download, ollama_benchmark, ollama_bridge, ollama_manifest_fix, ollama_model_manager, ollama_monitor, ollama_queue_monitor, optimize_context, photo_archive_report, photo_deduplication, proactive_scout, process_images, queue_manager, run_task, src.coordination.cross_bot_sync, src.dreams.dream_tracker, src.ideation.idea_pipeline, src.monitoring.session_monitor, src.openclaw_orchestration.task_runner, src.self_improvement.auto_engine, src.skills.proactive_watcher, sync_obsidian, telegram_sender, tests.test_auto_engine, tests.test_auto_reflection, tests.test_context_split, tests.test_cross_bot_sync, tests.test_doc_generator, tests.test_dream_tracker, tests.test_error_learning, tests.test_face_clustering, tests.test_idea_pipeline, tests.test_image_format_migrator, tests.test_memory_cleanup, tests.test_obsidian_link_checker, tests.test_ollama_batch, tests.test_ollama_benchmark, tests.test_ollama_manifest_fix, tests.test_ollama_model_manager, tests.test_ollama_monitor, tests.test_optimize_context, tests.test_photo_deduplication, tests.test_proactive_scout, tests.test_proactive_watcher, tests.test_sync_obsidian, tests.test_task_runner, tests.test_telegram_sender, tests.test_tool_discovery, tool_discovery, video_thumbnail_generator, yandex_metrika

### Public functions (signatures & docstrings)

| Function | Parameters | Returns | Decorators | Docstring |
| --- | --- | --- | --- | --- |
| `write_memory` | directory: Path, content: str | `Path` | — | — |
| `test_parse_memory_file_extracts_sections_entries_dates_and_links` |  | `None` | — | — |
| `test_analysis_finds_stale_missing_refs_and_duplicates` |  | `None` | — | — |
| `test_render_markdown_report_contains_health_sections` |  | `None` | — | — |
| `test_main_writes_markdown_and_json_reports` |  | `None` | — | — |

### CLI subcommands

- _No argparse subcommands discovered_

### Example usage

```bash
python -m tests.test_memory_analytics
```
```bash
# Filesystem workflow
python -m tests.test_memory_analytics --help
```

## `tests.test_memory_cleanup`

- **tool_id**: `tests/test_memory_cleanup.py`
- **Path**: `tests/test_memory_cleanup.py`
- **Description**: No module docstring available.
- **Risk level**: **low**
- **Capabilities**: Cleanup and maintenance, Deduplication and similarity, Filesystem-oriented API surface, Memory and ideation workflows
- **I/O profile**: filesystem, structured-data
- **Safety constraints**: filesystem_read
- **Decorator signals**: —
- **Dependencies**: auto_memory_cleanup, auto_reflection, batch_image_optimizer, business_dashboard, comfy_auto_quality, comfy_video_pipeline, context_split, conversation_extractor, doc_generator, error_learning, exif_date_normalizer, face_clustering, face_swap_batch, furniture_sales_database.import_sales, goal_decomposer, health_dashboard, image_format_migrator, media_tool, memory_analytics, memory_cleanup, nightly_pipeline, obsidian_dashboard, obsidian_link_checker, obsidian_researcher, ollama_batch, ollama_batch_download, ollama_benchmark, ollama_bridge, ollama_manifest_fix, ollama_model_manager, ollama_monitor, ollama_queue_monitor, optimize_context, photo_archive_report, photo_deduplication, proactive_scout, process_images, queue_manager, run_task, src.coordination.cross_bot_sync, src.dreams.dream_tracker, src.ideation.idea_pipeline, src.monitoring.session_monitor, src.openclaw_orchestration.task_runner, src.self_improvement.auto_engine, src.skills.proactive_watcher, sync_obsidian, telegram_sender, tests.test_auto_engine, tests.test_auto_reflection, tests.test_context_split, tests.test_cross_bot_sync, tests.test_doc_generator, tests.test_dream_tracker, tests.test_error_learning, tests.test_face_clustering, tests.test_idea_pipeline, tests.test_image_format_migrator, tests.test_memory_analytics, tests.test_obsidian_link_checker, tests.test_ollama_batch, tests.test_ollama_benchmark, tests.test_ollama_manifest_fix, tests.test_ollama_model_manager, tests.test_ollama_monitor, tests.test_optimize_context, tests.test_photo_deduplication, tests.test_proactive_scout, tests.test_proactive_watcher, tests.test_sync_obsidian, tests.test_task_runner, tests.test_telegram_sender, tests.test_tool_discovery, tool_discovery, video_thumbnail_generator, yandex_metrika

### Public functions (signatures & docstrings)

| Function | Parameters | Returns | Decorators | Docstring |
| --- | --- | --- | --- | --- |
| `setUp` |  | `None` | — | — |
| `tearDown` |  | `None` | — | — |
| `write_file` | relative_path: str, content: str | `Path` | — | — |
| `read_file` | relative_path: str | `str` | — | — |
| `test_cleanup_archives_deduplicates_and_compacts` |  | `None` | — | — |
| `test_dry_run_keeps_original_files_unchanged` |  | `None` | — | — |
| `test_cli_returns_success_and_colorized_output` |  | `None` | — | — |

### CLI subcommands

- _No argparse subcommands discovered_

### Example usage

```bash
python -m tests.test_memory_cleanup
```
```bash
# Filesystem workflow
python -m tests.test_memory_cleanup --help
```

## `tests.test_obsidian_link_checker`

- **tool_id**: `tests/test_obsidian_link_checker.py`
- **Path**: `tests/test_obsidian_link_checker.py`
- **Description**: No module docstring available.
- **Risk level**: **low**
- **Capabilities**: Data synchronization
- **I/O profile**: filesystem
- **Safety constraints**: filesystem_read
- **Decorator signals**: —
- **Dependencies**: auto_memory_cleanup, auto_reflection, batch_image_optimizer, comfy_video_pipeline, context_split, conversation_extractor, doc_generator, error_learning, face_clustering, goal_decomposer, health_dashboard, image_format_migrator, media_tool, memory_analytics, memory_cleanup, nightly_pipeline, obsidian_link_checker, obsidian_researcher, ollama_batch, ollama_batch_download, ollama_benchmark, ollama_bridge, ollama_manifest_fix, ollama_model_manager, ollama_monitor, ollama_queue_monitor, optimize_context, proactive_scout, process_images, run_task, src.coordination.cross_bot_sync, src.dreams.dream_tracker, src.ideation.idea_pipeline, src.monitoring.session_monitor, src.openclaw_orchestration.task_runner, src.self_improvement.auto_engine, src.skills.proactive_watcher, sync_obsidian, telegram_sender, tests.test_auto_engine, tests.test_auto_reflection, tests.test_context_split, tests.test_cross_bot_sync, tests.test_doc_generator, tests.test_dream_tracker, tests.test_error_learning, tests.test_face_clustering, tests.test_idea_pipeline, tests.test_image_format_migrator, tests.test_memory_analytics, tests.test_memory_cleanup, tests.test_ollama_batch, tests.test_ollama_benchmark, tests.test_ollama_manifest_fix, tests.test_ollama_model_manager, tests.test_ollama_monitor, tests.test_optimize_context, tests.test_photo_deduplication, tests.test_proactive_scout, tests.test_proactive_watcher, tests.test_sync_obsidian, tests.test_task_runner, tests.test_telegram_sender, tests.test_tool_discovery, tool_discovery, video_thumbnail_generator

### Public functions (signatures & docstrings)

| Function | Parameters | Returns | Decorators | Docstring |
| --- | --- | --- | --- | --- |
| `setUp` |  | `—` | — | — |
| `tearDown` |  | `—` | — | — |
| `test_valid_wiki_and_markdown_links` |  | `—` | — | — |
| `test_broken_wiki_file` |  | `—` | — | — |
| `test_broken_anchor` |  | `—` | — | — |
| `test_same_file_heading_link` |  | `—` | — | — |
| `test_ignores_links_in_fenced_code` |  | `—` | — | — |
| `test_vault_relative_path` |  | `—` | — | — |
| `test_relative_parent_path` |  | `—` | — | — |
| `test_case_insensitive_match` |  | `—` | — | — |
| `test_case_sensitive_miss` |  | `—` | — | — |
| `test_png_embed_resolves` |  | `—` | — | — |
| … | _1 more_ | | | |

### CLI subcommands

- _No argparse subcommands discovered_

### Example usage

```bash
python -m tests.test_obsidian_link_checker
```
```bash
# Filesystem workflow
python -m tests.test_obsidian_link_checker --help
```

## `tests.test_ollama_batch`

- **tool_id**: `tests/test_ollama_batch.py`
- **Path**: `tests/test_ollama_batch.py`
- **Description**: No module docstring available.
- **Risk level**: **high**
- **Capabilities**: Filesystem-oriented API surface, Model lifecycle management, Task orchestration
- **I/O profile**: filesystem, process, structured-data
- **Safety constraints**: filesystem_read, subprocess_execution
- **Decorator signals**: —
- **Dependencies**: auto_memory_cleanup, auto_reflection, batch_image_optimizer, business_dashboard, comfy_auto_quality, comfy_video_pipeline, context_split, conversation_extractor, doc_generator, error_learning, exif_date_normalizer, face_clustering, face_swap_batch, furniture_sales_database.import_sales, goal_decomposer, health_dashboard, image_format_migrator, media_tool, memory_analytics, memory_cleanup, nightly_pipeline, obsidian_dashboard, obsidian_link_checker, obsidian_researcher, ollama_batch, ollama_batch_download, ollama_benchmark, ollama_bridge, ollama_manifest_fix, ollama_model_manager, ollama_monitor, ollama_queue_monitor, optimize_context, photo_archive_report, photo_deduplication, proactive_scout, process_images, queue_manager, run_task, src.coordination.cross_bot_sync, src.dreams.dream_tracker, src.ideation.idea_pipeline, src.monitoring.session_monitor, src.openclaw_orchestration.task_runner, src.self_improvement.auto_engine, src.skills.proactive_watcher, sync_obsidian, telegram_sender, tests.test_auto_engine, tests.test_auto_reflection, tests.test_context_split, tests.test_cross_bot_sync, tests.test_doc_generator, tests.test_dream_tracker, tests.test_error_learning, tests.test_face_clustering, tests.test_idea_pipeline, tests.test_image_format_migrator, tests.test_memory_analytics, tests.test_memory_cleanup, tests.test_obsidian_link_checker, tests.test_ollama_benchmark, tests.test_ollama_manifest_fix, tests.test_ollama_model_manager, tests.test_ollama_monitor, tests.test_optimize_context, tests.test_photo_deduplication, tests.test_proactive_scout, tests.test_proactive_watcher, tests.test_sync_obsidian, tests.test_task_runner, tests.test_telegram_sender, tests.test_tool_discovery, tool_discovery, video_thumbnail_generator, yandex_metrika

### Public functions (signatures & docstrings)

| Function | Parameters | Returns | Decorators | Docstring |
| --- | --- | --- | --- | --- |
| `setUp` |  | `None` | — | — |
| `tearDown` |  | `None` | — | — |
| `write` | relative_path: str, content: str | `Path` | — | — |
| `make_fake_ollama` |  | `Path` | — | — |
| `base_env` |  | `dict[str, str]` | — | — |
| `test_load_prompts_supports_text_and_json` |  | `None` | — | — |
| `test_run_prompt_retries_with_exponential_backoff` |  | `None` | — | — |
| `fake_run_command` |  | `—` | — | — |
| `test_run_batch_preserves_input_order_and_prints_progress` |  | `None` | — | — |
| `fake_time` |  | `float` | — | — |
| `fake_runner` | prompt: str | `ollama_batch.PromptResult` | — | — |
| `test_cli_runs_batch_and_writes_json_output` |  | `None` | — | — |
| … | _1 more_ | | | |

### CLI subcommands

- _No argparse subcommands discovered_

### Example usage

```bash
python -m tests.test_ollama_batch
```
```bash
# Filesystem workflow
python -m tests.test_ollama_batch --help
```

## `tests.test_ollama_benchmark`

- **tool_id**: `tests/test_ollama_benchmark.py`
- **Path**: `tests/test_ollama_benchmark.py`
- **Description**: No module docstring available.
- **Risk level**: **high**
- **Capabilities**: Filesystem-oriented API surface, Model lifecycle management, Performance benchmarking
- **I/O profile**: filesystem, process, structured-data
- **Safety constraints**: filesystem_read, subprocess_execution
- **Decorator signals**: —
- **Dependencies**: ami_parser, auto_memory_cleanup, auto_reflection, batch_image_optimizer, business_dashboard, comfy_auto_quality, comfy_video_pipeline, context_split, conversation_extractor, doc_generator, error_learning, exif_date_normalizer, face_clustering, face_swap_batch, furniture_sales_database.import_sales, goal_decomposer, health_dashboard, image_format_migrator, marketplace_dashboard, media_tool, memory_analytics, memory_cleanup, nightly_pipeline, obsidian_dashboard, obsidian_link_checker, obsidian_researcher, ollama_batch, ollama_batch_download, ollama_benchmark, ollama_bridge, ollama_manifest_fix, ollama_model_manager, ollama_monitor, ollama_queue_monitor, optimize_context, photo_archive_report, photo_deduplication, proactive_scout, process_images, queue_manager, run_task, src.coordination.cross_bot_sync, src.dreams.dream_tracker, src.ideation.idea_pipeline, src.monitoring.session_monitor, src.openclaw_orchestration.task_runner, src.self_improvement.auto_engine, src.skills.proactive_watcher, sync_obsidian, telegram_sender, tests.test_auto_engine, tests.test_auto_reflection, tests.test_context_split, tests.test_cross_bot_sync, tests.test_doc_generator, tests.test_dream_tracker, tests.test_error_learning, tests.test_face_clustering, tests.test_idea_pipeline, tests.test_image_format_migrator, tests.test_memory_analytics, tests.test_memory_cleanup, tests.test_obsidian_link_checker, tests.test_ollama_batch, tests.test_ollama_manifest_fix, tests.test_ollama_model_manager, tests.test_ollama_monitor, tests.test_optimize_context, tests.test_photo_deduplication, tests.test_proactive_scout, tests.test_proactive_watcher, tests.test_sync_obsidian, tests.test_task_runner, tests.test_telegram_sender, tests.test_tool_discovery, tool_discovery, video_thumbnail_generator, yandex_metrika

### Public functions (signatures & docstrings)

| Function | Parameters | Returns | Decorators | Docstring |
| --- | --- | --- | --- | --- |
| `setUp` |  | `None` | — | — |
| `tearDown` |  | `None` | — | — |
| `read_json` | relative_path: str | `dict` | — | — |
| `test_score_prompt_output_prefers_exact_answers` |  | `None` | — | — |
| `test_run_benchmarks_saves_json_and_comparison` |  | `None` | — | — |
| `test_compare_and_history_helpers_use_saved_runs` |  | `None` | — | — |
| `test_cli_run_compare_and_history_with_fake_binaries` |  | `None` | — | — |

### CLI subcommands

- _No argparse subcommands discovered_

### Example usage

```bash
python -m tests.test_ollama_benchmark
```
```bash
# Filesystem workflow
python -m tests.test_ollama_benchmark --help
```

## `tests.test_ollama_manifest_fix`

- **tool_id**: `tests/test_ollama_manifest_fix.py`
- **Path**: `tests/test_ollama_manifest_fix.py`
- **Description**: No module docstring available.
- **Risk level**: **medium**
- **Capabilities**: Model lifecycle management
- **I/O profile**: filesystem, structured-data
- **Safety constraints**: filesystem_read
- **Decorator signals**: —
- **Dependencies**: auto_memory_cleanup, auto_reflection, batch_image_optimizer, business_dashboard, comfy_auto_quality, comfy_video_pipeline, context_split, conversation_extractor, doc_generator, error_learning, exif_date_normalizer, face_clustering, face_swap_batch, furniture_sales_database.import_sales, goal_decomposer, health_dashboard, image_format_migrator, media_tool, memory_analytics, memory_cleanup, nightly_pipeline, obsidian_dashboard, obsidian_link_checker, obsidian_researcher, ollama_batch, ollama_batch_download, ollama_benchmark, ollama_bridge, ollama_manifest_fix, ollama_model_manager, ollama_monitor, ollama_queue_monitor, optimize_context, photo_archive_report, photo_deduplication, proactive_scout, process_images, queue_manager, run_task, src.coordination.cross_bot_sync, src.dreams.dream_tracker, src.ideation.idea_pipeline, src.monitoring.session_monitor, src.openclaw_orchestration.task_runner, src.self_improvement.auto_engine, src.skills.proactive_watcher, sync_obsidian, telegram_sender, tests.test_auto_engine, tests.test_auto_reflection, tests.test_context_split, tests.test_cross_bot_sync, tests.test_doc_generator, tests.test_dream_tracker, tests.test_error_learning, tests.test_face_clustering, tests.test_idea_pipeline, tests.test_image_format_migrator, tests.test_memory_analytics, tests.test_memory_cleanup, tests.test_obsidian_link_checker, tests.test_ollama_batch, tests.test_ollama_benchmark, tests.test_ollama_model_manager, tests.test_ollama_monitor, tests.test_optimize_context, tests.test_photo_deduplication, tests.test_proactive_scout, tests.test_proactive_watcher, tests.test_sync_obsidian, tests.test_task_runner, tests.test_telegram_sender, tests.test_tool_discovery, tool_discovery, video_thumbnail_generator, yandex_metrika

### Public functions (signatures & docstrings)

| Function | Parameters | Returns | Decorators | Docstring |
| --- | --- | --- | --- | --- |
| `setUp` |  | `None` | — | — |
| `tearDown` |  | `None` | — | — |
| `test_canonicalize_digest_variants` |  | `None` | — | — |
| `test_fix_manifest_updates_sizes_and_mirrors_host` |  | `None` | — | — |

### CLI subcommands

- _No argparse subcommands discovered_

### Example usage

```bash
python -m tests.test_ollama_manifest_fix
```
```bash
# Filesystem workflow
python -m tests.test_ollama_manifest_fix --help
```

## `tests.test_ollama_model_manager`

- **tool_id**: `tests/test_ollama_model_manager.py`
- **Path**: `tests/test_ollama_model_manager.py`
- **Description**: No module docstring available.
- **Risk level**: **high**
- **Capabilities**: Cleanup and maintenance, General utility automation, Model lifecycle management
- **I/O profile**: filesystem, process
- **Safety constraints**: filesystem_destructive, filesystem_read, subprocess_execution
- **Decorator signals**: —
- **Dependencies**: ami_parser, auto_memory_cleanup, auto_reflection, batch_image_optimizer, business_dashboard, comfy_auto_quality, comfy_video_pipeline, context_split, conversation_extractor, doc_generator, error_learning, exif_date_normalizer, face_clustering, face_swap_batch, furniture_sales_database.import_sales, goal_decomposer, health_dashboard, image_format_migrator, marketplace_dashboard, media_tool, memory_analytics, memory_cleanup, nightly_pipeline, obsidian_dashboard, obsidian_link_checker, obsidian_researcher, ollama_batch, ollama_batch_download, ollama_benchmark, ollama_bridge, ollama_manifest_fix, ollama_model_manager, ollama_monitor, ollama_queue_monitor, optimize_context, photo_archive_report, photo_deduplication, proactive_scout, process_images, queue_manager, run_task, src.coordination.cross_bot_sync, src.dreams.dream_tracker, src.ideation.idea_pipeline, src.monitoring.session_monitor, src.openclaw_orchestration.task_runner, src.self_improvement.auto_engine, src.skills.proactive_watcher, sync_obsidian, telegram_sender, tests.test_auto_engine, tests.test_auto_reflection, tests.test_context_split, tests.test_cross_bot_sync, tests.test_doc_generator, tests.test_dream_tracker, tests.test_error_learning, tests.test_face_clustering, tests.test_idea_pipeline, tests.test_image_format_migrator, tests.test_memory_analytics, tests.test_memory_cleanup, tests.test_obsidian_link_checker, tests.test_ollama_batch, tests.test_ollama_benchmark, tests.test_ollama_manifest_fix, tests.test_ollama_monitor, tests.test_optimize_context, tests.test_photo_deduplication, tests.test_proactive_scout, tests.test_proactive_watcher, tests.test_sync_obsidian, tests.test_task_runner, tests.test_telegram_sender, tests.test_tool_discovery, tool_discovery, video_thumbnail_generator, yandex_metrika

### Public functions (signatures & docstrings)

| Function | Parameters | Returns | Decorators | Docstring |
| --- | --- | --- | --- | --- |
| `test_parse_model_list_extracts_name_size_and_age` |  | `None` | — | — |
| `test_parse_pull_progress_from_json_and_compute_speed_eta` |  | `None` | — | — |
| `test_get_disk_space_warns_below_ten_gib` |  | `None` | — | — |
| `test_cleanup_suggestions_flags_models_older_than_30_days` |  | `None` | — | — |
| `test_remove_model_prompts_for_confirmation` |  | `None` | — | — |
| `test_pull_model_renders_progress_and_success_message` |  | `None` | — | — |
| `wait` |  | `int` | — | — |
| `test_main_returns_error_for_unsupported_search` |  | `None` | — | — |

### CLI subcommands

- _No argparse subcommands discovered_

### Example usage

```bash
python -m tests.test_ollama_model_manager
```
```bash
# Filesystem workflow
python -m tests.test_ollama_model_manager --help
```

## `tests.test_ollama_monitor`

- **tool_id**: `tests/test_ollama_monitor.py`
- **Path**: `tests/test_ollama_monitor.py`
- **Description**: No module docstring available.
- **Risk level**: **medium**
- **Capabilities**: Data synchronization, Filesystem-oriented API surface, Model lifecycle management, Monitoring and observability
- **I/O profile**: filesystem, network, structured-data
- **Safety constraints**: filesystem_read, network_egress
- **Decorator signals**: —
- **Dependencies**: ami_parser, auto_memory_cleanup, auto_reflection, batch_image_optimizer, business_dashboard, comfy_auto_quality, comfy_video_pipeline, context_split, conversation_extractor, doc_generator, error_learning, exif_date_normalizer, face_clustering, face_swap_batch, furniture_sales_database.import_sales, goal_decomposer, health_dashboard, image_format_migrator, media_tool, memory_analytics, memory_cleanup, nightly_pipeline, obsidian_dashboard, obsidian_link_checker, obsidian_researcher, ollama_batch, ollama_batch_download, ollama_benchmark, ollama_bridge, ollama_manifest_fix, ollama_model_manager, ollama_monitor, ollama_queue_monitor, optimize_context, photo_archive_report, photo_deduplication, proactive_scout, process_images, queue_manager, run_task, src.coordination.cross_bot_sync, src.dreams.dream_tracker, src.ideation.idea_pipeline, src.monitoring.session_monitor, src.openclaw_orchestration.task_runner, src.self_improvement.auto_engine, src.skills.proactive_watcher, sync_obsidian, telegram_sender, tests.test_auto_engine, tests.test_auto_reflection, tests.test_context_split, tests.test_cross_bot_sync, tests.test_doc_generator, tests.test_dream_tracker, tests.test_error_learning, tests.test_face_clustering, tests.test_idea_pipeline, tests.test_image_format_migrator, tests.test_memory_analytics, tests.test_memory_cleanup, tests.test_obsidian_link_checker, tests.test_ollama_batch, tests.test_ollama_benchmark, tests.test_ollama_manifest_fix, tests.test_ollama_model_manager, tests.test_optimize_context, tests.test_photo_deduplication, tests.test_proactive_scout, tests.test_proactive_watcher, tests.test_sync_obsidian, tests.test_task_runner, tests.test_telegram_sender, tests.test_tool_discovery, tool_discovery, video_thumbnail_generator, yandex_metrika

### Public functions (signatures & docstrings)

| Function | Parameters | Returns | Decorators | Docstring |
| --- | --- | --- | --- | --- |
| `read` |  | `bytes` | — | — |
| `poll` |  | `int | None` | — | — |
| `setUp` |  | `None` | — | — |
| `tearDown` |  | `None` | — | — |
| `read_json` | relative_path: str | `dict` | — | — |
| `test_check_health_reports_connection_refused` |  | `None` | — | — |
| `test_check_health_reports_timeout` |  | `None` | — | — |
| `test_check_vram_reports_threshold_breach` |  | `None` | — | — |
| `test_gather_status_syncs_stderr_and_updates_daily_log` |  | `None` | — | — |
| `test_restart_starts_ollama_when_unhealthy` |  | `None` | — | — |
| `fake_health` |  | `dict` | — | — |
| `fake_sleep` | seconds: float | `None` | — | — |
| … | _2 more_ | | | |

### CLI subcommands

- _No argparse subcommands discovered_

### Example usage

```bash
python -m tests.test_ollama_monitor
```
```bash
# Network-aware run
python -m tests.test_ollama_monitor --help
```
```bash
# Filesystem workflow
python -m tests.test_ollama_monitor --help
```

## `tests.test_optimize_context`

- **tool_id**: `tests/test_optimize_context.py`
- **Path**: `tests/test_optimize_context.py`
- **Description**: No module docstring available.
- **Risk level**: **low**
- **Capabilities**: Context shaping and prompt preparation, General utility automation, Memory and ideation workflows, Monitoring and observability
- **I/O profile**: filesystem, structured-data
- **Safety constraints**: filesystem_read
- **Decorator signals**: —
- **Dependencies**: auto_reflection, batch_image_optimizer, comfy_video_pipeline, context_split, conversation_extractor, error_learning, face_clustering, health_dashboard, media_tool, memory_analytics, memory_cleanup, nightly_pipeline, obsidian_dashboard, obsidian_link_checker, ollama_batch, ollama_benchmark, ollama_bridge, ollama_manifest_fix, ollama_model_manager, ollama_monitor, ollama_queue_monitor, optimize_context, photo_archive_report, photo_deduplication, proactive_scout, queue_manager, src.coordination.cross_bot_sync, src.dreams.dream_tracker, src.ideation.idea_pipeline, src.monitoring.session_monitor, src.openclaw_orchestration.task_runner, src.self_improvement.auto_engine, src.skills.proactive_watcher, sync_obsidian, telegram_sender, tests.test_auto_engine, tests.test_auto_reflection, tests.test_context_split, tests.test_cross_bot_sync, tests.test_doc_generator, tests.test_dream_tracker, tests.test_error_learning, tests.test_face_clustering, tests.test_idea_pipeline, tests.test_image_format_migrator, tests.test_memory_analytics, tests.test_memory_cleanup, tests.test_obsidian_link_checker, tests.test_ollama_batch, tests.test_ollama_benchmark, tests.test_ollama_manifest_fix, tests.test_ollama_model_manager, tests.test_ollama_monitor, tests.test_photo_deduplication, tests.test_proactive_scout, tests.test_proactive_watcher, tests.test_sync_obsidian, tests.test_task_runner, tests.test_telegram_sender, tests.test_tool_discovery, tool_discovery, video_thumbnail_generator

### Public functions (signatures & docstrings)

| Function | Parameters | Returns | Decorators | Docstring |
| --- | --- | --- | --- | --- |
| `test_analyze_session_reports_stale_large_and_memory_bloat` |  | `—` | — | — |
| `test_text_session_log_extracts_paths_and_turns` |  | `—` | — | — |
| `test_write_report_generates_json_and_markdown` |  | `—` | — | — |
| `test_render_summary_includes_ansi_sequences_when_enabled` |  | `—` | — | — |

### CLI subcommands

- _No argparse subcommands discovered_

### Example usage

```bash
python -m tests.test_optimize_context
```
```bash
# Filesystem workflow
python -m tests.test_optimize_context --help
```

## `tests.test_photo_deduplication`

- **tool_id**: `tests/test_photo_deduplication.py`
- **Path**: `tests/test_photo_deduplication.py`
- **Description**: No module docstring available.
- **Risk level**: **low**
- **Capabilities**: Deduplication and similarity, Media processing
- **I/O profile**: filesystem, structured-data
- **Safety constraints**: filesystem_read
- **Decorator signals**: —
- **Dependencies**: auto_reflection, batch_image_optimizer, comfy_auto_quality, comfy_video_pipeline, context_split, conversation_extractor, error_learning, exif_date_normalizer, face_clustering, face_swap_batch, health_dashboard, image_format_migrator, media_tool, memory_analytics, memory_cleanup, nightly_pipeline, obsidian_dashboard, obsidian_link_checker, ollama_batch, ollama_benchmark, ollama_bridge, ollama_manifest_fix, ollama_model_manager, ollama_monitor, ollama_queue_monitor, optimize_context, photo_archive_report, photo_deduplication, proactive_scout, process_images, queue_manager, src.coordination.cross_bot_sync, src.dreams.dream_tracker, src.ideation.idea_pipeline, src.monitoring.session_monitor, src.openclaw_orchestration.task_runner, src.self_improvement.auto_engine, src.skills.proactive_watcher, sync_obsidian, telegram_sender, tests.test_auto_engine, tests.test_auto_reflection, tests.test_context_split, tests.test_cross_bot_sync, tests.test_doc_generator, tests.test_dream_tracker, tests.test_error_learning, tests.test_face_clustering, tests.test_idea_pipeline, tests.test_image_format_migrator, tests.test_memory_analytics, tests.test_memory_cleanup, tests.test_obsidian_link_checker, tests.test_ollama_batch, tests.test_ollama_benchmark, tests.test_ollama_manifest_fix, tests.test_ollama_model_manager, tests.test_ollama_monitor, tests.test_optimize_context, tests.test_proactive_scout, tests.test_proactive_watcher, tests.test_sync_obsidian, tests.test_task_runner, tests.test_telegram_sender, tests.test_tool_discovery, tool_discovery, video_thumbnail_generator

### Public functions (signatures & docstrings)

| Function | Parameters | Returns | Decorators | Docstring |
| --- | --- | --- | --- | --- |
| `setUp` |  | `None` | — | — |
| `tearDown` |  | `None` | — | — |
| `test_build_duplicate_groups_detects_identical_images` |  | `None` | — | — |
| `test_main_dry_run_writes_reports_without_deleting` |  | `None` | — | — |
| `test_main_move_moves_duplicates_to_duplicates_folder` |  | `None` | — | — |

### CLI subcommands

- _No argparse subcommands discovered_

### Example usage

```bash
python -m tests.test_photo_deduplication
```
```bash
# Filesystem workflow
python -m tests.test_photo_deduplication --help
```

## `tests.test_proactive_scout`

- **tool_id**: `tests/test_proactive_scout.py`
- **Path**: `tests/test_proactive_scout.py`
- **Description**: No module docstring available.
- **Risk level**: **high**
- **Capabilities**: Model lifecycle management
- **I/O profile**: filesystem, process, structured-data
- **Safety constraints**: filesystem_read, subprocess_execution
- **Decorator signals**: —
- **Dependencies**: ami_parser, auto_memory_cleanup, auto_reflection, batch_image_optimizer, business_dashboard, comfy_auto_quality, comfy_video_pipeline, context_split, conversation_extractor, doc_generator, error_learning, exif_date_normalizer, face_clustering, face_swap_batch, furniture_sales_database.import_sales, goal_decomposer, health_dashboard, image_format_migrator, media_tool, memory_analytics, memory_cleanup, nightly_pipeline, obsidian_dashboard, obsidian_link_checker, obsidian_researcher, ollama_batch, ollama_batch_download, ollama_benchmark, ollama_bridge, ollama_manifest_fix, ollama_model_manager, ollama_monitor, ollama_queue_monitor, optimize_context, photo_archive_report, photo_deduplication, proactive_scout, process_images, queue_manager, run_task, src.coordination.cross_bot_sync, src.dreams.dream_tracker, src.ideation.idea_pipeline, src.monitoring.session_monitor, src.openclaw_orchestration.task_runner, src.self_improvement.auto_engine, src.skills.proactive_watcher, sync_obsidian, telegram_sender, tests.test_auto_engine, tests.test_auto_reflection, tests.test_context_split, tests.test_cross_bot_sync, tests.test_doc_generator, tests.test_dream_tracker, tests.test_error_learning, tests.test_face_clustering, tests.test_idea_pipeline, tests.test_image_format_migrator, tests.test_memory_analytics, tests.test_memory_cleanup, tests.test_obsidian_link_checker, tests.test_ollama_batch, tests.test_ollama_benchmark, tests.test_ollama_manifest_fix, tests.test_ollama_model_manager, tests.test_ollama_monitor, tests.test_optimize_context, tests.test_photo_deduplication, tests.test_proactive_watcher, tests.test_sync_obsidian, tests.test_task_runner, tests.test_telegram_sender, tests.test_tool_discovery, tool_discovery, video_thumbnail_generator, yandex_metrika

### Public functions (signatures & docstrings)

| Function | Parameters | Returns | Decorators | Docstring |
| --- | --- | --- | --- | --- |
| `setUp` |  | `None` | — | — |
| `tearDown` |  | `None` | — | — |
| `test_scout_predict_uses_fast_model_and_limits_to_two_predictions` |  | `None` | — | — |
| `test_scout_run_background_skips_when_idle_threshold_not_met` |  | `None` | — | — |
| `test_worker_populates_cache_and_check_returns_cached_entry` |  | `None` | — | — |
| `test_expired_cache_entries_are_purged_on_lookup` |  | `None` | — | — |
| `test_cli_predict_status_check_and_clear` |  | `None` | — | — |

### CLI subcommands

- _No argparse subcommands discovered_

### Example usage

```bash
python -m tests.test_proactive_scout
```
```bash
# Filesystem workflow
python -m tests.test_proactive_scout --help
```

## `tests.test_proactive_watcher`

- **tool_id**: `tests/test_proactive_watcher.py`
- **Path**: `tests/test_proactive_watcher.py`
- **Description**: No module docstring available.
- **Risk level**: **high**
- **Capabilities**: General utility automation, Task orchestration
- **I/O profile**: filesystem, process
- **Safety constraints**: filesystem_read, subprocess_execution
- **Decorator signals**: —
- **Dependencies**: auto_memory_cleanup, auto_reflection, batch_image_optimizer, business_dashboard, comfy_auto_quality, comfy_video_pipeline, context_split, conversation_extractor, doc_generator, error_learning, exif_date_normalizer, face_clustering, face_swap_batch, furniture_sales_database.import_sales, goal_decomposer, health_dashboard, image_format_migrator, media_tool, memory_analytics, memory_cleanup, nightly_pipeline, obsidian_dashboard, obsidian_link_checker, obsidian_researcher, ollama_batch, ollama_batch_download, ollama_benchmark, ollama_bridge, ollama_manifest_fix, ollama_model_manager, ollama_monitor, ollama_queue_monitor, optimize_context, photo_archive_report, photo_deduplication, proactive_scout, process_images, queue_manager, run_task, src.coordination.cross_bot_sync, src.dreams.dream_tracker, src.ideation.idea_pipeline, src.monitoring.session_monitor, src.openclaw_orchestration.task_runner, src.self_improvement.auto_engine, src.skills.proactive_watcher, sync_obsidian, telegram_sender, tests.test_auto_engine, tests.test_auto_reflection, tests.test_context_split, tests.test_cross_bot_sync, tests.test_doc_generator, tests.test_dream_tracker, tests.test_error_learning, tests.test_face_clustering, tests.test_idea_pipeline, tests.test_image_format_migrator, tests.test_memory_analytics, tests.test_memory_cleanup, tests.test_obsidian_link_checker, tests.test_ollama_batch, tests.test_ollama_benchmark, tests.test_ollama_manifest_fix, tests.test_ollama_model_manager, tests.test_ollama_monitor, tests.test_optimize_context, tests.test_photo_deduplication, tests.test_proactive_scout, tests.test_sync_obsidian, tests.test_task_runner, tests.test_telegram_sender, tests.test_tool_discovery, tool_discovery, video_thumbnail_generator, yandex_metrika

### Public functions (signatures & docstrings)

| Function | Parameters | Returns | Decorators | Docstring |
| --- | --- | --- | --- | --- |
| `setUp` |  | `None` | — | — |
| `tearDown` |  | `None` | — | — |
| `test_scan_discovers_skills_and_tracks_files` |  | `None` | — | — |
| `test_usage_and_error_analysis_classify_skills` |  | `None` | — | — |
| `test_suggestions_and_report_generation` |  | `None` | — | — |
| `test_cli_scan_writes_report` |  | `None` | — | — |

### CLI subcommands

- _No argparse subcommands discovered_

### Example usage

```bash
python -m tests.test_proactive_watcher
```
```bash
# Filesystem workflow
python -m tests.test_proactive_watcher --help
```

## `tests.test_sync_obsidian`

- **tool_id**: `tests/test_sync_obsidian.py`
- **Path**: `tests/test_sync_obsidian.py`
- **Description**: No module docstring available.
- **Risk level**: **medium**
- **Capabilities**: Data synchronization, Filesystem-oriented API surface, Memory and ideation workflows
- **I/O profile**: filesystem, structured-data
- **Safety constraints**: filesystem_read
- **Decorator signals**: —
- **Dependencies**: auto_memory_cleanup, auto_reflection, batch_image_optimizer, business_dashboard, comfy_auto_quality, comfy_video_pipeline, context_split, conversation_extractor, doc_generator, error_learning, exif_date_normalizer, face_clustering, face_swap_batch, furniture_sales_database.import_sales, goal_decomposer, health_dashboard, image_format_migrator, media_tool, memory_analytics, memory_cleanup, nightly_pipeline, obsidian_dashboard, obsidian_link_checker, obsidian_researcher, ollama_batch, ollama_batch_download, ollama_benchmark, ollama_bridge, ollama_manifest_fix, ollama_model_manager, ollama_monitor, ollama_queue_monitor, optimize_context, photo_archive_report, photo_deduplication, proactive_scout, process_images, queue_manager, run_task, src.coordination.cross_bot_sync, src.dreams.dream_tracker, src.ideation.idea_pipeline, src.monitoring.session_monitor, src.openclaw_orchestration.task_runner, src.self_improvement.auto_engine, src.skills.proactive_watcher, sync_obsidian, telegram_sender, tests.test_auto_engine, tests.test_auto_reflection, tests.test_context_split, tests.test_cross_bot_sync, tests.test_doc_generator, tests.test_dream_tracker, tests.test_error_learning, tests.test_face_clustering, tests.test_idea_pipeline, tests.test_image_format_migrator, tests.test_memory_analytics, tests.test_memory_cleanup, tests.test_obsidian_link_checker, tests.test_ollama_batch, tests.test_ollama_benchmark, tests.test_ollama_manifest_fix, tests.test_ollama_model_manager, tests.test_ollama_monitor, tests.test_optimize_context, tests.test_photo_deduplication, tests.test_proactive_scout, tests.test_proactive_watcher, tests.test_task_runner, tests.test_telegram_sender, tests.test_tool_discovery, tool_discovery, video_thumbnail_generator, yandex_metrika

### Public functions (signatures & docstrings)

| Function | Parameters | Returns | Decorators | Docstring |
| --- | --- | --- | --- | --- |
| `setUp` |  | `—` | — | — |
| `tearDown` |  | `—` | — | — |
| `write_memory` | text: str | `None` | — | — |
| `set_mtime` | path: Path, timestamp: datetime | `None` | — | — |
| `test_daily_note_added_for_new_memory_file` |  | `—` | — | — |
| `test_updated_vault_note_marks_memory_section_stale` |  | `—` | — | — |
| `test_memory_section_generates_vault_index_reference` |  | `—` | — | — |
| `test_dry_run_preserves_files_and_logs_preview` |  | `—` | — | — |
| `test_conflict_prefers_newer_memory_timestamp` |  | `—` | — | — |

### CLI subcommands

- _No argparse subcommands discovered_

### Example usage

```bash
python -m tests.test_sync_obsidian
```
```bash
# Filesystem workflow
python -m tests.test_sync_obsidian --help
```

## `tests.test_task_runner`

- **tool_id**: `tests/test_task_runner.py`
- **Path**: `tests/test_task_runner.py`
- **Description**: No module docstring available.
- **Risk level**: **medium**
- **Capabilities**: Network-oriented API surface
- **I/O profile**: filesystem, network, structured-data
- **Safety constraints**: filesystem_read, network_egress
- **Decorator signals**: —
- **Dependencies**: auto_reflection, batch_image_optimizer, comfy_auto_quality, comfy_video_pipeline, context_split, conversation_extractor, error_learning, face_clustering, health_dashboard, image_format_migrator, media_tool, memory_analytics, memory_cleanup, nightly_pipeline, obsidian_dashboard, obsidian_link_checker, obsidian_researcher, ollama_batch, ollama_benchmark, ollama_bridge, ollama_manifest_fix, ollama_model_manager, ollama_monitor, ollama_queue_monitor, optimize_context, photo_archive_report, photo_deduplication, proactive_scout, queue_manager, src.coordination.cross_bot_sync, src.dreams.dream_tracker, src.ideation.idea_pipeline, src.monitoring.session_monitor, src.openclaw_orchestration.task_runner, src.self_improvement.auto_engine, src.skills.proactive_watcher, sync_obsidian, telegram_sender, tests.test_auto_engine, tests.test_auto_reflection, tests.test_context_split, tests.test_cross_bot_sync, tests.test_doc_generator, tests.test_dream_tracker, tests.test_error_learning, tests.test_face_clustering, tests.test_idea_pipeline, tests.test_image_format_migrator, tests.test_memory_analytics, tests.test_memory_cleanup, tests.test_obsidian_link_checker, tests.test_ollama_batch, tests.test_ollama_benchmark, tests.test_ollama_manifest_fix, tests.test_ollama_model_manager, tests.test_ollama_monitor, tests.test_optimize_context, tests.test_photo_deduplication, tests.test_proactive_scout, tests.test_proactive_watcher, tests.test_sync_obsidian, tests.test_telegram_sender, tests.test_tool_discovery, tool_discovery, video_thumbnail_generator

### Public functions (signatures & docstrings)

| Function | Parameters | Returns | Decorators | Docstring |
| --- | --- | --- | --- | --- |
| `setUp` |  | `None` | — | — |
| `tearDown` |  | `None` | — | — |
| `test_load_tasks_uses_file_stem_as_default_name` |  | `None` | — | — |
| `test_run_script_task_writes_success_log` |  | `None` | — | — |
| `test_retry_logic_uses_exponential_backoff` |  | `None` | — | — |
| `test_composite_task_marks_remaining_subtasks_skipped` |  | `None` | — | — |
| `test_tool_task_supports_external_api_calls` |  | `None` | — | — |
| `read` |  | `bytes` | — | — |
| `fake_urlopen` | request, timeout | `—` | — | — |
| `test_show_status_uses_latest_log_entries` |  | `None` | — | — |
| `test_cli_commands_cover_list_run_and_status` |  | `None` | — | — |

### CLI subcommands

- _No argparse subcommands discovered_

### Example usage

```bash
python -m tests.test_task_runner
```
```bash
# Network-aware run
python -m tests.test_task_runner --help
```
```bash
# Filesystem workflow
python -m tests.test_task_runner --help
```

## `tests.test_telegram_sender`

- **tool_id**: `tests/test_telegram_sender.py`
- **Path**: `tests/test_telegram_sender.py`
- **Description**: No module docstring available.
- **Risk level**: **high**
- **Capabilities**: Filesystem-oriented API surface, Media processing, Messaging and notifications, Network-oriented API surface
- **I/O profile**: filesystem, network, structured-data
- **Safety constraints**: filesystem_read, network_egress
- **Decorator signals**: —
- **Dependencies**: auto_reflection, batch_image_optimizer, comfy_auto_quality, comfy_video_pipeline, context_split, conversation_extractor, doc_generator, error_learning, exif_date_normalizer, face_clustering, face_swap_batch, health_dashboard, image_format_migrator, media_tool, memory_analytics, memory_cleanup, nightly_pipeline, obsidian_dashboard, obsidian_link_checker, obsidian_researcher, ollama_batch, ollama_benchmark, ollama_bridge, ollama_manifest_fix, ollama_model_manager, ollama_monitor, ollama_queue_monitor, optimize_context, photo_archive_report, photo_deduplication, proactive_scout, process_images, queue_manager, src.coordination.cross_bot_sync, src.dreams.dream_tracker, src.ideation.idea_pipeline, src.monitoring.session_monitor, src.openclaw_orchestration.task_runner, src.self_improvement.auto_engine, src.skills.proactive_watcher, sync_obsidian, telegram_sender, tests.test_auto_engine, tests.test_auto_reflection, tests.test_context_split, tests.test_cross_bot_sync, tests.test_doc_generator, tests.test_dream_tracker, tests.test_error_learning, tests.test_face_clustering, tests.test_idea_pipeline, tests.test_image_format_migrator, tests.test_memory_analytics, tests.test_memory_cleanup, tests.test_obsidian_link_checker, tests.test_ollama_batch, tests.test_ollama_benchmark, tests.test_ollama_manifest_fix, tests.test_ollama_model_manager, tests.test_ollama_monitor, tests.test_optimize_context, tests.test_photo_deduplication, tests.test_proactive_scout, tests.test_proactive_watcher, tests.test_sync_obsidian, tests.test_task_runner, tests.test_tool_discovery, tool_discovery, video_thumbnail_generator, yandex_metrika

### Public functions (signatures & docstrings)

| Function | Parameters | Returns | Decorators | Docstring |
| --- | --- | --- | --- | --- |
| `raise_for_status` |  | `—` | — | — |
| `json` |  | `—` | — | — |
| `test_reports_progress_until_complete` |  | `—` | — | — |
| `test_from_env_requires_values` |  | `—` | — | — |
| `setUp` |  | `—` | — | — |
| `create_sender` |  | `—` | — | — |
| `test_send_photo_uses_resize_and_uploads` |  | `—` | — | — |
| `fake_request` |  | `—` | — | — |
| `fake_resize` | path, reporter | `—` | — | — |
| `test_send_group_rejects_more_than_limit` |  | `—` | — | — |
| `test_send_group_builds_media_payload` |  | `—` | — | — |
| `fake_request` |  | `—` | — | — |
| … | _5 more_ | | | |

### CLI subcommands

- _No argparse subcommands discovered_

### Example usage

```bash
python -m tests.test_telegram_sender
```
```bash
# Network-aware run
python -m tests.test_telegram_sender --help
```
```bash
# Filesystem workflow
python -m tests.test_telegram_sender --help
```

## `tests.test_tool_discovery`

- **tool_id**: `tests/test_tool_discovery.py`
- **Path**: `tests/test_tool_discovery.py`
- **Description**: No module docstring available.
- **Risk level**: **low**
- **Capabilities**: Context shaping and prompt preparation
- **I/O profile**: filesystem, structured-data
- **Safety constraints**: filesystem_read
- **Decorator signals**: —
- **Dependencies**: auto_memory_cleanup, auto_reflection, batch_image_optimizer, business_dashboard, comfy_auto_quality, comfy_video_pipeline, context_split, conversation_extractor, doc_generator, error_learning, exif_date_normalizer, face_clustering, face_swap_batch, furniture_sales_database.import_sales, goal_decomposer, health_dashboard, image_format_migrator, media_tool, memory_analytics, memory_cleanup, nightly_pipeline, obsidian_dashboard, obsidian_link_checker, obsidian_researcher, ollama_batch, ollama_batch_download, ollama_benchmark, ollama_bridge, ollama_manifest_fix, ollama_model_manager, ollama_monitor, ollama_queue_monitor, optimize_context, photo_archive_report, photo_deduplication, proactive_scout, process_images, queue_manager, run_task, src.coordination.cross_bot_sync, src.dreams.dream_tracker, src.ideation.idea_pipeline, src.monitoring.session_monitor, src.openclaw_orchestration.task_runner, src.self_improvement.auto_engine, src.skills.proactive_watcher, sync_obsidian, telegram_sender, tests.test_auto_engine, tests.test_auto_reflection, tests.test_context_split, tests.test_cross_bot_sync, tests.test_doc_generator, tests.test_dream_tracker, tests.test_error_learning, tests.test_face_clustering, tests.test_idea_pipeline, tests.test_image_format_migrator, tests.test_memory_analytics, tests.test_memory_cleanup, tests.test_obsidian_link_checker, tests.test_ollama_batch, tests.test_ollama_benchmark, tests.test_ollama_manifest_fix, tests.test_ollama_model_manager, tests.test_ollama_monitor, tests.test_optimize_context, tests.test_photo_deduplication, tests.test_proactive_scout, tests.test_proactive_watcher, tests.test_sync_obsidian, tests.test_task_runner, tests.test_telegram_sender, tool_discovery, video_thumbnail_generator, yandex_metrika

### Public functions (signatures & docstrings)

| Function | Parameters | Returns | Decorators | Docstring |
| --- | --- | --- | --- | --- |
| `test_analyze_scripts_infers_capabilities_and_dependencies` |  | `None` | — | — |
| `test_generate_markdown_includes_examples_and_dependencies` |  | `None` | — | — |
| `test_suggest_tools_returns_contextual_reasoning` |  | `None` | — | — |
| `test_main_docs_command_writes_file` |  | `None` | — | — |
| `test_main_suggest_prints_json` |  | `None` | — | — |

### CLI subcommands

- _No argparse subcommands discovered_

### Example usage

```bash
python -m tests.test_tool_discovery
```
```bash
# Filesystem workflow
python -m tests.test_tool_discovery --help
```

## `yandex_metrika`

- **tool_id**: `yandex_metrika.py`
- **Path**: `yandex_metrika.py`
- **Description**: Yandex Metrika integration for furniture business analytics.
- **Risk level**: **medium**
- **Capabilities**: Analytics and reporting, Filesystem-oriented API surface, Network-oriented API surface
- **I/O profile**: filesystem, network, structured-data, database
- **Safety constraints**: database_access, filesystem_read, network_egress
- **Decorator signals**: —
- **Dependencies**: ami_parser, auto_memory_cleanup, auto_reflection, batch_image_optimizer, business_dashboard, comfy_auto_quality, comfy_video_pipeline, context_split, conversation_extractor, doc_generator, error_learning, exif_date_normalizer, face_clustering, face_swap_batch, furniture_sales_database.category_detector, furniture_sales_database.import_sales, furniture_sales_database.query_sales, goal_decomposer, health_dashboard, image_format_migrator, marketplace_dashboard, media_tool, memory_analytics, memory_cleanup, nightly_pipeline, obsidian_dashboard, obsidian_link_checker, obsidian_researcher, ollama_batch, ollama_benchmark, ollama_bridge, ollama_manifest_fix, ollama_model_manager, ollama_monitor, ollama_queue_monitor, optimize_context, photo_archive_report, photo_deduplication, proactive_scout, process_images, queue_manager, run_task, src.coordination.cross_bot_sync, src.dreams.dream_tracker, src.ideation.idea_pipeline, src.monitoring.session_monitor, src.openclaw_orchestration.task_runner, src.self_improvement.auto_engine, src.skills.proactive_watcher, sync_obsidian, telegram_sender, tests.test_auto_engine, tests.test_auto_reflection, tests.test_context_split, tests.test_cross_bot_sync, tests.test_dream_tracker, tests.test_error_learning, tests.test_face_clustering, tests.test_idea_pipeline, tests.test_memory_analytics, tests.test_memory_cleanup, tests.test_ollama_batch, tests.test_ollama_benchmark, tests.test_ollama_manifest_fix, tests.test_ollama_model_manager, tests.test_ollama_monitor, tests.test_proactive_scout, tests.test_proactive_watcher, tests.test_sync_obsidian, tests.test_telegram_sender, tests.test_tool_discovery, tool_discovery, video_thumbnail_generator

### Public functions (signatures & docstrings)

| Function | Parameters | Returns | Decorators | Docstring |
| --- | --- | --- | --- | --- |
| `get_counters` |  | `list[CounterInfo]` | — | Fetch all available counters for the authenticated user. |
| `get_counter_for_domain` | domain: str | `CounterInfo` | — | Discover counter by matching domain against site URL/host. |
| `get_counter_stats` | counter_id: int, date1: str, date2: str | `dict[str, float]` | — | Get traffic metrics for a counter in date range. |
| `get_traffic_by_source` | counter_id: int, date1: str, date2: str | `list[dict[str, Any]]` | — | Get visits split by traffic source. |
| `get_conversion_rate` | stats: dict[str, float] | `float` | — | Return conversion rate (%). Uses goalConversionRate when available. |
| `save_to_db` | db_path: str, domain: str, counter_id: int, date1: str, date2: str, stats: dict[str, float] | `None` | — | Create/update traffic table and persist aggregated metrics. |
| `fmt_row` | row: list[str] | `str` | — | — |
| `run` | date1: str | None, date2: str | None, db_path: str | `None` | — | Discover counters for target domains, fetch stats, output and persist. |

### CLI subcommands

- _No argparse subcommands discovered_

### Example usage

```bash
python -m yandex_metrika
```
```bash
# Network-aware run
python -m yandex_metrika --help
```
```bash
# Filesystem workflow
python -m yandex_metrika --help
```
