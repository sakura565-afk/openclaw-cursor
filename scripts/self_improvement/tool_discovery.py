#!/usr/bin/env python3
"""Cross-reference OpenClaw tool bundles, skills, and repo scripts with session history.

Scans:
  - ``$OPENCLAW_HOME/tools`` and ``$OPENCLAW_HOME/workspace/tools`` (one subfolder = one bundle)
  - Skill directories under ``$OPENCLAW_HOME/skills`` and ``.../workspace/skills``
  - Repository ``scripts/*.py`` helpers (same stems as ``python -m scripts.<stem>``)

Correlates with recent session logs and transcripts (JSON, log, markdown) using the same
parsing pipeline as ``conversation_extractor``.

Writes ``.learnings/tool_discovery.md`` with actionable usage recommendations.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from scripts.conversation_extractor import analyze_segments, parse_session_log
from scripts.optimize_context import repo_root

# Optional deep script profiling (capabilities, risk) for richer suggestions.
try:
    from scripts.tool_discovery import ToolProfile, analyze_scripts
except ImportError:  # pragma: no cover - defensive for odd PYTHONPATH
    ToolProfile = Any  # type: ignore[misc, assignment]
    analyze_scripts = None  # type: ignore[misc, assignment]


LEARNINGS_SUBDIR = ".learnings"
OUTPUT_NAME = "tool_discovery.md"
MAX_FILE_BYTES = 2 * 1024 * 1024
DEFAULT_SINCE_DAYS = 30
DEFAULT_MAX_FILES = 600
TEXT_SUFFIXES = frozenset({".log", ".txt", ".md"})
JSON_SUFFIXES = frozenset({".json"})


def _default_openclaw_home() -> Path:
    override = os.environ.get("OPENCLAW_HOME")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".openclaw"


def _repo_scripts_dir(root: Path) -> Path:
    return root / "scripts"


def _normalize_match_token(name: str) -> str:
    s = name.strip().lower()
    s = re.sub(r"^functions\.", "", s)
    s = re.sub(r"^mcp[_:]?", "", s)
    s = s.split("/")[-1]
    return re.sub(r"[^a-z0-9]+", "", s)


def _aliases_for_catalog_item(kind: str, name: str) -> set[str]:
    base = {name, name.lower(), _normalize_match_token(name)}
    base.discard("")
    if kind == "repo_script":
        base.add(_normalize_match_token(f"scripts.{name}"))
        base.add(_normalize_match_token(f"python-m-scripts-{name}"))
    if kind == "skill":
        base.add(_normalize_match_token(f"skill{name}"))
    return {b for b in base if b}


def _session_key_variants(session_key: str) -> set[str]:
    out = {session_key, session_key.lower(), _normalize_match_token(session_key)}
    out.discard("")
    return out


def usage_hits_for_item(kind: str, name: str, merged: Counter[str]) -> tuple[int, list[str]]:
    """Return total weighted hits and contributing raw session keys."""

    want = _aliases_for_catalog_item(kind, name)
    matched_keys: list[str] = []
    total = 0
    for key, count in merged.items():
        variants = _session_key_variants(key)
        if want.intersection(variants):
            total += count
            matched_keys.append(key)
        else:
            nk = _normalize_match_token(key)
            if nk and any(w in nk or nk in w for w in want if len(w) >= 4):
                total += count
                matched_keys.append(key)
    return total, matched_keys


@dataclass
class CatalogEntry:
    kind: str  # tool_bundle | skill | repo_script
    name: str
    path: Path | None
    note: str = ""


@dataclass
class RunConfig:
    repo_root: Path
    openclaw_home: Path
    since_days: int
    max_files: int
    output_path: Path
    extra_history_roots: tuple[Path, ...] = ()


def discover_tool_bundles(openclaw_home: Path) -> list[CatalogEntry]:
    out: list[CatalogEntry] = []
    for rel in ("tools", Path("workspace") / "tools"):
        root = openclaw_home / rel
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir()):
            if not child.is_dir() or child.name.startswith("."):
                continue
            out.append(CatalogEntry("tool_bundle", child.name, child.resolve(), ""))
    return out


def _read_skill_name(skill_dir: Path) -> str:
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        return skill_dir.name
    try:
        text = skill_md.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return skill_dir.name
    for line in text.splitlines()[:40]:
        m = re.match(r"^name:\s*(.+)$", line.strip(), re.I)
        if m:
            return m.group(1).strip().strip("\"'")
    return skill_dir.name


def discover_skills(openclaw_home: Path) -> list[CatalogEntry]:
    out: list[CatalogEntry] = []
    seen: set[str] = set()
    for rel in ("skills", Path("workspace") / "skills"):
        root = openclaw_home / rel
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir()):
            if not child.is_dir() or child.name.startswith("."):
                continue
            display = _read_skill_name(child)
            key = display.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(
                CatalogEntry(
                    "skill",
                    display,
                    child.resolve(),
                    note=f"folder `{child.name}`" if display != child.name else "",
                )
            )
    return out


def discover_repo_scripts(repo: Path) -> list[CatalogEntry]:
    scripts_dir = _repo_scripts_dir(repo)
    if not scripts_dir.is_dir():
        return []
    entries: list[CatalogEntry] = []
    for path in sorted(scripts_dir.glob("*.py")):
        if path.name == "__init__.py":
            continue
        stem = path.stem
        note = ""
        try:
            first = path.read_text(encoding="utf-8", errors="replace").splitlines()[:5]
            for line in first:
                if line.startswith('"""') or line.startswith("'''"):
                    rest = line.lstrip('"\'')
                    if rest.strip('"\''):
                        note = rest.strip('"\'' )[:160]
                    break
        except OSError:
            pass
        entries.append(CatalogEntry("repo_script", stem, path.resolve(), note=note))
    return entries


