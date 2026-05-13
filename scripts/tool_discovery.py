#!/usr/bin/env python3
"""Scan OpenClaw workspace and repo for scripts, skills, and tools; maintain ``data/tool_registry.json``."""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.coordination.iskra_kara_shared_memory import resolve_openclaw_workspace  # noqa: E402
DEFAULT_REGISTRY_PATH = REPO_ROOT / "data" / "tool_registry.json"

# (substrings to match in name + description + path), tag
TAG_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("image", "photo", "thumbnail", "jpeg", "png", "gif", "webp", "exif", "comfy", "faceswap"), "image"),
    (("audio", "sound", "wav", "mp3", "voice", "speech"), "audio"),
    (("video", "ffmpeg", "mp4", "thumbnail"), "video"),
    (("telegram", "slack", "notify", "sender", "message"), "messaging"),
    (("ollama", "model", "embedding", "llm", "inference"), "model"),
    (("sqlite", "sql", "parse", "ast", "yaml", "json", "code", "script"), "code"),
    (("queue", "cron", "batch", "pipeline", "nightly", "auto_", "automation", "sync", "bridge"), "automation"),
    (("memory", "dream", "obsidian", "note", "wiki"), "memory"),
    (("monitor", "health", "metric", "benchmark"), "monitoring"),
    (("dashboard", "html", "template"), "ui"),
)

SKIP_DIR_NAMES = frozenset(
    {
        ".git",
        "__pycache__",
        ".venv",
        "venv",
        "node_modules",
        ".mypy_cache",
        ".pytest_cache",
        ".tox",
        "dist",
        "build",
    }
)


@dataclass(frozen=True)
class RegistryEntry:
    name: str
    description: str
    path: str
    tags: tuple[str, ...]
    last_modified: str
    kind: str

    def to_json_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["tags"] = list(self.tags)
        return d


def _utc_iso_from_mtime(mtime: float) -> str:
    return datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()


def _should_skip_dir(path: Path) -> bool:
    return any(part in SKIP_DIR_NAMES for part in path.parts)


def _infer_tags(name: str, description: str, rel_hint: str, extra: Sequence[str] = ()) -> list[str]:
    corpus = f"{name} {description} {rel_hint}".lower()
    for fragment in extra:
        corpus += f" {str(fragment).lower()}"
    tags: list[str] = []
    for keywords, tag in TAG_RULES:
        if any(k in corpus for k in keywords) and tag not in tags:
            tags.append(tag)
    if not tags:
        tags.append("general")
    return sorted(set(tags))


def _extract_py_description(path: Path) -> str:
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source, filename=str(path))
    except (OSError, SyntaxError, UnicodeError):
        return "Could not parse Python module."
    doc = ast.get_docstring(tree)
    if doc:
        return doc.strip().splitlines()[0].strip()
    return f"Python script `{path.name}`."


def _extract_shell_description(path: Path) -> str:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[:40]
    except OSError:
        return f"Shell script `{path.name}`."
    comments: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#!"):
            continue
        if stripped.startswith("#"):
            text = stripped.lstrip("#").strip()
            if text:
                comments.append(text)
        elif stripped:
            break
    if comments:
        return comments[0][:500]
    return f"Shell script `{path.name}`."


