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

`python -m scripts.tool_discovery` walks **all** Python modules under the repo (skipping `__init__.py`, caches, and virtualenv trees), infers capabilities from names, docstrings, **function signatures**, and **decorators**, records **I/O** and **safety constraints**, maps dependencies, writes **`docs/tool_discovery.md`** by default, and suggests tools for a goal with scored reasoning.

### Commands

```bash
python -m scripts.tool_discovery registry
python -m scripts.tool_discovery analyze --format json
python -m scripts.tool_discovery docs
python -m scripts.tool_discovery docs --output docs/tool_discovery.md
python -m scripts.tool_discovery suggest "monitor queue latency" --context "safe local logs" --top 3
```

### What it analyzes

- **Capability analysis** from script paths, module docstrings, public function names, parameter and return annotations, per-function docstrings, argparse subcommands, and decorator patterns (for example Click or Typer).
- **Dependency analysis** using direct module imports and inferred relationships from shared capabilities or import sets.
- **Risk and I/O profiles** to distinguish low, medium, and high operational risk and filesystem, network, process, database, and structured-data behavior.
- **Safety constraints** such as `network_egress`, `subprocess_execution`, and filesystem read or write signals derived from imports and source patterns.
- **Contextual tool suggestion** via `suggest()` or `suggest_tools()` with explicit reasoning about capability fit, docstrings, I/O fit, safety, and possible tool chains.

