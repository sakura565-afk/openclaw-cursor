#!/usr/bin/env python3
"""
Scan the OpenClaw repository for runnable tools, compare them to workflow
surfaces and ``SKILL.md`` guidance, and emit a discovery report with concrete
integration recommendations.

Run from the repository root::

    python -m scripts.self_improvement.tool_discovery --format markdown
    python -m scripts.self_improvement.tool_discovery --format json -o reports/tool_discovery.json

The report combines:

- **Workflow gaps** — tools under ``scripts/`` or ``src/`` that never appear on
  deployment surfaces (README, docs, examples, nightly pipeline, etc.), via the
  repository-root ``tool_discovery`` module.
- **Script profiles** — lightweight AST-derived capability hints for
  ``scripts/*.py`` (from ``scripts.tool_discovery``).
- **Skill coverage** — every ``SKILL.md`` (case-insensitive name) under the
  repo, cross-referenced for ``python -m`` / dotted-module mentions.

Undeployed tools get prioritized recommendations (documentation, automation,
skills) based on kind, risk, and lexical overlap with existing skills.
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

# Repository root: scripts/self_improvement/tool_discovery.py -> parents[2]
REPO_ROOT = Path(__file__).resolve().parents[2]

_SKIP_DIR_PARTS = frozenset(
    {
        ".git",
        "__pycache__",
        ".venv",
        "venv",
        ".mypy_cache",
        ".pytest_cache",
        ".tox",
        "node_modules",
    }
)


def _ensure_repo_importable(repo: Path) -> None:
    key = str(repo.resolve())
    if key not in sys.path:
        sys.path.insert(0, key)


def _load_module_from_path(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _root_workflow_discovery(repo: Path) -> Any:
    return _load_module_from_path("_openclaw_root_tool_discovery", repo / "tool_discovery.py")


def _import_script_tool_discovery(repo: Path) -> Any:
    _ensure_repo_importable(repo)
    from scripts import tool_discovery as m  # noqa: PLC0415 — after path fix

    return m


def _analyze_scripts_lenient(std: Any, root: Path) -> list[Any]:
    """
    Same as ``scripts.tool_discovery.analyze_scripts`` but skips files that
    cannot be parsed (syntax errors in unrelated scripts should not block the
    whole discovery report).
    """

    profiles: list[Any] = []
    for path in std.discover_script_paths(root):
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
        except (OSError, SyntaxError, UnicodeError):
            continue
        name = path.stem
        imports = std.extract_imports(tree)
        functions = std.extract_functions(tree)
        commands = std.extract_cli_commands(tree)
        description = std.extract_description(tree)
        capabilities = std.infer_capabilities(name, description, commands, functions)
        io_profile = std.infer_io_profile(imports, source)
        risk_level = std.infer_risk_level(imports, commands)
        profiles.append(
            std.ToolProfile(
                name=name,
                path=path.relative_to(root),
                description=description,
                imports=imports,
                functions=functions,
                commands=commands,
                capabilities=capabilities,
                risk_level=risk_level,
                io_profile=io_profile,
            )
        )
    std.enrich_dependency_graph(profiles)
    for profile in profiles:
        profile.examples = std.build_examples(profile)
    return profiles


def iter_skill_markdown_files(repo: Path) -> list[Path]:
    """All ``SKILL.md`` files (case-insensitive basename), excluding noisy trees."""
    found: set[Path] = set()
    for path in repo.rglob("*.md"):
        if not path.is_file():
            continue
        if any(part in _SKIP_DIR_PARTS for part in path.parts):
            continue
        if path.name.upper() != "SKILL.MD":
            continue
        found.add(path.resolve())
    return sorted(found)


def _first_heading(text: str) -> str:
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("#"):
            return s.lstrip("#").strip() or "Skill"
    return "Skill"


_TOKEN_RE = re.compile(r"[a-z0-9_]{3,}", re.IGNORECASE)


def _tokens(text: str) -> set[str]:
    return {m.group(0).lower() for m in _TOKEN_RE.finditer(text)}


@dataclass
class SkillRecord:
    """Parsed ``SKILL.md`` inventory row."""

    relative_path: str
    title: str
    referenced_tool_ids: tuple[str, ...]
    char_count: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ToolIntegrationAdvice:
    tool_id: str
    relative_path: str
    kind: str
    summary: str
    workflow_gap: bool
    cited_in_skills: bool
    capabilities: tuple[str, ...] = ()
    risk_level: str = ""
    io_profile: tuple[str, ...] = ()
    related_skills: tuple[str, ...] = ()
    recommendations: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


@dataclass
class DiscoveryReport:
    repo_root: str
    generated_at_utc: str
    skills: tuple[SkillRecord, ...]
    skill_referenced_tool_ids: frozenset[str]
    tools: tuple[ToolIntegrationAdvice, ...]
    workflow_surface_count: int
    workflow_undeployed_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo_root": self.repo_root,
            "generated_at_utc": self.generated_at_utc,
            "workflow_surface_count": self.workflow_surface_count,
            "workflow_undeployed_count": self.workflow_undeployed_count,
            "skills": [s.to_dict() for s in self.skills],
            "skill_referenced_tool_ids": sorted(self.skill_referenced_tool_ids),
            "tools": [t.to_dict() for t in self.tools],
        }


def _parse_skills(repo: Path, extract_refs: Any) -> tuple[list[SkillRecord], set[str]]:
    records: list[SkillRecord] = []
    all_refs: set[str] = set()
    for path in iter_skill_markdown_files(repo):
        text = path.read_text(encoding="utf-8", errors="replace")
        refs = extract_refs(text)
        tool_like = frozenset(r for r in refs if r.startswith("scripts.") or r.startswith("src."))
        rel = path.relative_to(repo.resolve()).as_posix()
        records.append(
            SkillRecord(
                relative_path=rel,
                title=_first_heading(text),
                referenced_tool_ids=tuple(sorted(tool_like)),
                char_count=len(text),
            )
        )
        all_refs.update(tool_like)
    return records, all_refs


def _build_recommendations(
    *,
    tool_id: str,
    kind: str,
    workflow_gap: bool,
    cited_in_skills: bool,
    risk_level: str,
    related_skills: Sequence[str],
) -> list[str]:
    out: list[str] = []
    run_hint = f"python -m {tool_id}" if kind != "shell" else f"bash {tool_id}"

    if workflow_gap:
        out.append(
            f"Add `{run_hint}` to `README.md`, `scripts/README.md`, `docs/`, `examples/`, "
            "or `scripts/nightly_pipeline.py` so operators can discover it from scanned surfaces."
        )
    if not cited_in_skills:
        out.append(
            "No `SKILL.md` in the repository cites this entrypoint; add a short example block "
            f"with `{run_hint}` to the skill that owns this workflow."
        )
    else:
        out.append("Already referenced from at least one `SKILL.md`; keep examples in sync with CLI flags.")

    if kind == "src_cli":
        out.append(
            "This is a `src/` CLI module: prefer documenting it next to the subsystem README "
            "or the skill that describes that subsystem."
        )
    if risk_level == "high":
        out.append(
            "High-risk profile (subprocess/network/os-heavy): document safety constraints "
            "and required env vars beside the usage example."
        )
    if related_skills:
        out.append(
            "Related skills (keyword overlap with summary): "
            + ", ".join(f"`{p}`" for p in related_skills[:3])
            + " — consider cross-linking."
        )
    return out


def _related_skills(
    tool_summary: str,
    tool_id: str,
    skills: Sequence[SkillRecord],
    repo: Path,
) -> list[str]:
    tool_blob = f"{tool_id} {tool_summary}".lower()
    tt = _tokens(tool_blob)
    scored: list[tuple[int, str]] = []
    for sk in skills:
        try:
            body = (repo / sk.relative_path).read_text(encoding="utf-8", errors="replace").lower()
        except OSError:
            continue
        st = _tokens(body)
        overlap = len(tt & st)
        if overlap:
            scored.append((overlap, sk.relative_path))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [p for _o, p in scored[:5]]


def build_discovery_report(repo: Path | str) -> DiscoveryReport:
    root = Path(repo).resolve()
    wf = _root_workflow_discovery(root)
    std: Any
    try:
        std = _import_script_tool_discovery(root)
        profiles = _analyze_scripts_lenient(std, root)
    except Exception:  # noqa: BLE001 — optional deep analysis
        profiles = []

    profile_by_script: dict[str, Any] = {}
    for p in profiles:
        mid = f"scripts.{p.name}"
        profile_by_script[mid] = p

    wf_report = wf.build_workflow_gap_report(root)
    deployed = wf_report.deployed_refs
    undeployed_ids = {c.tool_id for c in wf_report.undeployed}

    extract_refs = wf.extract_references_from_text
    skill_rows, skill_refs = _parse_skills(root, extract_refs)

    advice_list: list[ToolIntegrationAdvice] = []
    for cand in wf_report.candidates:
        tid = cand.tool_id
        workflow_gap = tid not in deployed
        cited = tid in skill_refs

        prof = profile_by_script.get(tid)
        caps = tuple(prof.capabilities) if prof else ()
        risk = prof.risk_level if prof else ""
        io_pf = tuple(prof.io_profile) if prof else ()

        related = _related_skills(cand.summary, tid, skill_rows, root) if (workflow_gap or not cited) else []

        recs = _build_recommendations(
            tool_id=tid,
            kind=cand.kind,
            workflow_gap=workflow_gap,
            cited_in_skills=cited,
            risk_level=risk,
            related_skills=related,
        )

        advice_list.append(
            ToolIntegrationAdvice(
                tool_id=tid,
                relative_path=cand.relative_path,
                kind=cand.kind,
                summary=cand.summary,
                workflow_gap=workflow_gap,
                cited_in_skills=cited,
                capabilities=caps,
                risk_level=risk,
                io_profile=io_pf,
                related_skills=tuple(related[:3]),
                recommendations=tuple(recs),
            )
        )

    advice_list.sort(key=lambda x: (not x.workflow_gap, x.tool_id.lower()))

    return DiscoveryReport(
        repo_root=str(root),
        generated_at_utc=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        skills=tuple(skill_rows),
        skill_referenced_tool_ids=frozenset(skill_refs),
        tools=tuple(advice_list),
        workflow_surface_count=len(wf_report.surfaces_scanned),
        workflow_undeployed_count=len(wf_report.undeployed),
    )


def render_markdown(report: DiscoveryReport) -> str:
    lines: list[str] = [
        "# OpenClaw tool discovery report",
        "",
        f"_Generated {report.generated_at_utc}_ · repository `{report.repo_root}`",
        "",
        "## Summary",
        "",
        f"- **Workflow surfaces scanned:** {report.workflow_surface_count}",
        f"- **Tools not on workflow surfaces:** {report.workflow_undeployed_count}",
        f"- **`SKILL.md` files found:** {len(report.skills)}",
        f"- **Distinct tool ids cited from skills:** {len(report.skill_referenced_tool_ids)}",
        "",
        "## Skill inventory",
        "",
    ]
    if not report.skills:
        lines.append("_No `SKILL.md` files found. Add skills (e.g. under `.cursor/skills/.../SKILL.md`) to document agent workflows._")
        lines.append("")
    else:
        for sk in report.skills:
            lines.append(f"### `{sk.relative_path}`")
            lines.append("")
            lines.append(f"- **Title:** {sk.title}")
            lines.append(f"- **Size:** {sk.char_count} characters")
            if sk.referenced_tool_ids:
                lines.append("- **Referenced tools:**")
                for tid in sk.referenced_tool_ids:
                    lines.append(f"  - `{tid}`")
            else:
                lines.append("- **Referenced tools:** _none detected (use `python -m scripts.foo` or `scripts.bar` patterns)._")
            lines.append("")

    lines.extend(
        [
            "## Tools and integration recommendations",
            "",
            "Each row is a discovered entrypoint (``scripts/*.py``, ``scripts/*.sh``, or "
            "``src/**/*.py`` with a ``__main__`` guard). **Workflow gap** means the tool id "
            "does not appear on scanned deployment surfaces (README, docs, examples, nightly, …).",
            "",
        ]
    )

    for t in report.tools:
        if not t.workflow_gap and t.cited_in_skills:
            continue
        lines.append(f"### `{t.tool_id}`")
        lines.append("")
        lines.append(f"- **Path:** `{t.relative_path}` · **Kind:** {t.kind}")
        lines.append(f"- **Summary:** {t.summary}")
        lines.append(
            f"- **Workflow gap:** {'yes' if t.workflow_gap else 'no'} · "
            f"**Cited in skills:** {'yes' if t.cited_in_skills else 'no'}"
        )
        if t.capabilities:
            lines.append(f"- **Inferred capabilities:** {', '.join(t.capabilities)}")
        if t.risk_level:
            lines.append(f"- **Risk (scripts heuristic):** {t.risk_level}")
        if t.io_profile:
            lines.append(f"- **I/O profile:** {', '.join(t.io_profile)}")
        if t.related_skills:
            lines.append(f"- **Lexically related skills:** {', '.join(f'`{p}`' for p in t.related_skills)}")
        lines.append("")
        lines.append("**Recommendations**")
        lines.append("")
        for r in t.recommendations:
            lines.append(f"- {r}")
        lines.append("")

    lines.append("## Fully covered tools")
    lines.append("")
    covered = [t for t in report.tools if not t.workflow_gap and t.cited_in_skills]
    if not covered:
        lines.append("_None — every tool is missing either workflow surfaces or skill citations._")
    else:
        lines.extend(f"- `{t.tool_id}`" for t in sorted(covered, key=lambda x: x.tool_id.lower()))
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Discover OpenClaw tools, compare to workflow surfaces and SKILL.md files, "
            "and print a JSON or Markdown report."
        ),
    )
    p.add_argument(
        "--root",
        default=str(REPO_ROOT),
        help="Repository root (default: inferred from this file).",
    )
    p.add_argument("--format", choices=("markdown", "json"), default="markdown")
    p.add_argument("-o", "--output", help="Write report to this path instead of stdout.")
    return p.parse_args(list(argv) if argv is not None else None)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    root = Path(args.root).resolve()
    _ensure_repo_importable(root)
    report = build_discovery_report(root)
    if args.format == "json":
        body = json.dumps(report.to_dict(), indent=2)
    else:
        body = render_markdown(report)

    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(body, encoding="utf-8")
        print(f"Wrote {out}", file=sys.stderr)
    else:
        sys.stdout.write(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
