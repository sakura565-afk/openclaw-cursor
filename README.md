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

## Face Clustering

`python -m scripts.face_clustering` clusters face embeddings found in a photo archive.

### Usage

```bash
python -m scripts.face_clustering \
  --scan /path/to/photo_archive \
  --cluster-count 10 \
  --min-samples 3 \
  --export-json \
  --export-folders
```

### Options

- `--scan <path>`: recursively scan a directory for image files.
- `--cluster-count <N>`: optional target number of clusters; the script auto-tunes a distance threshold.
- `--min-samples <N>`: minimum number of face samples required to keep a cluster.
- `--export-json`: write `catalog.json` with cluster metadata and file membership.
- `--export-folders`: create `person_001/`, `person_002/`, ... folders containing symlinks to source files.
- `--backend`: `auto`, `face_recognition`, or `insightface`.

### Output

- `catalog.json` includes:
  - clustering threshold (`eps`)
  - list of clusters with `cluster_id`, size, and files
  - list of noise samples that did not match cluster rules
- `face_clusters/person_###/` directories (optional) with symlinks to clustered images.

### Caching

The script stores cached encodings in `.face_clustering_cache.json` under the scan root and skips re-encoding unchanged files by checking file size and nanosecond mtime.

