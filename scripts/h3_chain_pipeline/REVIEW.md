# Cross-review: `scripts/h3_chain_pipeline_kimi/`

Reviewed the neighboring implementation read-only. No ComfyUI/GPU calls were made;
every finding below was reproduced offline against the real `scripts/h3_i2v_run.py`,
the real `compare_video_texture.py`, and real ffmpeg.

Line numbers refer to `scripts/h3_chain_pipeline_kimi/pipeline.py` at the time of
review.

---

## 1. Critical: the renderer is called with two arguments it does not have

`build_renderer_argv()` (lines 227-276) always appends `--audio-offset <frames>`
(line 267) and appends `--server <url>` when `--server` is given (line 275).
Neither flag exists in `scripts/h3_i2v_run.py`.

The renderer's own argument list (lines 203-244 of `scripts/h3_i2v_run.py`) is:
`--image --last-frame --guide-image --guide-video --ref-image --turbo --sampler
--scheduler --ref-image-size --guide-frame --tst-tau --tst-log --prompt-file
--prompt-prefix --loras-json --clip-name --negative --width --height --length
--seed --steps --prefix --audio --output --wait`. The server URL is read from
the environment variable `COMFYUI_PROD_URL` (`h3_i2v_run.py:23`), not from a
command-line flag.

Reproduced against the real renderer:

```text
$ python scripts/h3_i2v_run.py --image x.png --prompt-file p.txt --audio a.wav \
      --output o.mp4 --audio-offset 0
h3_i2v_run.py: error: unrecognized arguments: --audio-offset 0

$ python scripts/h3_i2v_run.py ... --server http://127.0.0.1:8188
h3_i2v_run.py: error: unrecognized arguments: --server http://127.0.0.1:8188
```

argparse exits with status 2 before anything is sent to ComfyUI, so **every
render attempt fails**. The failure is swallowed by the broad
`except Exception` in `_process_chunk` (line 702) and recorded as
`status="render_error"`. After `--max-retries` the chunk falls into the `else`
branch of the `while` loop (line 706), `choose_best_attempt()` raises
`ValueError("no measured attempt is available")` on the empty candidate list,
and `main()` returns 2 with `h3 chain failed: ...`.

So the pipeline never renders anything, and it fails late and with a message
that points at the wrong place. The `FakePipeline` in
`scripts/h3_chain_pipeline_kimi/test_pipeline.py` overrides `_render_attempt`
(line 211), so the test suite never exercises the real argv — the bug is
invisible to the tests.

**Fix:** drop `--audio-offset` and `--server` from the argv; put the server
into the environment as `COMFYUI_PROD_URL` for the subprocess, and cut the
audio per chunk in the pipeline itself (see finding 4).

---

## 2. Critical: the audio never advances - every chunk carries the same track

The pipeline expects `h3_i2v_run.py` to cut the soundtrack at
`audio_offset_frames=plan.output_start` (line 694). That flag does not exist
(finding 1), and the renderer does not seek the audio either: it muxes with
`-stream_loop -1 -i src -i a.audio -t <dur>` and `-map 1:a:0`
(`h3_i2v_run.py:317-320`). The audio input is read from its beginning.

Reproduced with the renderer's own mux command:

```text
chunk0.mp4 duration=2.332993   <- audio = track[0.0 .. 2.33]
chunk1.mp4 duration=2.290998   <- audio = track[0.0 .. 2.29]   (NOT 2.33..)
```

Then `_assemble()` (line 803) feeds **the chunk videos** into the audio filter
graph and `build_audio_filter()` cuts each of them with
`atrim=end=<duration>` (line 287) - i.e. it takes the *first* `duration`
seconds of every chunk's audio. The result is the opening of the soundtrack
repeated once per chunk, not the soundtrack.

This is invisible in the tests because
`test_real_ffmpeg_assembly_dedups_and_preserves_exact_frame_count`
(`test_pipeline.py:323`) builds its inputs with a per-file sine tone, so the
repeated-vs-continued distinction is never asserted.

