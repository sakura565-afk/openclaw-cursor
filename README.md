# openclaw-cursor

OpenClaw orchestration via Cursor Cloud Agent.

## Video pipeline upscale

The declarative runner (`python -m video_pipeline.runner`) upscales each clip after H3 render. Configure via YAML `upscale`:

- `backend`: `auto` (default), `seedvr2`, `esrgan`, or `lanczos`
- `comfyui_url`: usually PROD ComfyUI (`http://127.0.0.1:8188`) — independent of `director_config.comfy_url` (LAB)
- `resolution`, `batch_size`, `temporal_overlap`, `blocks_to_swap`: SeedVR2 tuning

With `auto`, backends are chosen once from `/object_info` in order **seedvr2 → esrgan → lanczos**. SeedVR2 needs the `ComfyUI-SeedVR2_VideoUpscaler` pack on that ComfyUI instance; if ComfyUI is down or nodes are missing, the pipeline falls back to ffmpeg Lanczos and logs a warning. See `video_pipeline/README.md` for details.

## Tests

Run the full suite from the repository root (CI uses the same command):

```bash
python -m pytest tests/ -q
```

A successful run ends with a one-line summary, for example:

```text
851 passed, 8 subtests passed in 59.92s
```

You can also run without `-q` for more verbose output; the same summary line is printed at the end.

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

## Worktree Orchestrator (parallel Cursor agents)

`python -m scripts.worktree_orchestrator` hands every cloud agent its own `git worktree`
instead of letting all agents share one checkout, where uncommitted edits from one agent
leak into — and can break — another.

### Commands

```bash
python -m scripts.worktree_orchestrator create --slug demo
python -m scripts.worktree_orchestrator create --slug demo --base-ref main --variant 2
python -m scripts.worktree_orchestrator list
python -m scripts.worktree_orchestrator release --slug demo --keep
python -m scripts.worktree_orchestrator release --slug demo --delete-branch
python -m scripts.worktree_orchestrator reap --older-than-hours 24
python -m scripts.worktree_orchestrator doctor
```

### Behaviour

- Branch `cursor/<slug>` (or `cursor/<slug>-variant-<k>`) in `<repo>/.worktrees/<slug>`;
  `--worktree-root` and `--registry` override the defaults.
- `create` is idempotent: an existing branch or worktree is reused, never duplicated.
- `release` never deletes the branch unless `--delete-branch` is passed, so unpushed agent
  work survives. A locked or dirty worktree is logged, marked `error` in the registry, and
  reported as a failure instead of raising.
- `reap` releases worktrees whose owning pid is gone or that are older than the threshold,
  and skips anything marked `kept`.
- `list` / `doctor` reconcile the registry with `git worktree list --porcelain` (and, on
  Windows, with Scheduled Tasks), flagging `missing_on_disk`, `not_in_git` and
  `not_in_registry` drift.
- Registry: `data/worktrees.json` (`{"version": 1, "worktrees": [...]}`), written
  atomically, and carrying `agent_id` / `task_slug` / `pr_url` filled in by the caller.

### Queue manager integration

`scripts/queue_manager.py` isolates agents by default and appends the absolute worktree
path to the agent prompt. Use `--shared-checkout` for the old single-checkout behaviour:

```bash
python -m scripts.queue_manager run --branches feature-a feature-b
python -m scripts.queue_manager run --branches feature-a --shared-checkout
```

### Tests

```bash
python -m pytest tests/test_worktree_orchestrator.py
```

## Tool Discovery CLI

`python -m scripts.tool_discovery` discovers script capabilities, maps dependencies, generates docs, and suggests tools for a goal with contextual reasoning.

### Commands

```bash
python -m scripts.tool_discovery analyze --format json
python -m scripts.tool_discovery docs --output docs/tool_discovery.md
python -m scripts.tool_discovery suggest "monitor queue latency" --context "safe local logs" --top 3
python -m scripts.tool_discovery workflow --report-output docs/workflow_tool_gaps.md
```

