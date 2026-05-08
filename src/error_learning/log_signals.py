"""Shared log-line normalization and error signal detection for OpenClaw tooling."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterator

ERROR_SIGNAL_RE = re.compile(
    r"\b(error|exception|traceback|failed|failure|timeout|timed out|syntaxerror|"
    r"valueerror|keyerror|runtimeerror|typeerror|importerror|modulenotfounderror|"
    r"assertionerror|permissionerror|filenotfounderror|oserror|connectionerror)\b",
    re.IGNORECASE,
)


def normalize_error_line(line: str) -> str:
    """Collapse volatile tokens so similar failures bucket together."""

    cleaned = line.strip()
    cleaned = re.sub(r"^\[[^\]]+\]\s*", "", cleaned)
    cleaned = re.sub(r"^\d{4}-\d{2}-\d{2}[T ][^ ]+\s*", "", cleaned)
    cleaned = re.sub(r"\b0x[0-9a-f]+\b", "#addr", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b\d+\b", "#", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned[:220] or "Unspecified error"


def iter_log_file_lines(log_roots: list[Path]) -> Iterator[tuple[Path, str]]:
    """Yield (path, line) for text and JSON log files under the given roots."""

    for root in log_roots:
        if not root.exists():
            continue
        for log_file in sorted(root.rglob("*")):
            if not log_file.is_file():
                continue
            text = _read_text_safe(log_file)
            if not text:
                continue
            if log_file.suffix == ".json":
                try:
                    payload = json.loads(text)
                except json.JSONDecodeError:
                    payload = None
                if isinstance(payload, list):
                    for item in payload:
                        yield log_file, json.dumps(item, sort_keys=True)
                    continue
                if isinstance(payload, dict):
                    yield log_file, json.dumps(payload, sort_keys=True)
                    continue
            for line in text.splitlines():
                yield log_file, line


def _read_text_safe(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""
