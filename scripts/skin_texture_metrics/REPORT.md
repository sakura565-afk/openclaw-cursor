# Real-material report

Command run from the repository root with the project virtualenv:

```bash
.venv/Scripts/python.exe -m scripts.skin_texture_metrics --real-set --frames 12
```

Actual output on 2026-11-04 (12 decoded frames per clip, canonical `thigh` and
`chest` ROIs):

```text
label               | class        | structure | pore_rms | survival | pore      | pore_blur | lap
--------------------+--------------+-----------+----------+----------+-----------+-----------+------
plastic_full_v2     | plastic      | 0.144     | 4.750    | 0.831    | 5.327e+10 | 4.429e+10 | 3.05
sand_PORE_TIGHT_012 | digital_sand | 0.093     | 6.403    | 0.482    | 9.453e+10 | 4.558e+10 | 53.50
flow_PORE_FLOW_0.05 | pores        | 0.240     | 5.130    | 0.727    | 6.140e+10 | 4.462e+10 | 24.68
BC_ab_f0_10_before  | plastic      | 0.126     | 4.738    | 0.837    | 5.273e+10 | 4.413e+10 | 2.54
BC_ab_f0_10_after   | pores        | 0.214     | 5.079    | 0.773    | 5.937e+10 | 4.587e+10 | 3.70
gate BC_ab_f0_10: REJECT - added-band survival 0.226 < 0.450; the added component is noise-like
```

The real tests also reject the available BC 0.20 and 0.30 pairs.

## What the numbers separate

- Sand has more pore-band RMS than plastic (6.403 versus 4.750), but much lower
  blur survival (0.482 versus 0.831) and a lower structural score.
- The intermediate flow clip sits between plastic and sand in activity
  (5.130), retains more band energy than sand (0.727), and receives the highest
  structural score (0.240).
- High survival alone is not sufficient: smooth plastic has the highest raw
  survival because its little remaining band content is mostly low spatial
  frequency, shading, and compression structure.

## Honest failures and missing validation

1. **The single-image classifier does not reject BC noise.** `BC_ab_f0_10_after`
   is labelled `pores` (score 0.214, survival 0.773), although the eye verdict
   is BC noise. The pair gate catches it by measuring the actual added delta:
   added-band survival is only 0.226. Therefore the automatic rollback must use
   `is_pore_like(before, after)`, not the standalone class of `after`.
2. **There is no confirmed real-pore positive in this set.** `PORE_FLOW_0.05`
   is an intermediate synthetic case, not ground truth for biological pores.
   Calling it `pores` means it falls in the high-survival/non-plastic regime;
   it does not prove that resolved, anatomically plausible pores are visible.
3. The thresholds are calibrated and tested on the same reference set. This is
   a regression check against the existing eye verdict, not evidence of
   generalisation to new lighting, resolutions, body regions, or footage.

## Next eye checks required

- Blind native-resolution and magnified crop comparison of `PORE_FLOW_0.05`
  against `full_v2`: confirm whether the higher score corresponds to visible
  pores rather than merely weaker sand.
- Add at least one independently eye-approved real-pore clip and test it without
  moving the thresholds; otherwise the positive `pores` class remains validated
  only by synthetic structure.
- Check the BC 0.20/0.30 outputs and new skin finishes as before/after pairs;
  do not promote a result from the standalone table alone.
- Recheck thresholds on other resolutions and lighting after canonical ROI
  scaling. Temporal identity anchoring is a separate gate and is not established
  by these 12-frame spectral measurements.
