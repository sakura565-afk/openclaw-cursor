# Best-of-3: best-of-n-runner

- created_at: `2026-10-21T07:02:55.762105+00:00`
- judge_model: `MiniMax-M3.1-Flash`
- judge_mode: `llm`
- winner: variant 2 (`gpt-5.6-sol-high`) score 0.91

| # | model | score | state | latency s | branch | PR |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | `claude-opus-5-thinking-high` | 0.78 | completed | 812.4 | `cursor/best-of-n-runner-variant-1` | https://github.com/sakura565-afk/openclaw-cursor/pull/280 |
| 2 🏆 | `gpt-5.6-sol-high` | 0.91 | completed | 643.1 | `cursor/best-of-n-runner-variant-2` | https://github.com/sakura565-afk/openclaw-cursor/pull/281 |
| 3 | `gemini-3.7-flash-high` | 0.16 | completed | 391.7 | `cursor/best-of-n-runner-variant-3` | https://github.com/sakura565-afk/openclaw-cursor/pull/282 |

## Variant 1 — `claude-opus-5-thinking-high`

- verdict: All criteria met; judge/fallback split is clean but the report writer duplicates timestamp logic.
- diff: `{"available": true, "deletions": 18, "error": null, "files_changed": 4, "insertions": 612, "new_src_files": 1, "test_files": 1}`

## Variant 2 — `gpt-5.6-sol-high`

- verdict: Strongest variant: queue_manager batching reused as-is, judge parser handles all three JSON shapes, report schema exact.
- diff: `{"available": true, "deletions": 12, "error": null, "files_changed": 3, "insertions": 548, "new_src_files": 0, "test_files": 1}`

## Variant 3 — `gemini-3.7-flash-high`

- verdict: fallback diff score 0.16: 2 file(s) changed, +214/-4 lines, 0 test file(s), 0 new src file(s)
- error: judge response contained no JSON object
- diff: `{"available": true, "deletions": 4, "error": null, "files_changed": 2, "insertions": 214, "new_src_files": 0, "test_files": 0}`
