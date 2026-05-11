# Scripts

## Iskra → Kara shared memory (`kara_poll_iskra_results.py`)

Iskra (tasks bot) appends structured JSON objects to a locked queue file under the OpenClaw workspace; Kara’s cron drains that file and clears it. Default path:

`$OPENCLAW_WORKSPACE/shared_memory/iskra_kara_results.json` (or `~/.openclaw/workspace/...` when `OPENCLAW_WORKSPACE` is unset).

### Kara cron (replace legacy `tasks/results/` scan)

Point OpenClaw cron **Искра results → Кара proxy** (ID `646f9a49-8aed-4521-9e28-841f9366156b`) at this repo root:

```bash
python -m scripts.kara_poll_iskra_results
```

- If the queue has entries, the script prints a markdown batch on stdout for the proxy message body.
- If the queue is empty, it prints `NO_REPLY` and exits `0` (no message).
- If the queue file is corrupt or the lock cannot be acquired in time, the script reads new files under `$OPENCLAW_WORKSPACE/tasks/results/` (same layout as before) until the queue is healthy again. Fallback delivery is tracked in `shared_memory/iskra_kara_fallback_state.json`.

Options: `--workspace PATH`, `--results-path PATH`, `--no-fallback`, `--json` (machine-readable output for tests).

Writers use `src.coordination.iskra_kara_shared_memory.append_iskra_result` / `notify_kara_from_iskra` (see `auto_reflection`, `auto_memory_cleanup`, `dream_tracker`, `auto_engine`, `nightly_pipeline`).

## Obsidian link checker (`obsidian_link_checker.py`)

Walks an Obsidian vault, scans Markdown notes for internal links (`[[wiki-style]]`, standard `[text](path)` links to vault files), and writes a JSON report listing broken targets (missing note or missing `#heading` / `^block` anchor).

### Requirements

- Python 3.10+ (uses `Path | None` style annotations).

### Default vault path

1. `OBSIDIAN_VAULT_PATH` environment variable, if set.
2. On Windows: `C:\Users\user\Documents\Obsidian Vault`.
3. Otherwise: `~/Documents/Obsidian Vault`.

### Usage

From the repository root:

```bash
python scripts/obsidian_link_checker.py --vault /path/to/vault -o report.json
```

Options:

| Option | Description |
|--------|-------------|
| `--vault PATH` | Vault root directory (see defaults above). |
| `-o`, `--output PATH` | JSON report file (default: `obsidian_broken_links.json` in the current working directory). |
| `--case-sensitive` | Treat paths and note names as case-sensitive (overrides the default for your OS). |
| `--case-insensitive` | Case-insensitive matching (overrides the default). |

On Windows, matching is case-insensitive by default; on Linux and similar systems, it is case-sensitive by default.

### Exit codes

- `0` — no broken links.
- `1` — vault path missing or not a directory.
- `2` — one or more broken links (report still written).

### Report format

The JSON object includes:

- `vault` — absolute vault path.
- `case_sensitive` — whether path/name matching used case sensitivity.
- `scanned_files` — number of `.md` files scanned (excludes `.obsidian`, `.git`, etc.).
- `broken_count` — length of `broken_links`.
- `broken_links` — list of objects with `source_file`, `link_type` (`wiki` or `markdown`), `link_text`, `target_raw`, `reason` (`file_not_found` or `anchor_not_found`), and `resolved_path` when a file was found but the anchor was not.
- `generated_at` — UTC ISO timestamp.

Fenced code blocks (``` … ```) are skipped so example links inside snippets are not validated. HTTP(S) URLs and `obsidian://` links are ignored.

### Link behavior (summary Wiki-style links)

- `[[Note]]` resolves by note title (file stem); if several notes share the same name, the note in the same folder as the source is preferred when unique.
- `[[folder/Note]]` is resolved from the vault root; `./` and `../` are resolved relative to the source note.
- `[[Note#Heading]]` checks that the target `.md` contains a heading whose slug matches (same rules as common Markdown slugging: lowercased, punctuation to hyphens). Block references `[[Note#^id]]` look for `^id` in the target file.
- `![[asset.png]]` and non-`.md` paths try the source note’s folder first, then the vault root.
- Markdown links like `[label](other.md#section)` resolve paths relative to the source file.

When a heading link uses only an anchor (e.g. `[text](#section)`), resolution is checked against the **source** note.
