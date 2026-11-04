# Cross-review: `scripts/eval_judge_kimi/`

Reviewed: the neighbour implementation in `scripts/eval_judge_kimi/`
(`eval_judge.py`, 423 lines) against the brief and against the behaviour
measured on the real clips in `$HERMES_SCRATCH_DIR`
(`chain_high/full_v2.mp4`, `PORE_FLOW_0.05.mp4`).

Every finding below was reproduced with a direct CPU probe or a real run of
the neighbour's CLI; the numbers quoted are the observed ones, not estimates.
Line numbers refer to `scripts/eval_judge_kimi/eval_judge.py` at the time of
the review.

## Summary

The neighbour implements the shape of the brief correctly: a CLI, a shuffled
blind sheet, `report.json`, a prompt and a `--scores` path. The mechanics of
the blind sheet and of the score aggregation are sound.

The measurements underneath it are not. The three defects in "Blocking" below
each make a number in `report.json` wrong by a large factor or make the
number meaningless for the decision it is supposed to support.

---

## Blocking

### 1. The temporal metrics are computed on frames that are 64 apart, not on consecutive frames

`load_video_frames` (lines 202-237) samples N frames evenly across the clip:
for `full_v2.mp4` (319 frames) with `--frames 6` the sampled indices are
`[0, 64, 127, 191, 254, 318]`. `_temporal_metrics` (lines 264-277) then
hands exactly those frames to `texture_measure`, which computes the RGB delta
between *successive entries of the list* - i.e. between frames 64 apart.

Measured on `full_v2.mp4`:

| measurement | value |
|---|---|
| neighbour's `rgb_delta` (frames 64 apart) | 40.5330 |
| consecutive-frame `rgb_delta` (same clip) | 5.7304 |
| ratio | **7.1x** |

The 0.426 figure you get from the first 6 consecutive frames is a third
number again, because the clip's motion is not uniform.

This is not a rounding difference. `compare_video_texture.py` - the file the
brief points at for this logic - extracts the *first N consecutive* frames
(`extract()`, lines 75-83) precisely so that the delta is a flicker measure.
Measuring a delta across 2.7 seconds of motion mixes the motion itself into
the number, and the amount of motion depends on where in the clip the samples
land. Two candidates sampled at different moments of the same take get
different numbers for reasons that have nothing to do with flicker.

The neighbour's own `flicker_guard` (line 143) compares `motion_delta`, which
is computed the same way, so the guard and the metric agree with each other -
but both are measuring inter-sample motion, not flicker. The guard therefore
cannot detect a candidate whose flicker differs while its overall motion
matches, which is the exact case the guard exists for.

**Fix:** extract a run of consecutive frames (a separate short burst, as
`scripts/eval_judge/judge.py` does with `--flicker-frames`) for the temporal
metrics, and keep the evenly-spread sampling for the crops. The two jobs want
different frames.

### 2. The skin mask is a union of two loose priors and covers 83% of the frame - it selects wall

`skin_mask` (lines 21-34) ORs an HSV prior and a YCrCb prior
(`hsv_prior | ycrcb_prior`, line 31) and only removes near-white pixels
(`v < 250`, line 32). There is no background protection.

Measured on the bath clip frame at t=6s of `full_v2.mp4`:

| region | neighbour mask | `scripts/eval_judge/` mask |
|---|---|---|
| whole frame | **83.1%** | 48.1% |
| weathered wall, top left | **100%** | 0% |
| weathered wall, top right | **96%** | 0% |
| chest | 90% | 92% |
| thigh | 100% | 100% |

The wall is a warm, desaturated plaster and it passes both priors. The
neighbour then ranks crop candidates by mask coverage (lines 64-67), so the
highest-coverage window in the frame is a patch of *wall* - the mask is
near-saturated there too. The first crop the neighbour selects from this
frame was verified visually to be a tiled/stone bathroom surface, not skin.

That is the original failure mode of this pipeline: pores were measured on a
wall, the number looked like texture, and the eyes called the result
"digital sand". The prior in `scripts/skin_mask_v2.py` already solves this
with background-chroma protection (`skin_mask_v2()`, the `background_protection`
term at lines 209-216 of that file) - the neighbour's own docstring in
`scripts/eval_judge_kimi/README.md` claims the mask is a "prior", but it
reimplements the weaker version of it rather than reusing the one in the repo.

**Fix:** reuse `scripts/skin_mask_v2.py`'s chromatic fallback
(`skin_mask_v2(..., use_comfy=False)`, no ComfyUI call involved), or apply the
same border-chroma background protection. Then verify the coverage before
cutting crops, and refuse the run when the mask is empty - the brief's
explicit requirement, and the trap that made the previous mask 0%.

### 3. The crops are not saved, so the "numbered crops" in the brief do not exist as files

