# `scripts/h3_chain_pipeline/` - long H3 clips without a floating face

A production pipeline for long videos on MiniMax H3. It exists to fix one
complaint: **in a long clip the face drifts away in the tail.**

---

## 1. Why a chain and not a guide frame in the middle

The obvious fix is to put a guide frame in the middle of the clip and hope the
model holds the face. It does not work, and the reason is worth stating
precisely because it decides the whole design.

**A guide frame only holds the face for about two frames.** The VAE smooths
around the guide, so the constraint decays immediately in both directions. Our
own earlier attempt pinned frame 87 of a clip and it fixed exactly that
neighbourhood - the tail of the same clip kept drifting. A guide frame is a
local patch on a global problem.

**The tail is the problem, and the tail is far from any guide.** The longer the
clip, the larger the distance between the guide and the end, and the more
drift accumulates in between. Whatever is at the end of the clip is what the
viewer judges the identity by.

So the fix cannot be "hold the face harder at one point". It has to be **"never
let the clip get far from the face"**. That means: cut the clip into pieces
short enough that the identity survives one piece, and start each piece from
a frame the model itself produced.

### The chain

```
chunk 0:  [still] ──► H3 render ──► frames 0..174
                                        │ last frame (174)
chunk 1:                            [frame 174] ──► H3 render ──► frames 175..349
                                        │ last frame (349)
chunk 2:                            [frame 349] ──► H3 render ──► ...
```

Each chunk starts on the **last frame the model produced for the previous
chunk**. The identity is re-established from scratch at every boundary, and
the drift inside one chunk is bounded by the chunk length, not by the clip
length. The tail of a ten-minute clip drifts exactly as much as the tail of a
seven-second clip - and the tail of a seven-second clip is fine.

Rules that follow from this and are enforced in the code:

* **Each chunk has its own seed.** A chunk is a separate generation; sharing a
  seed across chunks would correlate the drift.
* **The character description is repeated verbatim.** The prompt file is passed
  unchanged to every chunk. A reworded description is a different character.
* **The duplicated boundary frame is trimmed.** Chunk *n+1* renders the anchor
  as its frame 0, so the assembled clip would contain that frame twice. It is
  dropped on concat (`select=gte(n\,1)`).
* **The audio is welded at the seams**, not concatenated, so there is no click
  where the track is cut.

### What denoise breaks

`denoise` is the other half of the story, and getting it wrong ruins the chain
in a way that is easy to misdiagnose.

* **`denoise = 1.0` is correct.** The chain needs the new chunk to *follow* the
  anchor frame. Denoise controls how much of the anchor survives into the
  result: at 1.0 the anchor is fully replaced by the new generation.
* **`denoise = 0.25` is the bug that looks like a solution.** At 0.25 the
  effective sigma is 0.800, and the anchor frame is essentially **overwritten**
  rather than continued. The result looks like it respects the guide image -
  the face is there, sharp, identical - and then the model has no continuity
  left to build on. The clip goes on from a frame the model did not really
  produce, and the drift returns.

The failure mode is confusing because the *local* frame looks great. You check
the frame next to the guide, it is perfect, and the clip is still broken at the
end. If the tail drifts, check denoise before you check anything else.

`denoise` is a node input of the workflow, not a flag of
`scripts/h3_i2v_run.py`, so this pipeline does not set it: the renderer builds
the graph and the graph must keep denoise at 1.0.

### Other findings that shaped the defaults

* **fl2va, not ref2va.** fl2va really pins the image to the frame
  (26.35 dB); ref2va only nudges it (16.8 dB). fl2va is the default.
* **Chunks longer than ~30-40 seconds show a visible "texture ratchet"** at
  the seams. The default chunk is **175 frames = 7.3 s at 24 fps**, which is
  the working sweet spot on 16 GB of VRAM.
* **FaceRefine (Carasibana) does not fix tail drift.** It fixes small faces
  inside a frame. It is not a substitute for the chain.

---

## 2. What the pipeline does

```
--plan          print the chunk table, touch nothing
render          for each chunk: stage the anchor, call h3_i2v_run.py, measure, gate
assemble        trim the duplicate frames, concat, weld the audio, mux
report          write the JSON report and the state file
```

### Splitting

`--total-frames` is the length of the finished clip. It is split into chunks of
at most `--chunk-frames` (default 175).

H3 only accepts lengths of the form `5 + 17k`. A requested chunk length is
snapped down to the nearest valid one, and the last chunk is trimmed to land
exactly on `--total-frames`.

Because chunk *n+1* renders the anchor as its frame 0, the arithmetic is:

```
assembled frames = sum(render_frames) - (number of chunks - 1)
```

The first chunk contributes all of its frames, every later chunk contributes
one less.

### Rendering

The renderer is `scripts/h3_i2v_run.py` and it is **not** reimplemented here -
it is called as a subprocess, so its graph, its validation and its error
messages stay the single source of truth.

Two house rules the pipeline obeys:

1. **`--image` is a file NAME, not a path.** The runner validates the graph
   against the live ComfyUI before sending it, and the validator compares the
   value with the file list of the server's input directory. A full path is not
   in that list and the graph is rejected before it is ever sent
   (`workflow rejected before sending: node 1 (LoadImage): input 'image'
   value ... not in [...]`). The pipeline copies every anchor into the ComfyUI
   input directory itself and passes only the bare name. The input directory
   comes from `COMFYUI_INPUT_DIR`, then from `COMFYUI_PROD_DIR`, then from a
   scan of the drive roots.
2. **`--audio` is required.** The pipeline passes a **silent** WAV: the
   renderer muxes that track into the chunk, and the real soundtrack is welded
   onto the assembled clip afterwards, so the chunk's copy of the audio cannot
   fight it.

The renderer has **no `--server` flag** - the ComfyUI URL comes from the
`COMFYUI_PROD_URL` environment variable. (The older `scripts/h3_chain_video.py`
passes `--server` to the renderer, which the renderer's parser rejects.) The
pipeline sets the environment variable instead.

### The quality gate

Every chunk is measured, not trusted. Two numbers decide whether it may enter
the clip:

* **face drift** - how far the face moved inside the face ROI between the
  chunk's first frame (the anchor) and its later frames. The gate uses the
  drift of the **last** frame and the mean, because the tail is where the
  identity dies. A chunk that holds the face for 170 frames and drifts over
  the last 5 still has a low mean; the tail number is the one that catches it.
* **texture** - the pore-band energy inside the same ROI, compared **relative
  to the first accepted chunk**. The absolute pore energy depends on the
  resolution and on the ROI; the ratio between two chunks of the same clip
  does not.

Both come from the repository's own measurement code
(`scripts/probe_face_drift.py`, `scripts/compare_video_texture.py`), imported as
libraries, so the numbers mean the same thing here as everywhere else.

The texture gate **fails closed**: a ratio that could not be computed (no
scipy, a failed decode) is an unknown, not a pass. The first chunk is the one
exception - it *is* the baseline, so there is nothing to compare it against.

A chunk that fails is re-rendered **with a new seed**, up to `--max-retries`
times, and the best variant is kept. Only that chunk is re-rendered; the
chunks that passed are not touched.

### Assembly

* The duplicated boundary frame is dropped from every chunk after the first.
* The chunks are concatenated without overlap.
* The audio is cut at the seams and welded with an **equal-power (`qsin`)
  crossfade of 40 ms**.
* The track is then made exactly as long as the picture.

The crossfade consumes 40 ms of track per seam, so the welded audio would
creep ahead of the picture by 40 ms per seam. Measured with a click track
(10 s, one click per second, cuts at 3 s and 6 s):

```text
without compensation: clicks heard at 0.0 1.0 2.0 2.966 3.96 4.96 5.926 6.92 ...
                       the click that belongs at 4.0 is heard at 3.96
with compensation:    clicks heard at 0.0 1.0 2.0 3.0 4.0 5.0 6.0 7.0 ...
```

So each cut is moved later by 40 ms for every seam that came before it.

The cuts are placed on the **render** timeline, not on the assembled one: each
seam drops one frame (41.7 ms at 24 fps) from the assembled clip and 40 ms
from the audio, and the two nearly cancel.

### Idempotence

Renders are expensive, so a second run must not repeat them. The state file
(default: `<output>.state.json`) records, per chunk: the index, the seed, the
hash of the prompt **content**, whether the chunk was accepted, the video, the
last frame, the anchor it was rendered from, and the metrics.

A chunk is reused only when **all** of these hold:

* it was accepted,
* the prompt hash matches,
* the seed is one this run would have chosen (a changed `--seed-base`
  invalidates it),
* the video file still exists,
* the anchor still matches the last frame of its predecessor.

The last point matters: if a predecessor was re-rendered with a new seed, its
successors were rendered from a frame that no longer exists in the clip, so
they are invalidated and re-rendered. A record that does not remember its
anchor is not reused - the comparison fails closed.

The prompt hash is taken over the **content** of the prompt file, not over its
name, so renaming the file does not invalidate hours of rendering.

---

## 3. Usage

```bash
# what would happen, without rendering anything
python scripts/h3_chain_pipeline/pipeline.py --plan \
    --image still.png --prompt-file prompt.txt --audio track.m4a \
    --output out.mp4 --total-frames 2100

# render it
python scripts/h3_chain_pipeline/pipeline.py \
    --image still.png --prompt-file prompt.txt --audio track.m4a \
    --output out.mp4 --total-frames 2100
```

`--plan` prints something like this and touches nothing - no ComfyUI, no
renderer, no state file:

```text
chunk  seed      frames  out frames        seconds  note
-----  --------  ------  ---------------  -------  -----------------
    0        42     175       0..174        7.29
    1        43     175     175..348        7.25  renders 175, contributes 174
    2        44     175     349..522        7.25  renders 175, contributes 174
    3        45     175     523..696        7.25  renders 175, contributes 174
    4        46     175     697..870        7.25  renders 175, contributes 174
    5        47     141     871..1010        5.83  renders 141, contributes 140

total: 6 chunks, 1011 frames rendered, 1000 frames in the finished clip (41.67 s)
prompt hash: 8ba6fff635c95855
note: last chunk renders 141 frames (needs 130); the assembly trims the surplus
```

### Options

| flag | meaning |
| --- | --- |
| `--image` | the still the first chunk starts from (a path) |
| `--prompt-file` | the character description, passed to every chunk verbatim |
| `--audio` | the soundtrack of the finished clip |
| `--output` | the finished mp4 |
| `--chunk-frames` | maximum chunk length, default 175 (7.3 s at 24 fps) |
| `--total-frames` | length of the finished clip in frames |
| `--width` / `--height` | the render size |
| `--steps` | the render steps |
| `--seed-base` | the seed of chunk 0; every chunk derives its own |
| `--max-retries` | extra attempts for a chunk that fails the gate, default 2 |
| `--max-face-drift` | the tail drift above which a chunk is rejected, default 8.0 |
| `--min-texture-ratio` | the pore ratio below which a chunk is rejected, default 0.0 |
| `--plan` | print the chunk table and stop |
| `--state` | the state file, default `<output>.state.json` |
| `--server` | the ComfyUI URL, passed to the renderer as `COMFYUI_PROD_URL` |
| `--work` | the scratch directory, default `<output>.h3chain` |
| `--report` | the report file, default `<output>.report.json` |
| `--keep-going` | assemble the clip even if a chunk never passed the gate |
| `--loras-json` / `--negative` | passed through to the renderer |

---

## 4. The report

`<output>.report.json` holds the whole job:

```jsonc
{
  "output": "...", "state": "...", "prompt_hash": "8ba6fff635c95855",
  "plan": { "total_frames": 1000, "chunks": [ { "index": 0, "seed": 42, ... } ] },
  "gate": { "max_face_drift": 8.0, "min_texture_ratio": 0.0, "max_retries": 2 },
  "chunks": [
    {
      "index": 0, "seed": 42, "accepted": true, "reused": false,
      "attempts": [
        { "attempt": 0, "seed": 42, "accepted": false,
          "reason": "face drift 12.30 at the tail exceeds the limit 8.00",
          "metrics": { "face_drift_last": 12.3, "texture_ratio": 0.98, ... } },
        { "attempt": 1, "seed": 1042, "accepted": true,
          "reason": "within the limits", "metrics": { ... } }
      ],
      "metrics": { ... }, "reason": "within the limits"
    }
  ],
  "seams": [
    { "index": 0, "face_delta": 3.21, "pore_before": 5.0e10,
      "pore_after": 4.9e10, "texture_ratio": 0.98 }
  ],
  "clip_drift": { "frames": 1000, "face_drift_mean": 1.8,
                  "face_drift_max": 4.2, "face_drift_last": 3.9,
                  "frames_holding_face": 998 }
}
```

* `chunks[].attempts` - every attempt, its seed, whether it passed and why.
* `chunks[].accepted` - false means the chunk is in the clip but is outside
  the limits; the best variant was kept.
* `seams` - the texture ratchet at the boundaries: the pore energy on each
  side of the seam and the ratio between them.
* `clip_drift` - the drift over the **whole assembled clip**, from
  `scripts/probe_face_drift.py`. This is the number that says whether the
  complaint is fixed.

---

## 5. Tests

```bash
python -m pytest tests/test_h3_chain_pipeline.py tests/test_h3_chain_pipeline_e2e.py -q
```

* `tests/test_h3_chain_pipeline.py` - the plan arithmetic, the state and its
  idempotence rules, the gate, the assembly and the audio weld.
* `tests/test_h3_chain_pipeline_e2e.py` - the whole chain with a **fake
  renderer** that writes a real mp4. It checks that the first chunk starts on
  the still, that the next chunk starts on the last frame of its predecessor,
  that the renderer is called with a file NAME, that a failing chunk is
  re-rendered with a new seed, that a second run renders nothing, and that the
  assembled clip has exactly the requested number of frames.

No test needs a GPU or a ComfyUI server.

---

## 6. Known limits

* **The gate is a heuristic.** Pore energy is not a pore classifier, and a
  fixed-ROI face drift also reacts to pose and lighting. The thresholds
  (`--max-face-drift 8.0`) are a starting point, not a measured optimum - they
  have to be calibrated on real H3 output, which needs a GPU.
* **The state file is atomic but not locked.** Two pipelines writing the same
  state file at the same time can still interleave.
* **`--server` is only a URL.** It is passed to the renderer as
  `COMFYUI_PROD_URL`; the input directory still comes from the environment or
  from the drive scan.
* **The last chunk can be shorter than the others.** That is the price of
  hitting `--total-frames` exactly.
