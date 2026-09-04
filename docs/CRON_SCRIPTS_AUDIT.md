# OpenClaw Cron Scripts Audit

**Date:** 2026-08-02
**Auditor:** Automated audit (Cloud Agent)
**Scope:** `scripts/`, `src/monitoring/`, root `.py` files — all `.py`, `.ps1`, `.sh` scripts in the repo

---

## 1. Executive Summary

The repository contains **56 Python scripts**, **1 PowerShell script**, and **3 shell scripts**.
Of these, approximately **16 are actively used by cron tasks or core OpenClaw tools**, **12 are support/library modules**, **25 are on-demand utilities**, and **3 are potentially dead or duplicated**.

### Critical Findings

| # | Severity | Issue |
|---|----------|-------|
| 1 | **CRITICAL** | `yandex_metrika.py` contains hardcoded credentials (`YANDEX_LOGIN`, `YANDEX_TOKEN`) in plaintext |
| 2 | **HIGH** | `log_download_status.ps1` referenced by 'Undress pipeline' cron **does not exist** in the repo |
| 3 | **HIGH** | `telegram_media_send_v2.py` is external (`~/.openclaw/skills/...`); `nightly_pipeline.py` references it but the send logic is a no-op (writes temp file, never invokes the script) |
| 4 | **HIGH** | `metrika_daily.py` referenced in cron description does not exist — the actual file is `yandex_metrika.py` |
| 5 | **MEDIUM** | `src/monitoring/session_monitor.py` has hardcoded Windows path `C:/Users/user/...` |
| 6 | **MEDIUM** | `health_dashboard.py` has hardcoded Windows drive letters (`C:\`, `E:\`, `H:\`, `Q:\`) |
| 7 | **LOW** | `scripts/self_improvement/auto_reflection.py` appears to be a near-duplicate of `scripts/auto_reflection.py` |
| 8 | **LOW** | `ollama_batch_download.py` opens log file without explicit `encoding="utf-8"` |

---

## 2. Script Inventory

### 2.1 Active Cron Scripts

| Script | Size | Lines | Cron Task | Classification | Notes |
|--------|------|-------|-----------|----------------|-------|
| `yandex_metrika.py` (root) | 11 KB | 324 | Мебель: SEO-улучшение дня | **BUGGY** | Hardcoded credentials (L25-26). No retry on API errors. No rate limiting. Token `Nastia56` is likely a password, not an OAuth token. |
| `scripts/nightly_pipeline.py` | 15 KB | 457 | nightly_pipeline (03:00) | **ACTIVE** | Well-structured with checkpointing. Telegram send step (L167-190) is incomplete — writes temp file but never actually calls `telegram_media_send_v2.py`. |
| `scripts/kara_poll_iskra_results.py` | 3 KB | 115 | Искра results → Кара proxy | **ACTIVE** | Clean implementation. Uses shared memory queue with file locking. Fallback to `tasks/results/` when queue corrupt. |
| `scripts/context_split.py` | 23 KB | 733 | Session Size Monitor (2h) | **ACTIVE** | Uses OpenRouter API. Proper UTF-8 encoding. Well-tested. |
| `scripts/memory_cleanup.py` | 19 KB | 549 | nightly_pipeline (sub-step) | **ACTIVE** | Called by nightly_pipeline. Handles UTF-8. |
| `scripts/sync_obsidian.py` | 15 KB | 488 | nightly_pipeline (sub-step) | **ACTIVE** | Called by nightly_pipeline's `obsidian_sync()`. |
| `scripts/auto_reflection.py` | 26 KB | 802 | Daily reflection cron | **ACTIVE** | Telegram integration via env vars (`TELEGRAM_BOT_TOKEN`). Properly uses env vars, not hardcoded. |
| `scripts/auto_memory_cleanup.py` | 11 KB | 310 | Memory management cron | **ACTIVE** | Hardcoded `Path.home() / ".openclaw"` — correct for Windows deployment. |
| `scripts/ollama_monitor.py` | 23 KB | 722 | Ollama health (background) | **ACTIVE** | Auto-restart loop. Proper VRAM monitoring via nvidia-smi. |

### 2.2 Active Support Scripts (used by cron scripts or tools)

| Script | Size | Lines | Classification | Notes |
|--------|------|-------|----------------|-------|
| `scripts/telegram_sender.py` | 12 KB | 329 | **ACTIVE** | Uses `TELEGRAM_BOT_TOKEN` from env (not hardcoded). Proper retry logic. Used by announce delivery. |
| `scripts/media_tool.py` | 4 KB | 143 | **ACTIVE** | Photo resize for Telegram via ffmpeg. Used by telegram_sender. |
| `scripts/media_tool.sh` | 1 KB | 52 | **ACTIVE** | Bash wrapper for media_tool.py. |
| `scripts/sqlite_helper.py` | 449 B | 15 | **ACTIVE** | DB connection helper. |
| `scripts/nouz_common.py` | 3 KB | 85 | **ACTIVE** | Shared code for Nouz search. |
| `scripts/run_task.py` | 1 KB | 42 | **ACTIVE** | YAML task runner. |
| `scripts/__init__.py` | 47 B | 1 | **ACTIVE** | Package marker. |
| `scripts/bootstrap.sh` | 322 B | 16 | **ACTIVE** | Env setup script. |
| `scripts/healthcheck.sh` | 752 B | 29 | **ACTIVE** | CI healthcheck. |
| `src/coordination/iskra_kara_shared_memory.py` | ~10 KB | 341 | **ACTIVE** | Queue implementation for Iskra→Kara handoff. File-locked JSON. |
| `src/monitoring/session_monitor.py` | ~4 KB | 123 | **BUGGY** | Hardcoded `C:/Users/user/...` path on L13. |

### 2.3 On-Demand Utilities (not cron, manual use)

| Script | Size | Lines | Classification | Notes |
|--------|------|-------|----------------|-------|
| `scripts/obsidian_link_checker.py` | 15 KB | 422 | ACTIVE | Manual vault check. |
| `scripts/obsidian_dashboard.py` | 15 KB | 465 | ACTIVE | Dashboard generation. |
| `scripts/obsidian_researcher.py` | 4 KB | 96 | ACTIVE | Research helper. |
| `scripts/conversation_extractor.py` | 25 KB | 735 | ACTIVE | Session log extraction. |
| `scripts/doc_generator.py` | 22 KB | 643 | ACTIVE | Documentation generation. |
| `scripts/tool_discovery.py` | 15 KB | 405 | ACTIVE | Tool finding. |
| `scripts/workflow_vcs.py` | 20 KB | 561 | ACTIVE | Workflow version control. |
| `scripts/ollama_batch.py` | 12 KB | 391 | ACTIVE | Batch Ollama queries. |
| `scripts/ollama_benchmark.py` | 22 KB | 634 | ACTIVE | Model benchmarking. |
| `scripts/ollama_bridge.py` | 10 KB | 260 | ACTIVE | API bridge. |
| `scripts/ollama_manifest_fix.py` | 13 KB | 375 | ACTIVE | Manifest repair. |
| `scripts/ollama_model_manager.py` | 23 KB | 630 | ACTIVE | Model management. |
| `scripts/ollama_queue_monitor.py` | 3 KB | 104 | ACTIVE | Queue monitoring. |
| `scripts/ollama_batch_download.py` | 2 KB | 73 | ACTIVE | Model downloading. Log file lacks encoding (L32). |
| `scripts/comfy_video_pipeline.py` | 30 KB | 681 | ACTIVE | Video pipeline. |
| `scripts/comfy_auto_quality.py` | 18 KB | 387 | ACTIVE | Quality automation. |
| `scripts/face_swap_batch.py` | 13 KB | 386 | ACTIVE | Face swap. |
| `scripts/face_clustering.py` | 25 KB | 680 | ACTIVE | Face grouping. |
| `scripts/scene_composer.py` | 20 KB | 543 | ACTIVE | Scene creation. |
| `scripts/lora_selector.py` | 16 KB | 441 | ACTIVE | LoRA selection. |
| `scripts/anatomy_analyzer.py` | 21 KB | 538 | ACTIVE | Anatomy analysis. |
| `scripts/batch_image_optimizer.py` | 15 KB | 417 | ACTIVE | Image optimization. |
| `scripts/image_format_migrator.py` | 8 KB | 263 | ACTIVE | Format migration. |
| `scripts/video_thumbnail_generator.py` | 13 KB | 344 | ACTIVE | Thumbnail generation. |
| `scripts/process_images.py` | 3 KB | 86 | ACTIVE | Image processing. |
| `scripts/photo_archive_report.py` | 8 KB | 243 | ACTIVE | Photo reports. |
| `scripts/photo_deduplication.py` | 12 KB | 356 | ACTIVE | Photo dedup. |
| `scripts/exif_date_normalizer.py` | 7 KB | 213 | ACTIVE | EXIF normalization. |
| `scripts/nouz_search.py` | 19 KB | 557 | ACTIVE | Nouz knowledge search. |
| `scripts/nouz_yaml_tagger.py` | 4 KB | 113 | ACTIVE | YAML tagging. |
| `scripts/raw_pipeline.py` | 2 KB | 60 | ACTIVE | RAW photo pipeline. |
| `scripts/marketplace_dashboard.py` | 8 KB | 248 | ACTIVE | Marketplace analytics. |
| `scripts/goal_decomposer.py` | 9 KB | 276 | ACTIVE | Goal breakdown. |
| `scripts/quality_gate.py` | 30 KB | 819 | ACTIVE | Quality checks. |
| `scripts/optimize_context.py` | 25 KB | 668 | ACTIVE | Context optimization. |
| `scripts/memory_analytics.py` | 22 KB | 646 | ACTIVE | Memory analytics. |
| `scripts/ami_parser.py` | 15 KB | 413 | ACTIVE | AMI parsing. |
| `scripts/error_learning.py` | 27 KB | 823 | ACTIVE | Error pattern learning. |
| `scripts/queue_manager.py` | 40 KB | 1189 | ACTIVE | Task queue management. |
| `scripts/proactive_scout.py` | 28 KB | 758 | ACTIVE | Proactive task prediction. |
| `scripts/health_dashboard.py` | 4 KB | 108 | **BUGGY** | Hardcoded Windows drives (L27). |
| `scripts/backup_custom_nodes.ps1` | 4 KB | 113 | ACTIVE | ComfyUI backup. Hardcoded paths but functional on target machine. |

### 2.4 Potentially Dead / Duplicated

| Script | Size | Lines | Classification | Notes |
|--------|------|-------|----------------|-------|
| `scripts/self_improvement/auto_reflection.py` | ~30 KB | 983 | **DEAD (duplicate)** | Near-duplicate of `scripts/auto_reflection.py`. Both exist; unclear which cron uses. The `scripts/` version (802 lines) is likely the canonical one. |
| `scripts/self_improvement/__init__.py` | — | — | **DEAD** | Package marker for dead duplicate. |

### 2.5 Root-Level Scripts (outside `scripts/`)

| Script | Size | Lines | Classification | Notes |
|--------|------|-------|----------------|-------|
| `yandex_metrika.py` (root) | 11 KB | 324 | **BUGGY** | See §3.1 |
| `tool_discovery.py` (root) | 15 KB | — | ACTIVE | Tool discovery (root copy). |
| `business_dashboard.py` (root) | 7 KB | — | ACTIVE | Business analytics. |

---

## 3. Detailed Bug Analysis

### 3.1 `yandex_metrika.py` — Hardcoded Credentials (CRITICAL)

```python
YANDEX_LOGIN = "Sakura565"
YANDEX_TOKEN = "Nastia56"
```

**Lines 25-26** contain plaintext credentials committed to the repository. This is a security risk:
- The token `Nastia56` looks like a **password**, not an OAuth token. Yandex Metrika API requires OAuth tokens (format: `y0_AgAAAA...`), so this token is almost certainly **invalid for API access** and all cron calls to Metrika API will fail with 401.
- Even if the token were valid, it should never be hardcoded.

**Fix:** Move to environment variables:
```python
YANDEX_LOGIN = os.environ.get("YANDEX_METRIKA_LOGIN", "")
YANDEX_TOKEN = os.environ.get("YANDEX_METRIKA_TOKEN", "")
```

**Additional issues:**
- No retry logic on `urllib.request.urlopen()` — any transient API error kills the cron run.
- No timeout handling besides the default `self.timeout = 30`.
- The cron task references `metrika_daily.py` which does not exist; actual file is `yandex_metrika.py`.

### 3.2 `log_download_status.ps1` — MISSING (HIGH)

The cron task 'Undress pipeline — статус качалок' calls `log_download_status.ps1`.
**This file does not exist in the repository.** Only `backup_custom_nodes.ps1` exists as a `.ps1` file.

The cron will fail every hour with "script not found".

**Fix:** Either:
1. Create `scripts/log_download_status.ps1` that checks ComfyUI model downloads (Pony v6 XL, XLabs FLUX IP-Adapter, InstantX FLUX IP-Adapter, inswapper_128.onnx).
2. Update the cron payload to use an existing script.

### 3.3 `nightly_pipeline.py` — Telegram Send Is a No-Op (HIGH)

The `send_telegram_summary()` function (lines 167-190):
1. Constructs the path to `telegram_media_send_v2.py` ✓
2. Checks if it exists ✓
3. Writes the message to a temp file ✓
4. **Logs the brief path and returns "OK" without ever invoking the telegram script** ✗

```python
def send_telegram_summary(brief: str) -> str:
    # ... constructs path, writes temp file ...
    log(f"Brief ready at: {Path.home() / '.openclaw' / 'workspace' / 'morning_brief.md'}")
    return "OK"  # <-- Never actually sends!
```

The Telegram delivery step always returns success without sending anything.

**Fix:** Either invoke the external script, or use the repo's own `telegram_sender.py`:
```python
from scripts.telegram_sender import TelegramConfig, TelegramSender
config = TelegramConfig.from_env()
sender = TelegramSender(config)
# Send as message via Bot API
```

### 3.4 `src/monitoring/session_monitor.py` — Hardcoded Windows Path (MEDIUM)

**Line 13:**
```python
LOG_PATH = Path("C:/Users/user/.openclaw/workspace/memory")
```

This will fail on any non-Windows system or any Windows user with a different username.

**Fix:**
```python
LOG_PATH = Path.home() / ".openclaw" / "workspace" / "memory"
```

### 3.5 `health_dashboard.py` — Hardcoded Drive Letters (MEDIUM)

**Line 27:**
```python
drives = {"C": "C:\\", "E": "E:\\", "H": "H:\\", "Q": "Q:\\"}
```

Windows-specific. Will produce errors on every non-target machine.

**Fix:** Auto-discover drives or read from config/env.

### 3.6 `ollama_batch_download.py` — Missing UTF-8 Encoding (LOW)

**Line 32:**
```python
with open(LOG, "a") as f:
```

No `encoding="utf-8"`. If model names or error messages contain non-ASCII, this will fail on systems where the default encoding is not UTF-8.

**Fix:**
```python
with open(LOG, "a", encoding="utf-8") as f:
```

---

## 4. Cron-Specific Issues

### 4.1 Backslash Path Problem in Cron Sessions

The user reports PowerShell breaking backslashes (`C:\Users\...` → `C:Users...`).
**Root cause:** When the cron model generates `python C:\Users\user\.openclaw\skills\...`, PowerShell interprets `\U`, `\u`, `\.` as escape sequences.

**Affected scripts:** Any cron payload that invokes scripts by Windows path.

**Fix options:**
1. Use forward slashes in all Python path arguments: `python C:/Users/user/.openclaw/...`
2. Wrap calls in a `.bat` file that handles escaping.
3. Use `-File` flag with PowerShell: `powershell -ExecutionPolicy Bypass -File "path.ps1"`
4. Move the external `telegram_media_send_v2.py` into this repo and reference via `python -m` syntax.

### 4.2 `apply_patch` Failures with Cyrillic Paths

The user reports `apply_patch` failing when writing to markdown files with Cyrillic names in cron sessions.

**Root cause:** OpenClaw's `apply_patch` may not properly handle UTF-8/Windows-1251 paths in cron execution context.

**Mitigation:** Cron payloads should avoid apply_patch for Cyrillic-path files. Use Python scripts with explicit `encoding="utf-8"` and `Path` objects instead.

### 4.3 `toolsAllow` Reset on `cron.update`

The user reports that patching a cron task via `cron.update` resets `payload.toolsAllow` to `[]`.

**Impact:** An empty `toolsAllow` array blocks all tool access in the cron session, making the model unable to execute scripts, write files, or perform any action.

**Recommendation:** After any `cron.update`, verify `toolsAllow` is not empty. If using the API, always explicitly set `toolsAllow` to the needed tools list.

---

## 5. TOP-5 Most Impactful Improvements

### #1: Move credentials out of `yandex_metrika.py` (Security + Functionality)
**File:** `yandex_metrika.py`, lines 25-26
**Action:** Replace hardcoded `YANDEX_LOGIN`/`YANDEX_TOKEN` with `os.environ.get()`. Add a proper OAuth token. Add retry logic to `_request_json()`.

### #2: Create `log_download_status.ps1` (Missing Cron Script)
**File:** `scripts/log_download_status.ps1` (new)
**Action:** Create the script that checks ComfyUI model download status for Pony v6 XL, XLabs FLUX IP-Adapter, InstantX FLUX IP-Adapter, and `inswapper_128.onnx`. Currently the hourly cron fails silently.

### #3: Fix `nightly_pipeline.py` Telegram delivery (Broken Feature)
**File:** `scripts/nightly_pipeline.py`, lines 167-190
**Action:** Replace the no-op `send_telegram_summary()` with actual Telegram delivery using the repo's own `scripts/telegram_sender.py`. This makes the morning brief actually reach the user.

### #4: Fix hardcoded path in `session_monitor.py` (Portability)
**File:** `src/monitoring/session_monitor.py`, line 13
**Action:** Replace `Path("C:/Users/user/.openclaw/workspace/memory")` with `Path.home() / ".openclaw" / "workspace" / "memory"`.

### #5: Resolve `auto_reflection.py` duplication (Maintenance)
**Files:** `scripts/auto_reflection.py` (802 lines) vs `scripts/self_improvement/auto_reflection.py` (983 lines)
**Action:** Determine which is canonical, mark the other as deprecated. The `self_improvement/` version has more features (session transcript analysis) but may be the older copy.

---

## 6. Cron Payload Recommendations

### Payloads That Need Recreation

| Cron Task | Issue | Recommended Fix |
|-----------|-------|-----------------|
| **Undress pipeline — статус качалок** | Calls `log_download_status.ps1` which doesn't exist | Create the script or update payload to reference existing script |
| **Мебель: SEO-улучшение дня** | References `metrika_daily.py` (doesn't exist) | Update to `python yandex_metrika.py` or `python -m yandex_metrika` |
| **nightly_pipeline** (Telegram step) | `send_telegram_summary()` is a no-op | Fix the function to use `telegram_sender.py` from this repo |

### Payloads That Should Be Verified After Any `cron.update`

All cron payloads — verify `toolsAllow` is not empty after patching.

---

## 7. Security Recommendations

1. **Rotate the Yandex token** — `Nastia56` has been committed to git history. Even after removing from code, rotate the credential.
2. **Audit `telegram_media_send_v2.py`** on the Windows machine — the user mentions a hardcoded `BOT_TOKEN` (`869732...YSvE`). This file is outside this repo at `~/.openclaw/skills/telegram-media-send/scripts/`. Verify it uses env vars.
3. **Add `.env` support** — Scripts that need credentials should read from env vars or a `.env` file (not committed to git).

---

## 8. Encoding & Platform Notes

- **UTF-8 handling is generally good** across the codebase. Most file operations use `encoding="utf-8"`.
- **Exception:** `ollama_batch_download.py` line 32, `health_dashboard.py` stdout wrapper on line 5.
- **Windows path compatibility:** Most scripts use `pathlib.Path` which handles both `/` and `\` correctly. The problematic cases are:
  - `session_monitor.py` — hardcoded string path
  - `health_dashboard.py` — hardcoded drive letters
  - `face_swap_batch.yaml` — hardcoded `C:\Users\user\comfyui\...` paths (acceptable for deployment config)
  - `backup_custom_nodes.ps1` — hardcoded defaults (acceptable, overridable via parameters)