def _parse_frontmatter_block(raw: str) -> tuple[dict[str, Any], str]:
    text = raw.lstrip("\ufeff")
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines()
    if len(lines) < 2 or lines[0].strip() != "---":
        return {}, text
    fm_lines: list[str] = []
    i = 1
    while i < len(lines):
        if lines[i].strip() == "---":
            break
        fm_lines.append(lines[i])
        i += 1
    if i >= len(lines):
        return {}, text
    body = "\n".join(lines[i + 1 :]).lstrip("\n")
    meta: dict[str, Any] = {}
    key_re = re.compile(r"^([A-Za-z0-9_+-]+):\s*(.*)$")
    j = 0
    blines = fm_lines
    while j < len(blines):
        raw_line = blines[j]
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            j += 1
            continue
        match = key_re.match(raw_line.rstrip())
        if not match:
            j += 1
            continue
        key, rest = match.group(1), match.group(2).strip()
        if rest in {"", "|", ">"}:
            items: list[str] = []
            k = j + 1
            while k < len(blines):
                candidate = blines[k]
                st = candidate.strip()
                if st.startswith("- "):
                    items.append(st[2:].strip().strip('"').strip("'"))
                    k += 1
                    continue
                if not st:
                    k += 1
                    continue
                if candidate[0] in " \t" and not st.startswith("- "):
                    k += 1
                    continue
                break
            if items:
                meta[key] = items
                j = k
                continue
        if rest.startswith("[") and rest.endswith("]"):
            inner = rest[1:-1]
            meta[key] = [p.strip().strip("'\"") for p in inner.split(",") if p.strip()]
        else:
            meta[key] = rest.strip('"').strip("'")
        j += 1
    return meta, body


def _skill_description(meta: dict[str, Any], body: str) -> str:
    desc = str(meta.get("description", "")).strip()
    if desc:
        return desc.replace("\n", " ")[:2000]
    paragraphs = [p.strip() for p in re.split(r"\n{2,}", body) if p.strip()]
    if paragraphs:
        return paragraphs[0].replace("\n", " ")[:2000]
    return "Skill definition (SKILL.md)."


def _normalize_tag_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return []
        if stripped.startswith("["):
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, list):
                return [str(x).strip() for x in parsed if str(x).strip()]
        return [t.strip() for t in re.split(r"[\s,]+", stripped) if t.strip()]
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    return [str(value).strip()]


def _iter_skill_markdown_roots(roots: Sequence[Path]) -> Iterator[Path]:
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("SKILL.md"):
            if _should_skip_dir(path):
                continue
            yield path.resolve()


def _iter_scripts_in_dir(directory: Path, kinds: tuple[str, ...] = (".py", ".sh")) -> Iterator[Path]:
    if not directory.is_dir():
        return
    for path in sorted(directory.iterdir()):
        if not path.is_file():
            continue
        if path.suffix not in kinds:
            continue
        if path.name == "__init__.py":
            continue
        yield path.resolve()


def _iter_skill_modules(src_skills: Path) -> Iterator[Path]:
    if not src_skills.is_dir():
        return
    for path in sorted(src_skills.glob("*.py")):
        if path.name == "__init__.py":
            continue
        yield path.resolve()


def collect_scan_roots(
    workspace: Path,
    repo_root: Path,
    *,
    include_repo: bool = True,
    include_global_skills: bool = True,
) -> list[tuple[str, Path]]:
    """Return labeled roots: (label, path) for scanning."""
    roots: list[tuple[str, Path]] = []
    ws = workspace.resolve()
    roots.append(("workspace", ws))
    scripts_ws = ws / "scripts"
    if scripts_ws.is_dir():
        roots.append(("workspace_scripts", scripts_ws))
    tools_ws = ws / "tools"
    if tools_ws.is_dir():
        roots.append(("workspace_tools", tools_ws))

    if include_global_skills:
        global_skills = Path.home() / ".openclaw" / "skills"
        if global_skills.is_dir():
            roots.append(("openclaw_home_skills", global_skills))

    if include_repo:
        rr = repo_root.resolve()
        roots.append(("repo_scripts", rr / "scripts"))
        roots.append(("repo_src_skills", rr / "src" / "skills"))
        repo_skills = rr / "skills"
        if repo_skills.is_dir():
            roots.append(("repo_skills", repo_skills))

    return roots


def _build_entry_for_script(path: Path, kind: str) -> RegistryEntry:
    resolved = path.resolve()
    stat = resolved.stat()
    last_modified = _utc_iso_from_mtime(stat.st_mtime)
    if path.suffix == ".py":
        description = _extract_py_description(resolved)
        file_kind = "script" if "scripts" in path.parts or "tools" in path.parts else "skill_module"
    else:
        description = _extract_shell_description(resolved)
        file_kind = "shell"
    name = path.stem
    tags = tuple(_infer_tags(name, description, path.as_posix()))
    resolved_kind = file_kind if kind == "script" else kind
    return RegistryEntry(
        name=name,
        description=description,
        path=str(resolved),
        tags=tags,
        last_modified=last_modified,
        kind=resolved_kind,
    )


