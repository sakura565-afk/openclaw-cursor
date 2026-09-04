#!/usr/bin/env python3
"""Discover workspace tools and patterns missing from ``TOOLS.md``.

Scans ``scripts/``, runnable ``src/`` modules, shell helpers, workflow YAML under
``examples/``, and agent prompt templates. Compares each entry against
``TOOLS.md`` (module references, paths, and ``##`` section titles). Writes a
markdown report to ``.learnings/tool_discovery.md``.

Run::

    python -m src.self_improvement.tool_discovery
    python -m src.self_improvement.tool_discovery --root /path/to/repo
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

LEARNINGS_DIR = ".learnings"
OUTPUT_NAME = "tool_discovery.md"
TOOLS_MD_NAME = "TOOLS.md"

_SKIP_TOOLS_MD_SECTIONS = frozenset(
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
        "tools",
    }
)

_HEADING_SLUG = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class DiscoveryFinding:
    """A tool or pattern worth documenting in ``TOOLS.md``."""

    name: str
    path: str
    kind: str
    summary: str
    value: str

    def to_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "path": self.path,
            "kind": self.kind,
            "summary": self.summary,
            "value": self.value,
        }


def _repo_root(start: Path | None = None) -> Path:
    if start is not None:
        return start.resolve()
    env = __import__("os").environ.get("TOOL_DISCOVERY_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    return Path(__file__).resolve().parents[2]


def _load_workflow_scanner():
    """Load repository-root ``tool_discovery.py`` (candidate discovery + ref extraction)."""

    repo = Path(__file__).resolve().parents[2]
    mod_path = repo / "tool_discovery.py"
    name = "_openclaw_workflow_tool_discovery"
    spec = importlib.util.spec_from_file_location(name, mod_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load workflow scanner from {mod_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _slug(text: str) -> str:
    s = _HEADING_SLUG.sub("-", text.strip().lower())
    return s.strip("-") or "tool"


def _normalize_token(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def _tokens_for_candidate(tool_id: str, relative_path: str) -> set[str]:
    tokens: set[str] = {tool_id, tool_id.lower(), _normalize_token(tool_id)}
    stem = Path(relative_path).stem
    tokens.update({stem, stem.lower(), _normalize_token(stem), _slug(stem)})
    if tool_id.startswith("scripts."):
        tokens.add(_normalize_token(tool_id.split(".", 1)[1]))
    if tool_id.startswith("src."):
        tokens.add(_normalize_token(tool_id.replace(".", "")))
    if relative_path:
        tokens.add(relative_path.replace("\\", "/").lower())
        tokens.add(_normalize_token(relative_path))
    return {t for t in tokens if t}


def collect_tools_md_registry(tools_md: Path, scanner: Any) -> tuple[set[str], set[str]]:
    """
    Return (reference_ids, section_slugs) parsed from ``TOOLS.md``.

    ``reference_ids`` uses the same ids as the workflow scanner (``scripts.foo``, paths, …).
    ``section_slugs`` holds normalized ``##`` headings for fuzzy title matching.
    """

    refs: set[str] = set()
    slugs: set[str] = set()
    if not tools_md.is_file():
        return refs, slugs
    try:
        text = tools_md.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return refs, slugs

    refs.update(scanner.extract_references_from_text(text))

    for block in re.split(r"^##\s+", text, flags=re.MULTILINE)[1:]:
        title = block.splitlines()[0].strip() if block.splitlines() else ""
        if not title:
            continue
        slug = _slug(title)
        if slug in _SKIP_TOOLS_MD_SECTIONS:
            continue
        slugs.add(slug)
        slugs.add(_normalize_token(title))
        # "Queue Monitor" may document scripts.queue_monitor
        slug_underscore = slug.replace("-", "_")
        slugs.add(slug_underscore)
        refs.add(f"scripts.{slug_underscore}")
        refs.add(slug_underscore)

    return refs, slugs


def _is_documented(
    tool_id: str,
    relative_path: str,
    refs: set[str],
    section_slugs: set[str],
) -> bool:
    if tool_id in refs or relative_path in refs:
        return True
    tokens = _tokens_for_candidate(tool_id, relative_path)
    if tokens.intersection(refs):
        return True
    norm_tokens = {_normalize_token(t) for t in tokens}
    if norm_tokens.intersection(section_slugs):
        return True
    for slug in section_slugs:
        if slug and (slug in norm_tokens or any(slug in nt for nt in norm_tokens if len(nt) >= 4)):
            return True
    return False


def _profile_map(repo: Path) -> dict[str, Any]:
    try:
        from scripts.tool_discovery import (
            ToolProfile,
            analyze_scripts,
            build_examples,
            discover_script_paths,
            enrich_dependency_graph,
            extract_cli_commands,
            extract_description,
            extract_functions,
            extract_imports,
            infer_capabilities,
            infer_io_profile,
            infer_risk_level,
        )
    except ImportError:
        return {}

    try:
        profiles = analyze_scripts(repo)
    except Exception:
        profiles = []
        for path in discover_script_paths(repo):
            try:
                source = path.read_text(encoding="utf-8")
                tree = ast.parse(source, filename=str(path))
            except Exception:
                continue
            name = path.stem
            imports = extract_imports(tree)
            functions = extract_functions(tree)
            commands = extract_cli_commands(tree)
            description = extract_description(tree)
            profiles.append(
                ToolProfile(
                    name=name,
                    path=path.relative_to(repo),
                    description=description,
                    imports=imports,
                    functions=functions,
                    commands=commands,
                    capabilities=infer_capabilities(name, description, commands, functions),
                    risk_level=infer_risk_level(imports, commands),
                    io_profile=infer_io_profile(imports, source),
                )
            )
        if profiles:
            enrich_dependency_graph(profiles)
            for profile in profiles:
                profile.examples = build_examples(profile)

    return {p.name: p for p in profiles}


def _infer_value(
    *,
    kind: str,
    tool_id: str,
    summary: str,
    profile: Any | None,
) -> str:
    caps: list[str] = list(profile.capabilities[:4]) if profile and profile.capabilities else []
    if kind == "shell":
        return (
            "Shell entrypoint for bootstrap, health checks, or media ops without a Python wrapper; "
            "documenting it gives agents and cron jobs a stable, copy-paste invocation."
        )
    if kind == "workflow_pattern":
        return (
            "Reusable workflow/pipeline layout under examples/; adding it to TOOLS.md helps agents "
            "compose proven multi-step flows instead of inventing YAML from scratch."
        )
    if kind == "prompt_pattern":
        return (
            "Curated agent prompt template; listing it in TOOLS.md steers model behavior for recurring "
            "task shapes (reviews, multi-step work, error recovery)."
        )
    if kind == "src_cli":
        return (
            f"Packaged CLI under ``{tool_id}`` with a ``__main__`` guard; valuable for features kept "
            "in src/ rather than scripts/ — document so operators use ``python -m …`` consistently."
        )
    if caps:
        joined = ", ".join(caps)
        risk = getattr(profile, "risk_level", None)
        extra = (
            f" Static analysis marks it **{risk}** risk — note guardrails in TOOLS.md before autonomous use."
            if risk == "high"
            else ""
        )
        return (
            f"Covers {joined}; reduces one-off scripting by reusing ``python -m {tool_id}`` "
            f"for operational work.{extra}"
        )
    return (
        f"Runnable automation ({summary[:120] or tool_id}) exists in the tree but is not indexed in "
        "TOOLS.md; documenting it avoids rediscovering the same entrypoint every session."
    )


def _display_name(tool_id: str, relative_path: str) -> str:
    stem = Path(relative_path).stem
    if tool_id.startswith("scripts."):
        return stem
    if tool_id.startswith("src."):
        return tool_id.removeprefix("src.")
    return stem or tool_id


def discover_tool_findings(repo: Path, scanner: Any) -> list[DiscoveryFinding]:
    tools_md = repo / TOOLS_MD_NAME
    refs, slugs = collect_tools_md_registry(tools_md, scanner)
    profiles = _profile_map(repo)
    findings: list[DiscoveryFinding] = []

    for candidate in scanner.discover_tool_candidates(repo):
        if _is_documented(candidate.tool_id, candidate.relative_path, refs, slugs):
            continue
        name = _display_name(candidate.tool_id, candidate.relative_path)
        prof = profiles.get(name) if candidate.kind == "script_module" else None
        summary = candidate.summary
        if prof and prof.description and prof.description != "No module docstring available.":
            summary = prof.description
        findings.append(
            DiscoveryFinding(
                name=name,
                path=candidate.relative_path,
                kind=candidate.kind,
                summary=summary,
                value=_infer_value(
                    kind=candidate.kind,
                    tool_id=candidate.tool_id,
                    summary=summary,
                    profile=prof,
                ),
            )
        )
    return findings


def discover_pattern_findings(repo: Path, scanner: Any) -> list[DiscoveryFinding]:
    """YAML workflows and prompt templates not referenced in ``TOOLS.md``."""

    tools_md = repo / TOOLS_MD_NAME
    refs, slugs = collect_tools_md_registry(tools_md, scanner)
    findings: list[DiscoveryFinding] = []

    examples = repo / "examples"
    if examples.is_dir():
        for path in sorted(examples.glob("*.yaml")) + sorted(examples.glob("*.yml")):
            rel = path.relative_to(repo).as_posix()
            token = path.stem
            if _is_documented(token, rel, refs, slugs):
                continue
            summary = f"Workflow example YAML (`{path.name}`)."
            try:
                head = path.read_text(encoding="utf-8", errors="replace")[:400]
                if "description:" in head.lower():
                    m = re.search(r"(?m)^description:\s*(.+)$", head)
                    if m:
                        summary = m.group(1).strip().strip("'\"")[:200]
            except OSError:
                pass
            findings.append(
                DiscoveryFinding(
                    name=path.stem,
                    path=rel,
                    kind="workflow_pattern",
                    summary=summary,
                    value=_infer_value(
                        kind="workflow_pattern",
                        tool_id=token,
                        summary=summary,
                        profile=None,
                    ),
                )
            )

    prompts = repo / "prompts" / "templates"
    if prompts.is_dir():
        for path in sorted(prompts.glob("*.md")):
            if path.name.startswith("_"):
                continue
            rel = path.relative_to(repo).as_posix()
            token = path.stem
            if _is_documented(token, rel, refs, slugs):
                continue
            summary = f"Agent prompt template (`{path.name}`)."
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
                for line in text.splitlines()[:12]:
                    if line.strip() and not line.strip().startswith("#"):
                        summary = line.strip()[:200]
                        break
            except OSError:
                pass
            findings.append(
                DiscoveryFinding(
                    name=path.stem,
                    path=rel,
                    kind="prompt_pattern",
                    summary=summary,
                    value=_infer_value(
                        kind="prompt_pattern",
                        tool_id=token,
                        summary=summary,
                        profile=None,
                    ),
                )
            )

    return findings


def discover_undocumented(repo: Path) -> list[DiscoveryFinding]:
    scanner = _load_workflow_scanner()
    combined = discover_tool_findings(repo, scanner) + discover_pattern_findings(repo, scanner)
    return sorted(combined, key=lambda f: (f.kind, f.name.lower()))


def render_markdown(repo: Path, findings: Sequence[DiscoveryFinding]) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    tools_md = repo / TOOLS_MD_NAME
    lines = [
        "# Tool discovery — not yet in TOOLS.md",
        "",
        f"_Generated {now}_",
        "",
        "## Scope",
        "",
        f"- **Repository:** `{repo.resolve()}`",
        f"- **Registry:** `{tools_md.relative_to(repo)}` "
        + ("(present)" if tools_md.is_file() else "_(missing — all scanned items are undocumented)_"),
        f"- **Undocumented findings:** **{len(findings)}**",
        "",
        "Scanned: `scripts/*.py`, `scripts/*.sh`, `src/**/*.py` with `__main__`, "
        "`examples/*.yaml`, `prompts/templates/*.md`.",
        "",
    ]

    if not findings:
        lines.extend(
            [
                "## Findings",
                "",
                "_Every scanned entrypoint appears to be covered in TOOLS.md "
                "(by module id, path, or section title)._",
                "",
            ]
        )
    else:
        lines.append("## Findings")
        lines.append("")
        for item in findings:
            lines.extend(
                [
                    f"### {item.name}",
                    "",
                    f"- **Path:** `{item.path}`",
                    f"- **Kind:** `{item.kind}`",
                    f"- **What it does:** {item.summary}",
                    f"- **Why it's valuable:** {item.value}",
                    "",
                ]
            )

    lines.extend(
        [
            "---",
            "",
            "*Produced by `python -m src.self_improvement.tool_discovery`*",
            "",
        ]
    )
    return "\n".join(lines)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scan the workspace for tools/patterns missing from TOOLS.md.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Repository root (default: TOOL_DISCOVERY_ROOT, else parent of src/).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help=f"Output markdown path (default: <root>/{LEARNINGS_DIR}/{OUTPUT_NAME}).",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    repo = _repo_root(args.root)
    out = (args.output or (repo / LEARNINGS_DIR / OUTPUT_NAME)).resolve()

    findings = discover_undocumented(repo)
    body = render_markdown(repo, findings)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(body, encoding="utf-8")
    print(f"Wrote {out.as_posix()} ({len(findings)} finding(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
