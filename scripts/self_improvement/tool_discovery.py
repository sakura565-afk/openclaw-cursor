#!/usr/bin/env python3
"""Discover tools from skills/ and TOOLS.md, track usage, suggest combos, refresh docs."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[2]
USAGE_SCHEMA_VERSION = 1
MARKER_BEGIN = "<!-- tool-discovery:auto-begin -->"
MARKER_END = "<!-- tool-discovery:auto-end -->"
FRONTMATTER_RE = re.compile(r"^---\s*\n(?P<body>.*?)\n---\s*\n", re.DOTALL)


@dataclass
class CatalogEntry:
    """Unified view of a discoverable tool (skill, TOOLS.md section, or script)."""

    id: str
    name: str
    source: str  # skill | tools_md | script
    rel_path: str | None
    description: str
    capabilities: list[str] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)
    risk_level: str | None = None
    io_profile: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "source": self.source,
            "path": self.rel_path,
            "description": self.description,
            "capabilities": self.capabilities,
            "commands": self.commands,
            "risk_level": self.risk_level,
            "io_profile": self.io_profile,
        }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def default_usage_path(repo: Path) -> Path:
    override = os.environ.get("TOOL_DISCOVERY_USAGE_FILE")
    if override:
        return Path(override).expanduser()
    return repo / ".learnings" / "tool_usage.json"


def default_skills_dirs(repo: Path) -> list[Path]:
    extra = os.environ.get("SKILLS_DIRS")
    dirs = [repo / "skills"]
    if extra:
        dirs.extend(Path(p.strip()).expanduser() for p in extra.split(",") if p.strip())
    return dirs


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    body = m.group("body")
    rest = text[m.end() :]
    meta: dict[str, str] = {}
    for line in body.splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        meta[key.strip().lower()] = val.strip().strip('"').strip("'")
    return meta, rest


def _first_markdown_heading(text: str) -> str | None:
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("#"):
            return s.lstrip("#").strip() or None
    return None


def _bullets_under_section(text: str, section_pattern: re.Pattern[str]) -> list[str]:
    items: list[str] = []
    lines = text.splitlines()
    in_section = False
    for line in lines:
        if section_pattern.match(line.strip()):
            in_section = True
            continue
        if in_section:
            if line.startswith("#") and not line.strip().startswith("# "):
                if re.match(r"^#{1,6}\s", line):
                    break
            stripped = line.strip()
            if stripped.startswith(("-", "*")) and len(stripped) > 1:
                items.append(stripped[1:].strip())
            elif not stripped:
                if items:
                    break
    return items


def infer_capabilities_from_text(name: str, description: str, extra: str = "") -> list[str]:
    try:
        from scripts import tool_discovery as td  # type: ignore[attr-defined]
    except ImportError:  # pragma: no cover - direct script execution
        sys.path.insert(0, str(ROOT))
        from scripts import tool_discovery as td  # type: ignore[no-redef]

    corpus = f"{name} {description} {extra}".lower()
    caps = [label for marker, label in td.KEYWORD_CAPABILITIES if marker in corpus]
    return sorted(set(caps or ["General utility automation"]))


def discover_skill_tools(skills_root: Path, repo: Path) -> list[CatalogEntry]:
    entries: list[CatalogEntry] = []
    if not skills_root.is_dir():
        return entries
    for path in sorted(skills_root.rglob("*.md")):
        raw = _read_text(path)
        if not raw.strip():
            continue
        meta, body = _parse_frontmatter(raw)
        name = meta.get("title") or meta.get("name") or _first_markdown_heading(body) or path.stem
        desc = meta.get("description") or ""
        if not desc:
            chunk = body.strip().split("\n\n", 1)[0].replace("\n", " ").strip()
            desc = chunk[:400] if chunk else f"Skill note at `{path.relative_to(skills_root)}`."
        cap_bullets = _bullets_under_section(
            body, re.compile(r"^#{2,3}\s*(Capabilities|Tools)\s*$", re.IGNORECASE)
        )
        capabilities = cap_bullets or infer_capabilities_from_text(name, desc, body[:2000])
        rel = str(path.relative_to(repo)) if path.is_relative_to(repo) else str(path)
        sid = f"skill:{rel.replace(chr(92), '/')}"
        entries.append(
            CatalogEntry(
                id=sid,
                name=name,
                source="skill",
                rel_path=rel.replace(chr(92), "/"),
                description=desc,
                capabilities=capabilities,
            )
        )
    return entries


def _slug_from_heading(title: str) -> str:
    s = title.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-") or "tool"


# Headings at ## level that are structural, not separate tools.
_SKIP_TOOLS_MD_SECTION_TITLES = frozenset(
    {
        "capabilities",
        "features",
        "commands",
        "introduction",
        "overview",
        "references",
        "see also",
        "related",
        "table of contents",
    }
)


def discover_from_tools_md(tools_md: Path, repo: Path) -> list[CatalogEntry]:
    entries: list[CatalogEntry] = []
    if not tools_md.is_file():
        return entries
    text = _read_text(tools_md)
    parts = re.split(r"^##\s+", text, flags=re.MULTILINE)
    if len(parts) < 2:
        return entries
    for block in parts[1:]:
        lines = block.splitlines()
        if not lines:
            continue
        title_line = lines[0].strip()
        if _slug_from_heading(title_line) in _SKIP_TOOLS_MD_SECTION_TITLES:
            continue
        body = "\n".join(lines[1:]).strip()
        slug = _slug_from_heading(title_line)
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip() and not p.strip().startswith("#")]
        desc = paragraphs[0] if paragraphs else body[:400]
        cap_bullets = _bullets_under_section(
            body, re.compile(r"^#{2,3}\s*(Capabilities|Features|Commands)\s*$", re.IGNORECASE)
        )
        capabilities = cap_bullets or infer_capabilities_from_text(title_line, desc or "", body[:2000])
        cmd_bullets = [
            b[1:-1] if (b.startswith("`") and b.endswith("`")) else b
            for b in _bullets_under_section(
                body, re.compile(r"^#{2,3}\s*Commands?\s*$", re.IGNORECASE)
            )
        ]
        rel = str(tools_md.relative_to(repo)) if tools_md.is_relative_to(repo) else str(tools_md)
        entries.append(
            CatalogEntry(
                id=f"tools_md:{slug}",
                name=title_line,
                source="tools_md",
                rel_path=rel.replace(chr(92), "/"),
                description=desc or "(no description)",
                capabilities=capabilities,
                commands=cmd_bullets,
            )
        )
    return entries


def discover_script_entries(repo: Path) -> list[CatalogEntry]:
    try:
        from scripts import tool_discovery as td  # type: ignore[attr-defined]
    except ImportError:
        sys.path.insert(0, str(ROOT))
        from scripts import tool_discovery as td  # type: ignore[no-redef]

    profiles = td.analyze_scripts(repo)
    out: list[CatalogEntry] = []
    for p in profiles:
        rel = str(p.path).replace(chr(92), "/")
        out.append(
            CatalogEntry(
                id=f"script:{p.name}",
                name=p.name,
                source="script",
                rel_path=rel,
                description=p.description,
                capabilities=p.capabilities,
                commands=p.commands,
                risk_level=p.risk_level,
                io_profile=p.io_profile,
            )
        )
    return out


def load_catalog(
    repo: Path, *, include_scripts: bool = True, skills_dirs: Iterable[Path] | None = None
) -> list[CatalogEntry]:
    dirs = list(skills_dirs) if skills_dirs is not None else default_skills_dirs(repo)
    catalog: list[CatalogEntry] = []
    seen_dirs: set[Path] = set()
    for d in dirs:
        d = d.resolve()
        if d in seen_dirs:
            continue
        seen_dirs.add(d)
        catalog.extend(discover_skill_tools(d, repo))
    tools_md = repo / "TOOLS.md"
    catalog.extend(discover_from_tools_md(tools_md, repo))
    if include_scripts:
        catalog.extend(discover_script_entries(repo))
    return catalog


def empty_usage_store() -> dict[str, Any]:
    return {"schema_version": USAGE_SCHEMA_VERSION, "tools": {}, "combos": {}}


def load_usage_store(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return empty_usage_store()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return empty_usage_store()
    if not isinstance(data, dict):
        return empty_usage_store()
    data.setdefault("schema_version", USAGE_SCHEMA_VERSION)
    data.setdefault("tools", {})
    data.setdefault("combos", {})
    if not isinstance(data["tools"], dict):
        data["tools"] = {}
    if not isinstance(data["combos"], dict):
        data["combos"] = {}
    return data


def save_usage_store(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _normalize_io(entry: CatalogEntry) -> set[str]:
    if entry.io_profile:
        return set(entry.io_profile)
    blob = " ".join(entry.capabilities + [entry.description, entry.name]).lower()
    inferred: set[str] = set()
    if any(k in blob for k in ("file", "path", "disk", "log", "markdown", "vault")):
        inferred.add("filesystem")
    if any(k in blob for k in ("http", "api", "network", "sync", "telegram", "request")):
        inferred.add("network")
    if any(k in blob for k in ("subprocess", "shell", "process", "queue")):
        inferred.add("process")
    return inferred or {"in-memory"}


def suggest_combinations(catalog: list[CatalogEntry], top_n: int = 10) -> list[dict[str, Any]]:
    by_cap: dict[str, list[CatalogEntry]] = {}
    for e in catalog:
        for c in e.capabilities:
            by_cap.setdefault(c, []).append(e)

    scored: list[tuple[int, CatalogEntry, CatalogEntry, list[str]]] = []
    seen: set[tuple[str, str]] = set()
    for i, a in enumerate(catalog):
        for b in catalog[i + 1 :]:
            if a.id == b.id:
                continue
            key = tuple(sorted((a.id, b.id)))
            if key in seen:
                continue
            seen.add(key)
            shared = set(a.capabilities) & set(b.capabilities)
            io_a, io_b = _normalize_io(a), _normalize_io(b)
            reasons: list[str] = []
            score = 0
            if shared:
                score += 5 + len(shared)
                reasons.append(f"Shared capabilities: {', '.join(sorted(shared))}")
            if a.source != b.source:
                score += 2
                reasons.append(f"Cross-source ({a.source} + {b.source})")
            comp = (io_a - io_b) and (io_b - io_a)
            if comp:
                score += 3
                reasons.append(f"Complementary I/O: {sorted(io_a)} vs {sorted(io_b)}")
            if a.commands and b.commands:
                score += 1
                reasons.append("Both expose CLI-style commands")
            if score > 0:
                scored.append((score, a, b, reasons or ["Heuristic affinity"]))

    scored.sort(key=lambda row: (-row[0], row[1].name, row[2].name))
    combos: list[dict[str, Any]] = []
    for score, a, b, reasons in scored[: max(1, top_n)]:
        combos.append(
            {
                "tools": [a.name, b.name],
                "ids": [a.id, b.id],
                "score": score,
                "reasoning": reasons,
                "example_flow": f"Use `{a.name}` then `{b.name}` (order depends on task).",
            }
        )
    return combos


def propose_improvements(
    catalog: list[CatalogEntry], usage: dict[str, Any], tools_md_path: Path
) -> list[dict[str, Any]]:
    proposals: list[dict[str, Any]] = []
    tools_block = usage.get("tools", {})
    counts = {k: int(v.get("count", 0)) for k, v in tools_block.items() if isinstance(v, dict)}
    max_count = max(counts.values(), default=0)

    md_names = {e.name.lower() for e in catalog if e.source == "tools_md"}
    for e in catalog:
        if len(e.description.strip()) < 30:
            proposals.append(
                {
                    "tool_id": e.id,
                    "kind": "thin_description",
                    "detail": "Expand description with purpose, inputs, and failure modes.",
                }
            )
        c = counts.get(e.id, 0)
        if c == 0 and e.source != "script":
            proposals.append(
                {
                    "tool_id": e.id,
                    "kind": "unused_capability",
                    "detail": "No tracked runs; confirm the tool is linked from runbooks or TOOLS.md.",
                }
            )
        if e.source == "skill" and e.name.lower() not in md_names and tools_md_path.is_file():
            proposals.append(
                {
                    "tool_id": e.id,
                    "kind": "missing_tools_md_crosslink",
                    "detail": f"Add a `## {e.name}` section (or alias) in TOOLS.md for discoverability.",
                }
            )
        if max_count > 0 and c >= max_count * 0.5 and not e.commands and e.source == "script":
            proposals.append(
                {
                    "tool_id": e.id,
                    "kind": "high_usage_cli",
                    "detail": "High usage script: document primary subcommands in TOOLS.md Commands list.",
                }
            )
    return proposals[:50]


def build_auto_doc_block(catalog: list[CatalogEntry], usage: dict[str, Any]) -> str:
    tools_block = usage.get("tools", {})
    lines = [
        MARKER_BEGIN,
        "",
        f"_Updated: {_utc_now()}_",
        "",
        "## Catalog summary",
        "",
        f"- Entries: **{len(catalog)}**",
        "",
        "| id | source | name | uses | capabilities |",
        "| --- | --- | --- | --- | --- |",
    ]
    for e in sorted(catalog, key=lambda x: (x.source, x.name.lower())):
        c = 0
        if isinstance(tools_block.get(e.id), dict):
            c = int(tools_block[e.id].get("count", 0))
        caps = ", ".join(e.capabilities[:4])
        if len(e.capabilities) > 4:
            caps += ", …"
        lines.append(f"| `{e.id}` | {e.source} | {e.name} | {c} | {caps} |")
    lines.extend(["", MARKER_END, ""])
    return "\n".join(lines)


def update_tools_md(repo: Path, catalog: list[CatalogEntry], usage: dict[str, Any], *, dry_run: bool) -> str:
    path = repo / "TOOLS.md"
    block = build_auto_doc_block(catalog, usage)
    if not path.exists():
        body = "\n".join(
            [
                "# TOOLS.md",
                "",
                "Human-written tool notes live above the auto-generated block.",
                "",
                block,
            ]
        )
        if not dry_run:
            path.write_text(body, encoding="utf-8")
        return body

    text = _read_text(path)
    if MARKER_BEGIN in text and MARKER_END in text:
        before, rest = text.split(MARKER_BEGIN, 1)
        _, after = rest.split(MARKER_END, 1)
        new_text = before.rstrip() + "\n\n" + block + after.lstrip()
    else:
        new_text = text.rstrip() + "\n\n" + block
    if not dry_run:
        path.write_text(new_text, encoding="utf-8")
    return new_text


def merge_usage_into_entries(catalog: list[CatalogEntry], usage: dict[str, Any]) -> list[dict[str, Any]]:
    tools_block = usage.get("tools", {})
    rows: list[dict[str, Any]] = []
    for e in catalog:
        u = tools_block.get(e.id, {})
        count = int(u.get("count", 0)) if isinstance(u, dict) else 0
        last = u.get("last_used_utc") if isinstance(u, dict) else None
        row = e.to_dict()
        row["usage_count"] = count
        row["last_used_utc"] = last
        rows.append(row)

    for tid, u in tools_block.items():
        if not isinstance(u, dict):
            continue
        if any(e.id == tid for e in catalog):
            continue
        count = int(u.get("count", 0))
        rows.append(
            {
                "id": tid,
                "name": tid,
                "source": "orphan_usage",
                "path": None,
                "description": "Present in usage log but not in current catalog scan.",
                "capabilities": [],
                "commands": [],
                "risk_level": None,
                "io_profile": [],
                "usage_count": count,
                "last_used_utc": u.get("last_used_utc"),
            }
        )

    return rows


def combo_usage_rows(usage: dict[str, Any]) -> list[dict[str, Any]]:
    combos = usage.get("combos", {})
    return [
        {"pair": k, "count": int(v.get("count", 0)) if isinstance(v, dict) else 0, "last_used_utc": v.get("last_used_utc") if isinstance(v, dict) else None}
        for k, v in sorted(
            combos.items(),
            key=lambda kv: -int(kv[1].get("count", 0) if isinstance(kv[1], dict) else 0),
        )
    ]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Tool discovery: skills/, TOOLS.md, scripts; usage, combos, doc refresh."
    )
    p.add_argument("--root", type=Path, default=ROOT, help="Repository root (default: inferred).")
    p.add_argument(
        "--usage-file",
        type=Path,
        default=None,
        help="Override usage JSON path (else .learnings/tool_usage.json or TOOL_DISCOVERY_USAGE_FILE).",
    )
    sub = p.add_subparsers(dest="command", required=True)

    lp = sub.add_parser("list-tools", help="List discovered tools with usage and improvement hints.")
    lp.add_argument("--format", choices=("json", "text"), default="json")
    lp.add_argument("--include-scripts", action=argparse.BooleanOptionalAction, default=True)
    lp.add_argument("--unused-only", action="store_true", help="Only entries with zero usage_count.")

    sp = sub.add_parser("suggest-combos", help="Suggest high-value tool pairs from the catalog.")
    sp.add_argument("--top", type=int, default=10)
    sp.add_argument("--include-scripts", action=argparse.BooleanOptionalAction, default=True)

    tp = sub.add_parser("track-usage", help="Increment usage for a tool id (and optional combo partner).")
    tp.add_argument("--tool", required=True, help="Catalog id, e.g. skill:skills/foo.md or script:queue_manager.")
    tp.add_argument("--count", type=int, default=1, help="Increment amount (default 1).")
    tp.add_argument("--with-tool", default=None, help="Optional second tool id to record a combo usage.")

    up = sub.add_parser("update-docs", help="Refresh TOOLS.md auto block from the current catalog and usage.")
    up.add_argument("--dry-run", action="store_true", help="Print result instead of writing TOOLS.md.")
    up.add_argument("--include-scripts", action=argparse.BooleanOptionalAction, default=True)

    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    repo = Path(args.root).resolve()
    usage_path = Path(args.usage_file) if args.usage_file else default_usage_path(repo)
    usage = load_usage_store(usage_path)

    if args.command == "list-tools":
        catalog = load_catalog(repo, include_scripts=args.include_scripts)
        rows = merge_usage_into_entries(catalog, usage)
        if args.unused_only:
            rows = [r for r in rows if int(r.get("usage_count", 0)) == 0]
        proposals = propose_improvements(catalog, usage, repo / "TOOLS.md")
        unused = [e for e in catalog if int(usage["tools"].get(e.id, {}).get("count", 0) or 0) == 0]
        payload = {
            "generated_at_utc": _utc_now(),
            "usage_file": str(usage_path),
            "tools": rows,
            "unused_tool_ids": [e.id for e in unused],
            "combo_usage": combo_usage_rows(usage),
            "improvement_proposals": proposals,
        }
        if args.format == "json":
            print(json.dumps(payload, indent=2))
        else:
            for r in rows:
                print(f"{r['usage_count']:5d}  {r['id']:<40}  {r['name']}")
        return 0

    if args.command == "suggest-combos":
        catalog = load_catalog(repo, include_scripts=args.include_scripts)
        payload = {
            "generated_at_utc": _utc_now(),
            "combos": suggest_combinations(catalog, top_n=max(1, args.top)),
            "catalog_size": len(catalog),
        }
        print(json.dumps(payload, indent=2))
        return 0

    if args.command == "track-usage":
        tid = args.tool.strip()
        n = max(0, args.count)
        tools = usage.setdefault("tools", {})
        entry = tools.setdefault(tid, {"count": 0})
        entry["count"] = int(entry.get("count", 0)) + n
        entry["last_used_utc"] = _utc_now()
        if args.with_tool:
            other = args.with_tool.strip()
            pair = "|".join(sorted((tid, other)))
            combos = usage.setdefault("combos", {})
            ce = combos.setdefault(pair, {"count": 0})
            ce["count"] = int(ce.get("count", 0)) + n
            ce["last_used_utc"] = _utc_now()
        save_usage_store(usage_path, usage)
        print(json.dumps({"ok": True, "usage_file": str(usage_path), "tool": tid, "added": n}, indent=2))
        return 0

    if args.command == "update-docs":
        catalog = load_catalog(repo, include_scripts=args.include_scripts)
        body = update_tools_md(repo, catalog, usage, dry_run=args.dry_run)
        if args.dry_run:
            print(body)
        else:
            print(json.dumps({"ok": True, "wrote": str(repo / "TOOLS.md"), "bytes": len(body)}, indent=2))
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