def _build_entry_for_skill_md(path: Path) -> RegistryEntry:
    resolved = path.resolve()
    stat = resolved.stat()
    last_modified = _utc_iso_from_mtime(stat.st_mtime)
    raw = resolved.read_text(encoding="utf-8", errors="replace")
    meta, body = _parse_frontmatter_block(raw)
    name_candidate = meta.get("name") or meta.get("title")
    name = str(name_candidate).strip() if name_candidate else resolved.parent.name or resolved.stem
    description = _skill_description(meta, body)
    explicit_tags = _normalize_tag_list(meta.get("tags"))
    inferred = _infer_tags(name, description, resolved.as_posix(), explicit_tags)
    merged = sorted(set(explicit_tags) | set(inferred))
    if not merged:
        merged = ["general"]
    return RegistryEntry(
        name=name,
        description=description,
        path=str(resolved),
        tags=tuple(merged),
        last_modified=last_modified,
        kind="skill",
    )


def scan_entries(scan_roots: Sequence[tuple[str, Path]]) -> list[RegistryEntry]:
    entries: list[RegistryEntry] = []
    seen_paths: set[str] = set()

    for label, root in scan_roots:
        if label == "workspace":
            for path in _iter_skill_markdown_roots([root]):
                key = str(path)
                if key in seen_paths:
                    continue
                seen_paths.add(key)
                entries.append(_build_entry_for_skill_md(path))
        elif label in {"workspace_scripts", "repo_scripts"}:
            for path in _iter_scripts_in_dir(root):
                key = str(path)
                if key in seen_paths:
                    continue
                seen_paths.add(key)
                entries.append(_build_entry_for_script(path, "script"))
        elif label == "workspace_tools":
            for path in _iter_scripts_in_dir(root, kinds=(".py", ".sh", ".js", ".ts")):
                key = str(path)
                if key in seen_paths:
                    continue
                seen_paths.add(key)
                suffix = path.suffix.lower()
                if suffix in {".py", ".sh"}:
                    entries.append(_build_entry_for_script(path, "tool"))
                else:
                    stat = path.stat()
                    name = path.stem
                    description = f"Tool file `{path.name}`."
                    entries.append(
                        RegistryEntry(
                            name=name,
                            description=description,
                            path=str(path.resolve()),
                            tags=tuple(_infer_tags(name, description, path.as_posix())),
                            last_modified=_utc_iso_from_mtime(stat.st_mtime),
                            kind="tool",
                        )
                    )
        elif label == "repo_src_skills":
            for path in _iter_skill_modules(root):
                key = str(path)
                if key in seen_paths:
                    continue
                seen_paths.add(key)
                e = _build_entry_for_script(path, "skill_module")
                entries.append(
                    RegistryEntry(
                        name=e.name,
                        description=e.description,
                        path=e.path,
                        tags=tuple(_infer_tags(e.name, e.description, path.as_posix())),
                        last_modified=e.last_modified,
                        kind="skill_module",
                    )
                )
        elif label in {"openclaw_home_skills", "repo_skills"}:
            for path in _iter_skill_markdown_roots([root]):
                key = str(path)
                if key in seen_paths:
                    continue
                seen_paths.add(key)
                entries.append(_build_entry_for_skill_md(path))

    return sorted(entries, key=lambda e: (e.name.lower(), e.path))


def diff_against_previous(
    current: Sequence[RegistryEntry],
    previous_tools: Sequence[dict[str, Any]] | None,
) -> dict[str, list[str]]:
    prev_by_path = {str(row.get("path", "")): row for row in (previous_tools or []) if row.get("path")}
    current_paths = {e.path for e in current}
    prev_paths = set(prev_by_path.keys())

    added = sorted(current_paths - prev_paths)
    removed = sorted(prev_paths - current_paths)
    modified: list[str] = []
    for entry in current:
        old = prev_by_path.get(entry.path)
        if not old:
            continue
        old_mtime = str(old.get("last_modified", ""))
        if old_mtime != entry.last_modified:
            modified.append(entry.path)
        elif old.get("description") != entry.description:
            modified.append(entry.path)

    return {"added": added, "removed": removed, "modified": modified}