The brief asks for the crops to be saved as PNG and for the prompt to list
numbered crops. The neighbour never writes a crop file: `analyze_video`
(lines 250-261) keeps the crops in memory as numpy arrays, `make_blind_sheet`
(lines 102-140) pastes them into the sheet, and the arrays are then dropped.
`grep` for `save(` in the file finds exactly two hits - the sheet
(line 139) and the temporary full frames written for the temporal metrics
(line 269).

Confirmed by running the neighbour's CLI: the output directory contains only
`blind_sheet.png` and `report.json`. `report.json` records
`"skin_crops": 6`, a count, not a path.

Two consequences:

* A vision judge that is asked to score *individual crops* has nothing to
  load. It can only look at the sheet, where the crops are 128px tiles
  scaled into a grid - at that size the difference between "pores" and
  "digital sand" is not resolvable, which is the whole point of the exercise.
* The report cannot be re-analysed later without re-running the extraction.

**Fix:** write each crop as a PNG under the artefact directory and record the
paths in `report.json`, as the brief specifies. The blind letter must still
be the only label the judge sees (see "Correct" below).

---

## Significant

### 4. `--out` is required, so the default artefact directory from `HERMES_SCRATCH_DIR` does not exist

`build_parser` (line 394) declares `--out` as `required=True`. The brief asks
for the artefact directory to default to `$HERMES_SCRATCH_DIR`. As written,
the tool refuses to run without an explicit path, and there is no fallback
that reads the environment variable anywhere in the file.

### 5. The frame count is checked but the frame *rate* is not read from the file the same way the metric is

`load_video_frames` takes the frame rate from `cv2.CAP_PROP_FPS`
(line 210), while the temporal metrics are computed by
`compare_video_texture.measure` on frames decoded by OpenCV. For the two
clips used here both report 24 fps, so this is not currently a live bug -
but a container whose declared rate differs from its real rate would make the
fps guard in `flicker_guard` (line 151) compare the wrong numbers. Reading
the rate with `ffprobe` (as `scripts/eval_judge/video.py:probe` does) and
using the same source for both is the safer arrangement.

### 6. The `flicker_guard` tolerance is a bare 35% of the motion delta

`flicker_guard` (lines 143-163) rejects a candidate when the motion delta
differs by more than `max(0.5, 0.35 * max(ref, cand))`. A 35% band on a
motion-delta figure is a wide window; `compare_video_texture.py`'s own
documentation records re-renders of the same seed differing by 1% in texture
and by hundreds of percent in temporal delta, so 35% is defensible as a
first cut - but it is a magic number with no derivation, and it is the only
thing standing between the reader and a numeric flicker verdict.

The README does disclose this ("The motion guard is a heuristic, not a proof"),
which is the right thing to do.

### 7. The score shape the prompt asks for is not the score shape the code accepts

`judge_prompt` (lines 341-353) asks the judge for

```json
{"A": [{"pore_readability": 5, "naturalness": 5, "wax_plastic": 5, "overlaid_noise": 5}]}
```

and `aggregate_scores` (lines 169-199) accepts exactly that - a dict of
blind label to a *list* of judge objects. This works (verified), but the
brief says "scores from an external judge", and a judge that answers with
the more obvious shape `{"A": {"pore_readability": 5, ...}}` - a flat
object - is rejected with `ValueError: scores for A must be a non-empty
list`. Accepting both costs three lines.

Note also that the criterion is named `overlaid_noise` here and
`overlay_noise` in the brief and in `scripts/eval_judge/`. Either is fine,
but the two implementations of the same tool should agree, or a score file
produced for one will not load in the other.

### 8. `aggregate_scores` compares against fixed thresholds, not against the other variants

`aggregate_scores` (lines 190-197) sets the verdict to `pass` when median
pore readability and naturalness are >= 6 and median wax/plastic and
overlaid noise are <= 4. These thresholds are not derived from anything
measured in this repo, and they are applied per variant in isolation.

The brief asks for a verdict on an A/B comparison. A judge scoring A at 7/8
and B at 6/7 has separated the two variants; the threshold rule calls both
`pass` and says nothing about which is better. The `median`/`Q1`/`Q3`/`IQR`
aggregation is right, and the ranking that could be built on top of it is
missing.

### 9. `select_skin_crops` raises when a frame has fewer skin windows than requested

Line 84-85: `raise ValueError("not enough non-empty skin crops in frame")`.
A candidate clip that is framed differently - tighter on the face, or with
the subject partly out of frame - aborts the whole run with exit code 2
rather than producing fewer crops for that candidate. The run then dies on
the first bad candidate and reports nothing about the others.

---

## Minor

### 10. The prompt does not identify which crop is which

`judge_prompt` (lines 341-353) lists the blind letters but no crop numbers,
although `make_blind_sheet` does number the rows on the sheet itself
(lines 133-136, `"01"`, `"02"`, ...). The judge is expected to count rows
on the image. It works, but the brief asks for "numbered crops" in the
prompt text.

### 11. `print_summary` prints the source file name next to the blind letter

