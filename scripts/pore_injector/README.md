# Pore injector — adding PORES (structure), not noise (energy)

This tool adds **pores** to skin in AI video: discrete dark pits with a
slightly brighter rim. It does **not** add broadband high-frequency energy,
which is what the eye reads as digital sand.

```
python scripts/pore_injector/pore_injector.py \
    --video  <in.mp4>  --out <out.mp4> \
    --strength 0.07 --pit-density 260 --rim 0.45 --pore-sigma 1.1 \
    --threshold 0.55 --temporal-sigma 3.0 --seed 1234
```

CPU only. No GPU, no ComfyUI, no network.

---

## Why the previous approach failed, and what is different here

The measured history of this pipeline, and what each measurement changed in
the design:

| What was tried | Result | What it taught |
|---|---|---|
| Isotropic noise field | +77% pore energy, eye said "digital sand" 9/10 | Energy is not structure. A pore must be a *shape*, not a statistic. |
| `detail_floor = 0.35` | noise smeared over the whole frame | A constant floor is the opposite of structure-driven amplitude. |
| HSV mask on float32 frames | mask was always 0% coverage | `cvtColor` on float32 `[0,1]` treats values as `0..255`. Every threshold must go through uint8. |
| HSV mask, uint8 fixed | 84% coverage — warm plaster and foam included | A prior that matches the background is worse than no mask. |
| One field + Farneback warp + temporal low-pass | +15.3% pores, −1.1% RGB delta | The mechanism was right; the *shape* of the field was not. |

The last row is the one this tool builds on. The mechanism (one seeded field
for the whole clip, warped by optical flow, temporally low-passed) is kept;
the field's shape and the amplitude law are replaced.

### 1. The field is a population of PITS, not a noise carpet

`field.py` scatters discrete pits over the frame. Each pit is a dark Gaussian
core with a brighter ring around it — the stretched skin at the lip of a
follicular opening. A plain Gaussian blob reads as a smudge; the rim is what
makes the mark read as an opening.

Pits are placed on a **jittered lattice** rather than at random, so they stay
evenly spread (real skin has no clumps and no bald patches) while the jitter
breaks the regularity that would make the result look printed-on.

A **low-frequency density modulation** clusters the pits into small groups
with smoother areas between. The modulation is mapped to a floor of
`1 - cluster_strength` rather than to zero: letting it reach zero produced
areas that the eye read as bald or airbrushed patches.

Per-pit variation is deliberately **tight** (depth 0.62–1.0, size 0.90–1.10).
An earlier version varied the depth over 0.35–1.0 and the size over
0.80–1.25, and the eye read the result as *"dirt and freckles"* rather than
pores: real follicular openings are far more uniform than that.

Two scale bands, like the BCNodes reference but sparse: a dense fine band
and a sparse coarse band of larger, deeper pits.

### 2. The amplitude comes from the skin's own structure

`inject.py` builds a **structure map** from the current frame: the local
standard deviation of a high-passed luma, measured at three scales. The
amount that is injected is

```
amount = structure_map × skin_mask × strength
```

There is **no constant floor anywhere**. Below `--threshold` the structure
map is exactly 0, so smooth skin receives nothing at all.

Three normalisations were implemented and measured on the bath clip:

| mode | pore gain (thigh / chest) | temporal HF delta |
|---|---|---|
| `percentile` (divide by a high percentile of masked energy) | +1.7% / +3.3% | +0.2% |
| `rank` (rank of each pixel among the masked ones) | +22.0% / +25.3% | **+13.4%** |
| `local` (**default**, divide by a blurred local background) | +17.3% / +26.8% | **+7.9%** |

The rank transform spreads the map over the whole `0..1` range, but the
ranking is global — a small change in one region re-ranks pixels everywhere,
and that re-ranking flickers. The local mode is a purely local operation,
so it is temporally stable, and it still spreads the map over the full range.

Two numerical details that are not cosmetic:

* The local standard deviation is computed as `sqrt(mean(residual²))` with
  the residual formed **first**, not as `E[x²] − E[x]²`. In float32 the
  subtraction cancels catastrophically on smooth content: two numbers of
  about 0.25 leave only ~7 significant digits, so the "variance" of a
  perfectly smooth frame came out as pure rounding noise. That made the
  structure map report detail where there was none.
* The blurs use `BORDER_REPLICATE`. With the default border, the high-pass
  of a smooth gradient produces a bright rim of rounding noise around the
  whole frame.

There is also an **absolute guard**: if the masked region carries no
measurable energy, nothing is injected whatever the ratio says. The local
normalisation is a ratio and therefore scale-invariant — on a flat frame
every pixel sits at the local average, the ratio is 1.0 everywhere, and
full-strength pores would be injected into a frame with no detail at all.

### 3. The mask

Two modes:

* `--mask path/to/mask.png` — an external mask, resized to the frame
  geometry. A directory of PNGs is accepted too, one per frame.
* **Fallback prior** — HSV plus the YCrCb skin box, computed on a uint8 copy
  of the frame.

The fallback prior was tuned against *measured* region statistics of the
bath clip, not by eye:

| region | H (deg) | S | V | Cr | Cb | r−g |
|---|---|---|---|---|---|---|
| forehead skin | 28 | 0.70 | 0.87 | 171 | 81 | 0.298 |
| cheek skin | 20 | 0.74 | 0.64 | 173 | 95 | 0.322 |
| chest skin | 20 | 0.59 | 0.77 | 169 | 95 | 0.302 |
| thigh skin | 22 | 0.65 | 0.72 | 169 | 92 | 0.294 |
| wall plaster | 30 | 0.24 | 0.58 | 139 | 117 | 0.071 |
| brick | 30 | 0.37 | 0.52 | 139 | 114 | 0.078 |
| foam | 230 | 0.03 | 0.77 | 128 | 130 | 0.004 |

