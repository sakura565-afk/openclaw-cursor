#!/usr/bin/env python3
"""
Scan ``src/`` and ``scripts/`` for runnable tools, compare against workflow
deployment surfaces (docs, nightly pipeline, examples), and report gaps.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Literal, Sequence

ToolKind = Literal["script_module", "src_cli", "shell"]

DEFAULT_SKIP_DIR_NAMES: frozenset[str] = frozenset(
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

# ``scripts.foo`` / ``src.bar.baz`` style references in prose or code.
_MODULE_REF = re.compile(
    r"\b((?:scripts|src)\.(?:[a-zA-Z_][a-zA-Z0-9_]*)(?:\.[a-zA-Z_][a-zA-Z0-9_]*)*)\b"
)
# ``python -m scripts.foo`` (also ``python3``, ``py``).
_PYTHON_M = re.compile(
    r"(?:^|[\s`'\"])(?:python3?|py)\s+-m\s+((?:scripts|src)(?:\.[a-zA-Z_][a-zA-Z0-9_]*)+)",
    re.MULTILINE,
)
_SHELL_SH = re.compile(r"\b(scripts/[a-zA-Z0-9_.-]+\.sh)\b")
_SCRIPTS_PY_PATH = re.compile(r"\bscripts/([a-zA-Z_][a-zA-Z0-9_]*)\.py\b")
_SRC_PY_PATH = re.compile(r"\bsrc/([a-zA-Z0-9_./-]+)\.py\b")


def _relative_under_repo(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)


def _module_path_from_file(repo: Path, py_file: Path) -> str | None:
    """Return dotted module id (``scripts.foo`` or ``src.a.b``) for a file under repo."""
    try:
        rel = py_file.resolve().relative_to(repo.resolve())
    except ValueError:
        return None
    parts = rel.parts
    if not parts or parts[0] not in ("scripts", "src"):
        return None
    if rel.suffix != ".py":
        return None
    body = ".".join(parts[:-1] + (rel.stem,))
    return body


def _file_has_main_guard(py_file: Path) -> bool:
    try:
        text = py_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return "if __name__" in text and "__main__" in text


def _first_line_docstring(py_file: Path) -> str:
    try:
        text = py_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    lines = text.splitlines()
    if not lines:
        return ""
    i = 0
    if lines[0].startswith("#!"):
        i = 1
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i >= len(lines) or not lines[i].strip().startswith(('"""', "'''")):
        return ""
    quote = lines[i].strip()[:3]
    first = lines[i].strip()
    if first.startswith(quote) and len(first) > 3 and first.endswith(quote) and first.count(quote[:1]) == 2:
        return first[3:-3].strip()
    buf: list[str] = []
    if first.startswith(quote):
        buf.append(first[len(quote) :])
    i += 1
    while i < len(lines):
        line = lines[i]
        if quote in line:
            before, _, _after = line.partition(quote)
            buf.append(before)
            break
        buf.append(line)
        i += 1
    joined = " ".join(s.strip() for s in buf if s.strip())
    return (joined[:200] + "…") if len(joined) > 200 else joined


def discover_tool_candidates(
    repo_root: Path | str,
    *,
    skip_dir_names: frozenset[str] = DEFAULT_SKIP_DIR_NAMES,
) -> list["ToolCandidate"]:
    root = Path(repo_root).resolve()
    found: list[ToolCandidate] = []

    scripts_dir = root / "scripts"
    if scripts_dir.is_dir():
        for path in sorted(scripts_dir.glob("*.py")):
            if path.name == "__init__.py":
                continue
            mid = _module_path_from_file(root, path)
            if not mid:
                continue
            found.append(
                ToolCandidate(
                    tool_id=mid,
                    relative_path=path.relative_to(root).as_posix(),
                    kind="script_module",
                    summary=_first_line_docstring(path) or "Python script under scripts/.",
                )
            )
        for path in sorted(scripts_dir.glob("*.sh")):
            rel = path.relative_to(root).as_posix()
            found.append(
                ToolCandidate(
                    tool_id=rel,
                    relative_path=rel,
                    kind="shell",
                    summary="Shell script under scripts/.",
                )
            )

    src_dir = root / "src"
    if src_dir.is_dir():
        for path in sorted(src_dir.rglob("*.py")):
            if any(part in skip_dir_names for part in path.parts):
                continue
            if path.name == "__init__.py":
                continue
            if not _file_has_main_guard(path):
                continue
            mid = _module_path_from_file(root, path)
            if not mid:
                continue
            found.append(
                ToolCandidate(
                    tool_id=mid,
                    relative_path=path.relative_to(root).as_posix(),
                    kind="src_cli",
                    summary=_first_line_docstring(path) or "Python module with __main__ under src/.",
                )
            )

    seen: set[str] = set()
    unique: list[ToolCandidate] = []
    for c in found:
        if c.tool_id in seen:
            continue
        seen.add(c.tool_id)
        unique.append(c)
    return sorted(unique, key=lambda x: x.tool_id.lower())


