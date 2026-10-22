#!/usr/bin/env python3
"""Проверка фактов в промпт-файлах задач.

Каждая задача Cursor живёт как `tasks/cursor_task_NN_<name>.md` и содержит
пути, имена файлов и числа. Если в промпте ошибка, агент честно её
воспроизведёт в PR — поэтому промпты стоит проверять **до** запуска.

Использование::

    python scripts/factcheck_tasks.py --dir tasks
    python scripts/factcheck_tasks.py --dir tasks --json
    python scripts/factcheck_tasks.py --file tasks/cursor_task_19_factcheck.md

Коды выхода: 0 — опровергнутых фактов нет, 1 — есть, 2 — не удалось прочитать.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional, Sequence

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.factcheck.claims import extract_claims  # noqa: E402
from src.factcheck.paths import default_paths  # noqa: E402
from src.factcheck.report import render_text, to_json  # noqa: E402
from src.factcheck.verify import CheckResult, Verdict, summarize, verify_claims  # noqa: E402

DEFAULT_GLOB = "cursor_task_*.md"


def collect_files(target: Path, pattern: str) -> list[Path]:
    """Собрать файлы задач: либо один файл, либо все по шаблону."""
    if target.is_file():
        return [target]
    if not target.is_dir():
        return []
    return sorted(target.glob(pattern))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Проверка фактов в промпт-файлах задач Cursor."
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--dir", help="каталог с файлами задач")
    source.add_argument("--file", help="один файл задачи")
    parser.add_argument("--pattern", default=DEFAULT_GLOB, help="шаблон имён файлов")
    parser.add_argument("--json", action="store_true", help="вывести отчёт в JSON")
    parser.add_argument(
        "--no-live", action="store_true", help="не проверять по диску"
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    target = Path(args.file) if args.file else Path(args.dir)
    files = collect_files(target, args.pattern)
    if not files:
        print(f"Файлы задач не найдены: {target}", file=sys.stderr)
        return 2

    paths = default_paths()
    all_results: list[CheckResult] = []
    per_file: dict[str, list[CheckResult]] = {}

    for file in files:
        text = file.read_text(encoding="utf-8", errors="replace")
        claims = extract_claims(text)
        results = verify_claims(claims, paths, live=not args.no_live)
        per_file[str(file)] = results
        all_results.extend(results)

    if args.json:
        import json

        print(json.dumps(
            {
                "summary": summarize(all_results),
                "files": {
                    name: {
                        "summary": summarize(results),
                        "results": [r.to_dict() for r in results],
                    }
                    for name, results in per_file.items()
                },
            },
            ensure_ascii=False, indent=2,
        ))
    else:
        print(f"Проверено файлов: {len(files)}")
        for name, results in per_file.items():
            counts = summarize(results)
            print(f"  {Path(name).name}: подтверждено {counts[Verdict.OK.value]}, "
                  f"опровергнуто {counts[Verdict.NOT_FOUND.value]}, "
                  f"не проверено {counts[Verdict.UNVERIFIABLE.value]}")
            for result in results:
                if result.verdict is Verdict.NOT_FOUND:
                    print(f"     [НЕ НАЙДЕНО] {result.claim.value}")
                    if result.suggestion:
                        print(f"                   -> {result.suggestion}")

    counts = summarize(all_results)
    if counts[Verdict.NOT_FOUND.value]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
