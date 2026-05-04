# openclaw-cursor

OpenClaw orchestration via Cursor Cloud Agent.

## Context splitter

`scripts/context_split.py` splits oversized contexts before sending them to a MiniMax model behind an OpenRouter-style chat completions endpoint.

### Behavior

- Detects semantic boundaries using double newlines and header-like blocks.
- Skips splitting when the estimated context size is below `100000` tokens.
- Targets chunks of about `50000` tokens with `5000` tokens of overlap.
- Recursively re-splits any chunk larger than `150000` tokens.
- Queries each chunk in parallel with a `60` second timeout and one retry.
- Synthesizes the per-chunk answers into one final answer.

The return value is a JSON object with:

- `answer`
- `n_chunks`
- `chunks_used`
- `method`
- `chunks_info`

### Environment

The script uses only the Python standard library and reads OpenRouter settings from environment variables by default:

- `OPENROUTER_API_KEY`
- `OPENROUTER_API_URL` or `OPENROUTER_BASE_URL`
- `OPENROUTER_MODEL`
- `OPENROUTER_SITE_URL` (optional)
- `OPENROUTER_APP_NAME` (optional)

### CLI usage

Pass the context directly:

```bash
python -m scripts.context_split "What does this say?" "Very large context text..."
```

Read the context from a file:

```bash
python -m scripts.context_split "What does this say?" --file path/to/context.txt
```

Override the defaults when needed:

```bash
python -m scripts.context_split \
  "Summarize this" \
  --file path/to/context.txt \
  --model minimax/minimax-m1 \
  --chunk-size 50000 \
  --overlap 5000 \
  --split-threshold 100000 \
  --recursive-limit 150000 \
  --timeout 60
```

