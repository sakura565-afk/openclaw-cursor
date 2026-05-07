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

## EXIF Date Normalizer

`python -m scripts.exif_date_normalizer` normalizes photo filenames by timestamp and writes a CSV audit log.

### Features

- Recursively scans folders via `--scan <path>`.
- Reads `DateTimeOriginal` from EXIF metadata.
- Renames files to `YYYY-MM-DD_HH-mm-ss_originalname.ext`.
- Supports dry-run preview by default; real rename happens only with `--fix`.
- Optional fallback `--folder-date` extracts date from parent folder names like `20240131` or `2024-01-31`.
- Timezone is configurable via `--tz` (default: `Europe/Moscow`).
- Writes CSV log columns: `old_name,new_name,date_source,status`.
- Supported formats: JPG, PNG, TIFF, HEIC.

### Examples

```bash
# Preview only (dry-run)
python -m scripts.exif_date_normalizer --scan /data/photos

# Apply rename and store log
python -m scripts.exif_date_normalizer --scan /data/photos --fix --csv-log logs/exif_rename.csv

# Allow folder date fallback and custom timezone
python -m scripts.exif_date_normalizer --scan /data/photos --folder-date --tz Europe/Berlin
```

