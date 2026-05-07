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

## Conversation Extractor

`python -m scripts.conversation_extractor` parses OpenClaw session transcripts
(JSON, JSONL, or NDJSON) under `~/.openclaw/sessions/` and emits a structured
report containing user messages, assistant responses, tool calls, tool
outcomes, and detected error / success / learning patterns.

```bash
python -m scripts.conversation_extractor
python -m scripts.conversation_extractor --sessions-dir ~/.openclaw/sessions
python -m scripts.conversation_extractor --output report.json --markdown report.md
```

The default sessions directory is resolved from `$OPENCLAW_SESSIONS_DIR`,
falling back to `%APPDATA%/openclaw/sessions` on Windows or
`~/.openclaw/sessions` elsewhere. All path handling uses `pathlib.Path` so the
script runs unchanged on Windows, macOS, and Linux.

Run its test suite with:

```bash
python -m unittest tests.test_conversation_extractor
```