Repository root `tool_discovery.py` runs the same workflow scan: `python3 tool_discovery.py --root . --format markdown`.

### What it analyzes

- **Deep capability analysis** based on script name, functions, subcommands, and docstring signals.
- **Dependency analysis** using direct module imports and inferred relationships from shared capabilities/import sets.
- **Risk and I/O profiles** to distinguish low/medium/high operational risk and filesystem/network/process behavior.
- **Contextual tool suggestion** scoring with explicit reasoning about capability fit, I/O fit, safety constraints, and possible tool chains.
- **Workflow gap report** (`workflow` / root `tool_discovery.py`): compares runnable modules under `src/` and `scripts/` against operator-facing files (README, `docs/`, `examples/`, `scripts/nightly_pipeline.py`, `scripts/auto_reflection.py`) and lists entrypoints that are not mentioned there.

## NOUZ integration (Obsidian typing + search)

NOUZ adds typed YAML frontmatter to markdown notes, indexes them into SQLite, stores embeddings as NumPy `.npy` files, and exposes **library-style** Python helpers that mirror MCP tools (no separate MCP server process).

### Architecture

| Layer | Role |
| ----- | ---- |
| **Vault** | Markdown notes under `OBSIDIAN_VAULT_PATH` (default `E:\Obsidianstore` on Windows). |
| **SQLite** | `openclaw-cursor/data/nouz.db` — notes table plus `wiki_links` for `[[wiki]]` edges (bidirectional navigation for bundles). |
| **Embeddings** | `openclaw-cursor/data/embeddings/<uid>.npy` — one normalized float32 vector per note after sync. |
| **Embedding backend** | MiniMax (`MINIMAX_API_KEY`, `MINIMAX_GROUP_ID`, optional `MINIMAX_EMBED_MODEL`) when configured; otherwise Ollama `OLLAMA_EMBED_URL` (default `http://127.0.0.1:11434`) and `OLLAMA_EMBED_MODEL` (default `nomic-embed-text`). |

Override data directory with `NOUZ_DATA_DIR` if you keep the vault elsewhere.

### YAML tagger

Adds only **missing** keys (existing YAML is preserved):

- `level` — default `quant` (`quant`, `module`, `pattern`, `artifact`, `log`, `hypothesis`, `task`)
- `role` — default `description` (`description`, `experiment`, `hypothesis`, `task`, `spec`, `brief`)
- `status` — default `draft` (`draft`, `processing`, `ready`, `archived`)
- `domain` — inferred from folder names and tags (`ai`, `infra`, `photo`, `business`, else `general`)
- `core_id` — `null` until you assign a cluster id manually

```bash
python -m scripts.nouz_yaml_tagger --vault /path/to/vault
python -m scripts.nouz_yaml_tagger --vault /path/to/vault --dry-run
```

### MCP-style API (`scripts.nouz_search`)

Import from automation or agents:

- `find_notes(level=..., domain=..., status=...)` → `{uid, path, title, level, status}`
- `semantic_search(query, top_k)` → `{uid, path, title, similarity}`
- `get_context_bundle(note_id, depth)` → `{note, parents, children, bridges}` — parents/children follow wiki links; **bridges** are notes sharing the same `core_id`
- `get_core_profile(core_id)` → `{core_id, title, description, note_count}`
- `notes_near_core(core_id, limit)` → list of notes ordered by path

CLI examples:

```bash
python -m scripts.nouz_search sync --vault /path/to/vault
python -m scripts.nouz_search sync --no-embed
python -m scripts.nouz_search find --domain photo --status draft
python -m scripts.nouz_search semantic "deployment checklist" --top-k 5
python -m scripts.nouz_search bundle <uid> --depth 2
python -m scripts.nouz_search core-profile my-core
python -m scripts.nouz_search near-core my-core --limit 10
```

### Tests

```bash
python -m unittest tests.test_nouz
```