def build_registry_payload(
    scan_roots: Sequence[tuple[str, Path]],
    previous: dict[str, Any] | None = None,
) -> dict[str, Any]:
    entries = scan_entries(scan_roots)
    prev_tools = previous.get("tools") if isinstance(previous, dict) else None
    changes = diff_against_previous(entries, prev_tools if isinstance(prev_tools, list) else None)
    return {
        "version": 1,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "scan_roots": [{"path": str(p.resolve()), "label": label} for label, p in scan_roots],
        "changes_since_previous_run": changes,
        "tools": [e.to_json_dict() for e in entries],
    }


def load_registry(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def write_registry(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def refresh_registry(
    workspace: Path,
    repo_root: Path,
    registry_path: Path,
    *,
    include_repo: bool = True,
    include_global_skills: bool = True,
) -> dict[str, Any]:
    roots = collect_scan_roots(workspace, repo_root, include_repo=include_repo, include_global_skills=include_global_skills)
    previous = load_registry(registry_path)
    payload = build_registry_payload(roots, previous)
    write_registry(registry_path, payload)
    return payload


def _match_search(entry: RegistryEntry, name_pat: str | None, tag: str | None, query: str | None) -> bool:
    if name_pat:
        if name_pat.lower() not in entry.name.lower():
            return False
    if tag:
        if tag.lower() not in {t.lower() for t in entry.tags}:
            return False
    if query:
        q = query.lower()
        blob = f"{entry.name} {entry.description} {' '.join(entry.tags)} {entry.path}".lower()
        if q not in blob:
            return False
    return not (name_pat is None and tag is None and query is None)


def filter_entries(
    entries: Sequence[RegistryEntry],
    *,
    name: str | None = None,
    tag: str | None = None,
    query: str | None = None,
) -> list[RegistryEntry]:
    if name is None and tag is None and query is None:
        return list(entries)
    return [e for e in entries if _match_search(e, name, tag, query)]


def entries_from_payload(payload: dict[str, Any]) -> list[RegistryEntry]:
    tools = payload.get("tools")
    if not isinstance(tools, list):
        return []
    out: list[RegistryEntry] = []
    for row in tools:
        if not isinstance(row, dict):
            continue
        try:
            tags_raw = row.get("tags") or []
            tags = tuple(str(t) for t in tags_raw) if isinstance(tags_raw, list) else ()
            out.append(
                RegistryEntry(
                    name=str(row["name"]),
                    description=str(row.get("description", "")),
                    path=str(row["path"]),
                    tags=tags,
                    last_modified=str(row.get("last_modified", "")),
                    kind=str(row.get("kind", "unknown")),
                )
            )
        except KeyError:
            continue
    return out


def format_entry_detail(entry: RegistryEntry) -> str:
    lines = [
        f"name:        {entry.name}",
        f"kind:        {entry.kind}",
        f"path:        {entry.path}",
        f"last_modified: {entry.last_modified}",
        f"tags:        {', '.join(entry.tags)}",
        f"description: {entry.description}",
    ]
    return "\n".join(lines)


def find_entry_by_identifier(entries: Sequence[RegistryEntry], identifier: str) -> RegistryEntry | None:
    needle = identifier.strip()
    if not needle:
        return None
    lowered = needle.lower()
    for e in entries:
        if e.name.lower() == lowered:
            return e
    for e in entries:
        if e.path.lower() == lowered or e.path.lower().endswith(lowered):
            return e
    for e in entries:
        if lowered in e.path.lower():
            return e
    for e in entries:
        if lowered in e.name.lower():
            return e
    return None


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Discover tools, skills, and scripts; refresh data/tool_registry.json; search and list.",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=None,
        help="OpenClaw workspace root (default: OPENCLAW_WORKSPACE or ~/.openclaw/workspace).",
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=REPO_ROOT,
        help="openclaw-cursor repository root (default: parent of scripts/).",
    )
    parser.add_argument(
        "--registry",
        type=Path,
        default=DEFAULT_REGISTRY_PATH,
        help=f"Registry JSON path (default: {DEFAULT_REGISTRY_PATH}).",
    )
    parser.add_argument(
        "--no-repo",
        action="store_true",
        help="Do not scan repository scripts/skills (workspace and global skills only).",
    )
    parser.add_argument(
        "--no-global-skills",
        action="store_true",
        help="Do not scan ~/.openclaw/skills.",
    )
    parser.add_argument("--json", action="store_true", help="Output JSON instead of plain text.")

    sub = parser.add_subparsers(dest="command", metavar="command")

    sub.add_parser("refresh", help="Rebuild the registry file only.")

    p_list = sub.add_parser("list", help="List all tools (refreshes registry first).")
    p_list.add_argument("--kind", help="Filter by kind (script, skill, shell, ...).")

    p_search = sub.add_parser("search", help="Search tools by name substring, tag, and/or free-text query.")
    p_search.add_argument("--name", help="Substring match on tool name.")
    p_search.add_argument("--tag", help="Match a category tag (e.g. image, audio, code, automation).")
    p_search.add_argument("query", nargs="?", help="Free-text substring match.")

    p_show = sub.add_parser("show", help="Show one tool by name or path fragment.")
    p_show.add_argument("identifier", help="Tool name, path, or unique path suffix.")

    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    workspace = Path(args.workspace).expanduser().resolve() if args.workspace else resolve_openclaw_workspace()
    repo_root = Path(args.repo).expanduser().resolve()
    registry_path = Path(args.registry).expanduser().resolve()

    include_repo = not args.no_repo
    include_global = not args.no_global_skills

    if args.command is None:
        payload = refresh_registry(
            workspace,
            repo_root,
            registry_path,
            include_repo=include_repo,
            include_global_skills=include_global,
        )
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            ch = payload.get("changes_since_previous_run", {})
            print(f"Registry updated: {registry_path}", file=sys.stderr)
            print(f"Tools indexed: {len(payload.get('tools', []))}", file=sys.stderr)
            if isinstance(ch, dict):
                for key in ("added", "modified", "removed"):
                    items = ch.get(key) or []
                    if items:
                        print(f"  {key}: {len(items)}", file=sys.stderr)
        return 0

    payload = refresh_registry(
        workspace,
        repo_root,
        registry_path,
        include_repo=include_repo,
        include_global_skills=include_global,
    )
    entries = entries_from_payload(payload)

    if args.command == "refresh":
        if args.json:
            print(json.dumps({"registry": str(registry_path), "count": len(entries)}, indent=2))
        else:
            print(f"Refreshed {len(entries)} tools → {registry_path}")
        return 0

    if args.command == "list":
        if getattr(args, "kind", None):
            k = args.kind.lower()
            entries = [e for e in entries if e.kind.lower() == k]
        if args.json:
            print(json.dumps([e.to_json_dict() for e in entries], indent=2))
        else:
            for e in entries:
                tag_str = ",".join(e.tags)
                print(f"{e.name:32} [{e.kind:12}] tags={tag_str}")
                print(f"    {e.path}")
        return 0

    if args.command == "search":
        matched = filter_entries(entries, name=args.name, tag=args.tag, query=args.query)
        if args.json:
            print(json.dumps([e.to_json_dict() for e in matched], indent=2))
        else:
            if not matched:
                print("No matches.")
            for e in matched:
                print(f"- {e.name} ({', '.join(e.tags)}) → {e.path}")
        return 0

    if args.command == "show":
        entry = find_entry_by_identifier(entries, args.identifier)
        if entry is None:
            if args.json:
                print(json.dumps({"error": "not_found", "identifier": args.identifier}, indent=2))
            else:
                print(f"No tool matched {args.identifier!r}.", file=sys.stderr)
            return 2
        if args.json:
            print(json.dumps(entry.to_json_dict(), indent=2))
        else:
            print(format_entry_detail(entry))
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