def extract_references_from_text(text: str) -> set[str]:
    refs: set[str] = set()
    for match in _MODULE_REF.finditer(text):
        refs.add(match.group(1))
    for match in _PYTHON_M.finditer(text):
        refs.add(match.group(1))
    for match in _SHELL_SH.finditer(text):
        refs.add(match.group(1))
    for match in _SCRIPTS_PY_PATH.finditer(text):
        refs.add(f"scripts.{match.group(1)}")
    for match in _SRC_PY_PATH.finditer(text):
        refs.add(f"src.{match.group(1).replace('/', '.')}")
    return refs


def _is_text_candidate(path: Path) -> bool:
    suffix = path.suffix.lower()
    return suffix in {".md", ".py", ".yaml", ".yml", ".json", ".txt", ".toml", ".ini", ".sh", ""}


def iter_default_deployment_surfaces(repo_root: Path) -> Iterator[Path]:
    """Operator-facing and automation files that define what is wired into workflows."""

    root = repo_root.resolve()
    singles = [
        root / "README.md",
        root / "scripts" / "README.md",
        root / "scripts" / "nightly_pipeline.py",
        root / "scripts" / "auto_reflection.py",
    ]
    for p in singles:
        if p.is_file():
            yield p

    docs = root / "docs"
    if docs.is_dir():
        yield from sorted(p for p in docs.rglob("*") if p.is_file() and _is_text_candidate(p))

    examples = root / "examples"
    if examples.is_dir():
        yield from sorted(p for p in examples.rglob("*") if p.is_file() and _is_text_candidate(p))

    gh = root / ".github"
    if gh.is_dir():
        yield from sorted(p for p in gh.rglob("*") if p.is_file() and _is_text_candidate(p))


def collect_deployed_references(
    repo_root: Path | str,
    extra_surfaces: Sequence[Path | str] | None = None,
) -> tuple[set[str], dict[str, list[str]]]:
    """
    Return (reference_ids, source_index) where each reference appears in at least one surface file.
    ``source_index`` maps reference -> list of relative surface paths mentioning it.
    """

    root = Path(repo_root).resolve()
    surfaces: list[Path] = []
    seen_paths: set[Path] = set()

    def _add_surface(p: Path) -> None:
        resolved = p.resolve()
        if resolved.is_file() and resolved not in seen_paths:
            seen_paths.add(resolved)
            surfaces.append(resolved)

    for p in iter_default_deployment_surfaces(root):
        _add_surface(p)
    if extra_surfaces:
        for raw in extra_surfaces:
            p = Path(raw)
            if not p.is_absolute():
                p = (root / p).resolve()
            _add_surface(p)

    refs: set[str] = set()
    index: dict[str, list[str]] = {}

    for surf in surfaces:
        try:
            text = surf.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        found_here = extract_references_from_text(text)
        rel_surf = _relative_under_repo(surf, root)
        for r in found_here:
            refs.add(r)
            index.setdefault(r, []).append(rel_surf)

    for key in index:
        index[key] = sorted(set(index[key]))

    return refs, index


@dataclass(frozen=True)
class ToolCandidate:
    tool_id: str
    relative_path: str
    kind: ToolKind
    summary: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_id": self.tool_id,
            "path": self.relative_path,
            "kind": self.kind,
            "summary": self.summary,
        }


@dataclass
class WorkflowGapReport:
    repo_root: str
    generated_at_utc: str
    surfaces_scanned: tuple[str, ...]
    deployed_references: frozenset[str]
    candidates: tuple[ToolCandidate, ...] = ()
    undeployed: tuple[ToolCandidate, ...] = ()
    reference_sources: dict[str, list[str]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo_root": self.repo_root,
            "generated_at_utc": self.generated_at_utc,
            "surfaces_scanned": list(self.surfaces_scanned),
            "deployed_reference_count": len(self.deployed_refs),
            "candidates": [c.to_dict() for c in self.candidates],
            "undeployed": [c.to_dict() for c in self.undeployed],
            "reference_index": dict(sorted(self.reference_sources.items())),
        }

    @property
    def deployed_refs(self) -> frozenset[str]:
        return self.deployed_references


