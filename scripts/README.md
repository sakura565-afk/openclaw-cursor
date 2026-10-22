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

## Best-of-N cloud agents (`best_of_n.py`)

Runs one task through **N competing Cursor cloud agents** — each on its own
model, branch (`cursor/<slug>-variant-<k>`), worktree and pull request — then
scores the variants and marks a winner. The batch launcher, exponential
backoff/jitter poller and transient retries all come from `queue_manager.py`;
this module only adds fan-out, judging and reporting.

### Usage

```bash
# inline prompt, default model triple
python scripts/queue_manager.py run --best-of 3 \
  --task-slug add-widget --task-prompt "Implement scripts/widget.py with tests"

# YAML task spec (task_runner format: name/description/prompt/acceptance_criteria)
python scripts/queue_manager.py best-of-n --best-of 3 \
  --task-spec tasks/add_widget.yaml \
  --best-of-models claude-opus-5-thinking-high gpt-5.6-sol-high gemini-3.7-flash-high \
  --best-of-judge-model MiniMax-M3.1-Flash
```

`--best-of` below 2 keeps the classic one-agent-per-branch queue behaviour
unchanged. `--best-of-no-telegram` writes the report without the digest.

### Scoring

1. **LLM-as-a-Judge** (preferred). The acceptance criteria from the task spec
   plus each variant's diff summary are sent to the judge model, which must
   answer `{"score": 0..1, "verdict": str, "reasons": [str]}`. The parser
   accepts bare JSON, a ```json fence, or JSON embedded in prose; unusable
   output yields `score = None` plus an `error` and never raises. The transport
   is whatever `scripts/llm_call.py` (or
   `src/openclaw_orchestration/llm_call.py`) exposes — when neither exists the
   runner goes straight to the fallback.
2. **Deterministic fallback** (no network): weighted diff statistics — tests
   touched (0.35), new `src/` files (0.25), files changed (0.20), line volume
   (0.20). Reports produced this way are marked `judge_mode: "fallback"`.

Ties are broken by the lower `latency_s`, then the lower variant index, so the
winner is always deterministic.

### Outputs

- `data/best_of_N/<slug>_<UTC timestamp>.json` — machine-readable report
  (schema documented in `data/best_of_N/README.md`).
- `data/best_of_N/<slug>_<UTC timestamp>.md` — variant comparison table.
- Telegram digest via `telegram_sender.py` (winner first, one line per variant),
  sent only when there are ≥ 2 variants, a winner, and a configured bot.

### Configuration

| Setting | Env var | Config key (`config/best_of_n.yaml`) |
| --- | --- | --- |
| Variant models | `OPENCLAW_BEST_OF_MODELS` | `models` |
| Judge model | `OPENCLAW_BEST_OF_JUDGE_MODEL` | `judge_model` |
| Report directory | `OPENCLAW_BEST_OF_REPORT_DIR` | — |
| Worktree root | `OPENCLAW_WORKTREE_ROOT` | — |

Worktree isolation prefers `scripts/worktree_orchestrator.py` (Task 12) when it
is importable and otherwise falls back to a minimal `git worktree add` wrapper.

## Video assembler (`video_assembler.py`)

The editing layer between `scene_composer.py` (still PNG → MP4 with camera
motion) and the finished promo reel: unify clips, hard-concat them, pick a music
window that fits the reel, normalise loudness, mux, and check the seams.

```bash
python scripts/video_assembler.py probe clip.mp4
python scripts/video_assembler.py concat clip_a.mp4 clip_b.mp4 --output reel.mp4
python scripts/video_assembler.py rife clip.mp4 clip60.mp4 --fps 60
python scripts/video_assembler.py trim-music track.mp3 bed.m4a --duration 11.75
python scripts/video_assembler.py mux reel.mp4 bed.m4a final.mp4
python scripts/video_assembler.py seam-report final.mp4 --at 5.875
python scripts/video_assembler.py assemble a.mp4 b.mp4 --music track.mp3 -o promo.mp4
```

### Rules the module enforces

| Rule | Why |
| --- | --- |
| Every path reaches ffmpeg through `ffmpeg_path()` (`\` → `/`) | On Windows a backslash is an escape: ffmpeg can exit `0` and write nothing. Existence is then checked in native form via `native_path()`. |
| AAC re-encodes always pass `-ar 48000 -ac 2` | Without them the sample rate drifts down (we measured 44.1 kHz → mono out of `scene_composer`) and the audio ends up slowed and noisy. |
| `ffprobe` after every ffmpeg call (`validate()`) | The `moov` atom is written at the *end* of the file, so an interrupted encode leaves a non-empty but unreadable MP4. |
| Hard concat, never `xfade` | `xfade` ghosts the furniture (a translucent offset copy) and jumps brightness by 15-25 % on the transition frame. |
| Clips are scaled + padded to one resolution/fps/SAR before concat | The concat demuxer needs identical parameters, otherwise black bars, stretching or an aspect error. |
| Seams are judged on per-channel `std`, not mean RGB | Two frames can share a mean and still differ wildly in contrast. A `std` delta above ~8 units is visible. |
| Music windows come from an RMS envelope (0.5 s bins) + spectral flux | A window is rejected outright if any bin sits more than 6 dB below the track level, which skips quiet intros and mid-track dips. |
| Short fragments are measured with RMS bins, not `ebur128` | `ebur128` is correct on a full track but reports a meaningless -70 LUFS on a few seconds of audio. |

Target loudness is -14 LUFS (`loudnorm=I=-14:TP=-1.5:LRA=11`) with a 0.6 s
fade-in and a 1.0 s fade-out. `scene_composer.py` imports `AUDIO_SAMPLE_RATE`,
`AUDIO_CHANNELS` and `ffmpeg_path` from here so both tools emit the same format.

Dependencies: ffmpeg/ffprobe on `PATH` (or `imageio-ffmpeg`) plus numpy.
