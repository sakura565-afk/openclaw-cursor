# Cross-review: `scripts/eval_judge/`

## Scope

At review time the neighbour contained `__init__.py`, `skin.py`, `video.py`,
`metrics.py`, `sheet.py`, `scoring.py`, and `judge.py`. No neighbour test file
was present. The review used source inspection and direct CPU probes of the
public helpers.

## Findings

1. **Mask closing does not use its dilation result** — `skin.py:129-138`.
   `_fill_small_holes` computes `dilated`, but then erodes `padded`, which is the
   original mask. This is erosion of the source, not morphological closing, and
   removes thin skin regions instead of filling holes. A direct CPU probe with a
   7x7 mask containing one foreground pixel changed its area from 1.0 to 0.0.
   The erosion must read the dilated representation (with matching padding).

2. **Identical flat frames look maximally uncorrelated** — `video.py:117-130`.
   `frame_correlation` returns 0.0 whenever the denominator is zero. Two
   identical constant frames therefore return 0.0, although the helper is
   documented as a same-take probe. A direct probe on two identical 16x16 frames
   filled with value 127 returned 0.0. If this feeds the motion guard, static
   clips can be rejected as different motion; identical zero-variance inputs
   need an explicit equality case and differing flat inputs need a defined
   policy.

3. **The numpy Gaussian fallback changes image geometry** — `metrics.py:78-94`.
   The second convolution is applied through a transposed, freshly padded array
   and does not produce the original shape. With `_ndimage = None`, a direct call
   on a 16x16 image returned 26x26. On hosts without scipy this invalidates the
   pore spectrum and survival ratio instead of providing the documented
   equivalent fallback.

4. **The motion guard is one-sided and misses static/moving mismatches** —
   `metrics.py:261-269`. It rejects only `candidate_delta / reference_delta > 2`.
   A much smaller candidate delta is accepted, and a zero reference delta makes
   the ratio `None`, also accepted. Direct probes returned `comparable: True`
   both for reference=10/candidate=0 and reference=0/candidate=10 with matching
   FPS/frame counts and no optional correlation. These are different motion and
   must not receive numeric flicker verdicts under the brief.

5. **Temporal metrics are reimplemented rather than reused** —
   `metrics.py:164-205`. The brief explicitly says to use the logic from
   `scripts/compare_video_texture.py`, not rewrite it. This module imports scipy
   and `band_energy`, but not `compare_video_texture.measure`; RGB/HF/face drift
   are separate implementations and can drift from the established measurement.

6. **The “blind” prompt discloses source-identifying crop paths** —
   `judge.py:115-116,165-169`. Crop files are stored under `crops/<name>`, and
   `build_prompt` prints every path to the vision judge. A candidate name in
   `--candidate name=path` (or its filename stem) therefore reaches the judge,
   contrary to the module's own claim that no filename reaches it. The prompt
   should reference only blind labels and crop numbers, not those paths.

7. **Invalid flicker numbers are still reported and printed** —
   `judge.py:343-350,553-568`. Candidate `metrics` always include temporal RGB,
   HF, and face drift, and `_print_summary` always prints all three even when
   `flicker.status` is `not comparable`. The required guard must suppress these
   numbers for invalid comparisons, not merely add a warning after displaying
   them.

## What was not found

No absolute path literals, GPU rendering, or ComfyUI endpoint calls were found
in the neighbour files reviewed. No neighbour tests were present, so the probes
above were run directly against the public helpers.

## Remaining risks in this implementation

The motion guard uses a scalar temporal-delta heuristic, so different motion
with a similar magnitude can pass. The chroma mask is not person-aware, and
independent top-coverage crop selection does not guarantee anatomically paired
crops across variants. These limits are disclosed in `README.md`.