**Fix:** take the source track once (`--audio`), cut it at the seam positions
with `atrim=start=...:end=...`, and weld the cuts. That is what
`scripts/h3_chain_pipeline/assemble.py` does.

---

## 3. High: the audio weld consumes 40 ms per seam and does not compensate

`build_audio_filter()` (lines 279-301) chains `acrossfade=d=0.04:c1=qsin:c2=qsin`
between the segments and pads the result to `total_duration` with
`apad=whole_dur=...,atrim=end=...` (line 299).

An `acrossfade` of duration *d* **shortens** the track by *d*: the two halves
overlap, so N seams cost N x 40 ms. The track therefore drifts ahead of the
picture by 40 ms per seam, and the shortfall is patched with silence at the
end.

Measured with a click track (10 s, one click per second, cuts at 3 s and 6 s):

```text
disjoint cuts, no compensation:
  clicks heard at 0.0 1.0 2.0 2.966 3.96 4.96 5.926 6.92 7.92 8.92
                             ^^^^        ^^^^          ^^^^
                          -34 ms     -40 ms       -74 ms   (vs 3.0, 4.0, 7.0)
```

The compensation is to move each cut *later* by 40 ms for every seam that came
before it: the material that belongs at a seam is then the material that is
heard at it.

**Fix:** `assemble.audio_boundaries()` in the neighboring package applies
`position + index * crossfade`; the click track then lands back on 3.0, 4.0
and 7.0.

---

## 4. High: the gate measures the mean drift, not the tail

The complaint this pipeline exists for is "the face drifts away in the tail of
the clip". The gate uses `metrics.get("face_drift")` (lines 172 and 215).

`compare_video_texture.measure()` returns `face_drift` as the **mean** over
all sampled frames of the difference against the *first* sampled frame
(`compare_video_texture.py:118-131`). A chunk that holds the face perfectly for
170 frames and drifts hard over the last 5 reports a low mean and passes the
gate. The value that exposes the problem is `face_drift_max`, and the one
that pins it down is the drift of the **last** frame - both are returned by
`measure()` and both are ignored.

Note also that the ROI is the repository default, `DEFAULT_ROIS =
[("thigh", ...), ("chest", ...)]` (`compare_video_texture.py:71`), so the pore
energy is measured on the thigh and the chest, not on the face. `face_roi`
only affects the drift series, not the pore value.

**Fix:** `scripts/h3_chain_pipeline/gates.py` measures with
`scripts/probe_face_drift.py` (the repository's own definition of the face
ROI and of the tail drift) and gates on `face_drift_last` plus
`face_drift_max`; the pore is measured on the face ROI via
`rois=[("face", FACE_NORM)]` and a canonical size, so chunks at different
resolutions stay comparable.

---

## 5. Medium: the state fingerprint covers the config, but a changed seed base kills the run instead of the chunk

`ChainConfig` carries `seed_base`, `chunk_frames`, `width`, `height` and
`steps`, and `_fingerprint()` (line 517) hashes `asdict(self.config)` together
with the sha256 of the image, the prompt and the audio. A changed
`--seed-base` therefore produces a different fingerprint, and `load_state()`
rejects the old state file with

```text
state fingerprint does not match this job; use a new state file
```

Verified:

```text
fingerprint differs when seed_base changes: True
load_state REJECTED the old state -> state fingerprint does not match this job
```

So the "changed seed base silently reuses the old take" hazard is **not**
present - the fingerprint catches it. The consequence is the opposite problem:
the whole state is thrown away, and the user is told to use a new state file.
Every accepted chunk of a long clip is lost, including the ones that had
nothing to do with the seed change. For a job whose renders cost minutes of
GPU time each, that is the expensive way to invalidate one chunk.

The more precise gap is at the chunk level: `_process_chunk()` returns
immediately when `chunk.get("status") == "selected"` (line 660), so a chunk
that was accepted is reused without checking that the seed it was rendered
with is still the one this run would choose. With the fingerprint in place
this cannot happen today, but it makes the chunk-level logic depend on a
global check - changing the seed base forces a full re-render instead of a
per-chunk one.

