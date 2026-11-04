# Review of the parallel implementation in `scripts/pore_injector_kimi/`

Reviewed against the same task specification, and against measurements on the
same clip (`chain_high/full_v2.mp4`, 736×1120, 24 fps, 319 frames). Every
number below was produced by running the code, not by reading it.

Files reviewed:

* `scripts/pore_injector_kimi/pore_injector.py` (411 lines)
* `scripts/pore_injector_kimi/__init__.py` (5 lines)

---

## Summary

The implementation is competent, well documented, and gets several things
right that the older `skin_detail_inject.py` gets wrong — in particular it
has **no constant amplitude floor**, it generates the field **once** for the
whole clip, it keeps only the **largest connected component** of the mask,
and it rejects foam on both value and saturation.

It nevertheless does not meet the specification, and on the target clip it
does not produce a measurable gain. The defects are listed by severity
below; each one names the file, the line range, what happens, and what was
measured.

---

## Blocking defects

### 1. There is no CLI at all

`pore_injector.py` defines `process_video(...)` and stops at line 411. There
is no `main()`, no `argparse`, and no `if __name__ == "__main__":` block —
verified:

```
$ grep -n "__main__\|argparse\|def main" scripts/pore_injector_kimi/pore_injector.py
NO CLI FOUND
```

The specification requires a command line interface with `--video`,
`--out`, `--strength`, `--pit-density`, `--rim`, `--pore-sigma`,
`--threshold`, `--mask`, `--temporal-sigma`, `--seed`, `--two-octave`,
`--max-frames`, and optionally `--rife-out`. None of them exist as
parameters. `scripts/pore_injector_kimi/__init__.py` exports only
`generate_pore_field`, so there is not even a package-level entry point.

The tool is therefore not runnable as the deliverable describes, and there
is no way to invoke it from the shell.

### 2. The fallback prior does not accept float32 frames

The specification requires the fallback prior to work on float32 `[0, 1]`
frames, converting through uint8. Lines 21–22:

```python
if frame_bgr.dtype != np.uint8 or frame_bgr.ndim != 3 or frame_bgr.shape[2] != 3:
    raise ValueError("frame_bgr must be a uint8 HxWx3 image")
```

Running it on a float32 frame raises instead of converting:

```
float32 input: FAILS -> ValueError: frame_bgr must be a uint8 HxWx3 image
```

This is not the same as the bug it was written to avoid. The old bug was a
*silent* wrong answer (0% coverage); this is a loud failure. The pipeline
happens to sidestep it because `cv2.VideoCapture` yields uint8 BGR
(line 340), so the internal path never sees float32 — but the documented
entry point rejects the very input format the specification names, and
`local_structure_amplitude` has the identical guard (lines 55–57).

### 3. The mask covers 81.7% of the frame — the exact failure the task names

Measured on the target clip:

```
per-frame coverage: [81.6 81.6 81.7 81.7 81.7 81.7]  mean=81.7%
```

The task says the prior must "take only the largest connected region … so
that coverage is not 80% of the frame". This implementation *does* take the
largest connected component (lines 37–41), but on this clip that component
covers 81.7%, because the warm-lit wall and the body are one connected blob.

The prior is hue + saturation + value only (lines 25–29):

```python
skin_hue = (hue <= 25) | (hue >= 170)
not_foam = (value <= np.uint8(242)) & (saturation >= np.uint8(26))
candidate = (skin_hue & not_foam & (value >= 40)).astype(np.uint8) * 255
```

Measured region statistics on the same clip show why this fails:

| region | H (deg) | S | V | Cr | Cb | r−g |
|---|---|---|---|---|---|---|
| skin (cheek/chest/thigh) | 20–22 | 0.59–0.74 | 0.64–0.77 | 169–173 | 81–95 | 0.29–0.32 |
| wall plaster | 30 | 0.24 | 0.58 | **139** | **117** | **0.071** |
| brick | 30 | 0.37 | 0.52 | **139** | **114** | **0.078** |

The wall sits at hue 30° and saturation 0.24, and the hue test
`hue <= 25` uses OpenCV's 0–179 hue scale, i.e. **≤ 50° in degrees** — so
the wall at 30° passes the hue test, and its saturation of 0.24 (62/255)
clears the `saturation >= 26` floor as well.

Two constraints separate them cleanly and are missing here: the YCrCb box
(`Cr >= 145` — skin 169, wall 139) and the red ordering (`r − g >= 0.15` —
skin ~0.30, wall ~0.07). Adding both takes the same clip to **52%**
coverage with the wall at **0%**.

Also note lines 44–45:

```python
mask[~not_foam] = 0
```

This re-applies the value/saturation test to the *blurred* mask, but
`not_foam` is a boolean array and `mask` is uint8 — the indexing works, yet
it can only ever remove pixels, never add any, so it cannot fix an
over-inclusive prior.

