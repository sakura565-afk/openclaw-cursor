# openclaw-cursor

OpenClaw orchestration via Cursor Cloud Agent.

## Ollama Model Manager

`python -m scripts.ollama_model_manager` provides a stdlib-only CLI for managing local Ollama models used by OpenClaw.

### Commands

```bash
python -m scripts.ollama_model_manager list
python -m scripts.ollama_model_manager pull llama3.2
python -m scripts.ollama_model_manager remove llama3.2
python -m scripts.ollama_model_manager remove llama3.2 --yes
python -m scripts.ollama_model_manager show llama3.2
python -m scripts.ollama_model_manager search llama
python -m scripts.ollama_model_manager cleanup
python -m scripts.ollama_model_manager cleanup --days 45
```

### Features

- Lists local models from `ollama list` in a colored table with model name, size, and modified date.
- Pulls models with a live progress table that includes layer progress, transfer speed, and ETA.
- Checks free disk space before pulling and warns when less than 10 GB is available.
- Removes models through `ollama rm` with a confirmation prompt by default.
- Shows model metadata, parameters, and Modelfile configuration using `ollama show`.
- Searches for new models with `ollama search` when the local Ollama CLI supports that command.
- Suggests cleanup candidates for models older than 30 days by using the `MODIFIED` value from `ollama list` as the local staleness signal.

### Tests

Run the focused test suite with:

```bash
python -m unittest tests.test_ollama_model_manager
```

## Auto-Reflection Cron

`scripts/auto_reflection.py` is a self-reflection job that scans markdown
session transcripts under `memory/`, identifies what worked and what failed,
and writes concise actionable insights to `.learnings/LEARNINGS.md`.

### Usage

```bash
python3 scripts/auto_reflection.py                         # last 7 days
python3 scripts/auto_reflection.py --days 1                # daily run
python3 scripts/auto_reflection.py --days 30 \
    --reference-date 2026-05-07 \
    --json-out logs/reflection_2026-05-07.json
```

### Features

- Walks `memory/**.md` and splits each transcript into heading-anchored blocks.
- Tags each block with a category (`image`, `video`, `model`, `memory`,
  `automation`, `telegram`, `test`, `general`) using filename + content
  heuristics.
- Detects success and failure patterns via keyword scans, markdown table status
  cells, and counter-style metrics (e.g. `'failed': 0`, `processed: 12`).
  Suppresses false positives when the corresponding metric is zero.
- Filters entries by a configurable lookback window (`--days`) anchored on
  `--reference-date` (defaults to today, UTC).
- Renders a per-day section in `.learnings/LEARNINGS.md`. Reruns for the same
  reference date overwrite the existing block in place (idempotent), so the
  file is safe to commit.
- Optional JSON dump (`--json-out`) for downstream tooling.
- File-based lock (`.learnings/.cron.lock`, configurable) prevents concurrent
  runs and auto-reclaims stale locks after `--lock-timeout` seconds.

### Cron example

```cron
# Daily at 03:15 UTC
15 3 * * * cd /srv/project && python3 scripts/auto_reflection.py \
    --days 1 >> logs/auto_reflection.log 2>&1
```

### Tests

```bash
python3 -m unittest tests.test_auto_reflection
```