**Fix:** the neighboring package stores the anchor and the seed in the chunk
record and checks both per chunk (`state.accepted_for(..., seeds=...)`), so a
changed seed base invalidates the affected chunks instead of the whole job.

---

## 6. Medium: a chunk that never rendered crashes the run instead of being reported

When every attempt of a chunk fails to render, the `else` branch of the retry
loop (line 706) calls `choose_best_attempt(existing, ...)` with an empty
`existing` list - because a failed render leaves no file behind. That raises

```text
ValueError: no measured attempt is available
```

Reproduced directly:

```python
>>> choose_best_attempt([], max_face_drift=8.0, texture_floor=0.0)
ValueError: no measured attempt is available
```

The exception escapes `_process_chunk()`, is caught by the broad
`except Exception` in `main()` (line 1004) and turned into
`h3 chain failed: no measured attempt is available` with exit code 2. The
user does not learn *which* chunk failed, and the state file is left with the
chunk in status `rendering`.

Note that this is the same code path that the two critical bugs above push
every run into, so today it is the *normal* failure message of this
implementation.

**Fix:** the neighboring package records the failed chunk in the report with
the reason, keeps the state consistent, and exits with a message that names
the chunk.

---

## 7. Low: the plan arithmetic is correct, but the defaults are not the working ones

`build_chunk_plan()` (line 179) honours `total_frames` exactly. Verified over
several inputs:

```text
total= 120 -> n=3 render=[56, 56, 22] kept_sum=120  MATCHES=True
total= 166 -> n=3 render=[56, 56, 56] kept_sum=166  MATCHES=True
total= 240 -> n=5 render=[56, 56, 56, 56, 22] kept_sum=240  MATCHES=True
total= 300 -> n=6 render=[56, 56, 56, 56, 56, 39] kept_sum=300  MATCHES=True
```

The `tail_trim` term trims the last chunk and the sum of the kept frames is
always the requested length. The plan arithmetic is sound.

The defaults are the problem: `--chunk-frames 56` and `--total-frames 166`
(lines 939-940) describe a 6.9 s clip built from the **minimum** H3 length
(56 = 5 + 17*3). The working sweet spot on 16 GB of VRAM is 175 frames
(7.3 s), and a chunk of 56 frames means three times as many seams - and
therefore three times as many places for the texture ratchet to appear - for
a clip of the same length. The default should be the length that was measured
to work.

`build_chunk_plan()` also rejects any chunk length that is not `5 + 17k`
(`ValueError` at line 187). That is the right constraint to enforce - H3 only
accepts those lengths - but it means `--chunk-frames 120` is an error rather
than being snapped to the nearest valid length, which is what a user typing
a round number expects.

---

## 8. Low: `--plan` is offline, but it does not show the chain

`main()` (line 992) returns the plan before any rendering, and
`test_plan_cli_is_offline_and_writes_no_state` (`test_pipeline.py:89`) patches
`subprocess.run` to prove it. That part is correct and verified.

What the plan does not show is the **anchor** each chunk starts from - the one
thing that makes the chain legible. A user reading the plan cannot tell that
chunk 3 continues chunk 2's last frame rather than starting from the still.
The neighboring package's `--plan` prints the chunk number, the seed, the
first/last frame, the expected duration and the note about the trimmed
boundary frame.

---

## 9. Low: the test file is not collected by pytest

`scripts/h3_chain_pipeline_kimi/test_pipeline.py` sits under `scripts/`, and
`pyproject.toml` sets `testpaths = ["tests"]`. The suite is therefore not
collected by a plain `pytest` run:

```text
$ python -m pytest --collect-only -q | grep -c h3_chain_pipeline_kimi
0
```

The tests still run when the file is passed explicitly, and they pass. But a
green `pytest -q` in CI says nothing about this implementation.

---

## 10. Low: the absolute-path guard does not cover this package

