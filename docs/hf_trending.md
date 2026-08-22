# HuggingFace Trending Model Watcher

Daily cron that watches HuggingFace Hub for new model releases in families we
care about and Telegram-alerts Andrey (Istranewbot, `chat_id=25979298`) when
something new appears.

## What it does

1. Pulls Hub model lists (stdlib `urllib` only — no `huggingface_hub`):
   - `GET /api/models?sort=downloads&direction=-1&limit=50`
   - `GET /api/models?sort=createdAt&direction=-1&limit=50`
2. Keeps models whose **name** (segment after `org/`) matches a family
   prefix (case-insensitive).
3. Diffs against `data/hf_seen.json`. On **first run** (file missing), the
   pulled set is treated as already-seen — no alert flood for the whole top-50.
4. When the diff is non-empty and `--send-alert` is set, sends a Telegram
   message via `telegram_media_send_v2.py`.
5. When `--update-state` is set, atomically writes the updated seen-map
   (`.tmp` → `os.replace`).

Default CLI mode is **dry-run**: prints the diff, does **not** send Telegram,
does **not** update state.

## Families

| Prefix | Notes |
| --- | --- |
| `qwen*` | Qwen3.x, Qwen3.5+, Qwen-VL |
| `gemma*` | Gemma 2/3/4 |
| `devstral*` | Mistral Devstral coding |
| `ministral*` | Mistral small |
| `mistral*` | Mistral family (after more-specific prefixes) |
| `minimax*` | MiniMax M-series |
| `nemotron*` | NVIDIA Nemotron |
| `llama*` | Meta Llama |
| `kimi*` | Moonshot Kimi |
| `deepseek*` | DeepSeek |
| `glm*` | Zhipu GLM |

Override with `--family qwen,gemma`.

## State file

`data/hf_seen.json`:

```json
{
  "schema_version": 1,
  "last_run": "2026-08-21T08:00:00Z",
  "seen": {
    "Qwen/Qwen3.8-27B": "2026-08-19",
    "google/gemma-4-31b": "2026-08-15"
  }
}
```

## Register (Windows Task Scheduler)

```powershell
powershell -ExecutionPolicy Bypass -File C:\Users\user\.openclaw\workspace\scripts\register_hf_trending_cron.ps1
```

Creates task **HF-Trending-Daily** daily at **08:00 MSK**:

```text
powershell -ExecutionPolicy Bypass -File C:\Users\user\.openclaw\workspace\scripts\hf_trending.ps1
```

Dry-run (print only):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\register_hf_trending_cron.ps1 -DryRun
```

## Run manually

Python (repo root):

```bash
python -m scripts.hf_trending
python -m scripts.hf_trending --print-json
python -m scripts.hf_trending --family qwen,gemma --limit 20
python -m scripts.hf_trending --send-alert --update-state
```

PowerShell wrapper (redirects stdout/stderr under `logs/`):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\hf_trending.ps1
```

Logs: `logs/hf_trending_YYYY-MM-DD.out.log` / `.err.log`.

## Output format

Human-readable dry-run:

```text
New models (2):
  - Qwen/Qwen3.9-7B-Instruct [qwen] downloads=12400 lastModified=2026-08-21
  - google/gemma-4-9b [gemma] downloads=8100 lastModified=2026-08-21
```

`--print-json` prints the diff array only.

## Alert format

```text
🆕 HuggingFace new models (3):

• Qwen/Qwen3.9-7B-Instruct (qwen, 12.4k downloads, 2026-08-21)
• google/gemma-4-9b (gemma, 8.1k downloads, 2026-08-21)
• mistralai/Devstral-Small-2 (devstral, 5.6k downloads, 2026-08-20)

Open HF: https://huggingface.co/Qwen/Qwen3.9-7B-Instruct
```

Capped at the top 10 by downloads. If more, appends
`(+N more in data/hf_seen.json)`.

Telegram is invoked as:

```text
python C:\Users\user\.openclaw\skills\telegram-media-send\scripts\telegram_media_send_v2.py <text_file> --caption "..."
```

## Env vars

| Variable | Purpose |
| --- | --- |
| `HF_TOKEN` / `HUGGINGFACE_API_KEY` | Optional Bearer token for Hub API (`hf_*`). |
| `HF_TRENDING_TELEGRAM_SCRIPT` | Override path to `telegram_media_send_v2.py`. |
| `TELEGRAM_CHAT_ID` / `CHAT_ID` | Defaults to `25979298` (Andrey / Istranewbot). |
| `PYTHONIOENCODING` | Set to `utf-8` by the `.ps1` wrapper. |