### 4. On the target clip the injection makes the pore band measurably WORSE

Running the implementation as-is on the target clip (60 frames):

```json
"pore_band_energy":  { "before": 3.436e13, "after": 3.303e13, "delta_pct": -3.89 },
"temporal_rgb_delta":{ "before": 3.610,    "after": 3.631,    "delta_pct":  +0.60 },
"temporal_hf_delta": { "before": 2.972,    "after": 2.816,    "delta_pct": -5.26 }
```

**−3.9% pore energy.** The tool does not add texture to this clip. The
`strength` default of 2.0 (line 269) with `threshold=0.008` (line 272)
simply is not enough to move the metric.

For reference, the implementation in `scripts/pore_injector/` produces
**+4.7% to +11.8%** on the same clip and the same ROI.

---

## Significant defects

### 5. The metric ROI is the whole frame, so the pore number is meaningless

Lines 154–162 compute the metric ROI as the **bounding box of the mask**,
and lines 337–338 take it from the first frame. Measured:

```
mask bbox ROI = (0, 0, 736, 1120) of frame (1120, 736)
-> the ROI covers 100% of the frame
```

Because the mask covers 81.7% and spans the whole frame, the bounding box is
the whole frame, and the reported pore-band energy is dominated by the
textured plaster wall rather than by skin. The wall is *more* textured than
the skin on this clip, so the metric moves in the wrong direction. This is
the single largest reason the −3.9% above is not a strength problem.

The repository already has a canonical ROI convention for this clip
(`scripts/compare_video_texture.py`: `DEFAULT_ROIS` = thigh and chest,
`FACE_ROI`), and a canonical band-energy implementation
(`scripts/probe_pore_spectrum.band_energy`) — which this file correctly
imports (line 16). The ROI choice should follow the same convention.

### 6. The field is normalised by its maximum, so one freak pit squashes everything

Lines 136–141:

```python
peak = float(np.max(np.abs(field)))
if peak > 0.0:
    field = field / peak
    field[np.abs(field) < 0.02] = 0.0
```

`np.max` is not robust. Measured on the target geometry:

```
density=0.003: max|f|=1.000  p99|f|=0.367  ratio max/p99=2.7
density=0.001: max|f|=1.000  p99|f|=0.211  ratio max/p99=4.7
```

At the default density the single deepest pit is 2.7× deeper than the 99th
percentile, so the normalisation is set by that one pit. At lower densities
it is 4.7×. The whole field is then scaled down accordingly, and the
subsequent hard cutoff at 0.02 removes **59.8%** (default) to **83.9%**
(density 0.001) of the pixels — with the threshold applied *after* the
max-normalisation, so it cuts a different amount on every clip.

A percentile normalisation (99th) plus a soft clip keeps the depth of a
typical pit stable regardless of the worst overlap.

### 7. The pits are placed by uniform random sampling, so they clump

Line 98:

```python
impulses = (rng.random(shape, dtype=np.float32) < density).astype(np.float32)
```

Every pixel is an independent Bernoulli trial. The result is a Poisson
distribution of pit centres, which clumps and leaves bald patches — the
opposite of skin, where follicular openings are spread fairly evenly with
small variations in density.

A jittered lattice (one candidate per cell, jittered within the cell) plus
a low-frequency density modulation gives an even spread with organic
variation, and it is what the eye needs: with a uniformly random field the
rendered result was judged to read as "dirt and freckles" rather than pores.

### 8. The rim is a wide halo, not a lip

Lines 102–104:

```python
centre = _blur_impulses(impulses, sigma)
surround = _blur_impulses(impulses, sigma * 1.8)
return -centre + float(rim) * surround
```

The "rim" is a Gaussian 1.8× wider than the core, so the bright ring sits
around 2–3 core radii out and is correspondingly broad. On a 1.25 px core
the mark spans roughly 8–10 px, with a soft halo around it — that reads as
a smudge around a spot, not as the lit lip of a small opening.

A narrow ring at ~1.6 core radii with a ~0.55 core sigma keeps the whole
mark at 3–4 px, which is what a real follicular opening occupies inside the
3–12 px pore band. The eye made this call explicitly: the wide-halo version
was judged to read as "dirt and freckles", the narrow-rim version as
"SKIN PORES / fine follicular texture".

### 9. The mask is applied twice in the temporal blend, and the field is warped cumulatively

Lines 351–359:

```python
amplitude = local_structure_amplitude(frame, threshold)
raw_injection = strength * 255.0 * field * amplitude * mask_float
...
carried = _warp_layer(previous_injection, flow, grid_x, grid_y)
injection = alpha * raw_injection + (1.0 - alpha) * carried
injection *= mask_float
```

