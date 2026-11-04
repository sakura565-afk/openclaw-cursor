# Skin shape metric: blind human calibration

## Result

- Human/truth: **20/20**.
- Old metric/human: **16/20**.
- New metric/human: **20/20**.
- New metric/constructive truth: **20/20**.
- Corrected crops: **10, 11, 18, 20**.

## Truth / human / old / new

| crop | case | truth | human | old | new | pore RMS | survival | components/10k | width px |
|---:|---|---|---|---|---|---:|---:|---:|---:|
| 1 | plastic_level_192 | plastic | plastic | plastic | plastic | 0.0000 | 0.0000 | 0.000 | 0.000 |
| 2 | mixed_core | digital_sand | digital_sand | digital_sand | digital_sand | 8.8830 | 0.3198 | 1099.447 | 1.128 |
| 3 | pores_coarse_1 | pores | pores | pores | pores | 5.7485 | 0.6201 | 46.115 | 7.225 |
| 4 | mixed_2 | digital_sand | digital_sand | digital_sand | digital_sand | 13.0902 | 0.2285 | 1315.375 | 1.128 |
| 5 | noise_core | digital_sand | digital_sand | digital_sand | digital_sand | 6.8396 | 0.1162 | 1552.192 | 1.128 |
| 6 | pores_coarse_3 | pores | pores | pores | pores | 6.9162 | 0.6178 | 47.743 | 7.001 |
| 7 | noise_sigma_16 | digital_sand | digital_sand | digital_sand | digital_sand | 9.1138 | 0.1141 | 1567.654 | 1.128 |
| 8 | mixed_1 | digital_sand | digital_sand | digital_sand | digital_sand | 10.7612 | 0.2572 | 1201.714 | 1.128 |
| 9 | noise_sigma_24 | digital_sand | digital_sand | digital_sand | digital_sand | 13.8059 | 0.1148 | 1540.799 | 1.128 |
| 10 | fine_pores_diagnostic_0 | pores | pores | digital_sand | pores | 5.0598 | 0.2557 | 259.874 | 2.523 |
| 11 | fine_pores_diagnostic_3 | pores | pores | digital_sand | pores | 5.3331 | 0.2690 | 134.006 | 2.985 |
| 12 | plastic_core | plastic | plastic | plastic | plastic | 0.0000 | 0.0000 | 0.000 | 0.000 |
| 13 | plastic_level_160 | plastic | plastic | plastic | plastic | 0.0000 | 0.0000 | 0.000 | 0.000 |
| 14 | pores_core | pores | pores | pores | pores | 5.8114 | 0.6208 | 50.998 | 7.001 |
| 15 | noise_sigma_20 | digital_sand | digital_sand | digital_sand | digital_sand | 11.5065 | 0.1179 | 1535.102 | 1.128 |
| 16 | mixed_3 | digital_sand | digital_sand | digital_sand | digital_sand | 15.0799 | 0.2122 | 1346.842 | 1.128 |
| 17 | pores_coarse_2 | pores | pores | pores | pores | 6.3732 | 0.6207 | 52.083 | 7.047 |
| 18 | fine_pores_diagnostic_1 | pores | pores | plastic | pores | 3.4521 | 0.2513 | 277.778 | 2.523 |
| 19 | plastic_level_96 | plastic | plastic | plastic | plastic | 0.0000 | 0.0000 | 0.000 | 0.000 |
| 20 | fine_pores_diagnostic_2 | pores | pores | digital_sand | pores | 6.6245 | 0.2705 | 250.380 | 2.523 |

## Candidate comparison

A ratio above 1 has a positive class-bound gap; below 1 means overlap.

| candidate | direction | pore range | sand range | separation ratio |
|---|---|---|---|---:|
| peak_area_fraction | lower_is_pore_like | 0.0221..0.1243 | 0.2794..0.3077 | 2.2489 |
| component_density_per_10k | lower_is_pore_like | 46.1155..277.7778 | 1099.4466..1567.6541 | 3.9580 |
| median_component_width_px | higher_is_pore_like | 2.5231..7.2252 | 1.1284..1.1284 | 2.2361 |
| pit_noise_area_ratio | higher_is_pore_like | 0.5065..0.7432 | 0.6040..0.6345 | 0.7983 |
| laplacian_peak_correlation | lower_is_pore_like | 0.1862..0.5165 | 0.6014..0.6240 | 1.1644 |
| survival_existing_baseline | higher_is_pore_like | 0.2513..0.6208 | 0.1141..0.3198 | 0.7858 |

## Threshold derivation

- Core controls only: 4 cases.
- Core pore maximum density: 50.998264.
- Core sand minimum density: 1099.446615.
- Sparse maximum = midpoint: **575.222439**.
- Minimum density and activity guards: **25.499132**, **2.905683** pore RMS.
- The four fine-pore failures were not used to choose the maximum density threshold.

## Limits

The 20 crops are a calibration/regression set, not an independent generalisation test. Temporal correlation is unregistered and can fall during real motion. A `pores` verdict is metric evidence, not biological proof; before/after rollback still uses the added delta.
