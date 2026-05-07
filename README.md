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

## Photo Deduplication

`python -m scripts.photo_deduplication` scans photo archives and groups duplicate images using perceptual/average hashing.

### Supported formats

- JPG / JPEG
- PNG
- TIFF
- BMP
- HEIC (requires Pillow HEIF support in environment)

### Usage

```bash
python -m scripts.photo_deduplication --scan /path/to/archive --dry-run
python -m scripts.photo_deduplication --scan /path/to/archive --move --hash-type both
python -m scripts.photo_deduplication --scan /path/to/archive --hash-type perceptual --threshold 95
```

### CLI flags

- `--scan <path>`: recursively scans directory for images.
- `--dry-run`: generates report only, no file deletion/move.
- `--move`: moves duplicates to `<scan>/duplicates/` instead of deleting.
- `--hash-type [perceptual|average|both]`: hashing mode.
- `--threshold`: duplicate similarity threshold in percent.
- `--json-out`: path to JSON report file.
- `--csv-out`: path to CSV duplicates list.

### Tests

```bash
python -m unittest tests.test_photo_deduplication
```

