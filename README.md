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

## Tool Discovery CLI

The `tool_discovery.py` module at the repository root (also available as `python -m scripts.tool_discovery`) scans the tree for `SKILL.md` files, analyzes `scripts/*.py` plus runnable `src/**/*.py` modules (argparse + `__main__`), writes a JSON catalog, tracks usage counts for ranking, and suggests tools or skills from a task description.

### Commands

```bash
python3 tool_discovery.py --root . index
python3 tool_discovery.py --root . list
python3 tool_discovery.py --root . list --format json
python3 tool_discovery.py analyze --format json
python3 tool_discovery.py analyze --scripts-only --format json
python3 tool_discovery.py docs --output docs/tool_discovery.md
python3 tool_discovery.py suggest "monitor queue latency" --context "safe local logs" --top 3
python3 tool_discovery.py suggest "monitor queue latency" --track
python3 tool_discovery.py suggest-all "sync notes to Obsidian" --top 5
python3 tool_discovery.py record-usage ollama_monitor --delta 1
```

Catalog output defaults to `$OPENCLAW_WORKSPACE/catalog/`, `$OPENCLAW_TOOL_CATALOG_DIR`, or `./.openclaw/catalog/`, containing `tool_skill_index.json`, `tool_usage.json`, and a short `README.txt`.

### What it analyzes

- **Skills**: any `SKILL.md` under the repo root, `~/.openclaw/workspace`, or `~/.openclaw/skills` (skipping `.git`, `node_modules`, virtualenvs), with title, description, and use-case bullets.
- **Deep capability analysis** for tools from script name, functions, subcommands, and docstring signals.
- **Dependency analysis** using direct module imports and inferred relationships from shared capabilities/import sets.
- **Risk and I/O profiles** to distinguish low/medium/high operational risk and filesystem/network/process behavior.
- **Contextual tool suggestion** scoring with explicit reasoning about capability fit, I/O fit, safety constraints, possible tool chains, and optional usage-frequency boosts.

Run tests with:

```bash
python3 -m unittest tests.test_tool_discovery
```