`raw_injection` already contains `mask_float`, and `carried` is the previous
injection, which was itself masked on the previous iteration. The final
`injection *= mask_float` therefore multiplies by the mask **a second
time**. The injected amplitude ends up scaled by `mask²`, so a mask value of
0.6 becomes 0.36 — the soft edges of the mask receive less than the flat
interior, and the two terms of the blend are weighted differently from what
the formula says. Measured:

```
mask values: frac>0.5=0.514, mean over soft=0.516
a value of 0.600 in the mask becomes 0.360 after the second multiplication
```

There is a related issue with how the field is warped. Line 349 warps the
field itself, cumulatively:

```python
field = _warp_layer(field, flow, grid_x, grid_y)
```

The flow direction is **correct** — verified on synthetic blobs: a marker at
`(100, 150)` moves to `(100, 156)` when the content shifts right by 6 px,
which is the right backward mapping. The problem is that each warp
re-interpolates the field, so the resampling compounds. Measured over 79
frames on the target clip:

```
cumulative field warp : std=0.0930  (original 0.2643)  blur loss 64.8%
coordinate-map warp    : std=0.2383  (original 0.2643)  blur loss  9.8%

high-pass energy (pore band proxy):
  cumulative   0.0009
  coord-map    0.0188
```

The cumulative warp loses **95% of the high-frequency content** of the
field over 79 frames. The pits are still there but they have been smoothed
into low-frequency blobs, so the thing that was supposed to read as pores
stops reading as pores. Composing the warp on a **coordinate map** and
resampling the pristine original once per frame keeps 90% of the field's
contrast.

### 9b. The accumulated flow drift is large on this clip

The field's warp is the sum of 300 frame-to-frame flows, so any systematic
error in the flow accumulates without bound. Measured on the target clip
with the same flow code, against a whole-frame phase correlation that says
the camera moves by less than 1 px over the whole clip:

```
plain           frame 120: WALL=  51.8px  SKIN=  98.1px
plain           frame 300: WALL=  53.5px  SKIN= 104.9px
median3+clamp6  frame 120: WALL=  51.4px  SKIN=  49.2px
median3+clamp6  frame 300: WALL=  60.0px  SKIN=  66.0px
median5+clamp4  frame 120: WALL=  48.2px  SKIN=  40.1px
median5+clamp4  frame 300: WALL=  51.3px  SKIN=  55.3px

ground truth: whole-frame shift at frame 300 = (-0.07, +0.41) px
```

The static wall accumulates ~50 px of displacement while the whole frame
moves less than 1 px, so that displacement is flow error, not motion.
A median filter on the displacement plus a magnitude clamp removes about
half of it (the skin drift drops from 105 px to 55 px) but does not remove
it.

### 10. The output is written with `mp4v` and has no audio

Lines 323–324:

```python
writer = cv2.VideoWriter(str(output_path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
```

Measured on the produced file:

```
codec_name=mpeg4
codec_type=video
width=736
height=1120
size = 0.8 MB
```

Two problems. The codec is MPEG-4 Part 2, not H.264 — the rest of the
pipeline encodes with `libx264 -crf 16 -pix_fmt yuv420p`, so the output
here is not comparable with the other clips and is not on the delivery
profile. And there is **no audio stream at all**: `cv2.VideoWriter` cannot
mux audio, so the audio track of the source is silently dropped. The
comparison clips in this pipeline all keep their audio.

### 11. The report is written next to the output with the wrong suffix

Line 409:

```python
report_path = output_path.with_suffix(".json")
```

For `--out clip.mp4` this writes `clip.json`. The convention used by the
other scripts in this repository is `<stem>_report.json` or an explicitly
named report file. As written, a `--out foo.mp4` and a `--out foo.json`
cannot coexist, and the report file name does not say what it contains.

---

## Minor issues

* **Line 241: `while frames < max_frames`** — `measure_video` takes
  `max_frames` as a required positional argument and callers must pass a
  positive number, but `process_video` allows `max_frames=None` (line 273)
  and then calls `measure_video(source_path, metric_roi, frames_processed)`
  (line 378). That path happens to work, but `max_frames=0` is rejected
  while `max_frames=None` means "all" — the two conventions are mixed
  between the two functions.
* **Line 372: `raise ValueError("input video has no decodable frames")`** —
  but `height` and `width` are only assigned inside the loop (line 319),
  so on an empty video the `metric_roi = (0, 0, width, height)` fallback
  on line 376 would raise `UnboundLocalError` first. The check at 372 comes
  after the loop but the fallback assignment at 376 uses variables that were
  never bound.
* **Line 302: `fps = float(capture.get(cv2.CAP_PROP_FPS))`** then a check
  for non-finite — good — but the fallback of 25.0 fps is a guess. A clip
  whose real rate is not 25 would be re-timed silently.