def _history_scan_roots(cfg: RunConfig) -> list[Path]:
    oc = cfg.openclaw_home
    roots = [
        oc / "logs",
        oc / "workspace" / "logs",
        oc / "workspace" / "memory",
        oc / "temp" / "openclaw-cursor" / "logs",
        cfg.repo_root / "memory",
        cfg.repo_root / "logs",
    ]
    roots.extend(cfg.extra_history_roots)
    return [p.resolve() for p in roots if p.exists()]


def _suffix_ok(path: Path) -> bool:
    suf = path.suffix.lower()
    return suf in TEXT_SUFFIXES or suf in JSON_SUFFIXES


def iter_history_files(cfg: RunConfig) -> list[Path]:
    cutoff = datetime.now(timezone.utc).timestamp() - cfg.since_days * 86400
    found: list[tuple[float, Path]] = []
    for root in _history_scan_roots(cfg):
        try:
            for path in root.rglob("*"):
                if not path.is_file():
                    continue
                if path.name == OUTPUT_NAME and LEARNINGS_SUBDIR in path.parts:
                    continue
                if not _suffix_ok(path):
                    continue
                try:
                    st = path.stat()
                except OSError:
                    continue
                if st.st_mtime < cutoff or st.st_size > MAX_FILE_BYTES:
                    continue
                found.append((st.st_mtime, path.resolve()))
        except OSError:
            continue
    found.sort(key=lambda x: x[0], reverse=True)
    out: list[Path] = []
    seen: set[Path] = set()
    for _, p in found:
        if p in seen:
            continue
        seen.add(p)
        out.append(p)
        if len(out) >= cfg.max_files:
            break
    return out


def aggregate_tool_usage(paths: Iterable[Path]) -> tuple[Counter[str], int, int]:
    merged: Counter[str] = Counter()
    ok = 0
    failed = 0
    for path in paths:
        try:
            segments = parse_session_log(path)
            if not segments:
                failed += 1
                continue
            digest = analyze_segments(segments, path.as_posix())
            merged.update(digest.all_tools())
            ok += 1
        except (OSError, ValueError, TypeError):
            failed += 1
    return merged, ok, failed


def _profile_by_name(repo: Path) -> dict[str, Any]:
    if analyze_scripts is None:
        return {}
    profiles = analyze_scripts(repo)
    return {p.name: p for p in profiles}


def _classify_hits(hits: int, files_ok: int) -> str:
    if files_ok == 0:
        return "no_history"
    if hits == 0:
        return "unused"
    if hits < 3:
        return "underused"
    if hits < 20:
        return "moderate"
    return "frequent"


