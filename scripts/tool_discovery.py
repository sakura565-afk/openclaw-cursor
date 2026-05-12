#!/usr/bin/env python3
"""Analyze OpenClaw tool usage from session transcripts and .learnings/ vs SKILL.md tools."""

from __future__ import annotations

import argparse
import importlib.util
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Sequence

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.conversation_extractor import (  # noqa: E402
    TOOL_REGEXES,
    analyze_segments,
    extract_tool_signals,
    parse_json_session,
    parse_session_log,
    read_text,
)

DEFAULT_OUTPUT = Path(".learnings") / "tool_discovery.md"
MAX_FILE_BYTES = 2 * 1024 * 1024

DEFAULT_GLOBS: tuple[str, ...] = (
    "logs/**/*.log",
    "logs/**/*.json",
    "memory/**/*.log",
    "memory/**/*.json",
    "memory/**/*_log.md",
    "memory/**/*.md",
    ".learnings/**/*.md",
    ".learnings/**/*.json",
)

SKIP_DIR_NAMES = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "__pycache__",
        ".venv",
        "venv",
        "node_modules",
        "dist",
        "build",
        ".mypy_cache",
        ".pytest_cache",
        ".tox",
    }
)

# Known agent tool names (Cursor/OpenClaw-style) → optimization hints when sequenced.
CHAIN_HINTS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("read_file", "read_file"), "Back-to-back file reads: consider `grep`/`rg`, semantic search, or listing "
    "a directory once instead of many narrow reads."),
    (("glob_file_search", "read_file"), "Glob then read: batch paths or use a single multi-file analysis pass "
    "when many matches exist."),
    (("grep", "read_file"), "Grep → read_file is solid; if hits are dense, consider widening grep context "
    "before opening files."),
    (("bash", "bash"), "Chained shell steps: merge into one script or a single `bash -lc` with `set -euo pipefail`."),
    (("run_terminal_cmd", "run_terminal_cmd"), "Repeated terminal commands: combine into one invocation where safe."),
    (("str_replace", "read_file"), "Edit then re-read: ensure the model is not re-fetching unchanged regions; "
    "trust the patch result when tests pass."),
)


def normalize_tool_name(raw: str) -> str:
    s = raw.strip()
    if not s:
        return ""
    s = s.split("(", 1)[0].strip()
    s = s.split("[", 1)[0].strip()
    s = s.strip("`\"'")
    lower = s.lower()
    if lower.startswith("functions."):
        s = s[10:]
    return s


def _should_skip_path(path: Path) -> bool:
    return any(part in SKIP_DIR_NAMES for part in path.parts)


def iter_scan_files(root: Path, globs: Sequence[str]) -> Iterator[Path]:
    seen: set[Path] = set()
    for pattern in globs:
        for path in root.glob(pattern):
            if not path.is_file() or _should_skip_path(path):
                continue
            try:
                if path.stat().st_size > MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            rp = path.resolve()
            if rp in seen:
                continue
            seen.add(rp)
            yield path


def ordered_tool_hits_from_text(text: str) -> list[str]:
    """Non-overlapping tool-like tokens in source order (for logs without structured roles)."""

    hits: list[tuple[int, str]] = []
    for rg in TOOL_REGEXES:
        for m in rg.finditer(text):
            name = (m.group(1) or "").strip()
            if len(name) < 2 or len(name) > 80:
                continue
            lowered = name.lower()
            if lowered in {"true", "false", "null", "object", "string", "inputs"}:
                continue
            hits.append((m.start(), normalize_tool_name(name)))
    hits.sort(key=lambda x: x[0])
    ordered: list[str] = []
    for _, nm in hits:
        if nm and (not ordered or ordered[-1] != nm):
            ordered.append(nm)
    return ordered


def tool_sequence_from_segments(
    segments: list[tuple[int, str | None, str]], raw_text: str
) -> list[str]:
    seq: list[str] = []
    for _turn, role, text in segments:
        if (role or "").lower() == "tool":
            nm = normalize_tool_name(text)
            if nm:
                seq.append(nm)
    if seq:
        return seq
    return ordered_tool_hits_from_text(raw_text)


def bigrams(seq: list[str]) -> Counter[tuple[str, str]]:
    c: Counter[tuple[str, str]] = Counter()
    for a, b in zip(seq, seq[1:]):
        if a and b:
            c[(normalize_tool_name(a).lower(), normalize_tool_name(b).lower())] += 1
    return c


def longest_same_run(seq: list[str]) -> tuple[str, int]:
    if not seq:
        return "", 0
    best = ("", 0)
    cur = seq[0]
    run = 1
    for x in seq[1:]:
        nx = normalize_tool_name(x)
        cx = normalize_tool_name(cur)
        if nx.lower() == cx.lower():
            run += 1
        else:
            key = normalize_tool_name(cur)
            if run > best[1] and key:
                best = (key, run)
            cur = x
            run = 1
    key = normalize_tool_name(cur)
    if run > best[1] and key:
        best = (key, run)
    return best


