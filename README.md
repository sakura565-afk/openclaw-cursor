# openclaw-cursor

OpenClaw orchestration via Cursor Cloud Agent.

## Image Format Migrator

`python -m scripts.image_format_migrator` converts/compresses photo archives to JPEG.

### Supported conversions

- Input formats: PNG, TIFF, BMP, WEBP, HEIC, JPEG
- Output format: JPEG
- JPEG quality: configurable (`--quality`), default `85`
- EXIF: preserved by default (`--preserve-exif`, disable via `--no-preserve-exif`)

### CLI usage

```bash
# Recursive scan and conversion
python -m scripts.image_format_migrator --scan /path/to/archive

# Convert one file рядом с оригиналом
python -m scripts.image_format_migrator --single /path/to/image.png

# Custom output directory
python -m scripts.image_format_migrator --scan /path/to/archive --output /path/to/output

# Preview only (no writes)
python -m scripts.image_format_migrator --scan /path/to/archive --dry-run

# Replace originals
python -m scripts.image_format_migrator --scan /path/to/archive --overwrite
```

For `--scan`, if `--output` is omitted and `--overwrite` is off, output is created next to the source directory with suffix `_converted`.

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

