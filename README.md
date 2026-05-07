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

## Photo Archive Report

`python -m scripts.photo_archive_report` analyzes a photo/video archive and produces statistics in markdown tables plus JSON for charting.

### Usage

```bash
python -m scripts.photo_archive_report --scan /path/to/archive
python -m scripts.photo_archive_report --scan /path/to/archive --check-integrity
python -m scripts.photo_archive_report --scan /path/to/archive --check-integrity --output reports/photo_archive.md
python -m scripts.photo_archive_report --scan /path/to/archive --verbose --output reports/photo_archive.md
```

### What it reports

- Total files and file counts by extension (`jpg`, `png`, `heic`, `mp4`, etc.)
- Distribution by file modification year/month
- File size statistics: total, average, median, max (bytes)
- Optional integrity checks:
  - broken image detection (`Pillow` open/verify)
  - suspicious tiny `HEIC/JPEG` files (`< 1KB`)
  - detailed broken/suspicious files table

If `--output report.md` is provided, the tool writes:
- markdown report to `report.md`
- JSON payload to `report.json` (same directory/stem)

### Tests

```bash
python -m unittest tests.test_photo_archive_report
```