def _load_repo_tool_discovery_module(root: Path) -> Any | None:
    impl = root / "tool_discovery.py"
    if not impl.is_file():
        return None
    module_name = "_openclaw_skill_catalog_impl"
    spec = importlib.util.spec_from_file_location(module_name, impl)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


def build_skill_catalog(root: Path) -> tuple[list[Any], list[str]]:
    notes: list[str] = []
    mod = _load_repo_tool_discovery_module(root)
    if mod is None:
        notes.append("Repository root `tool_discovery.py` not found; SKILL.md catalog skipped.")
        return [], notes
    roots: list[Path] = []
    skills_dir = root / "skills"
    if skills_dir.is_dir():
        roots.append(skills_dir)
    roots.append(root)
    try:
        discovery = mod.ToolDiscovery(roots=roots)
        catalog = discovery.discover_all(force=True)
        return list(catalog), notes
    except Exception as exc:  # pragma: no cover - defensive
        notes.append(f"Skill catalog scan failed: {exc}")
        return [], notes


def skill_match_keys(skill: Any) -> set[str]:
    keys: set[str] = set()
    for attr in ("skill_id", "name"):
        raw = getattr(skill, attr, "") or ""
        s = str(raw).strip()
        if s:
            keys.add(normalize_tool_name(s).lower())
    path = getattr(skill, "path", None)
    if isinstance(path, Path):
        keys.add(path.stem.lower())
        if path.parent.name and path.parent.name.lower() not in {"skills", ".", ""}:
            keys.add(path.parent.name.lower())
    return {k for k in keys if len(k) >= 2}


def skill_used_in_transcripts(skill: Any, used_normalized: set[str], used_raw: set[str]) -> bool:
    aliases = skill_match_keys(skill)
    if not aliases:
        return False
    blob = " ".join(sorted(used_raw)).lower()
    for a in aliases:
        if len(a) <= 2:
            continue
        if a in used_normalized:
            return True
        if a in blob:
            return True
        for u in used_normalized:
            if len(u) < 3:
                continue
            if a in u or u in a:
                return True
    return False


@dataclass
class UsageAggregate:
    tool_counts: Counter[str] = field(default_factory=Counter)
    files_scanned: int = 0
    bigrams: Counter[tuple[str, str]] = field(default_factory=Counter)
    same_tool_runs: Counter[str] = field(default_factory=Counter)
    per_file_top: list[tuple[str, str, int]] = field(default_factory=list)

    def register_file(self, rel: str, merged: Counter[str], seq: list[str]) -> None:
        self.files_scanned += 1
        self.tool_counts.update(merged)
        self.bigrams.update(bigrams(seq))
        tool, run = longest_same_run(seq)
        if run >= 3 and tool:
            self.same_tool_runs[f"{normalize_tool_name(tool)}×{run}"] += 1
        if merged:
            top_name, top_ct = merged.most_common(1)[0]
            self.per_file_top.append((rel, top_name, top_ct))


def collect_usage(root: Path, globs: Sequence[str]) -> UsageAggregate:
    agg = UsageAggregate()
    for path in iter_scan_files(root, globs):
        raw = read_text(path)
        if not raw.strip():
            continue
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            rel = path.as_posix()
        segments = parse_session_log(path)
        if not segments and path.suffix.lower() == ".md":
            segments = parse_json_session(path)
        digest = analyze_segments(segments, rel)
        textual = extract_tool_signals(raw)
        merged: Counter[str] = Counter()
        for name, ct in digest.all_tools().items():
            merged[normalize_tool_name(name)] += ct
        for name, ct in textual.items():
            merged[normalize_tool_name(name)] += ct
        seq = tool_sequence_from_segments(segments, raw)
        agg.register_file(rel, merged, seq)
    return agg


def chain_opportunities(agg: UsageAggregate) -> list[str]:
    lines: list[str] = []
    for (a, b), hint in CHAIN_HINTS:
        key = (a.lower(), b.lower())
        ct = agg.bigrams.get(key, 0)
        if ct >= 2:
            lines.append(f"- **`{a}` → `{b}`** ({ct}×): {hint}")
    for (a, b), ct in agg.bigrams.most_common(12):
        if ct < 3:
            continue
        if any((a, b) == (x[0].lower(), x[1].lower()) for x, _ in CHAIN_HINTS):
            continue
        lines.append(
            f"- Frequent pair **`{a}` → `{b}`** ({ct}×): review whether a single higher-level step "
            f"(search, batch read, or scripted pipeline) could replace part of this chain."
        )
    for run_label, ct in agg.same_tool_runs.most_common(8):
        if ct >= 1:
            lines.append(
                f"- Repeated tool streak `{run_label}` in {ct} file(s): consider batching, caching "
                "intermediate results, or narrowing scope."
            )
    return lines


