#!/usr/bin/env python3
"""Backward-compatible entry for ``python -m scripts.auto_reflection``.

Implementation lives in ``scripts.self_improvement.auto_reflection`` (metrics,
``.learnings/reflections.md``, cron notes). Prefer:

    python3 -m scripts.self_improvement.auto_reflection
"""

from __future__ import annotations

import sys
import urllib.request

from scripts.self_improvement.auto_reflection import (  # noqa: F401
    Insight,
    ReflectionRun,
    build_parser,
    collect_globs,
    dedupe_insights,
    extract_insights_from_json,
    extract_insights_from_text,
    insight_fingerprint,
    iter_session_files,
    load_state,
    main,
    maybe_post_results,
    normalize_insight_text,
    post_telegram_summary,
    post_webhook,
    read_and_extract,
    run_reflection,
    save_state,
    utc_now,
    write_insight_artifacts,
    write_latest_pointers,
)

if __name__ == "__main__":
    raise SystemExit(main())
