# MEMORY

Structured agent memory for this OpenClaw-Cursor workspace. Sections use dated list items (`YYYY-MM-DD`) so staleness tooling can age entries; prune or consolidate old bullets with [`auto_memory_cleanup`](scripts/auto_memory_cleanup.py). Health reports: [`memory_analytics.py`](scripts/memory_analytics.py).

## Daily Notes

- 2026-05-15 Added repository-root MEMORY.md aligned with parsers in `scripts/memory_analytics.py` and `scripts/auto_memory_cleanup.py`.

## Repository shortcuts

Facts that save repeated README lookups. Related: [Environment defaults](#environment-defaults).

- 2026-05-15 Tool discovery CLI: `python -m scripts.tool_discovery analyze` or root `python3 tool_discovery.py --root .`.
- 2026-05-15 Ollama lifecycle: `python -m scripts.ollama_model_manager list` (see repository README).

## Environment defaults

Where the tooling expects Markdown and queue state outside this clone. Cross-ref: [Repository shortcuts](#repository-shortcuts).

- 2026-05-15 Windows layout often mirrors `C:\Users\user\.openclaw\workspace\MEMORY.md`; override with `OPENCLAW_MEMORY_PATH` or point cleanup at `--memory` explicitly.
- 2026-05-15 `OPENCLAW_MEMORY_DIR` selects the memory directory (defaults in `scripts/queue_manager.py`); queue JSON stays alongside that tree.

## Maintenance

Operational hygiene for this file.

- 2026-05-15 Run `python scripts/memory_analytics.py --input MEMORY.md` for staleness buckets, stale entries, duplicates, and missing internal anchors.
- 2026-05-15 Run `python -m scripts.auto_memory_cleanup --analyze --memory MEMORY.md` before `--dry-run` or `--auto` to review section stats safely.
