#!/usr/bin/env python3
"""Scan an Obsidian vault for broken internal links and write a JSON report."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
from urllib.parse import unquote, urlparse

WIKI_LINK_RE = re.compile(r"!?\[\[([^\]]+)\]\]")
MD_LINK_RE = re.compile(r"(?<!!)\[([^\]]*)\]\(([^)]+)\)")

# Paths / folders to skip (Obsidian internals and tooling).
SKIP_DIR_NAMES = frozenset({".obsidian", ".git", "__pycache__", ".trash"})


def default_vault_path() -> Path:
    env = os.environ.get("OBSIDIAN_VAULT_PATH")
    if env:
        return Path(env).expanduser()
    if os.name == "nt":
        return Path(r"C:\Users\user\Documents\Obsidian Vault")
    return Path.home() / "Documents" / "Obsidian Vault"


def default_case_sensitive() -> bool:
    """POSIX defaults to case-sensitive matching; Windows to case-insensitive."""
    return os.name != "nt"


def slugify_heading(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower())
    return normalized.strip("-")


def iter_markdown_files(vault: Path) -> Iterator[Path]:
    for path in vault.rglob("*.md"):
        parts = set(path.relative_to(vault).parts)
        if parts & SKIP_DIR_NAMES:
            continue
        yield path


def strip_code_fences(text: str) -> str:
    """Drop fenced code blocks so we do not match links inside them."""
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    in_fence = False
    for line in lines:
        stripped = line.lstrip()
        if in_fence:
            if stripped.startswith("```"):
                in_fence = False
            continue
        if stripped.startswith("```"):
            in_fence = True
            continue
        out.append(line)
    return "".join(out)


def split_wiki_target(raw: str) -> tuple[str, str | None]:
    """Split `path|alias` then path vs anchor (first `#` that starts an anchor section)."""
    target = raw.split("|", 1)[0].strip()
    if not target:
        return "", None
    if target.startswith("#"):
        # Same-file heading link: [[#Heading]]
        return "", target[1:] or None
    if "#" in target:
        path_part, anchor = target.split("#", 1)
        return path_part.strip(), anchor.strip() or None
    return target, None


def normalize_key(path_posix: str, case_sensitive: bool) -> str:
    return path_posix if case_sensitive else path_posix.lower()


@dataclass
class VaultIndex:
    vault: Path
    case_sensitive: bool
    by_rel: dict[str, Path] = field(default_factory=dict)
    by_stem: dict[str, list[Path]] = field(default_factory=lambda: defaultdict(list))

    @classmethod
    def build(cls, vault: Path, case_sensitive: bool) -> VaultIndex:
        self = cls(vault=vault.resolve(), case_sensitive=case_sensitive)
        for md in iter_markdown_files(vault):
            rel = md.relative_to(self.vault).as_posix()
            self.by_rel[normalize_key(rel, case_sensitive)] = md
            stem_key = normalize_key(md.stem, case_sensitive)
            self.by_stem[stem_key].append(md)
        return self

    def resolve_wiki_path(self, source: Path, path_part: str) -> Path | None:
        """Resolve wiki link path (no anchor) to an existing path, or None."""
        path_part = path_part.replace("\\", "/").strip()
        if not path_part:
            return None

        # Absolute-within-vault: leading /
        if path_part.startswith("/"):
            path_part = path_part.lstrip("/")

        use_source_parent = path_part.startswith("./") or path_part.startswith("../")
        suffix = Path(path_part).suffix.lower()

        def try_asset_bases(rel: str) -> Path | None:
            bases = (source.parent, self.vault) if not use_source_parent else (source.parent,)
            for base in bases:
                candidate = (base / rel).resolve()
                if _is_under_vault(candidate, self.vault) and candidate.is_file():
                    return candidate
            return None

        # Attachments / explicit non-markdown file names (e.g. ![[diagram.png]])
        if suffix and suffix not in {".md", ".markdown"}:
            found = try_asset_bases(path_part)
            return found

        if "/" in path_part or path_part.endswith((".md", ".markdown")):
            base: Path = source.parent if use_source_parent else self.vault
            candidate = (base / path_part).resolve()
            if not _is_under_vault(candidate, self.vault):
                return None
            trials = _trial_paths(candidate)
            for trial in trials:
                if trial.is_file():
                    return trial.resolve()
            return None

        stem_key = normalize_key(Path(path_part).stem, self.case_sensitive)
        matches = self.by_stem.get(stem_key, [])
        if not matches:
            return None
        if len(matches) == 1:
            return matches[0]
        same_dir = [m for m in matches if m.parent == source.parent]
        if len(same_dir) == 1:
            return same_dir[0]
        return matches[0]


def _is_under_vault(path: Path, vault: Path) -> bool:
    try:
        path.resolve().relative_to(vault.resolve())
        return True
    except ValueError:
        return False


def _trial_paths(candidate: Path) -> list[Path]:
    out: list[Path] = []
    if candidate.suffix.lower() == ".md":
        out.append(candidate)
    else:
        out.append(candidate.with_suffix(".md"))
        out.append(candidate)
    return out


def collect_heading_slugs(text: str) -> set[str]:
    slugs: set[str] = set()
    for line in text.splitlines():
        m = re.match(r"^#{1,6}\s+(.+?)(?:\s+#\s+[\w-]+)?\s*$", line.rstrip())
        if not m:
            continue
        title = m.group(1).strip()
        # Obsidian / Markdown allows trailing inline `{#custom-id}` in some setups
        if "{" in title:
            title = title.split("{", 1)[0].strip()
        slugs.add(slugify_heading(title))
        slugs.add(slugify_heading(unicodedata.normalize("NFKD", title)))
    return slugs


def anchor_exists_in_note(note_path: Path, anchor: str) -> bool:
    if anchor.startswith("^"):
        marker = "^" + anchor[1:].strip()
        text = note_path.read_text(encoding="utf-8", errors="replace")
        return marker in text
    text = note_path.read_text(encoding="utf-8", errors="replace")
    slugs = collect_heading_slugs(text)
    want = slugify_heading(anchor)
    if want in slugs:
        return True
    return slugify_heading(unicodedata.normalize("NFKD", anchor)) in slugs


def parse_md_link_target(raw_target: str) -> tuple[str, str | None]:
    raw_target = raw_target.strip()
    if raw_target.startswith("#"):
        frag = raw_target[1:]
        return "", unquote(frag) if frag else None
    if "://" in raw_target:
        parsed = urlparse(raw_target)
        fragment = unquote(parsed.fragment) if parsed.fragment else None
        host_or_path = (
            parsed.path.strip()
            or (parsed.netloc.split("/", 1)[-1] if parsed.netloc else "")
        )
        path_only = unquote(host_or_path)
        if path_only == "" and fragment:
            return "", fragment
        return path_only, fragment
    if "#" in raw_target:
        path_part, frag = raw_target.split("#", 1)
        return unquote(path_part.strip()), unquote(frag) if frag else None
    return unquote(raw_target), None


def resolve_markdown_href(source: Path, vault: Path, path_part: str) -> Path | None:
    if not path_part:
        return source
    path_part = path_part.replace("\\", "/").strip()
    candidate = (source.parent / path_part).resolve()
    if not _is_under_vault(candidate, vault):
        return None
    for trial in _trial_paths(candidate):
        if trial.is_file():
            return trial
    return None


def check_vault(
    vault: Path,
    *,
    case_sensitive: bool | None = None,
) -> dict[str, object]:
    vault = vault.expanduser().resolve()
    if not vault.is_dir():
        raise FileNotFoundError(f"Vault is not a directory: {vault}")

    cs = default_case_sensitive() if case_sensitive is None else case_sensitive
    index = VaultIndex.build(vault, cs)
    broken: list[dict[str, object]] = []
    scanned = 0

    for md_path in iter_markdown_files(vault):
        scanned += 1
        rel_source = md_path.relative_to(vault).as_posix()
        text = md_path.read_text(encoding="utf-8", errors="replace")
        scan_text = strip_code_fences(text)

        for m in WIKI_LINK_RE.finditer(scan_text):
            raw_inner = m.group(1)
            path_part, anchor = split_wiki_target(raw_inner)
            if path_part == "" and anchor is None:
                continue
            target_path: Path | None
            if path_part == "":
                target_path = md_path
            else:
                target_path = index.resolve_wiki_path(md_path, path_part)
            link_display = m.group(0)
            if target_path is None or not target_path.is_file():
                broken.append(
                    {
                        "source_file": rel_source,
                        "link_type": "wiki",
                        "link_text": link_display,
                        "target_raw": raw_inner,
                        "reason": "file_not_found",
                        "resolved_path": None,
                    }
                )
                continue
            if anchor is not None and target_path.suffix.lower() in {".md", ".markdown"}:
                if not anchor_exists_in_note(target_path, anchor):
                    broken.append(
                        {
                            "source_file": rel_source,
                            "link_type": "wiki",
                            "link_text": link_display,
                            "target_raw": raw_inner,
                            "reason": "anchor_not_found",
                            "resolved_path": target_path.relative_to(vault).as_posix(),
                        }
                    )

        for m in MD_LINK_RE.finditer(scan_text):
            href = m.group(2).strip()
            if not href or href.startswith(("#", "mailto:", "obsidian://")):
                continue
            if re.match(r"^[a-z][a-z0-9+.-]*:", href, re.I):
                # http:, https:, vscode:, etc.
                if href.lower().startswith(("http://", "https://")):
                    continue
                continue
            path_part, anchor = parse_md_link_target(href.split("?", 1)[0])
            if not path_part:
                if anchor:
                    if not anchor_exists_in_note(md_path, anchor):
                        broken.append(
                            {
                                "source_file": rel_source,
                                "link_type": "markdown",
                                "link_text": m.group(0),
                                "target_raw": href,
                                "reason": "anchor_not_found",
                                "resolved_path": rel_source,
                            }
                        )
                continue

            suffix = Path(path_part).suffix.lower()
            if suffix and suffix not in {".md", ".markdown"}:
                resolved = (md_path.parent / path_part).resolve()
                if _is_under_vault(resolved, vault) and resolved.is_file():
                    continue
                broken.append(
                    {
                        "source_file": rel_source,
                        "link_type": "markdown",
                        "link_text": m.group(0),
                        "target_raw": href,
                        "reason": "file_not_found",
                        "resolved_path": None,
                    }
                )
                continue

            resolved = resolve_markdown_href(md_path, vault, path_part)
            if resolved is None or not resolved.is_file():
                broken.append(
                    {
                        "source_file": rel_source,
                        "link_type": "markdown",
                        "link_text": m.group(0),
                        "target_raw": href,
                        "reason": "file_not_found",
                        "resolved_path": None,
                    }
                )
                continue
            if anchor and not anchor_exists_in_note(resolved, anchor):
                broken.append(
                    {
                        "source_file": rel_source,
                        "link_type": "markdown",
                        "link_text": m.group(0),
                        "target_raw": href,
                        "reason": "anchor_not_found",
                        "resolved_path": resolved.relative_to(vault).as_posix(),
                    }
                )

    return {
        "vault": str(vault),
        "case_sensitive": cs,
        "scanned_files": scanned,
        "broken_count": len(broken),
        "broken_links": broken,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--vault",
        type=Path,
        default=default_vault_path(),
        help="Obsidian vault root (default: OBSIDIAN_VAULT_PATH or standard location).",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("obsidian_broken_links.json"),
        help="JSON report output path.",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--case-sensitive",
        action="store_true",
        help="Match note paths and names with case sensitivity (overrides platform default).",
    )
    group.add_argument(
        "--case-insensitive",
        action="store_true",
        help="Case-insensitive note matching (overrides platform default).",
    )
    args = parser.parse_args(argv)

    case_sensitive: bool | None
    if args.case_insensitive:
        case_sensitive = False
    elif args.case_sensitive:
        case_sensitive = True
    else:
        case_sensitive = None

    try:
        report = check_vault(args.vault, case_sensitive=case_sensitive)
    except FileNotFoundError as e:
        print(e, file=sys.stderr)
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(
        f"Scanned {report['scanned_files']} files; "
        f"{report['broken_count']} broken link(s). Report: {args.output.resolve()}",
        file=sys.stderr,
    )
    return 0 if report["broken_count"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
