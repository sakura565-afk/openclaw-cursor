# openclaw-cursor

OpenClaw orchestration via Cursor Cloud Agent.

## Proactive Scout

`scripts/proactive_scout.py` speculatively prepares likely follow-up requests so a later user question can be served from cache instead of starting from scratch.

### Behavior

- Uses stdlib only.
- Predicts at most 2 follow-ups from the current result.
- Only starts speculative work after more than 15 seconds of idle time.
- Uses a fast-model label (`openclaw-fast` by default, override with `OPENCLAW_SCOUT_MODEL`).
- Caches prepared follow-ups for 5 minutes.
- Supports these task families:
  - image -> `variants`, `more`, `change_style`, `upscale`
  - code -> `add_tests`, `optimize`, `explain`, `refactor`
  - video -> `shorter`, `different_angle`, `change_format`
  - analysis -> `more_details`, `what_if`, `alternatives`

### Python API

- `scout_check(question) -> cached_or_none`
- `scout_predict(task_type, result) -> list`
- `scout_run_background(predictions)`

The cache/state directory defaults to `~/.openclaw/proactive_scout` and can be overridden with `OPENCLAW_SCOUT_DIR`.

### CLI

Check whether a prepared answer is already cached:

```bash
python3 scripts/proactive_scout.py check "Can you add tests for this change?"
```

Show cache, jobs, and runtime state:

```bash
python3 scripts/proactive_scout.py status
```

Clear all cached speculative results:

```bash
python3 scripts/proactive_scout.py clear
```

Predict likely follow-ups and start background work when idle time is high enough:

```bash
python3 scripts/proactive_scout.py predict code "Implemented the CLI and cache layer." --idle-seconds 18
```