`tests/test_no_absolute_paths.py` scans `tests/` and `scripts/` recursively,
so `scripts/h3_chain_pipeline_kimi/` **is** covered - the guard passes for
these files. The one hit in the package is
`test_pipeline.py:169` (`server="http://127.0.0.1:8188"`), which is a URL and
is correctly ignored by the scanner. No machine-specific path was found.

---

## Checked and not found

- The renderer **is** the existing `scripts/h3_i2v_run.py`, called as a
  subprocess - it was not reimplemented. Good.
- The `--image` value **is** a bare file name: `_prepare_initial_image()`
  (line 462) and `_stage_anchor()` (line 476) copy the file into the ComfyUI
  input directory and return `dest.name`. The LoadImage validator would
  accept it. Good.
- There is **no** middle guide-frame approach; the chained first image is
  used. Good.
- The state file is written atomically through a temporary file plus
  `os.replace` (`save_state`, line 202), and the report is written the same
  way. Good.
- The concat drops the duplicated boundary frame with `select=gte(n\,1)` and
  the final frame count is verified against the request (line 828). Good.
- `c1=qsin:c2=qsin` is the correct equal-power curve; `c1=equal` would have
  been a dip at the seam. Good.
- The renderer is invoked with `cwd=REPO_ROOT` and `PYTHONPATH=REPO_ROOT`
  (line 686), so the repository's imports work. Good.
- The gate does reject a chunk whose texture ratio is below the floor
  (`evaluate_gate`, line 165). The fail-open case that the neighbor found in
  the *other* implementation (a ratio of 0 passing an enabled texture gate)
  is not present here - `metrics.get("pore", 0.0) < texture_floor` fails
  closed.

## Risks the neighbor documents itself

The neighbor states in its `REVIEW.md` that the metric gate is a heuristic
(pore energy is not a pore classifier, a fixed-ROI drift also reacts to pose
and lighting) and that the state file is atomic but not locked against
concurrent writers. Both limits are real and are stated honestly.

---

## Risks in MY implementation (`scripts/h3_chain_pipeline/`)

The two implementations were reviewed against each other, so it is fair to
state what is still unproven on my side. The neighbor's review of my code
found two real defects, both of which are fixed in the current tree:

* **The seam frames were deleted before they were measured.** `assemble.py`
  returned file paths from inside a `TemporaryDirectory()` and opened them
  after the directory was gone, so every two-chunk run died with
  `FileNotFoundError` on the seam metrics - *after* the final MP4 had been
  muxed. The frames are now read into memory inside the scope.
* **The texture gate failed open.** A `texture_ratio` of 0 (metric
  unavailable) passed an enabled `--min-texture-ratio`. The gate now fails
  closed, with an explicit `texture_measured` flag so that "the first chunk
  has no baseline yet" is still distinguished from "the metric could not be
  computed".

What remains unproven on my side:

* **No real H3 render was made.** The whole pipeline was exercised with a fake
  renderer that writes a real mp4. The argv sent to `scripts/h3_i2v_run.py`
  is built the way the runner's own `--help` documents it, but no GPU run has
  confirmed that the chain actually holds the face.
* **The gate thresholds are guesses.** `--max-face-drift 8.0` is the number the
  repository's own `probe_face_drift.py` uses to call a frame "holding the
  face" (8/255), not a calibrated limit. `--min-texture-ratio` defaults to 0
  (off) because the right value needs real output.
* **The denoise value is not enforced by this pipeline.** `denoise` is a node
  input of the workflow inside `scripts/h3_i2v_run.py`, not a flag. The
  pipeline cannot set it and therefore cannot protect against it; the README
  states the requirement (1.0) but nothing checks it.
* **The audio weld is verified with a click track, not with music.** The 40 ms
  compensation is measured correct on a synthetic track; on real music the
  40 ms shift is inaudible but the seam is still a splice of two different
  moments of the track.
* **The state file is atomic but not locked.** Two pipelines writing the same
  state file can interleave.
* **`--server` is only a URL.** It is handed to the renderer as
  `COMFYUI_PROD_URL`; the ComfyUI input directory still comes from the
  environment or from the drive scan, and the two can disagree.
