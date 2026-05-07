# Scripts

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