def render_report(
    root: Path,
    agg: UsageAggregate,
    skills: list[Any],
    notes: list[str],
    globs: Sequence[str],
) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    used_raw = set(agg.tool_counts.keys())
    used_norm = {normalize_tool_name(x).lower() for x in used_raw if x}

    unused = [s for s in skills if not skill_used_in_transcripts(s, used_norm, used_raw)]

    lines = [
        "# OpenClaw tool discovery",
        "",
        f"_Generated {ts} · workspace `{root.as_posix()}`_",
        "",
        "## Scope",
        "",
        "Scanned globs (relative to workspace root):",
        "",
        *(f"- `{g}`" for g in globs),
        "",
        f"- Files matched: **{agg.files_scanned}**",
        "",
    ]
    if notes:
        lines.extend(["## Scan notes", ""])
        lines.extend(f"- {n}" for n in notes)
        lines.append("")

    lines.extend(
        [
            "## Tool usage (transcripts + learnings)",
            "",
            "Counts merge structured session tool calls with textual mentions (`invoke X`, MCP labels, "
            "inline JSON tool names).",
            "",
        ]
    )
    if agg.tool_counts:
        for name, ct in agg.tool_counts.most_common(40):
            lines.append(f"- `{name}` — **{ct}**")
    else:
        lines.append("_No tool-like signals found in scanned files._")

    lines.extend(["", "## Hot files (top tool per file)", ""])
    for rel, name, ct in sorted(agg.per_file_top, key=lambda x: -x[2])[:25]:
        lines.append(f"- `{rel}` → `{name}` ({ct})")

    lines.extend(["", "## SKILL.md catalog vs usage", ""])
    if not skills:
        lines.append("_No skills loaded (add `skills/**/SKILL.md` or root `SKILL.md`, and ensure "
                     "repo `tool_discovery.py` exists for parsing)._")
    else:
        lines.append(f"- Catalog size: **{len(skills)}** skill(s)")
        lines.append(f"- Possibly unused (no transcript / learning match): **{len(unused)}**")
        lines.append("")
        if unused:
            for s in sorted(unused, key=lambda x: str(getattr(x, "name", "")).lower())[:40]:
                sid = getattr(s, "skill_id", "")
                p = getattr(s, "path", "")
                nm = getattr(s, "name", "")
                lines.append(f"- `{nm}` (`{sid}`) — `{p}`")
            if len(unused) > 40:
                lines.append(f"- _…and {len(unused) - 40} more_")
        lines.append("")
        lines.append(
            "**Recommendation:** For unused skills, link them from runbooks or prompts, or archive "
            "stale `SKILL.md` files to reduce noise."
        )

    chains = chain_opportunities(agg)
    lines.extend(["", "## Tool-chain opportunities", ""])
    if chains:
        lines.extend(chains)
    else:
        lines.append(
            "_No strong recurring pairs or redundant runs detected at current thresholds. "
            "Re-run after more sessions accumulate under `logs/` or `.learnings/`._"
        )

    lines.extend(
        [
            "",
            "## Recommendations",
            "",
            "1. Route repetitive read/search chains through repository-specific scripts under `scripts/` "
            "when the same sequence appears often.",
            "2. Prefer structured exports (JSON session logs) so tool calls parse reliably; prose-only logs "
            "depend on regex fallbacks.",
            "3. Align `SKILL.md` names and `id` fields with actual tool identifiers your agent emits, "
            "so this report can flag genuinely unused skills.",
            "",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def run_analysis(
    root: Path,
    *,
    globs: Sequence[str] | None = None,
    output: Path | None = None,
) -> Path:
    root = root.resolve()
    globs = globs or DEFAULT_GLOBS
    skills, notes = build_skill_catalog(root)
    agg = collect_usage(root, globs)
    out = (output or (root / DEFAULT_OUTPUT)).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    md = render_report(root, agg, skills, notes, globs)
    out.write_text(md, encoding="utf-8")
    return out


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Scan OpenClaw transcripts and .learnings/ for tool usage; compare with SKILL.md tools.",
    )
    p.add_argument("--root", type=Path, default=_REPO_ROOT, help="Workspace root (default: repository root)")
    p.add_argument(
        "--output",
        type=Path,
        default=None,
        help=f"Markdown report path (default: <root>/{DEFAULT_OUTPUT.as_posix()})",
    )
    p.add_argument(
        "--glob",
        action="append",
        dest="extra_globs",
        default=[],
        help="Extra glob relative to root (repeatable). Default globs always apply unless --only-glob is set.",
    )
    p.add_argument(
        "--only-glob",
        action="store_true",
        help="Use only globs from --glob (do not include built-in default globs).",
    )
    return p.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    root = Path(args.root).resolve()
    globs: tuple[str, ...]
    if args.only_glob:
        if not args.extra_globs:
            print("error: --only-glob requires at least one --glob", file=sys.stderr)
            return 2
        globs = tuple(args.extra_globs)
    else:
        globs = tuple({*DEFAULT_GLOBS, *tuple(args.extra_globs)})

    out = run_analysis(root, globs=globs, output=args.output)
    print(f"Wrote {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
