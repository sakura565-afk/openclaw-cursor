# Yandex Metrika Anomaly Detector

Daily cron that reads cached Metrika stats from `data/metrika_cache/` (written
by `scripts/metrika_daily.py` and `scripts/metrika_landings.py`) and
Telegram-alerts when traffic deviates sharply from the 14-day baseline.

## What it does

1. Scans `data/metrika_cache/**/*.json` — **no Metrika API calls** in this script.
2. For each counter, computes a 14-day baseline (mean visits/visitors,
   excluding today).
3. Flags anomalies:
   - **Sites:** drop &gt; 35% or rise &gt; 100%
   - **Landing pages** (`kind: landing` or under `landings/`): drop &gt; 50% or
     rise &gt; 100%
4. Deduplicates via `data/metrika_anomalies.jsonl` so the same ongoing anomaly
   is not alerted every day.
5. On **first run** (history file missing), seeds the jsonl without Telegram
   spam.
6. When `--send-alert` is set, sends Telegram via `telegram_media_send_v2.py`.

Default CLI mode is **dry-run**: prints anomalies, does **not** send Telegram,
does **not** append history.

## Cache format

Each file under `data/metrika_cache/` (e.g. `sites/94834593.json`,
`landings/promo.json`):

```json
{
  "counter_id": 94834593,
  "name": "amadey.ru",
  "kind": "site",
  "daily": [
    {"date": "2026-08-08", "visits": 1200, "visitors": 900}
  ]
}
```

`series` is accepted as an alias for `daily`; `users` for `visitors`.

Target sites: **amadey.ru** (counter `94834593`), **divaninfo.ru** (counter
`63403`), plus landing counters from `metrika_landings.py`.

## History file

`data/metrika_anomalies.jsonl` — one JSON object per line:

```json
{"date": "2026-08-22", "key": "94834593:visits:drop", "counter_id": 94834593, "counter_name": "amadey.ru", "kind": "site", "metric": "visits", "anomaly_type": "drop", "today": 650, "avg_14d": 1200, "delta_pct": -45.83, "z_score": -2.1, "alerted": true, "recovered": false}
```

Recovery records close an active alert episode:

```json
{"date": "2026-08-23", "key": "94834593:visits:drop", "recovered": true, "alerted": false}
```

## Register (Windows Task Scheduler)

```powershell
powershell -ExecutionPolicy Bypass -File C:\Users\user\.openclaw\workspace\scripts\register_metrika_anomaly_cron.ps1
```

Creates task **Metrika-Anomaly-Daily** daily at **09:30 MSK** (after
**Metrika-Daily-Telegram** at 09:00):

```text
powershell -ExecutionPolicy Bypass -File C:\Users\user\.openclaw\workspace\scripts\metrika_anomaly.ps1
```

Dry-run (print only):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\register_metrika_anomaly_cron.ps1 -DryRun
```

## Run manually

Python (repo root):

```bash
python -m scripts.metrika_anomaly
python -m scripts.metrika_anomaly --print-json
python -m scripts.metrika_anomaly --send-alert --update-history
```

PowerShell wrapper (redirects stdout/stderr under `logs/`):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\metrika_anomaly.ps1
```

Logs: `logs/metrika_anomaly_YYYY-MM-DD.out.log` / `.err.log`.

## Alert format

Text file (emoji OK):

```text
⚠️ Аномалии Яндекс Метрики (1):

📉 Аномалия метрики 2026-08-22: amadey.ru visits=650 vs avg=1200 (-45.8%, z=-2.1)
```

Telegram caption (no emoji, cp1251-safe):

```text
Metrika anomaly 2026-08-22
```

Invocation:

```text
python C:\Users\user\.openclaw\skills\telegram-media-send\scripts\telegram_media_send_v2.py <text_file> --caption "Metrika anomaly 2026-08-22"
```

## Env vars

| Variable | Purpose |
| --- | --- |
| `YANDEX_METRIKA_TOKEN` | Metrika OAuth token (used by `metrika_daily.py` / `metrika_landings.py`, not this script). Stored at `~/.openclaw/credentials/yandex_metrika_token`. |
| `METRIKA_ANOMALY_TELEGRAM_SCRIPT` | Override path to `telegram_media_send_v2.py`. |
| `TELEGRAM_CHAT_ID` / `CHAT_ID` | Defaults to `25979298` (Andrey / Istranewbot). |
| `PYTHONIOENCODING` | Set to `utf-8` by the `.ps1` wrapper. |
