# Offline texture report

All measurements were CPU-only on 2026-11-04. No renderer or ComfyUI server was contacted. Commands use the project virtualenv and artifacts below `HERMES_SCRATCH_DIR`; generated reports and decoded frames were also kept there, outside the repository.

## Reference calibration

Command:

```bash
.venv/Scripts/python.exe -m scripts.skin_texture_metrics --real-set --frames 12 \
  --work "$HERMES_SCRATCH_DIR/chain_texture_gate_calibration" \
  --json-out "$HERMES_SCRATCH_DIR/chain_texture_gate_calibration/report.json"
```

| Reference | Class | Structure | Pore RMS | Raw survival | Pore |
|---|---|---:|---:|---:|---:|
| plastic full_v2 | plastic | 0.144 | 4.750 | 0.831 | 5.327e10 |
| sand PORE_TIGHT_012 | digital_sand | 0.093 | 6.403 | 0.482 | 9.453e10 |
| flow PORE_FLOW_0.05 | pores | 0.240 | 5.130 | 0.727 | 6.140e10 |
| BC before | plastic | 0.126 | 4.738 | 0.837 | 5.273e10 |
| BC after | pores | 0.214 | 5.079 | 0.773 | 5.937e10 |

The real BC after image passes the new 0.18 structure floor and has high raw survival, but its added-component survival is **0.226**, so the new gate rejects it. This is why raw pore energy or finished-image survival is insufficient.

## MASTURB2 existing single/chained artifacts

The offline command in `README.md` produced:

```text
label                 | frames | pore_rms | survival | structure | pore
----------------------+--------+----------+----------+-----------+----------
single_chunk          | 12     | 5.913    | 0.803    | 0.606     | 7.613e+10
assembled             | 12     | 5.818    | 0.812    | 0.528     | 7.274e+10
seam_before           | 6      | 5.935    | 0.797    | 0.626     | 7.682e+10
seam_after            | 6      | 5.873    | 0.820    | 0.563     | 7.422e+10
source_seam_window    | 12     | 5.904    | 0.808    | 0.595     | 7.552e+10
assembled_seam_window | 12     | 5.841    | 0.818    | 0.538     | 7.323e+10

assembled / single: survival 101.1%, structure 87.1%
seam after / before: survival 102.9%, structure 89.9%
assembled / source seam: survival 101.2%, structure 90.4%
seam added survival: 0.563; finished structure: 0.539; texture-only gate: ACCEPT
```

Survival does not degrade at this join. Structure drops about 10% at the seam and 13% overall, so the correct conclusion is bounded degradation, not “no change.” The texture-only verdict sets drift to zero to isolate texture and is not a full chunk acceptance.

## GOWN negative control

`GOWN_P1.mp4` and `GOWN_P2.mp4` are 175 frames each. For this offline check they were joined with the second chunk's duplicate frame zero removed and x264 CRF 16, yielding 349 frames. The same offline comparison used boundary 175:

```text
label                 | frames | pore_rms | survival | structure | pore
----------------------+--------+----------+----------+-----------+----------
single_chunk          | 12     | 5.564    | 0.517    | 0.090     | 7.614e+10
assembled             | 12     | 4.052    | 0.553    | 0.059     | 4.818e+10
seam_before           | 6      | 2.197    | 0.661    | 0.000     | 1.297e+10
seam_after            | 6      | 2.299    | 0.679    | 0.000     | 1.488e+10
source_seam_window    | 12     | 2.248    | 0.671    | 0.000     | 1.392e+10
assembled_seam_window | 12     | 2.237    | 0.673    | 0.000     | 1.376e+10

assembled / single: survival 106.9%, structure 64.9%
assembled / source seam survival: 100.4%
seam added survival: 0.632; finished structure: 0.000; texture-only gate: REJECT
reason: structure score 0.000 is below floor 0.180
```

GOWN also demonstrates why high survival alone is not enough. Its first sampled chunk already falls below the 0.18 absolute structure floor, so it is not a valid positive baseline for a successful long render.

## Validation boundary

The MASTURB2 boundary is inferred from frame counts because no original chain state was found. Existing-reference thresholds are calibration regressions, not blind validation. No claim is made about anatomically correct pores, identity preservation, subjective seamlessness, or real GPU retry behavior.