def build_workflow_gap_report(
    repo_root: Path | str,
    *,
    extra_surfaces: Sequence[Path | str] | None = None,
) -> WorkflowGapReport:
    root = Path(repo_root).resolve()
    resolved_surfaces: list[Path] = []
    seen_surf: set[Path] = set()
    for p in iter_default_deployment_surfaces(root):
        rp = p.resolve()
        if rp not in seen_surf:
            seen_surf.add(rp)
            resolved_surfaces.append(p)
    if extra_surfaces:
        for raw in extra_surfaces:
            p = Path(raw)
            if not p.is_absolute():
                p = (root / p).resolve()
            if p.is_file():
                rp = p.resolve()
                if rp not in seen_surf:
                    seen_surf.add(rp)
                    resolved_surfaces.append(p)

    deployed, ref_index = collect_deployed_references(root, extra_surfaces=extra_surfaces)
    candidates = tuple(discover_tool_candidates(root))

    undeployed = tuple(c for c in candidates if c.tool_id not in deployed)

    return WorkflowGapReport(
        repo_root=str(root),
        generated_at_utc=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        surfaces_scanned=tuple(sorted({_relative_under_repo(s, root) for s in resolved_surfaces})),
        deployed_references=frozenset(deployed),
        candidates=candidates,
        undeployed=undeployed,
        reference_sources=dict(ref_index),
    )


def render_workflow_gap_markdown(report: WorkflowGapReport) -> str:
    lines = [
        "# Workflow tool discovery",
        "",
        f"_Generated {report.generated_at_utc}_",
        "",
        "## Scope",
        "",
        f"- Repository: `{report.repo_root}`",
        f"- Candidate tools: **{len(report.candidates)}** (``scripts/*.py``, ``scripts/*.sh``, "
        "``src/**/*.py`` with a ``__main__`` guard)",
        f"- Deployment surfaces scanned: **{len(report.surfaces_scanned)}** files",
        f"- Distinct references found in those surfaces: **{len(report.deployed_refs)}**",
        f"- **Undeployed** (not mentioned on any surface): **{len(report.undeployed)}**",
        "",
        "## Surfaces",
        "",
    ]
    if report.surfaces_scanned:
        lines.extend(f"- `{p}`" for p in report.surfaces_scanned)
    else:
        lines.append("_No deployment surfaces found (add README.md, docs/, examples/, etc.)._")
    lines.extend(["", "## Available but not on deployment surfaces", ""])

    if not report.undeployed:
        lines.append(
            "_Every discovered entrypoint is referenced in at least one scanned surface "
            "(module id or ``scripts/…`` / ``src/…`` path form)._"
        )
    else:
        lines.append(
            "These paths exist under ``src/`` or ``scripts/`` but their ids do not appear in "
            "the scanned operator docs, examples, nightly pipeline, or related automation stubs."
        )
        lines.append("")
        for c in sorted(report.undeployed, key=lambda x: (x.kind, x.tool_id.lower())):
            lines.append(f"### `{c.tool_id}`")
            lines.append("")
            lines.append(f"- **Path:** `{c.relative_path}`")
            lines.append(f"- **Kind:** {c.kind}")
            lines.append(f"- **Summary:** {c.summary}")
            lines.append("")

    lines.extend(
        [
            "## How to integrate",
            "",
            "- Add a short operator note to `README.md` or `scripts/README.md` with a "
            "`python -m …` example, **or**",
            "- Wire the tool into `scripts/nightly_pipeline.py`, `examples/`, or another scanned surface.",
            "",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def render_workflow_gap_json(report: WorkflowGapReport) -> str:
    return json.dumps(report.to_dict(), indent=2)


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Discover Python and shell tools under src/ and scripts/, compare to deployment "
            "surfaces (README, docs, examples, nightly pipeline), and list gaps."
        ),
    )
    parser.add_argument(
        "--root",
        default=".",
        help="Repository root (default: current directory).",
    )
    parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        help="Report serialization format.",
    )
    parser.add_argument(
        "--output",
        "-o",
        metavar="PATH",
        help="Write report to this file instead of printing to stdout.",
    )
    parser.add_argument(
        "--extra-surface",
        action="append",
        default=[],
        metavar="PATH",
        help="Additional file to treat as a deployment surface (repeatable).",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    root = Path(args.root).resolve()
    extras: list[str] = list(args.extra_surface or [])
    report = build_workflow_gap_report(root, extra_surfaces=extras)

    if args.format == "json":
        body = render_workflow_gap_json(report)
    else:
        body = render_workflow_gap_markdown(report)

    out = args.output
    if out:
        out_path = Path(out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(body, encoding="utf-8")
        print(f"Wrote {out_path}", file=sys.stderr)
    else:
        sys.stdout.write(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
