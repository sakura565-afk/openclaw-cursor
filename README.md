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

## Tool Discovery

`python -m scripts.tool_discovery` scans an OpenClaw workspace for skills,
scripts, and MCP servers and produces a searchable tool registry.

### Default scan locations

| Kind        | Default path                                                      |
| ----------- | ----------------------------------------------------------------- |
| Skills      | `%USERPROFILE%/.openclaw/workspace/skills`                        |
| Scripts     | `%USERPROFILE%/.openclaw/workspace/scripts`                       |
| MCP servers | `%USERPROFILE%/.openclaw/workspace/mcp_servers`                   |

On non-Windows hosts the equivalent `~/.openclaw/workspace/...` paths are
used. Override any path with `--skills-dir`, `--scripts-dir`, `--mcp-dir`,
or the `OPENCLAW_SKILLS_DIR` / `OPENCLAW_SCRIPTS_DIR` / `OPENCLAW_MCP_DIR`
/ `OPENCLAW_WORKSPACE` environment variables.

### Commands

```bash
python -m scripts.tool_discovery scan
python -m scripts.tool_discovery scan --output registry.json
python -m scripts.tool_discovery search "image upscale" --limit 5
python -m scripts.tool_discovery show skill:image-toolbox
python -m scripts.tool_discovery summary --format text
python -m scripts.tool_discovery summary --format markdown --output TOOLS.md
python -m scripts.tool_discovery export --format json --output registry.json
```

### Features

- Discovers skills by reading `SKILL.md` frontmatter (with a stdlib-only
  YAML-subset parser) and falls back to Markdown sections (`## Parameters`,
  `## Use cases`) when frontmatter is absent.
- Discovers scripts in any common language. Python scripts are parsed with
  `ast` to extract module docstrings and `argparse.add_argument` calls,
  including `type`, `choices`, `default`, `required`, and `store_true`
  flags. Shell / JS / PowerShell scripts use leading-comment heuristics.
- Discovers MCP servers from the standard `mcp_servers.json` /
  `mcpServers` shape, from `servers: [...]` lists, and from per-server
  directories that ship a `mcp.json` / `manifest.json` / `package.json`.
- Builds a searchable registry with deterministic IDs (`skill:<slug>`,
  `script:<slug>`, `mcp:<slug>`), normalized search text, and a simple
  weighted ranker (`scripts.tool_discovery.search_registry`).
- Exports JSON, Markdown, and a compact text summary suitable for
  injection into agent context windows.
- Tolerates missing roots, malformed JSON, and broken Python syntax by
  recording a `parse_warnings` list on the affected tool instead of
  failing the whole scan.

### Tests

```bash
python -m unittest tests.test_tool_discovery
```