def build_suggestions(
    entries: list[CatalogEntry],
    merged: Counter[str],
    files_ok: int,
    profiles: dict[str, Any],
) -> list[str]:
    """Return prioritized markdown bullet lines (deduped, capped)."""

    lines: list[tuple[int, str]] = []

    if files_ok == 0:
        lines.append(
            (
                0,
                "- **Expand history coverage**: No parseable session files were found in the scanned "
                "directories for the chosen window. Set `OPENCLAW_HOME`, widen `--since-days`, add "
                "`--extra-history-root`, or export transcripts under `~/.openclaw/workspace/memory/` "
                "or `<repo>/memory/` so this report can rank real tool usage.",
            )
        )

    for e in entries:
        hits, matched = usage_hits_for_item(e.kind, e.name, merged)
        bucket = _classify_hits(hits, files_ok)

        if e.kind == "repo_script" and bucket in ("unused", "underused", "no_history"):
            prof = profiles.get(e.name)
            caps = ", ".join(prof.capabilities[:4]) if prof else "general automation"
            cmd = f"python -m scripts.{e.name}"
            lines.append(
                (
                    3 if bucket == "unused" else 5,
                    f"- **Run `{cmd}`** when work matches: {caps}. "
                    f"It logged **{hits}** tool signal(s) in-session — use `--help` first, then wire "
                    f"it into recurring flows (cron, `nightly_pipeline`, or agent rules).",
                )
            )

        if e.kind == "skill" and bucket in ("unused", "underused", "no_history"):
            loc = f"`{e.path}`" if e.path else "skill path"
            lines.append(
                (
                    2 if bucket == "unused" else 4,
                    f"- **Skill `{e.name}`** ({loc}): add a one-line *when-to-use* rule in the agent "
                    f"system prompt or session template (e.g. \"invoke `{e.name}` before editing X\"). "
                    f"Current session hits: **{hits}**.",
                )
            )

        if e.kind == "tool_bundle" and bucket in ("unused", "underused", "no_history"):
            loc = f"`{e.path}`" if e.path else "bundle path"
            lines.append(
                (
                    2,
                    f"- **Tool bundle `{e.name}`** ({loc}): confirm the MCP / gateway exposes this bundle; "
                    f"if it should be used more, add an explicit checklist step for tasks that need its "
                    f"capability. Session hits: **{hits}**.",
                )
            )

        if hits > 0 and len(matched) > 6:
            lines.append(
                (
                    8,
                    f"- **Consolidate calls to `{e.name}`** ({e.kind}): many distinct transcript keys "
                    f"({len(matched)}) map to this item — prefer stable tool names in prompts to improve "
                    f"telemetry and caching.",
                )
            )

    # Promote top under-used high-value scripts (risk/capability heuristic)
    for e in entries:
        if e.kind != "repo_script":
            continue
        hits, _ = usage_hits_for_item(e.kind, e.name, merged)
        prof = profiles.get(e.name)
        if not prof or hits >= 10:
            continue
        if prof.risk_level == "high" and hits == 0:
            lines.append(
                (
                    6,
                    f"- **Gate `{e.name}`**: marked **high** risk in static analysis but unused in "
                    f"sessions — document required env vars / approvals in `SKILL.md` or repo docs before "
                    f"agents invoke it autonomously.",
                )
            )

    lines.sort(key=lambda x: x[0])
    out: list[str] = []
    seen: set[str] = set()
    for _, text in lines:
        if text in seen:
            continue
        seen.add(text)
        out.append(text)
        if len(out) >= 45:
            break
    return out


