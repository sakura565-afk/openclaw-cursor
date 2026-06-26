# openclaw-cursor

OpenClaw orchestration via Cursor Cloud Agent.

## Sitemap generator (divaninfo.ru)

`python -m scripts.sitemap_generator` builds `public/sitemap.xml` and `public/robots.txt` from product JSON files, category metadata, and blog posts. Uses only the Python 3.11+ standard library.

### Product data format

Place product JSON files in `data/` (or pass `--products-dir`):

```json
{
  "slug": "divan-bergamo",
  "category": "divany",
  "lastmod": "2026-05-10"
}
```

Supported fields for `lastmod`: `lastmod`, `updated_at`, `modified`, `date`. You can also set `url_path` instead of `slug` + `category`.

Categories are read from `seo/meta_templates.json`. Blog posts are scanned from `blog/*.md` (date is taken from YAML frontmatter).

### Commands

```bash
# Defaults: domain=https://divaninfo.ru, output=public/, products-dir=data/
python -m scripts.sitemap_generator

python -m scripts.sitemap_generator --domain https://divaninfo.ru --output public --products-dir data

python scripts/sitemap_generator.py --domain divaninfo.ru --output ./public --products-dir ./data
```

### Output

- `public/sitemap.xml` — home, catalog, categories, products, blog
- `public/robots.txt` — allows all crawlers and links to the sitemap

Priorities: home `1.0` (daily), categories `0.8` (weekly), products `0.6` (monthly), blog `0.7` (weekly).

See `sample-sitemap.xml` for a minimal example with two products.

### Tests

```bash
python -m pytest tests/test_sitemap_generator.py -q
```

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

