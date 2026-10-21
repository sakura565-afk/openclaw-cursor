# Cron Registry

One registry for every Scheduled Task on the box, with a **liveness** check.

Per-task alerts answer "did the last run fail?". They cannot answer "has this
task stopped running at all?" — a dead task writes nothing, so it alerts
nothing and stays silent for weeks. The registry answers that question by
comparing the age of each task's state file against the period the task is
supposed to run at.

- Registry: `data/cron_registry.json`
- Checker: `scripts/cron_registry.py`
- Previous-run snapshot: `data/cron_registry_last.json` (written by `--diff`)
- Wrappers: `scripts/cron_registry.ps1`, `scripts/register_cron_registry_cron.ps1`
- Logs: `logs/cron_registry_YYYY-MM-DD.out.log` / `.err.log`

## What it does

1. **check** — for every registry entry: resolve `state_file` and `log_file`
   (globs allowed, newest match by mtime wins), read the state file's mtime and
   `last_status`, read the log tail, and assign a status.
2. **diff** — compare with `data/cron_registry_last.json` and keep **only what
   changed**. A stable machine produces an empty list.
3. **validate** — compare the registry with `schtasks /Query /FO CSV /V`:
   tasks that exist on the box but are not in the registry, registry entries
   that point at tasks that no longer exist, and name mismatches (case/spaces).
4. **digest** — one message, blocks sorted by severity. Telegram delivery
   happens **only when the diff is non-empty**, via `scripts/telegram_sender.py`
   (`TelegramSender.send_message`, env `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`).

## Registry format

```json
{
  "version": 1,
  "stale_factor": 1.5,
  "ignore_task_prefixes": ["\\Microsoft\\"],
  "tasks": [
    {
      "name": "Ollama-Health-Daily",
      "enabled": true,
      "command": "powershell -ExecutionPolicy Bypass -File C:\\...\\ollama_daily_smoke.ps1",
      "schedule": "daily 06:00 MSK",
      "period_hours": 24,
      "state_file": "data/ollama_health_*.json",
      "log_file": "logs/ollama_health_*.out.log",
      "alert": "telegram",
      "notes": "optional"
    }
  ]
}
```

| Field | Meaning |
| --- | --- |
| `name` | Exact Scheduled Task name (used for the `schtasks` cross-check). |
| `enabled` | `false` ⇒ status `disabled`, never alerts. |
| `command` | Documentation only: what the task actually runs. |
| `schedule` | Human-readable schedule. |
| `period_hours` | Expected period; drives the stale window. |
| `state_file` | Path or glob, relative to the workspace root (absolute also works). |
| `log_file` | Path or glob of the wrapper log; used for the exit-code signal. |
| `alert` | Alert channel for this task (currently informational). |
| `notes` | Free text — e.g. whether the path was verified on the host. |
| `stale_factor` | Optional per-task override of the global `stale_factor`. |