So the prior uses: warm hue, `S ≥ 0.10`, `V ≤ 0.95` (this is the foam
rejection — foam is bright *and* desaturated), the YCrCb skin box narrowed
to `Cr ≥ 145` (the classic 133 lower bound also accepts the warm plaster),
and `r − g ≥ 0.15` (skin is redder than green; plaster is not).

Then **only the largest connected component** survives, and only **small**
enclosed holes are filled. That last detail was measured: an unrestricted
flood-fill fills the bathtub interior too, because the body surrounds it —
the tub region went from 17% selected to 100% selected.

Result on the bath clip: **52% of the frame selected**, with the wall at 0%
and the brick at 4%.

### 4. Temporal stability

* **One field for the whole clip**, generated once from the seed. This is the
  anti-crawl requirement: BCNodes draws a different random field per frame
  from one seed, which makes the pores crawl by construction.
* The field is warped by **Farneback optical flow** so a pit stays on the
  same patch of skin. The warp is composed on a **coordinate map**, not on
  the field, so the field is resampled from its pristine original exactly
  once per frame and the pits do not soften as the clip gets longer.
* The injected **amount** is low-passed over time with an exponential filter.

> **A latent bug this tool fixes.** The previous pipeline computed Farneback
> on a **float32** grayscale image, and Farneback returns essentially zero
> flow for float32 input: measured 0.4% non-zero pixels and a mean
> displacement of 0.000000, against 99.9% and 0.0415 for the same frames in
> uint8. The old "flow warp" was therefore a **no-op** — the field never
> moved with the subject. This tool converts to uint8 explicitly, so the
> warp actually works.

---

## Report

After processing, a JSON report is written next to the output file
(`<out>_report.json`) and printed to stdout. Every number is computed from
the frames that were actually processed:

```json
{
  "frames_processed": 319,
  "mask": { "mode": "fallback_prior", "coverage_pct": 52.1 },
  "pore_field": { "pit_count": 15580, "coverage_fraction": 0.633, "seed": 1234 },
  "metrics": {
    "pore_band_energy":  { "input": 6.31e10, "output": 6.75e10, "delta_pct": 6.95 },
    "temporal_rgb_delta": { "input": 0.01286, "output": 0.01328, "delta_pct": 3.20 },
    "temporal_hf_delta":  { "input": 2.726, "output": 3.413, "delta_pct": 25.19 }
  }
}
```

The pore band energy uses the **canonical** implementation from
`scripts/probe_pore_spectrum.py` (3–12 px), on purpose: a second
implementation of the same metric would silently produce a different scale
and every comparison against earlier numbers would be meaningless.

Use `--roi x0,y0,x1,y1` (normalised) to measure the band energy on a skin
region instead of the whole frame. The whole-frame number is dominated by
the textured wall, so on a clip where the background carries more detail
than the skin it understates the change on skin.

---

## Parameters

| flag | default | meaning |
|---|---|---|
| `--video` | required | input video |
| `--out` | required | output video |
| `--strength` | `0.07` | depth of a pit core, as a fraction of the 0..1 range |
| `--pit-density` | `260` | fine-band pits per 10 000 pixels |
| `--rim` | `0.45` | brightness of the rim, relative to the pit depth |
| `--pore-sigma` | `1.1` | core sigma of a fine pit, in pixels |
| `--threshold` | `0.55` | structure threshold; below it nothing is injected |
| `--mask` | none | PNG mask or a directory of PNGs |
| `--temporal-sigma` | `3.0` | temporal low-pass time constant, in frames |
| `--seed` | `1234` | field seed |
| `--two-octave` / `--single-octave` | two bands | the coarse band of larger pits |
| `--max-frames` | `0` | process at most N frames (0 = all) |
| `--flow` / `--no-flow` | flow on | warp the field with optical flow |
| `--structure-reference` | `local` | `local`, `rank` or `percentile` |
| `--rife-out` | none | also write an interpolated copy, if RIFE is available |
| `--report` | next to `--out` | path of the JSON report |

`--rife-out` is honest about what it can do: RIFE in this repository runs
through ComfyUI, which needs the GPU, and this tool is CPU-only by design.
It looks for a `rife` filter in the local ffmpeg build, and if there is none
it says so in the report (`"rife": {"requested": true, "available": false,
"reason": "..."}`) and continues without it.

---

## What is still not solved

* **The eye is still the final judge.** The measurements here are
  quantitative: pore-band energy, temporal RGB and HF deltas, mask coverage.
  They show the field is structure rather than energy and that the
  injection is temporally stable, but they do not prove the eye accepts the
  result. The parameter defaults were chosen by looking at rendered crops
  and asking which read as pores rather than as dirt — that is the
  strongest evidence available here, and it is still a judgement.
* **Flicker is reduced, not eliminated.** The flow warp of a sharp pit field
  resamples high-frequency content every frame, which costs temporal HF
  delta. The measured cost is roughly +8% on the 60-frame window with the
  default settings, against +13% for the rank normalisation and +36% for the
  previous noise-based injector at a comparable texture gain.
* **The fallback prior is a prior.** It selects 52% of the bath clip, but
  it leaks into hair and along the bathtub rim. A real segmentation mask
  (`--mask`) is always better, and the fallback exists so the tool works
  without one.
* **The coarse band is barely distinguishable** from the fine band in the
  measurements so far. It is in the field, but whether it contributes
  anything the eye can see is not established.
