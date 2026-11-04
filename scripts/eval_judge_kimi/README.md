# Blind A/B skin judge

Run from the repository root:

```bash
python -m scripts.eval_judge_kimi \
  --reference reference.mp4 --candidates candidate_a.mp4 candidate_b.mp4 \
  --frames 6 --crops 2 --out judge_output --seed 7 --dump-prompt
```

The reference is also a blind variant. `blind_sheet.png` contains only shuffled
A/B/C-style labels; source names are kept separately in `report.json` under
`blind_key`. Every sampled frame contributes the requested number of skin crops.
Crops are resized to 128x128 before the 3-12 px FFT band-energy measurement.

Temporal RGB/HF delta and face drift are calculated by the existing
`scripts/compare_video_texture.py` implementation. A candidate with an FPS
mismatch or a motion-delta mismatch greater than 35% (with a small static-clip
tolerance) gets `flicker.status = "not comparable"` and a reason; no flicker
numbers are emitted for that candidate. The motion guard is a heuristic, not a
proof that two independently rendered clips have identical motion.

## External scores

Pass `--scores scores.json`. Keys are blind labels and values are lists so
multiple judges can be aggregated:

```json
{
  "A": [
    {"pore_readability": 8, "naturalness": 7, "wax_plastic": 2, "overlaid_noise": 2},
    {"pore_readability": 6, "naturalness": 8, "wax_plastic": 4, "overlaid_noise": 2}
  ],
  "B": [
    {"pore_readability": 3, "naturalness": 4, "wax_plastic": 8, "overlaid_noise": 7}
  ]
}
```

For every criterion the report contains median, Q1, Q3, and IQR. The verdict is
`pass` when median pore readability and naturalness are at least 6 and median
wax/plastic and overlaid noise are at most 4; otherwise it is `fail`.
