#!/usr/bin/env python3
"""Проверка фактов в PR или в diff-файле.

Использование::

    python scripts/factcheck_pr.py --diff path/to.diff
    python scripts/factcheck_pr.py --pr 279 --fetch
    python scripts/factcheck_pr.py --diff pr.diff --json

Коды выхода: 0 — опровергнутых фактов нет, 1 — есть опровергнутые
(непроверенные не считаются ошибкой), 2 — не удалось прочитать вход.

По умолчанию сеть не используется. ``--pr`` без ``--fetch`` тоже работает
без сети: номер PR используется только как подпись в отчёте.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional, Sequence

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.factcheck.claims import extract_from_diff  # noqa: E402
from src.factcheck.paths import default_paths  # noqa: E402
from src.factcheck.report import render_text, to_json  # noqa: E402
from src.factcheck.verify import Verdict, summarize, verify_claims  # noqa: E402

GITHUB_API = "https://api.github.com"


def _token() -> str:
    """Токен GitHub из окружения. Никогда из кода."""
    for name in ("GITHUB_TOKEN", "GH_TOKEN"):
        value = os.environ.get(name)
        if value:
            return value
    return ""


def fetch_pr_diff(pr_number: int, repo: str) -> str:
    """Скачать diff PR через GitHub API."""
    token = _token()
    if not token:
        raise RuntimeError(
            "Нет токена GitHub: задайте GITHUB_TOKEN или GH_TOKEN в окружении"
        )
    url = f"{GITHUB_API}/repos/{repo}/pulls/{pr_number}"
    headers = {
        "Accept": "application/vnd.github.v3.diff",
        "User-Agent": "openclaw-factcheck",
        "Authorization": f"Bearer {token}",
    }
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read().decode("utf-8", "replace")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Проверка фактов (пути, имена файлов, числа, секреты) в PR или diff."
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--diff", help="путь к diff-файлу")
    source.add_argument("--pr", type=int, help="номер PR (с --fetch тянет diff из GitHub)")
    parser.add_argument(
        "--repo", default="sakura565-afk/openclaw-cursor", help="репозиторий в формате owner/name"
    )
    parser.add_argument(
        "--fetch", action="store_true", help="скачать diff PR из GitHub (нужен токен)"
    )
    parser.add_argument("--json", action="store_true", help="вывести отчёт в JSON")
    parser.add_argument(
        "--no-live", action="store_true", help="не проверять по диску (только синтаксис и секреты)"
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    if args.diff:
        diff_path = Path(args.diff)
        if not diff_path.exists():
            print(f"Файл не найден: {diff_path}", file=sys.stderr)
            return 2
        diff = diff_path.read_text(encoding="utf-8", errors="replace")
    else:
        if not args.fetch:
            print(
                "Нужен --diff или --pr вместе с --fetch: без сети diff не взять.",
                file=sys.stderr,
            )
            return 2
        try:
            diff = fetch_pr_diff(args.pr, args.repo)
        except (RuntimeError, urllib.error.URLError, OSError) as exc:
            print(f"Не удалось получить diff: {exc}", file=sys.stderr)
            return 2

    claims = extract_from_diff(diff)
    results = verify_claims(claims, default_paths(), live=not args.no_live)

    if args.json:
        print(to_json(results))
    else:
        print(render_text(results))

    counts = summarize(results)
    if counts[Verdict.NOT_FOUND.value]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
