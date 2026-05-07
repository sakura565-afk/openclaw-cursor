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

## Error Learning System

`scripts/error_learning.py` records errors hit during automation runs to
`.learnings/ERRORS.md` so subsequent runs can read them back, dedupe repeats,
and surface fix hints.

### CLI

```bash
python -m scripts.error_learning log \
    --message "ModuleNotFoundError: No module named 'requests'" \
    --category boot \
    --context-json '{"step":"startup"}'

python -m scripts.error_learning list --limit 10
python -m scripts.error_learning suggest --message "FileNotFoundError: cfg.yaml"
```

### Library

```python
from scripts.error_learning import log_error, get_recent_errors, suggest_fixes

try:
    do_thing()
except Exception as exc:
    log_error(exc, category="ollama_pull", context={"model": "llama3.2"})

for record in get_recent_errors(limit=5, category="ollama_pull"):
    print(record.summary())

for tip in suggest_fixes("connection refused"):
    print("-", tip)
```

Repeated identical errors increment an `Occurrences` counter and refresh
`Last Seen` rather than appending duplicate sections. Each section is keyed by
a stable signature derived from the category and a normalized form of the
message (numbers/paths/quoted strings are squashed).

### Tests

```bash
python -m unittest tests.test_error_learning
```

