"""Shared paths, markdown walking, and domain detection for NOUZ tooling."""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Iterator

IGNORED_DIR_NAMES = frozenset({".obsidian", ".git", "__pycache__", "node_modules"})

# Ordered: first match wins (path segments + tag text, case-insensitive).
DOMAIN_ALIASES: tuple[tuple[str, str], ...] = (
    ("ai", "ai"),
    ("ml", "ai"),
    ("llm", "ai"),
    ("infra", "infra"),
    ("infrastructure", "infra"),
    ("devops", "infra"),
    ("photo", "photo"),
    ("photography", "photo"),
    ("business", "business"),
    ("biz", "business"),
)


def repo_root() -> Path:
    """Repository root (parent of ``scripts``)."""
    return Path(__file__).resolve().parents[1]


def default_data_dir() -> Path:
    """SQLite + embedding files live under ``openclaw-cursor/data``."""
    return repo_root() / "openclaw-cursor" / "data"


def default_vault_path() -> Path:
    """OBSIDIAN_VAULT_PATH or Windows-style default from the task brief."""
    raw = os.environ.get("OBSIDIAN_VAULT_PATH", r"E:\Obsidianstore")
    return Path(raw)


def iter_markdown_files(vault: Path) -> Iterator[Path]:
    """Yield ``*.md`` files under vault, skipping common hidden/tool dirs."""
    if not vault.is_dir():
        return
    for path in vault.rglob("*.md"):
        parts_lower = {p.lower() for p in path.parts}
        if parts_lower & IGNORED_DIR_NAMES:
            continue
        yield path


def note_uid(rel_path: str) -> str:
    """Stable short id from vault-relative posix path."""
    normalized = rel_path.replace("\\", "/").strip("/")
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return digest[:16]


_FIRSTHeading = re.compile(r"^#\s+(.+)$", re.MULTILINE)


def extract_title(markdown_body: str, fallback: str) -> str:
    """First ATX heading line or fallback stem."""
    match = _FIRSTHeading.search(markdown_body.lstrip())
    if match:
        return match.group(1).strip()
    return fallback


def detect_domain(path: Path, tags: list[str]) -> str:
    """Infer domain from path segments and tags (ai, infra, photo, business)."""
    haystack_parts = [p.lower() for p in path.parts]
    haystack_tags = [t.strip("#").lower() for t in tags]
    combined = " ".join(haystack_parts + haystack_tags)
    for needle, domain in DOMAIN_ALIASES:
        if needle in combined:
            return domain
    for part in haystack_parts:
        for needle, domain in DOMAIN_ALIASES:
            if needle in part:
                return domain
    return "general"
