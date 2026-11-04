# H3 long-video chain (Kimi implementation)

Resumable CLI for long H3 clips. It renders independent chunks through the existing `scripts/h3_i2v_run.py`; it does not modify or import the renderer. The renderer still owns workflow validation and the H3 defaults.

## Why last-frame chaining, not a middle guide frame

A guide frame placed in the middle of a long H3 shot held only about two frames in our measurements: VAE smoothing removes the constraint, and the tail drifts again. Here every chunk starts from the actual last frame of the selected previous chunk. That puts an identity anchor at every join instead of hoping a temporary mid-shot constraint survives.

The first chunk uses `--image`. Each later chunk uses a PNG extracted from the preceding chunk and copied into ComfyUI's `input` directory. Only the PNG **basename** is passed to `h3_i2v_run.py`; its `LoadImage` validator rejects absolute paths. Every retry of the same chunk uses the same incoming anchor, so a bad take cannot contaminate its own retry. The prompt file is passed unchanged to every attempt.

The implementation intentionally does not pass `--guide-image` and does not override the renderer's denoise default. A low denoise is not a conservative face lock in this workflow: measured H3 denoise `0.25` corresponds to effective sigma `0.800`, so the supplied frame is overwritten and identity/texture can drift. Denoise `1.0` follows the normal i2v generation schedule; lowering it while expecting stronger anchoring has the opposite practical effect here. `fl2va` is the checkpoint intended to land on a supplied frame; `ref2va` only nudges toward it.

## Offline plan

```bash
python -m scripts.h3_chain_pipeline_kimi \
  --image portrait.png \
  --prompt-file prompt.txt \
  --audio track.m4a \
  --output long.mp4 \
  --chunk-frames 56 \
  --total-frames 120 \
  --seed-base 42 \
  --plan
```

`--plan` needs no input files, `COMFYUI_PROD_DIR`, ffmpeg, server, or GPU. It starts no subprocess and writes neither state nor report.

H3 accepts lengths `5 + 17*k`. The planner removes one duplicated first frame from every chunk after the first. If an exact requested output length cannot be represented by those constraints, it trims only the tail of the final chunk. `kept_frames` therefore sums exactly to `--total-frames`; the table also shows the final tail trim.

## Render and resume

```bash
COMFYUI_PROD_DIR=/path/to/portable-root \
python -m scripts.h3_chain_pipeline_kimi \
  --image portrait.png --prompt-file prompt.txt --audio track.m4a \
  --output long.mp4 --chunk-frames 56 --total-frames 120 \
  --width 736 --height 1120 --steps 20 --seed-base 42 \
  --max-retries 2 --max-face-drift 8
```

`COMFYUI_PROD_DIR` points to the portable root containing `ComfyUI/input`. If it is unset, the real run resolves the directory through `scripts/local_paths.py`. Audio and prompt paths go directly to the existing renderer; each chunk receives `--audio-offset` equal to its retained output start frame.

Defaults:

- state: `OUTPUT.state.json` (for example, `long.mp4.state.json`);
- report: `OUTPUT.report.json`;
- work directory: hidden `.OUTPUT_STEM.h3-chain` beside the output;
- `--max-texture-drop 0.25` and `--min-texture 0`.

The state fingerprint covers the effective configuration and SHA-256 hashes of the initial image, prompt, and audio. Changing any of them requires a new state file instead of silently mixing takes. State writes use a temporary file plus atomic replace after every attempt.

A selected chunk is not rendered again on resume. A missing last-frame PNG is re-extracted from its selected video without rendering. If the selected video itself is missing, that chunk and all downstream chunks are invalidated because their anchors depend on it. Do not run two writers against the same state file; there is no inter-process lock.

## Quality gate and retry algorithm

For every rendered attempt, ffmpeg uniformly samples at most 24 frames, including the first and last. The pipeline calls the repository's `compare_video_texture.measure` implementation, obtaining pore-band energy (3–12 px), face drift, RGB delta, and HF delta.

For chunk `i`, the texture floor is:

```text
max(--min-texture,
    median(pore energy of previously selected chunks) * (1 - --max-texture-drop))
```

The first chunk has no relative texture baseline, so only `--min-texture` applies to its texture. An attempt is rejected if:

1. face drift, pore energy, or frame count is invalid/non-finite;
2. fewer than two frames were measured;
3. mean face drift exceeds `--max-face-drift`; or
4. pore energy is below the current texture floor.

A rejection rerenders **only that chunk**, from the same incoming anchor, with `seed = seed_base + chunk_index * 1,000,000 + attempt`. Accepted earlier chunks are untouched. Attempt zero is the initial render; `--max-retries N` permits at most `N` additional renders.

If every attempt fails the gate, the pipeline selects the measured attempt with the smallest normalized gate penalty (drift excess plus texture deficit), then lower drift, higher pore energy, and earlier attempt as tie-breakers. It still assembles the best-effort result, but marks that chunk and `summary.quality_gate_passed` as false; the CLI exits `3`. Input/usage/render/assembly failures exit `2`; a fully accepted run exits `0`.

## Assembly and report

- Chunk zero keeps all frames. Each later chunk drops frame zero, the duplicate of the previous chunk's last frame.
- Segments are normalized with ffmpeg and concatenated. The last segment is cut to the planner's `kept_frames` so the output has exactly the requested frame count.
- Retained chunk audio is joined with `acrossfade=d=0.04:c1=qsin:c2=qsin`. `qsin` is ffmpeg's equal-power curve. The track is padded/trimmed to the exact video duration before AAC muxing.
- Seams compare the previous chunk's last frame with the next chunk's first **retained** frame (its original frame one). The report includes RGB/face deltas, before/after pore energy, texture ratio/drop, and a `texture_ratchet` flag using `--max-texture-drop`.

State chunk entries contain:

```json
{
  "index": 1,
  "initial_seed": 1000042,
  "render_frames": 56,
  "kept_frames": 55,
  "output_start": 56,
  "status": "selected",
  "attempts": [
    {
      "attempt": 0,
      "seed": 1000042,
      "image_name": "h3chain_job_anchor_001.png",
      "video": "work/chunk_001/attempt_000/chunk_1_0.mp4",
      "texture_floor": 75.0,
      "accepted": false,
      "status": "rejected",
      "metrics": {"face_drift": 12.0, "pore": 100.0, "frames": 24},
      "reasons": ["face_drift 12 exceeds 8"]
    }
  ],
  "selected_attempt": 1,
  "selected_seed": 1000043,
  "gate_passed": true,
  "metrics": {"face_drift": 2.0, "pore": 110.0, "frames": 24},
  "video": "work/chunk_001/attempt_001/chunk_1_1.mp4",
  "last_frame": "work/anchors/chunk_001_last.png"
}
```

The report repeats these chunk records and adds `seams` plus summary means/extrema, render/retry counts, worst-drift chunk, and texture-ratchet seam count.

## Tests and remaining validation boundary

```bash
python -m pytest -q scripts/h3_chain_pipeline_kimi/test_pipeline.py
```

Tests cover planning, offline behavior, renderer argv, gate/retry/best-effort selection, state resume and anchor repair, and real local ffmpeg assembly/last-frame extraction on synthetic media. They never contact ComfyUI.

Not verified here: actual H3 rendering, visual identity quality on real footage, metric thresholds on a real portrait, long-run audio sync, and concurrent writers. Pore energy is a rejection signal, not proof of natural pores; face-ROI drift can also respond to pose and lighting, not identity alone.
