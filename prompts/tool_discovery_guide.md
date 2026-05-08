# Tool discovery guide (OpenClaw Cursor)

This repository maintains an auto-generated catalog of executable scripts under `scripts/` and importable modules under `src/`. Use it to pick the right automation entry point without reading every file by hand.

## Where artifacts live

| Artifact | Role |
|----------|------|
| `docs/tool_discovery/manifest.json` | Machine-readable manifest (purpose, CLI hints, public symbols, notes). |
| `docs/tool_discovery/INDEX.md` | Human skim list with links into per-module pages. |
| `docs/tool_discovery/by_module/*.md` | One Markdown page per scanned `.py` file (purpose, argparse scrape, symbols). |

Generator implementation: `src/skills/tool_discovery.py`.

## Regenerating (required after you change tools)

From the repository root:

```bash
python3 src/skills/tool_discovery.py --write
```

Commit the updated `docs/tool_discovery/` tree together with your code changes.

## CI guard

Pull requests and pushes run `python3 src/skills/tool_discovery.py --check`, which fails if the manifest or Markdown drift from the current sources.

## How to read the manifest quickly

Each entry includes:

- **`purpose`** — First line of the module docstring when present; otherwise the `ArgumentParser` description; otherwise a short inferred stub (covers files with no docstrings).
- **`has_cli_guard`** — Whether `if __name__ == "__main__"` appears (CLI wrapper signal).
- **`entry_functions`** — Heuristic hits such as `main`, `cli`, `run`, `parse_args`.
- **`cli.arguments`** — Statically extracted `argparse` flags (`add_argument` calls anywhere in the module AST). Dynamic or delegated parsers may list nothing even though the script is a CLI.
- **`symbols`** — Public **top-level** classes and functions (names not starting with `_`). Nested helpers and private symbols are intentionally skipped.
- **`notes`** — Edge-case hints (for example, class-heavy pipelines plus a `main()`, or guards without matched argparse).

## Choosing between scripts and `src/`

- Prefer `scripts/*.py` for one-off CLIs and batch processors (for example `batch_image_optimizer.py`, `face_swap_batch.py`, `comfy_auto_quality.py`).
- Prefer `src/**` for reusable libraries coordinated by other tooling (orchestration, monitoring, skills).

## Practical usage patterns for agents

1. Open `docs/tool_discovery/INDEX.md` and jump to the module page matching your task name or domain.
2. If you need structured filtering, load `manifest.json` and select tools by `purpose`, `has_cli_guard`, or `cli.subcommands`.
3. When docstrings are missing, rely on parser descriptions and the extracted argparse table; fall back to opening the source path listed in `relative_path`.
4. For **class-based tools**, read the **Symbols** section on the Markdown page, then trace from `main()` or the documented programmatic API (for example `process_pil_image` in `comfy_auto_quality.py`).
5. After adding or renaming a script under `scripts/` or modules under `src/`, always rerun `--write` so discovery stays truthful.

## Environment overrides

- **`OPENCLAW_REPO_ROOT`** — Forces the repository root when discovery runs outside the default layout.
