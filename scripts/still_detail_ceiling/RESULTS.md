# Measured four-style result

This is the actual offline run recorded in `results/report.json`. All deltas
are versus Lanczos on the same target-scale ROI. `survE` is after-blur pore-band
energy; `pSurv` and `HFsv` deltas are percentage points. MAE is RGB error on the
0--255 scale after return to source geometry.

The requested width 1072 was normalized to 1088 for `/32` alignment. All four
height roundings stayed below the declared 1% AR tolerance; no scaling
violation was recorded.

| Style | Candidate | pore energy | survE | pSurv | HFsv | Laplacian | MAE | Flags |
|---|---|---:|---:|---:|---:|---:|---:|---|
| qe_graded_w0.9 | bicubic | +0.9% | +0.5% | -0.2 | +0.5 | -1.5% | 0.197 | |
| qe_graded_w0.9 | lanczos | 0.0% | 0.0% | 0.0 | 0.0 | 0.0% | 0.157 | |
| qe_graded_w0.9 | seedvr2 cached | +12.5% | +4.5% | -4.4 | -2.5 | +10.4% | 10.561 | NOISE_LIKE, OVERSHARP |
| qe_graded_w0.9 | pore_minimax | +3.3% | +1.6% | -1.0 | -0.7 | +5.1% | 0.593 | NOISE_LIKE |
| qe_graded_w0.9 | pore_kimi | +0.0% | +0.0% | +0.0 | -0.1 | +0.2% | 0.219 | |
| final_stand_graded_crop | bicubic | +1.4% | +1.1% | -0.2 | +1.6 | -4.8% | 0.369 | |
| final_stand_graded_crop | lanczos | 0.0% | 0.0% | 0.0 | 0.0 | 0.0% | 0.365 | |
| final_stand_graded_crop | pore_minimax | +7.9% | +2.3% | -3.6 | -1.2 | +16.6% | 0.831 | NOISE_LIKE |
| final_stand_graded_crop | pore_kimi | -0.2% | -0.2% | +0.0 | -0.1 | +0.2% | 0.382 | |
| qe_bathe_undress | bicubic | +1.8% | +2.3% | +0.3 | +1.4 | -3.1% | 0.502 | |
| qe_bathe_undress | lanczos | 0.0% | 0.0% | 0.0 | 0.0 | 0.0% | 0.520 | |
| qe_bathe_undress | pore_minimax | +3.7% | +1.2% | -1.4 | -3.3 | +15.9% | 0.987 | NOISE_LIKE |
| qe_bathe_undress | pore_kimi | +0.4% | +0.2% | -0.1 | -0.1 | +0.4% | 0.547 | |
| KATYA_UNDDRESSED | bicubic | +0.4% | +0.4% | +0.0 | -3.2 | +7.5% | 0.092 | small ROI |
| KATYA_UNDDRESSED | lanczos | 0.0% | 0.0% | 0.0 | 0.0 | 0.0% | 0.142 | small ROI |
| KATYA_UNDDRESSED | pore_minimax | +0.0% | +0.0% | +0.0 | +0.0 | +0.0% | 0.142 | small ROI |
| KATYA_UNDDRESSED | pore_kimi | -0.1% | -0.1% | -0.0 | -0.6 | +1.2% | 0.150 | small ROI |

Real-ESRGAN and CodeFormer were unavailable: their Python packages are not
installed. No network installation was attempted. SeedVR2 had a cached output
only for qe; it is unavailable for the other three styles in this run.

## Why positive surviving energy did not win

Task 1's added-component gate rejected MiniMax on the three non-trivial ROIs:
added-band survival was 0.258, 0.270 and 0.261, below the 0.450 threshold. The
SeedVR2 qe delta scored 0.157 and was likewise rejected. Its raw pore energy
increase therefore must not be presented as restored pores.

The qe review sheet was also inspected independently. It confirmed harsh
contours/halo-like outlining and a colour/contrast shift in SeedVR2, artificial
pinpoint speckling in MiniMax, and a much quieter but nearly unchanged Kimi
result. This visual observation applies only to the qe sheet, not to unreviewed
styles.

## Human review crops

- qe_graded_w0.9: source `[432,320,688,576]`, compare Lanczos, cached SeedVR2,
  MiniMax and Kimi on the native-target sheet.
- final_stand_graded_crop: source `[384,384,640,640]`.
- qe_bathe_undress: source `[256,256,512,512]`.
- KATYA_UNDDRESSED: source `[272,152,320,200]`; artifact review only, because a
  clean pore-scale ROI was not available.

No candidate in this run proved a true still-detail ceiling lift. Lanczos is
the conservative baseline; MiniMax adds measurable but noise-like synthetic
texture, SeedVR2 adds oversharpened/noise-like change on the cached qe sample,
and Kimi has negligible measured effect. A human still has to reject or accept
the exact saved crops.
