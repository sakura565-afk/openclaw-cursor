# Skin-metric independent calibration

Constructive truth is not human eye labelling. Disagreements are retained.

## Summary

- Core constructive controls: 4/4 metric matches.
- All synthetic cases: 16/20 constructive-label matches.
- Blind visual labels loaded: 20; metric/visual matches: 14.
- Constructive mismatches: fine_pores_diagnostic_0, fine_pores_diagnostic_1, fine_pores_diagnostic_2, fine_pores_diagnostic_3.
- Visual/metric mismatches: mixed_core, mixed_2, fine_pores_diagnostic_0, fine_pores_diagnostic_1, fine_pores_diagnostic_2, fine_pores_diagnostic_3.

## Label -> metric -> class

| case | blind crop | constructive | visual | pore_rms | survival | metric | truth match |
|---|---:|---|---|---:|---:|---|---|
| plastic_core | 12 | plastic | plastic | 0.0000 | 0.0000 | plastic | True |
| plastic_level_96 | 19 | plastic | plastic | 0.0000 | 0.0000 | plastic | True |
| plastic_level_160 | 13 | plastic | plastic | 0.0000 | 0.0000 | plastic | True |
| plastic_level_192 | 1 | plastic | plastic | 0.0000 | 0.0000 | plastic | True |
| pores_core | 14 | pores | pores | 5.8114 | 0.6208 | pores | True |
| pores_coarse_1 | 3 | pores | pores | 5.7485 | 0.6201 | pores | True |
| pores_coarse_2 | 17 | pores | pores | 6.3732 | 0.6207 | pores | True |
| pores_coarse_3 | 6 | pores | pores | 6.9162 | 0.6178 | pores | True |
| noise_core | 5 | digital_sand | digital_sand | 6.8396 | 0.1162 | digital_sand | True |
| noise_sigma_16 | 7 | digital_sand | digital_sand | 9.1138 | 0.1141 | digital_sand | True |
| noise_sigma_20 | 15 | digital_sand | digital_sand | 11.5065 | 0.1179 | digital_sand | True |
| noise_sigma_24 | 9 | digital_sand | digital_sand | 13.8059 | 0.1148 | digital_sand | True |
| mixed_core | 2 | digital_sand | pores | 8.8830 | 0.3198 | digital_sand | True |
| mixed_1 | 8 | digital_sand | digital_sand | 10.7612 | 0.2572 | digital_sand | True |
| mixed_2 | 4 | digital_sand | pores | 13.0902 | 0.2285 | digital_sand | True |
| mixed_3 | 16 | digital_sand | digital_sand | 15.0799 | 0.2122 | digital_sand | True |
| fine_pores_diagnostic_0 | 10 | pores | pores | 5.0598 | 0.2557 | digital_sand | False |
| fine_pores_diagnostic_1 | 18 | pores | pores | 3.4521 | 0.2513 | plastic | False |
| fine_pores_diagnostic_2 | 20 | pores | pores | 6.6245 | 0.2705 | digital_sand | False |
| fine_pores_diagnostic_3 | 11 | pores | pores | 5.3331 | 0.2690 | digital_sand | False |

## Provisional thresholds

- plastic maximum pore RMS: **4.900**
- digital-sand maximum survival: **0.600**
- action: retain existing values provisionally; do not auto-fit or claim validation
- rationale: The retained thresholds separate the four constructive core controls. The normal fine-pore diagnostics test a different spatial scale and expose whether energy plus blur survival is sufficient; failures are reported, not used to move a threshold until a blind human labels the same sheets.
- caveat: Final visual judgement remains with a human. Synthetic labels establish what was constructed, not what a human sees; AI visual labels are only a pre-check.
