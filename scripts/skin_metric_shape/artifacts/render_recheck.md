# Earlier render verdicts rechecked with shape

The same decoded pixels and canonical ROI scale are used for old and new columns.

| render | old | new | pore RMS | survival | components/10k | width px | temporal corr |
|---|---|---|---:|---:|---:|---:|---:|
| CHEF_ONE_REF2VA | digital_sand | pores | 8.0965 | 0.5697 | 67.369 | 1.606 | 0.7460 |
| CHEF_SMALL | pores | pores | 6.1108 | 0.6458 | 41.768 | 2.783 | 0.4642 |
| CHEF_REF2VA | digital_sand | pores | 17.8086 | 0.4453 | 74.763 | 2.641 | 0.7411 |
| CHEF_ANAT | pores | pores | 6.2548 | 0.6426 | 42.498 | 2.811 | 0.8275 |
| CHEF_FIX | pores | pores | 6.1583 | 0.6989 | 43.334 | 2.971 | 0.6955 |
| CHEF_FIX2 | missing | — | — | — | — | — | — |
| CHEF_FIX3 | missing | — | — | — | — | — | — |
| PORE_TIGHT_012 | digital_sand | digital_sand | 6.4026 | 0.4822 | 1304.530 | 1.128 | 0.9994 |
| PORE_FLOW_0.05 | pores | pores | 5.1305 | 0.7267 | 1225.799 | 1.128 | 0.9972 |
| BOHO_MUSIC3 | digital_sand | pores | 17.2797 | 0.4504 | 72.242 | 3.545 | 0.8203 |
| BOHO_INSTR2 | digital_sand | pores | 17.2797 | 0.4504 | 72.242 | 3.545 | 0.8203 |
| BOHO_SONG_ALIGNED | digital_sand | pores | 10.3443 | 0.5021 | 66.013 | 2.696 | 0.0093 |
| BOHO_SONG_GUITAR | digital_sand | pores | 10.3443 | 0.5021 | 66.013 | 2.696 | 0.0093 |
| BOHO_INSTR | digital_sand | pores | 16.6169 | 0.4750 | 68.549 | 3.472 | 0.7367 |

Old sand/plastic reinterpreted as pores: **CHEF_ONE_REF2VA, CHEF_REF2VA, BOHO_MUSIC3, BOHO_INSTR2, BOHO_SONG_ALIGNED, BOHO_SONG_GUITAR, BOHO_INSTR**.
Missing inputs: **CHEF_FIX2, CHEF_FIX3**.

Changed classes are metric reinterpretations, not new human labels. Temporal correlations are unregistered and are not used by classify().