`print_summary` (line 372) prints `Path(row['path']).name` in the first
column and the blind letter in the second. The console table is for the
operator, not for the judge, so this is not a leak of the kind that matters -
 but it does mean the console and the sheet disagree about which row is
which, which is a needless source of confusion when transcribing results.

### 12. `texture_measure` is called with `rois=[]`

Line 271: `texture_measure(paths, rois=[], canonical=None)`. The empty ROI
list means the pore band is never measured through the canonical path - which
is deliberate here, since the neighbour measures the pore band itself on the
crops (line 253) - but it also means `face_drift` is computed against
`compare_video_texture.FACE_ROI`, a normalised face rectangle that was
measured on the H3 704x1056 frame.

On the bath clip that rectangle lands roughly on the face (it resolves to
x 240-418, y 209-396 on a 736x1120 frame, and a visual check of that
region shows the face with a shoulder below it), so the drift number is not
meaningless here. It is still a fixed rectangle carried over from a clip at
a different resolution and a different framing, and the tool offers no way
to override it - a clip framed tighter on the face would put a good part of
the ROI on the shoulder.

**Fix:** expose the face ROI as an option, the way
`scripts/eval_judge/judge.py` does with `--face-roi`.

---

## What was done right

Worth stating, because these are the parts that would be expensive to redo:

* **The blind sheet itself is correct.** `make_blind_sheet` shuffles with
  `random.Random(seed)` (line 112), labels with bare letters, and puts no
  source name on the image. Verified visually on the produced PNG: the only
  text on the sheet is the seed and the variant count.
* **`blind_key` keeps the un-blinding map in `report.json`, not on the
  sheet** (lines 332-335), and the sheet file itself contains no file names.
* **The score aggregation is well built**: it validates that every criterion
  is a number in 1..10 (lines 179-182), rejects booleans, and reports
  median, Q1, Q3 and IQR per criterion using `np.percentile`.
* **The pore band is the canonical one.** `band_energy` is imported from
  `scripts/probe_pore_spectrum.py` (line 18) and used with the 3-12 px band
  (line 253) rather than re-derived.
* **The tool is CPU-only and never touches ComfyUI or a GPU** - confirmed by
  grepping the file for the ComfyUI endpoints and by the fact that the
  neighbour never imports `scripts/skin_mask_v2.py`.
* **No absolute path literals** - the CI check `tests/test_no_absolute_paths.py`
  passes on the neighbour's files.
* **The motion guard does refuse to print a numeric flicker verdict** when
  the guard fires (lines 363-370 of `print_summary`): the numbers are
  replaced by the reason. This is the behaviour the brief asks for and the
  neighbour gets it right.

---

## What was not found

* No GPU rendering, no ComfyUI call, no network access in the neighbour's
  implementation.
* No absolute path literals in `scripts/eval_judge_kimi/`.
* No place where the source file name is written onto the contact sheet.
* The `--seed` shuffle is deterministic and the labels do not depend on the
  argument order.

---

## Risks in this implementation (`scripts/eval_judge/`)

Recording these honestly, since the review found real defects in the
neighbour's code and the same class of defect could be sitting here:

1. **The chromatic skin prior still leaks onto the bathtub.** The mask is
   now the background-protected one from `scripts/skin_mask_v2.py`, which
   takes the weathered wall from 84% to 0%, but a visual check of the
   overlay on the bath clip still shows the prior covering parts of the
   white tub and the wet water. The mask is a fallback, not a person
   segmenter, and the report says so: it carries the coverage number
   (`crop_geometry.reference_skin_coverage`) and the per-crop confidence, and
   it warns on stderr above 35% coverage. A person-aware mask (the ComfyUI
   path in `skin_mask_v2.py`) would be the fix, and it is out of scope here
   because this tool must not touch ComfyUI.
2. **The crops are paired by construction, but only because the reference
   defines the boxes.** If a candidate is framed differently from the
   reference, the same box lands on a different body part in that candidate.
   The per-crop `skin_confidence` number is what makes this visible; it is
   not an error, but it is a limit.
3. **The comparability guard is a heuristic.** It combines frame rate, frame
   count, the temporal delta ratio and a low-resolution frame correlation.
   Two different takes with similar motion and similar colour will pass it.
   The guard is calibrated to catch the failure that actually bit us - a
   candidate at a different frame rate, or a different seed - and it is
   documented as a guard rather than a proof.
4. **The judge is external by necessity, so the tool cannot check the
   judgement.** It can only reduce the variance: paired crops, a blind
   sheet, four fixed criteria, and an IQR that makes a disagreeing judge
   visible instead of averaging it away. A judge that gives 5/10 and 4/10
   on the same crop will still be able to do that.
5. **The temporal metrics need the two clips to be the same take**, and
   nothing in the file can prove they are. The report says
   `not comparable` with a reason when it can, and says nothing when it
   cannot - a candidate that passes every check is trusted.