def render_report(
    cfg: RunConfig,
    catalog: list[CatalogEntry],
    history_paths: list[Path],
    merged: Counter[str],
    files_ok: int,
    files_failed: int,
    suggestions: list[str],
) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# Tool discovery — usage vs catalog",
        "",
        f"Generated **{now}**.",
        "",
        "## What was scanned",
        "",
        f"- **Repository root**: `{cfg.repo_root}`",
        f"- **OpenClaw home**: `{cfg.openclaw_home}` (`OPENCLAW_HOME` overrides defaults)",
        f"- **History window**: last **{cfg.since_days}** day(s), up to **{cfg.max_files}** files, "
        f"max **{MAX_FILE_BYTES // (1024 * 1024)}** MiB per file",
        "",
        "### Catalog counts",
        "",
    ]

    kinds: Counter[str] = Counter(e.kind for e in catalog)
    for k in sorted(kinds):
        lines.append(f"- `{k}`: **{kinds[k]}**")
    lines.append("")
    lines.append("### Session / log roots")
    lines.append("")
    for r in _history_scan_roots(cfg):
        lines.append(f"- `{r}`")
    lines.append("")
    lines.append(f"- **History files picked**: **{len(history_paths)}**")
    lines.append(f"- **Files parsed with tool signals**: **{files_ok}** (empty/unparseable: **{files_failed}**)")
    lines.append("")

    lines.append("## Top session tool signals")
    lines.append("")
    if merged:
        for name, ct in merged.most_common(25):
            lines.append(f"- `{name}` — **{ct}**")
    else:
        lines.append("- *(none)*")
    lines.append("")

    lines.append("## Catalog vs session hits")
    lines.append("")
    lines.append("| Kind | Name | Hits | Status | Path |")
    lines.append("| --- | --- | ---: | --- | --- |")
    for e in sorted(catalog, key=lambda x: (x.kind, x.name.lower())):
        hits, _ = usage_hits_for_item(e.kind, e.name, merged)
        status = _classify_hits(hits, files_ok)
        path_cell = f"`{e.path}`" if e.path else "—"
        note = f" {e.note}" if e.note else ""
        lines.append(f"| {e.kind} | `{e.name}` | {hits} | {status} | {path_cell}{note} |")
    lines.append("")

    lines.append("## Actionable recommendations")
    lines.append("")
    if suggestions:
        for text in suggestions:
            lines.append(text)
    else:
        lines.append("- *(no suggestions — usage aligns with defaults or catalog is empty)*")
    lines.append("")

    lines.append("---")
    lines.append("*Produced by `python -m scripts.self_improvement.tool_discovery`*")
    lines.append("")
    return "\n".join(lines)


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Map OpenClaw tools/skills/repo scripts to session usage; write .learnings/tool_discovery.md."
    )
    p.add_argument("--repo-root", type=Path, default=None, help="Repository root (default: auto-detect)")
    p.add_argument("--openclaw-home", type=Path, default=None, help="OpenClaw home (default: OPENCLAW_HOME or ~/.openclaw)")
    p.add_argument("--since-days", type=int, default=DEFAULT_SINCE_DAYS, help="Only include history files modified recently")
    p.add_argument("--max-files", type=int, default=DEFAULT_MAX_FILES, help="Cap number of history files to parse")
    p.add_argument(
        "--output",
        type=Path,
        default=None,
        help=f"Output markdown path (default: <repo>/{LEARNINGS_SUBDIR}/{OUTPUT_NAME})",
    )
    p.add_argument(
        "--extra-history-root",
        type=Path,
        action="append",
        default=[],
        help="Additional directory to scan recursively (repeatable)",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    repo = (args.repo_root or repo_root()).resolve()
    oc = (args.openclaw_home or _default_openclaw_home()).expanduser().resolve()
    out = (args.output or (repo / LEARNINGS_SUBDIR / OUTPUT_NAME)).resolve()
    extra = tuple(Path(p).expanduser().resolve() for p in (args.extra_history_root or []))

    cfg = RunConfig(
        repo_root=repo,
        openclaw_home=oc,
        since_days=max(1, args.since_days),
        max_files=max(10, args.max_files),
        output_path=out,
        extra_history_roots=extra,
    )

    catalog: list[CatalogEntry] = []
    catalog.extend(discover_tool_bundles(oc))
    catalog.extend(discover_skills(oc))
    catalog.extend(discover_repo_scripts(repo))

    history_paths = iter_history_files(cfg)
    merged, ok, bad = aggregate_tool_usage(history_paths)
    profiles = _profile_by_name(repo)
    sug = build_suggestions(catalog, merged, ok, profiles)

    body = render_report(cfg, catalog, history_paths, merged, ok, bad, sug)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(body, encoding="utf-8")
    print(f"Wrote {out.as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
