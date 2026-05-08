"""Merge learned error patterns into MEMORY.md without duplicating entries."""

from __future__ import annotations

import re
from pathlib import Path

from src.coordination.cross_bot_sync import atomic_write_text, normalize_memory_key
from src.error_learning.store import ErrorObservation

SECTION_HEADING = "## Error learning"
SECTION_START_RE = re.compile(rf"^{re.escape(SECTION_HEADING)}\s*$", re.MULTILINE)


def format_learning_bullet(obs: ErrorObservation) -> str:
    sources_hint = ""
    if obs.sources:
        top = sorted(obs.sources.items(), key=lambda x: (-x[1], x[0]))[:3]
        names = ", ".join(f"{path}×{n}" for path, n in top)
        sources_hint = f"; sources: {names}"
    return (
        f"- **[{obs.category}]** {obs.normalized_text} "
        f"(seen {obs.count}×, last {obs.last_seen[:10]}{sources_hint})"
    )


def _split_memory_sections(text: str) -> tuple[str, str | None, str]:
    """Return (before_heading, body_inside_error_learning_or_None, tail_after_section)."""

    match = SECTION_START_RE.search(text)
    if not match:
        return text, None, ""

    before = text[: match.start()]
    rest = text[match.end() :]
    next_section = re.search(r"^\n## [^#]", rest, re.MULTILINE)
    if next_section:
        body = rest[: next_section.start()]
        tail = rest[next_section.start() + 1 :]  # keep leading ## for markdown
        return before, body, tail
    return before, rest, ""


def merge_error_learning_section(memory_text: str, bullets: list[str]) -> str:
    """Insert or replace the Error learning section, merging bullets by normalized key."""

    entry_map: dict[str, str] = {}
    for line in bullets:
        raw = line.strip()
        if raw.startswith("- "):
            key = normalize_memory_key(raw)
            if key:
                entry_map[key] = raw

    before, body, tail = _split_memory_sections(memory_text)
    if body is not None:
        for raw_line in body.splitlines():
            line = raw_line.strip()
            if line.startswith("- "):
                key = normalize_memory_key(line)
                if key:
                    entry_map.setdefault(key, line)

    merged_body = "\n".join(sorted(entry_map.values(), key=str.lower))
    block = f"{SECTION_HEADING}\n\n{merged_body}\n"

    if body is None:
        base = memory_text.rstrip()
        if base:
            return base + "\n\n" + block + "\n"
        return block

    tail_stripped = tail.lstrip()
    middle = before.rstrip() + "\n\n" + block
    if tail_stripped:
        return middle + "\n" + tail_stripped
    return middle + "\n"


def sync_observations_to_memory(
    memory_path: Path,
    observations: list[ErrorObservation],
    *,
    max_entries: int = 40,
) -> Path:
    """Append/update the Error learning section with top observations by recurrence."""

    ranked = sorted(observations, key=lambda o: (-o.count, o.last_seen))[:max_entries]
    bullets = [format_learning_bullet(o) for o in ranked]
    existing = memory_path.read_text(encoding="utf-8") if memory_path.exists() else ""
    updated = merge_error_learning_section(existing, bullets)
    atomic_write_text(memory_path, updated)
    return memory_path