* **No `--rife-out`** option, which the specification lists as optional but
  expected. The RIFE in this repository is a ComfyUI node and needs the
  GPU; a CPU-only tool should say so explicitly rather than omit the flag.
* **No `scripts/pore_injector_kimi/tests/`** — the directory does not exist
  (verified with `ls`), so nothing in this implementation is covered by a
  test. The tests that would catch defects 1, 2 and 3 are exactly the ones
  missing.
* **Line 141: `field[np.abs(field) < 0.02] = 0.0`** — a hard cutoff applied
  after a max-normalisation. It creates hard edges in the field and its
  effect depends on the clip.

---

## What the implementation does well

Worth stating plainly, because these are the parts that are genuinely
better than the older `skin_detail_inject.py`:

* **No constant amplitude floor.** `local_structure_amplitude` (lines 49–64)
  subtracts the threshold and clamps at zero, so smooth regions receive
  exactly nothing. This is the requirement that the old
  `detail_floor = 0.35` violated, and it is met here.
* **The field is generated once** for the whole clip (lines 342–346), not
  per frame, so the pores do not crawl.
* **The largest connected component is kept** (lines 37–41) and the foam
  rejection on both value and saturation is explicit (line 28).
* **The canonical band-energy implementation is reused** (line 16) rather
  than reimplemented, which is the right call for comparability.
* **The temporal blend warps the previous injection** (line 357) rather
  than assuming it stays put, which is the correct idea even though the
  double-masking described in defect 9 spoils the arithmetic.
* Input validation is thorough (lines 21–22, 55–57, 108–116, 285–296).

---

## The measurements behind these findings

Region statistics used in defect 3, measured on
`chain_high/full_v2.mp4` frame 0:

```
region            h_deg              s              v              Cr             Cb             r-g
forehead_skin    28[ 22, 32]  0.70[0.55,0.98]  0.87[0.43,0.99]  171.0   81.0   0.298   0.267
cheek_skin       20[ 16, 24]  0.74[0.58,0.98]  0.64[0.53,0.80]  173.0   95.0   0.322   0.153
chest_skin       20[ 18, 24]  0.59[0.50,0.95]  0.77[0.47,0.95]  169.0   95.0   0.302   0.157
thigh_skin       22[ 16, 26]  0.65[0.58,0.99]  0.72[0.37,0.84]  169.0   92.0   0.294   0.180
wall_plaster     30[ 22, 34]  0.24[0.06,0.47]  0.58[0.50,0.64]  139.0  117.0   0.071   0.071
brick_tan        30[ 26, 34]  0.37[0.22,0.68]  0.52[0.24,0.60]  139.0  114.0   0.078   0.082
tub_white        22[ 16, 24]  0.56[0.18,0.78]  0.69[0.60,0.82]  165.0   96.0   0.263   0.157
foam             230[ 26,260]  0.03[0.02,0.49]  0.77[0.64,0.81]  128.0  130.0   0.004  -0.020
hair             28[ 14, 34]  0.74[0.28,1.00]  0.42[0.02,0.89]  139.0  114.0   0.075   0.078
```

Float32 Farneback returns no flow, which is a separate trap worth
recording (measured on the same frames):

```
OLD pipeline (float32 gray): |dx| mean=0.000000  nonzero=19.80%
NEW pipeline (uint8 gray):    |dx| mean=0.041522  nonzero=100.00%
```

`scripts/pore_injector_kimi/pore_injector.py` uses uint8 throughout
(`frame` from `cv2.VideoCapture`), so it does not hit that particular trap —
but it is the trap the task warns about, and it is worth recording that the
implementation in `scripts/pore_injector/` had to fix it explicitly.

Flow sign convention, verified on synthetic blobs:

```
content moved RIGHT by +6px -> flow dx median= +5.999
marker at original (100,150) -> in the next frame it appears at (100, 156)
after 3 steps of 6px: coordinate map offset median = -18.00 (expect -18)
```

---

## Priority order for a fix

1. Add the CLI (defect 1) — without it the tool is not a deliverable.
2. Accept float32 `[0, 1]` frames in the prior by converting through uint8
   (defect 2).
3. Add the YCrCb box and the red-ordering test to the prior, and measure the
   coverage (defect 3) — 81.7% means pores in the wall.
4. Use a canonical skin ROI for the pore-band metric instead of the mask
   bounding box (defect 5) — otherwise the headline number is the wall's
   texture.
5. Normalise the field by a percentile and soften the cutoff (defect 6).
6. Place the pits on a jittered lattice with a density modulation (defect 7).
7. Narrow the rim (defect 8).
8. Encode with libx264 and mux the source audio (defect 10).
9. Fix the double masking in the temporal blend (defect 9).
