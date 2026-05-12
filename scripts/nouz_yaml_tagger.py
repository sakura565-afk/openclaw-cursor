#!/usr/bin/env python3
"""
Scan an Obsidian vault and add NOUZ YAML frontmatter fields when missing.

Uses python-frontmatter; preserves existing keys. Domain is inferred from path/tags.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import frontmatter

from scripts.nouz_common import default_vault_path, detect_domain, iter_markdown_files

DEFAULT_LEVEL = "quant"
DEFAULT_ROLE = "description"
DEFAULT_STATUS = "draft"


def _normalize_tags(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list):
        return [str(x) for x in raw if x is not None]
    return []


def _coerce_frontmatter_tags(meta: dict[str, Any]) -> list[str]:
    return _normalize_tags(meta.get("tags"))


def merge_nouz_defaults(meta: dict[str, Any], rel_path: Path) -> dict[str, Any]:
    """Return new metadata dict with only missing NOUZ keys filled."""
    updated = dict(meta)
    tags = _coerce_frontmatter_tags(updated)

    if "level" not in updated or updated["level"] is None or str(updated["level"]).strip() == "":
        updated["level"] = DEFAULT_LEVEL

    if "role" not in updated or updated["role"] is None or str(updated["role"]).strip() == "":
        updated["role"] = DEFAULT_ROLE

    if "status" not in updated or updated["status"] is None or str(updated["status"]).strip() == "":
        updated["status"] = DEFAULT_STATUS

    if "domain" not in updated or updated["domain"] is None or updated["domain"] == "":
        updated["domain"] = detect_domain(rel_path, tags)

    if "core_id" not in updated:
        updated["core_id"] = None

    return updated


def tag_file(path: Path, vault_root: Path, dry_run: bool) -> bool:
    """Load markdown, merge defaults, write if changed. Returns True if file was modified."""
    post = frontmatter.load(path)
    rel = path.relative_to(vault_root)
    new_meta = merge_nouz_defaults(post.metadata, rel)
    if new_meta == post.metadata:
        return False
    post.metadata = new_meta
    if dry_run:
        return True
    path.write_text(frontmatter.dumps(post), encoding="utf-8")
    return True


def tag_vault(vault_root: Path, dry_run: bool = False) -> dict[str, int]:
    """Scan vault for markdown and apply NOUZ defaults."""
    stats = {"scanned": 0, "updated": 0, "skipped_missing": 0}
    if not vault_root.is_dir():
        stats["skipped_missing"] = 1
        return stats

    for md in iter_markdown_files(vault_root):
        stats["scanned"] += 1
        if tag_file(md, vault_root, dry_run):
            stats["updated"] += 1
    return stats


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Add NOUZ YAML fields to Obsidian markdown.")
    parser.add_argument(
        "--vault",
        type=Path,
        default=None,
        help="Obsidian vault root (default: OBSIDIAN_VAULT_PATH or E:\\Obsidianstore).",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print actions without writing files.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    vault = args.vault if args.vault is not None else default_vault_path()
    stats = tag_vault(vault, dry_run=args.dry_run)
    print(
        f"vault={vault} scanned={stats['scanned']} updated={stats['updated']} "
        f"missing_root={stats['skipped_missing']} dry_run={args.dry_run}"
    )
    return 0 if stats["skipped_missing"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
