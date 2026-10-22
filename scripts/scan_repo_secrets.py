"""Просканировать репозиторий на встроенные секреты.

Использование: python scripts/scan_repo_secrets.py [путь-к-репозиторию]
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.factcheck.claims import iter_secret_matches  # noqa: E402


def main() -> int:
    repo = Path(sys.argv[1]) if len(sys.argv) > 1 else _REPO_ROOT
    proc = subprocess.run(
        ["git", "-C", str(repo), "ls-files"],
        capture_output=True, text=True, timeout=120,
    )
    files = [line for line in proc.stdout.splitlines() if line.strip()]
    found = []
    for rel in files:
        path = repo / rel
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for name, value in iter_secret_matches(text):
            found.append((name, value, rel))

    print(f"Проверено файлов: {len(files)}")
    print(f"Найдено секретов: {len(found)}")
    for name, value, rel in found:
        print(f"  [{name}] {rel}: {value[:12]}...")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
