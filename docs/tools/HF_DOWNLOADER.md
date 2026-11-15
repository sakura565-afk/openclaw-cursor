# Verified Hugging Face downloader

`tools/hf_download.py` downloads Hugging Face model files with byte-range resume,
per-destination process locking, and mandatory size/SHA-256 verification.

**Ready means exact size AND SHA-256 match, nothing else.** An existing path, a
completed HTTP request, or a merged file with the expected size is not sufficient.
The CLI returns a nonzero exit code if any selected file is not verified.

## Plan format

A plan is a JSON object with a `files` array:

```json
{
  "files": [
    {
      "repo": "org/model",
      "path": "model.safetensors",
      "dest": "models/model.safetensors",
      "size": 123456789,
      "sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    },
    {
      "repo": "org/model",
      "path": "config.json",
      "dest": "models/config.json",
      "size": 1234,
      "sha256": ""
    }
  ]
}
```

- `repo` is the Hugging Face repository ID, including its namespace.
- `path` is the file path inside the repository on the `main` revision.
- `dest` is the local output path.
- `size` is the exact expected byte count.
- `sha256` is the expected lowercase hexadecimal SHA-256. If it is empty, the
  downloader reads `lfs.oid` from the Hugging Face file metadata API. Both
  `sha256:<hex>` and bare hexadecimal object IDs are accepted.

Set `HF_ENDPOINT` to use a compatible mirror; it defaults to
`https://huggingface.co`.

## Command line

```bash
python tools/hf_download.py --plan plan.json --report report.json
python tools/hf_download.py --plan plan.json --only model.safetensors
python tools/hf_download.py --plan plan.json --dry-run
python tools/hf_download.py --plan plan.json --segments 6
```

`--only` selects entries when its substring occurs in the repository, repository
path, or destination. `--dry-run` prints selected tasks and performs no network or
file writes. The default report is `report.json`; it contains one JSON entry per
selected file with status, measured size and digest, verification result, elapsed
seconds, and segment count.

## Resume and layout rules

For a destination `model.bin`, segments are stored as:

```text
model.bin.parts/
  layout.json
  p000
  p001
  ...
```

Each segment has a fixed half-open byte range in the overall file. A complete
segment is skipped. A partial segment is resumed with a new HTTP `Range` header
starting at its current length. Interrupted transfers therefore retain useful
bytes. Transient failures use exponential backoff and three retries by default.

`layout.json` binds the parts directory to the repository, path, expected size,
and segment count. **Do not change `--segments` while resuming an incomplete
file.** If the existing layout differs, the result is
`segment_layout_mismatch`; no segment is removed, truncated, or rewritten. Move
the old `.parts` directory aside or keep the original segment count before
retrying.

Connections whose average speed remains below 200 KB/s for 60 seconds are aborted
and resumed according to the retry policy. The server must honor byte ranges.

After all segments are present, they are concatenated in order into
`<dest>.merge`. The downloader verifies the merged file's exact size and SHA-256,
then atomically replaces `<dest>`. A failed verification returns
`checksum_mismatch` and does not install the merged bytes. Parts are removed only
after a successful verified install. After a checksum failure, inspect or move
the retained `.parts` directory before starting a clean attempt.

## Lock semantics

Each destination has a `<dest>.lock` file created with atomic exclusive creation.
A second process using the same destination waits instead of entering the same
`.parts` directory. Once it acquires the lock, it hashes and verifies an existing
destination and returns that completed result when valid. Waiting is limited to
120 seconds by default and produces `lock_timeout` on expiry.

A crashed process can leave a stale lock. After confirming that no downloader is
using that destination, remove the stale `.lock` manually. The tool deliberately
does not steal locks automatically.

## Result statuses

- `ok`: exact expected size and SHA-256 were verified.
- `download_failed`: a segment exhausted its retries; resume with the same layout.
- `checksum_mismatch`: merged size or digest differed; never treat the file as ready.
- `segment_layout_mismatch`: existing parts belong to another layout; data preserved.
- `metadata_failed`: an empty plan hash could not be resolved from HF LFS metadata.
- `lock_timeout`: another process still owns the destination lock.

## Tests

The offline tests use only a local HTTP server with configurable truncation,
corruption, and delays:

```bash
python -m pytest tests/test_hf_download.py -q
```
