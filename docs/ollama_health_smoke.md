# Ollama Health Smoke

Daily cron smoke test that walks the OpenClaw `DEFAULT_CHAIN` (see
`scripts/ollama_chain.py`) and records per-model latency / token metrics.

## What it does

1. Probes each of the 9 chain models with: `2+2? Just the number, no explanation.`
2. Per-model timeout: **15 seconds** (timeout ⇒ fail).
3. Writes `data/ollama_health_YYYY-MM-DD.json` (UTC date) with fields:
   `model`, `ok`, `latency_s`, `eval_count`, `tokens_per_second`, `error`.
4. On any non-exempt failure, sends a Telegram alert via
   `telegram_media_send_v2.py` using Istranewbot
   (`telegram_config.json`, `chat_id=25979298`).
5. Paid `grok-4.5` failures (including known 403) do **not** alert.
   If `XAI_API_KEY` is missing, xAI models are skipped gracefully.
6. Deletes `data/ollama_health_*.json` older than 30 days.

## Register (Windows Task Scheduler)

From an elevated or interactive PowerShell session:

```powershell
powershell -ExecutionPolicy Bypass -File C:\Users\user\.openclaw\workspace\scripts\register_ollama_health_cron.ps1
```

This creates task **Ollama-Health-Daily** running daily at **06:00 MSK**:

```text
powershell -ExecutionPolicy Bypass -File C:\Users\user\.openclaw\workspace\scripts\ollama_daily_smoke.ps1
```

Dry-run:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\register_ollama_health_cron.ps1 -DryRun
```

## Run manually

Python (repo root):

```bash
python -m scripts.ollama_daily_smoke
python -m scripts.ollama_daily_smoke --json
python -m scripts.ollama_daily_smoke --no-alert
```

PowerShell wrapper (redirects stdout/stderr under `logs/`):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\ollama_daily_smoke.ps1
powershell -ExecutionPolicy Bypass -File scripts\ollama_daily_smoke.ps1 -NoAlert
```

## Interpreting output

Console summary:

```text
Wrote data/ollama_health_2026-08-21.json
ok=8/9 fail=1 alertable=1
  [OK] minimax-m3:cloud latency_s=1.2 eval_count=2 tok/s=1.6 error=None
  [FAIL] gpt-oss:20b latency_s=15.0 eval_count=0 tok/s=0.0 error=timeout after 15s
  [SKIP] grok-4.5 ... error=skipped: XAI_API_KEY missing
```

JSON report keys:

| Field | Meaning |
| --- | --- |
| `ok` | Probe succeeded (or graceful xAI skip). |
| `latency_s` | Wall time for the `ollama run` probe. |
| `eval_count` | Tokens from verbose stderr when available, else estimate. |
| `tokens_per_second` | `eval_count / latency` (or parsed verbose rate). |
| `error` | Failure / skip reason. |
| `alert_failure_count` | Failures that triggered (or would trigger) Telegram. |
| `telegram_alert` | Result of the alert send attempt. |
| `cleaned_up` | Health filenames removed by the 30-day retention pass. |

Process exit code is `1` when `alert_failure_count > 0`, else `0`.
