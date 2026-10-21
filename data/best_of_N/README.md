# data/best_of_N

Reports written by `scripts/best_of_n.py` (one JSON + one Markdown file per run,
named `<task-slug>_<UTC timestamp>.json` / `.md`).

`best-of-n-runner_20261021T064512Z.*` is a **synthetic example** produced
offline with mocked agent launches, judge replies and diff statistics. It is
committed so the report schema and the Markdown layout can be reviewed without
spending cloud-agent runs; it does not describe real agents or real pull
requests.

JSON schema:

```json
{
  "task_slug": "...", "n": 3, "judge_model": "...", "judge_mode": "llm|fallback",
  "created_at": "...", "winner": {"index": 2, "model": "...", "pr_url": "..."},
  "variants": [
    {"index": 1, "model": "...", "branch": "...", "agent_id": "...",
     "pr_url": "...", "score": 0.82, "verdict": "...", "latency_s": 123.4,
     "state": "completed", "error": null, "judge_mode": "llm",
     "diff_stats": {"files_changed": 3, "insertions": 548, "deletions": 12,
                    "test_files": 1, "new_src_files": 0,
                    "available": true, "error": null}}
  ]
}
```

`judge_mode` is `llm` when at least one variant was graded by the LLM judge and
`fallback` when every variant fell back to the deterministic diff heuristics
documented next to the `FALLBACK_WEIGHT_*` constants in `scripts/best_of_n.py`.