Top level: `version`, `stale_factor` (default `1.5`),
`ignore_task_prefixes` (task-name prefixes excluded from the "outside the
registry" report — Microsoft/vendor noise).

## How to pick `period_hours`

`period_hours` is how often the task is *expected to refresh its state file*,
and the stale window is `period_hours * stale_factor`:

| Task cadence | `period_hours` | Stale after |
| --- | --- | --- |
| every 15 minutes | `0.25` | 22 min |
| hourly | `1` | 1.5 h |
| daily | `24` | 36 h |
| weekly / one-shot | `168` | 10.5 days |

Rules of thumb:

- Set it to the real cadence, not to the cadence you wish you had.
- For a daemon with a frequent heartbeat, round **up** (the registry entry for
  `Ollama AI Server` uses `0.25` even though the heartbeat is ~5 min) so a
  single missed heartbeat does not flap the digest.
- Need a tighter window for one task? Add `"stale_factor": 1.0` to that entry.

## Statuses and severity

Severity order: `failing` > `stale` > `missing_state` > `unknown_task` > `ok`.

| Status | Meaning |
| --- | --- |
| `failing` | State reports `last_status` ∈ error/failed/…, or `ok: false`, or the log tail ends with `exit=<non-zero>`. |
| `stale` | Newest liveness signal is older than `period_hours * stale_factor` — the task missed more than 1.5 runs. |
| `missing_state` | `state_file` is configured but nothing matches the pattern. |
| `unknown_task` | Not present in `schtasks` output, or the entry declares neither `state_file` nor `log_file`. |
| `disabled` | `"enabled": false`. |
| `ok` | State file is fresh and reports no failure. |

Status is derived in that order: a failure signal wins over staleness, so a
task that is both long-silent and reporting an error shows as `failing` with
both reasons in the detail column.

`last_status` is read from the first present of `last_status`, `status`,
`state`, `result`, `health`, `outcome`; then the boolean `ok`; then counters
(`alert_failure_count`, `fail_count`, `error_count`, `failures`); then a
non-empty `error` / `last_error`. No task script has to change — if the state
file has none of those fields, only the mtime window applies.

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Everything ok, or only info-level findings (`missing_state`, `unknown_task`, `disabled`). |
| `1` | At least one task is `stale` or `failing`. |
| `2` | The registry itself is broken (missing file, bad JSON, invalid schema). |

## Reading the digest

```text
🗂 Реестр cron-задач — 2026-10-21 12:00 UTC

Изменений: 2 (alert: 1, info: 1)

🔴 Ollama-Health-Daily — failing (было: ok)
   что не так: state сообщает last_status=error (alert_failure_count=2)
   state: data/ollama_health_2026-10-21.json (обновлён 6.0ч назад, порог 36.0ч)
   последний успех: 2026-10-21T06:00:12+00:00

🟢 Cursor-CloudAgent-Poll — ok (было: stale)
   что изменилось: свежесть 0м при пороге 22м
   state: data/cursor_agent_status.json (обновлён 0м назад, порог 22м)
```

- Only **changed** tasks appear. Nothing changed ⇒
  `✅ Всё штатно: изменений с прошлого прогона нет.` and **no** Telegram message.
- Change kinds: `appeared` (new registry entry), `disappeared` (entry removed),
  `status_changed`, `age_changed`.
- `age_changed` only fires when the state file crosses a freshness bucket
  (`fresh` ≤ 1 period < `aging` ≤ stale window), so a 15-minute task does not
  generate a message on every tick.
- Registry/`schtasks` mismatches are appended under
  `Проверка реестра против schtasks:`.

## Run manually

From the repository root:

```bash
python -m scripts.cron_registry --check --no-send     # table for every task
python -m scripts.cron_registry --validate            # registry vs schtasks only
python -m scripts.cron_registry --json --no-send      # machine-readable
python -m scripts.cron_registry --send                # full run + Telegram digest
python -m scripts.cron_registry --root C:\Users\user\.openclaw\workspace
```

`--check` / `--diff` / `--validate` select stages; when none is given all three
run. `--no-check` / `--no-diff` / `--validate` turn individual stages off, and
`--no-send` always wins over `--send`. The previous-run snapshot is written only
when the `diff` stage runs, so `--check --no-send` never touches it.

PowerShell wrapper (sets `PYTHONIOENCODING=utf-8`, redirects both streams):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\cron_registry.ps1
powershell -ExecutionPolicy Bypass -File scripts\cron_registry.ps1 -NoSend
```

## Register (Windows Task Scheduler)

```powershell
powershell -ExecutionPolicy Bypass -File scripts\register_cron_registry_cron.ps1 -DryRun
powershell -ExecutionPolicy Bypass -File scripts\register_cron_registry_cron.ps1
```

Creates task **Cron-Registry-Check**, every 30 minutes:

```text
powershell -ExecutionPolicy Bypass -File C:\Users\user\.openclaw\workspace\scripts\cron_registry.ps1
```

`-DryRun` prints the `schtasks /Create …` line and exits without creating
anything. Override the interval with `-IntervalMinutes 15`, the wrapper path
with `-RegistryPs1 …`, the task name with `-TaskName …`.

## Add a new task to the registry

1. Find the exact task name: `schtasks /Query /FO LIST | findstr /i "TaskName"`.
2. Find the state file the script actually writes (check the script, not the
   docs — e.g. `scripts/hf_trending.py` writes `data/hf_seen.json`, *not*
   `data/hf_trending_state.json`). Use a glob for dated files.
3. Append an entry to `data/cron_registry.json` with `period_hours` set to the
   real cadence.
4. Verify:

```bash
python -m scripts.cron_registry --check --no-send
python -m scripts.cron_registry --validate
```

The new entry shows up as `appeared` in the next digest. Expect `unknown_task`
if the name does not match `schtasks`, and `missing_state` if the path is wrong
— both are registry bugs, not task bugs.

## Notes on `schtasks` parsing

- `schtasks /Query /FO CSV /V` repeats its header row per folder; repeated
  headers are skipped.
- The header is localized on non-English Windows; the `TaskName` column is
  located by name when possible and otherwise falls back to column index 1
  (`HostName` is always first).
- If `schtasks` is missing or fails, validation returns a single
  `[error] …` line and the rest of the run continues.

## Tests

```bash
pytest tests/test_cron_registry.py
```

All tests use `tmp_path` and a mocked `subprocess`-style callable for
`schtasks`; nothing touches the real `data/` directory and no network is used.
