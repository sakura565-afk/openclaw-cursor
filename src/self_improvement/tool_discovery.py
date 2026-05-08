"""Tool and capability discovery pipeline for self-improvement.

This module implements a **multi-stage discovery pipeline** that helps an autonomous
agent (or a human maintainer) find:

- Third-party Python capabilities implied by ``import`` statements versus what is
  declared in dependency files.
- External binaries invoked via ``subprocess`` that may be missing from ``PATH``.
- HTTP / async / CLI integration patterns worth documenting or extending.
- Gaps between documentation (README, ``docs/``) and what the codebase actually uses.

The pipeline is designed to be **deterministic** (no network calls by default),
**stdlib-first** (optional dependency checks use :mod:`importlib`), and **extensible**
(subclass :class:`ToolDiscoveryPipeline` or pass custom roots / file readers).

Stages (see :class:`PipelineStage`):

1. **code_analysis** — Walk Python sources under configurable roots; use :mod:`ast`
   to collect imports, subprocess calls, and notable API usage.
2. **documentation_review** — Scan Markdown for install commands, URLs, and
   capability keywords; correlate with code signals.
3. **experimentation** — Probe the local environment (import resolution,
   ``shutil.which``) to validate hypotheses from earlier stages.
4. **synthesis** — Merge evidence into ranked :class:`DiscoveryCandidate` records
   with suggested follow-up actions.

Typical usage::

    from pathlib import Path
    from src.self_improvement.tool_discovery import ToolDiscoveryPipeline

    pipeline = ToolDiscoveryPipeline(root_dir=Path("."))
    report = pipeline.run()
    path = pipeline.save_report(report)
    print(path)

CLI::

    python -m src.self_improvement.tool_discovery --root-dir . discover
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import shutil
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Sequence, Set, Tuple

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


class CandidateKind(str, Enum):
    """High-level classification for a discovery candidate."""

    DEPENDENCY_GAP = "dependency_gap"
    MISSING_BINARY = "missing_binary"
    DOC_CODE_DRIFT = "doc_code_drift"
    CAPABILITY_PATTERN = "capability_pattern"
    EXPERIMENT_NOTE = "experiment_note"


class PipelineStage(str, Enum):
    """Ordered stages executed by :meth:`ToolDiscoveryPipeline.run`."""

    CODE_ANALYSIS = "code_analysis"
    DOCUMENTATION_REVIEW = "documentation_review"
    EXPERIMENTATION = "experimentation"
    SYNTHESIS = "synthesis"


@dataclass
class DiscoveryCandidate:
    """A single actionable or informational discovery item."""

    kind: CandidateKind
    title: str
    summary: str
    confidence: float
    evidence: Dict[str, Any] = field(default_factory=dict)
    suggested_actions: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["kind"] = self.kind.value
        return payload


@dataclass
class StageResult:
    """Structured output from one pipeline stage."""

    stage: PipelineStage
    status: str
    details: Dict[str, Any] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "stage": self.stage.value,
            "status": self.status,
            "details": self.details,
            "notes": self.notes,
        }


@dataclass
class DiscoveryReport:
    """Full report returned by :meth:`ToolDiscoveryPipeline.run`."""

    generated_at: str
    root_dir: str
    candidates: List[DiscoveryCandidate]
    stages: List[StageResult]
    summary: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "root_dir": self.root_dir,
            "candidates": [c.as_dict() for c in self.candidates],
            "stages": [s.as_dict() for s in self.stages],
            "summary": self.summary,
        }


# ---------------------------------------------------------------------------
# Helpers: stdlib, requirements parsing, AST
# ---------------------------------------------------------------------------


def _utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _stdlib_top_level_names() -> Set[str]:
    """Return the set of standard-library top-level module names when available."""
    names = getattr(sys, "stdlib_module_names", None)
    if names is not None:
        return set(names)
    # Fallback for older interpreters (subset; conservative unknown handling).
    return {
        "abc",
        "argparse",
        "ast",
        "asyncio",
        "collections",
        "dataclasses",
        "datetime",
        "enum",
        "functools",
        "glob",
        "importlib",
        "io",
        "itertools",
        "json",
        "logging",
        "math",
        "os",
        "pathlib",
        "re",
        "shutil",
        "subprocess",
        "sys",
        "tempfile",
        "typing",
        "unittest",
        "urllib",
        "uuid",
        "warnings",
        "zipfile",
    }


_STDLIB = _stdlib_top_level_names()


def top_level_module_name(module: str) -> str:
    """Return the top-level segment for dotted imports (e.g. ``urllib.parse`` → ``urllib``)."""
    return module.split(".", 1)[0].strip()


def parse_requirements_names(requirements_path: Path) -> Set[str]:
    """Extract normalized distribution names from a ``requirements.txt``-style file."""
    if not requirements_path.is_file():
        return set()
    names: Set[str] = set()
    for raw in requirements_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # Strip extras and version specifiers coarsely.
        token = line.split()[0]
        token = token.split("[", 1)[0]
        token = re.split(r"[<>=!~]", token, maxsplit=1)[0].strip()
        if token:
            names.add(token.lower().replace("-", "_"))
    return names


# Map common PyPI distribution names to import roots (minimal heuristic table).
_DIST_IMPORT_ALIASES: Dict[str, Set[str]] = {
    "beautifulsoup4": {"bs4"},
    "pillow": {"PIL"},
    "pyyaml": {"yaml"},
    "python_frontmatter": {"frontmatter"},
    "markdown2": {"markdown2"},
}


def requirement_matches_import(req_normalized: str, import_top: str) -> bool:
    """Heuristic: does a requirements.txt token likely cover this import root?"""
    imp = import_top.lower()
    if req_normalized == imp:
        return True
    for dist, roots in _DIST_IMPORT_ALIASES.items():
        if req_normalized == dist.replace("-", "_") and import_top in roots:
            return True
    return False


class _CodeAnalysisVisitor(ast.NodeVisitor):
    """Collect imports, subprocess-related calls, and integration patterns."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.imports: List[Tuple[str, Optional[str]]] = []
        self.subprocess_commands: List[List[str]] = []
        self.http_hits: List[str] = []
        self.todo_markers: List[str] = []

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.imports.append((alias.name, alias.asname))
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        mod = node.module or ""
        for alias in node.names:
            self.imports.append((f"{mod}.{alias.name}" if mod else alias.name, alias.asname))
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        cmd = _extract_subprocess_command(node)
        if cmd:
            self.subprocess_commands.append(cmd)

        # HTTP client libraries (narrow — avoid flagging dict.get, Path.open, etc.).
        if isinstance(node.func, ast.Attribute):
            attr = node.func.attr
            if attr in {"get", "post", "put", "delete", "request", "Session"}:
                base = node.func.value
                if isinstance(base, ast.Name) and base.id in {"requests", "httpx", "aiohttp", "urllib3"}:
                    self.http_hits.append(f"{base.id}.{attr}")
        self.generic_visit(node)


def _extract_subprocess_command(node: ast.Call) -> Optional[List[str]]:
    """If this is a subprocess call with a constant argv, return the argv strings."""
    func = node.func
    name = ""
    if isinstance(func, ast.Attribute):
        name = func.attr
    elif isinstance(func, ast.Name):
        name = func.id

    if name not in {"run", "call", "check_call", "check_output", "Popen"}:
        return None

    args = node.args
    keywords = {kw.arg: kw.value for kw in node.keywords if kw.arg}

    first: Optional[ast.AST] = None
    if args:
        first = args[0]
    elif "args" in keywords:
        first = keywords["args"]

    if first is None:
        return None

    if isinstance(first, (ast.List, ast.Tuple)):
        out: List[str] = []
        for elt in first.elts:
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                out.append(elt.value)
            else:
                return None
        return out if out else None

    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        # Single shell string — skip structured extraction.
        return None

    return None


def analyze_python_file(path: Path) -> Dict[str, Any]:
    """Parse a Python file and return structured scan results."""
    text = path.read_text(encoding="utf-8", errors="replace")
    todos = []
    for line in text.splitlines():
        if "TODO" in line.upper() or "FIXME" in line.upper():
            stripped = line.strip()
            if len(stripped) < 200:
                todos.append(stripped)

    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError as exc:
        return {
            "path": str(path),
            "error": str(exc),
            "imports": [],
            "subprocess_commands": [],
            "http_hits": [],
            "todo_markers": todos,
        }

    visitor = _CodeAnalysisVisitor(path)
    visitor.visit(tree)
    visitor.todo_markers.extend(todos)

    imports_flat = []
    for full, _ in visitor.imports:
        imports_flat.append(top_level_module_name(full))

    return {
        "path": str(path),
        "imports": sorted(set(imports_flat)),
        "subprocess_commands": visitor.subprocess_commands,
        "http_hits": visitor.http_hits,
        "todo_markers": visitor.todo_markers,
    }


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


def iter_python_files(roots: Sequence[Path], *, ignore_dirs: Set[str]) -> Iterator[Path]:
    """Yield ``*.py`` files under roots, skipping common noise directories."""
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            parts = set(path.parts)
            if parts & ignore_dirs:
                continue
            if any(p in ignore_dirs for p in path.parts):
                continue
            yield path


def iter_markdown_files(root: Path, *, ignore_dirs: Set[str]) -> Iterator[Path]:
    """Yield Markdown files at repo root and under ``docs/``."""
    candidates = [root / "README.md", root / "readme.md"]
    docs = root / "docs"
    if docs.is_dir():
        candidates.extend(sorted(docs.rglob("*.md")))
    for furniture_readme in root.glob("**/README.md"):
        if "furniture_sales_database" in furniture_readme.parts:
            candidates.append(furniture_readme)

    seen: Set[Path] = set()
    for path in candidates:
        try:
            rp = path.resolve()
        except OSError:
            rp = path
        if rp in seen or not path.is_file():
            continue
        if any(p in ignore_dirs for p in path.parts):
            continue
        seen.add(rp)
        yield path


INSTALL_RE = re.compile(
    r"(pip|pip3|uv\s+pip|python\s+-m\s+pip)\s+install\s+([a-zA-Z0-9_\-\[\]\.,]+)",
    re.IGNORECASE,
)
BACKTICK_RE = re.compile(r"`([^`]+)`")


class ToolDiscoveryPipeline:
    """Discover tools, APIs, and integration capabilities from code and docs.

    Parameters
    ----------
    root_dir:
        Repository root used for dependency files and documentation discovery.
    scan_roots:
        Directories searched for Python modules (default: ``src``, ``scripts``).
    requirements_paths:
        Files checked for declared dependencies (default: ``requirements.txt``).
    ignore_dir_names:
        Directory base names skipped during filesystem walks (e.g. ``.git``, ``venv``).
    which_fn:
        Injectable resolver for external binaries (defaults to :func:`shutil.which`).
    import_probe:
        Callable ``(top_level_module) -> bool`` returning True if import resolves.
        Defaults to :func:`_default_import_probe`.
    """

    def __init__(
        self,
        root_dir: Path | str | None = None,
        *,
        scan_roots: Optional[Sequence[str]] = None,
        requirements_paths: Optional[Sequence[str]] = None,
        ignore_dir_names: Optional[Sequence[str]] = None,
        which_fn: Optional[Callable[[str], Optional[str]]] = None,
        import_probe: Optional[Callable[[str], bool]] = None,
    ) -> None:
        self.root_dir = Path(root_dir or Path.cwd()).resolve()
        self.scan_roots = [Path(self.root_dir / r) for r in (scan_roots or ("src", "scripts"))]
        self.requirements_paths = [self.root_dir / p for p in (requirements_paths or ("requirements.txt",))]
        self.ignore_dirs = set(ignore_dir_names or {".git", ".venv", "venv", "node_modules", "__pycache__"})
        self.which_fn = which_fn or shutil.which
        self.import_probe = import_probe or _default_import_probe

    def run(self) -> DiscoveryReport:
        """Execute all stages and return a :class:`DiscoveryReport`."""
        stages: List[StageResult] = []

        code_result = self._stage_code_analysis()
        stages.append(code_result)

        doc_result = self._stage_documentation_review(code_result.details)
        stages.append(doc_result)

        exp_result = self._stage_experimentation(code_result.details, doc_result.details)
        stages.append(exp_result)

        candidates = self._stage_synthesis(code_result.details, doc_result.details, exp_result.details)
        summary = self._build_summary(candidates, stages)

        return DiscoveryReport(
            generated_at=_utc_iso(),
            root_dir=str(self.root_dir),
            candidates=candidates,
            stages=stages,
            summary=summary,
        )

    def save_report(self, report: DiscoveryReport, logs_dir: Optional[Path] = None) -> Path:
        """Serialize ``report`` as JSON under ``logs/tool_discovery_<date>.json``."""
        out_dir = Path(logs_dir or self.root_dir / "logs")
        out_dir.mkdir(parents=True, exist_ok=True)
        day = report.generated_at[:10]
        path = out_dir / f"tool_discovery_{day}.json"
        path.write_text(json.dumps(report.as_dict(), indent=2), encoding="utf-8")
        return path

    # --- stages ---

    def _stage_code_analysis(self) -> StageResult:
        """Scan Python tree with AST-assisted extraction."""
        aggregate_imports: Set[str] = set()
        subprocess_commands: List[Dict[str, Any]] = []
        todo_markers: List[str] = []
        http_hint_files: List[str] = []
        errors: List[str] = []
        file_count = 0

        for py_path in iter_python_files(self.scan_roots, ignore_dirs=self.ignore_dirs):
            file_count += 1
            data = analyze_python_file(py_path)
            if data.get("error"):
                errors.append(f"{data['path']}: {data['error']}")
            aggregate_imports.update(data.get("imports") or [])
            for argv in data.get("subprocess_commands") or []:
                subprocess_commands.append({"path": data["path"], "argv": argv})
            todo_markers.extend(data.get("todo_markers") or [])
            if data.get("http_hits"):
                http_hint_files.append(data["path"])

        details = {
            "python_files_scanned": file_count,
            "unique_import_roots": sorted(aggregate_imports),
            "subprocess_calls": subprocess_commands,
            "todo_markers_sample": todo_markers[:40],
            "http_related_files": sorted(set(http_hint_files)),
            "parse_errors": errors,
        }
        notes = []
        if errors:
            notes.append(f"{len(errors)} file(s) had syntax errors and were only partially analyzed.")
        return StageResult(stage=PipelineStage.CODE_ANALYSIS, status="ok", details=details, notes=notes)

    def _stage_documentation_review(self, code_details: Dict[str, Any]) -> StageResult:
        """Cross-check Markdown docs against dependency signals."""
        declared_installs: Set[str] = set()
        doc_keywords: Set[str] = set()
        urls: List[str] = []

        for md in iter_markdown_files(self.root_dir, ignore_dirs=self.ignore_dirs):
            text = md.read_text(encoding="utf-8", errors="replace")
            for m in INSTALL_RE.finditer(text):
                declared_installs.add(m.group(2).split("[", 1)[0].strip().lower().replace("-", "_"))
            for m in BACKTICK_RE.finditer(text):
                inner = m.group(1).strip()
                if inner.isidentifier():
                    doc_keywords.add(inner.lower())
            for m in re.finditer(r"https?://[^\s)>`]+", text):
                u = m.group(0).rstrip(".,)")
                urls.append(u)

        req_names: Set[str] = set()
        for rp in self.requirements_paths:
            req_names |= parse_requirements_names(rp)

        imports = set(code_details.get("unique_import_roots") or [])
        third_party_in_code = sorted(m for m in imports if m not in _STDLIB and m != "")

        drift_hints = []
        for dk in sorted(doc_keywords):
            if dk in {"pip", "python", "true", "false", "none"}:
                continue
            if dk not in imports and len(dk) > 2:
                drift_hints.append(dk)

        details = {
            "markdown_files": [str(p) for p in iter_markdown_files(self.root_dir, ignore_dirs=self.ignore_dirs)],
            "doc_install_mentions": sorted(declared_installs),
            "requirements_combined": sorted(req_names),
            "third_party_import_roots": third_party_in_code,
            "doc_only_identifiers_sample": drift_hints[:30],
            "urls_sample": urls[:25],
        }
        return StageResult(stage=PipelineStage.DOCUMENTATION_REVIEW, status="ok", details=details, notes=[])

    def _stage_experimentation(self, code_details: Dict[str, Any], doc_details: Dict[str, Any]) -> StageResult:
        """Validate imports and binaries using local environment probes."""
        req_names: Set[str] = set()
        for rp in self.requirements_paths:
            req_names |= parse_requirements_names(rp)

        third_party = [m for m in (doc_details.get("third_party_import_roots") or []) if m not in _STDLIB]

        import_resolution: Dict[str, bool] = {}
        for name in third_party:
            import_resolution[name] = bool(self.import_probe(name))

        binary_checks: List[Dict[str, Any]] = []
        for call in code_details.get("subprocess_calls") or []:
            argv = call.get("argv") or []
            if not argv:
                continue
            bin_name = argv[0]
            if "/" in bin_name or bin_name.endswith(".py"):
                continue
            binary_checks.append(
                {
                    "binary": bin_name,
                    "argv_preview": argv[:5],
                    "source_path": call.get("path"),
                    "found_path": self.which_fn(bin_name),
                }
            )

        details = {
            "import_probe_results": import_resolution,
            "subprocess_binary_checks": binary_checks,
        }
        return StageResult(stage=PipelineStage.EXPERIMENTATION, status="ok", details=details, notes=[])

    def _stage_synthesis(
        self,
        code_details: Dict[str, Any],
        doc_details: Dict[str, Any],
        exp_details: Dict[str, Any],
    ) -> List[DiscoveryCandidate]:
        """Turn raw stage outputs into prioritized :class:`DiscoveryCandidate` entries."""
        candidates: List[DiscoveryCandidate] = []
        req_names: Set[str] = set()
        for rp in self.requirements_paths:
            req_names |= parse_requirements_names(rp)

        third_party = [m for m in (doc_details.get("third_party_import_roots") or []) if m not in _STDLIB]

        # Dependency gaps
        for mod in third_party:
            covered = any(requirement_matches_import(r, mod) for r in req_names)
            if not covered:
                candidates.append(
                    DiscoveryCandidate(
                        kind=CandidateKind.DEPENDENCY_GAP,
                        title=f"Import '{mod}' not matched in declared requirements",
                        summary=(
                            "The codebase imports this module but no requirement entry clearly "
                            "provides it. Verify PyPI package name and add it to requirements."
                        ),
                        confidence=0.75,
                        evidence={"module": mod, "requirements_files": [str(p) for p in self.requirements_paths]},
                        suggested_actions=[
                            f"Add the correct PyPI package for `{mod}` to requirements.txt (or optional deps).",
                            "Run tests in a clean virtualenv to confirm the dependency closure.",
                        ],
                    )
                )

        probes = exp_details.get("import_probe_results") or {}
        for mod, ok in probes.items():
            if not ok:
                candidates.append(
                    DiscoveryCandidate(
                        kind=CandidateKind.EXPERIMENT_NOTE,
                        title=f"Import '{mod}' did not resolve in the current environment",
                        summary="``importlib.util.find_spec`` returned no module spec for this top-level name.",
                        confidence=0.65,
                        evidence={"module": mod},
                        suggested_actions=[
                            "Install missing packages or guard optional imports with try/except.",
                            "Document optional integrations if this import is deliberately soft.",
                        ],
                    )
                )

        seen_bins: Set[str] = set()
        for check in exp_details.get("subprocess_binary_checks") or []:
            b = check.get("binary")
            if not b or b in seen_bins:
                continue
            seen_bins.add(b)
            if check.get("found_path"):
                continue
            candidates.append(
                DiscoveryCandidate(
                    kind=CandidateKind.MISSING_BINARY,
                    title=f"Subprocess invokes '{b}' but it was not found on PATH",
                    summary="Experimentation used shutil.which and found no executable for this command.",
                    confidence=0.7,
                    evidence=check,
                    suggested_actions=[
                        f"Install `{b}` or document it as an optional host dependency.",
                        "Consider resolving absolute paths via configuration for portability.",
                    ],
                )
            )

        # Capability patterns (lightweight static hints)
        if code_details.get("http_related_files"):
            candidates.append(
                DiscoveryCandidate(
                    kind=CandidateKind.CAPABILITY_PATTERN,
                    title="HTTP client usage detected",
                    summary="AST scan found attribute calls typical of HTTP clients in some modules.",
                    confidence=0.55,
                    evidence={"paths": code_details["http_related_files"]},
                    suggested_actions=[
                        "Standardize on one HTTP stack if multiple libraries appear.",
                        "Add integration tests or mocks around outbound HTTP.",
                    ],
                )
            )

        if code_details.get("todo_markers_sample"):
            candidates.append(
                DiscoveryCandidate(
                    kind=CandidateKind.CAPABILITY_PATTERN,
                    title="TODO/FIXME markers present",
                    summary="Embedded markers may describe planned capabilities or integrations.",
                    confidence=0.45,
                    evidence={"sample": code_details["todo_markers_sample"][:15]},
                    suggested_actions=[
                        "Triage TODO/FIXME comments into issues or scheduled refactors.",
                    ],
                )
            )

        doc_only = doc_details.get("doc_only_identifiers_sample") or []
        if doc_only:
            candidates.append(
                DiscoveryCandidate(
                    kind=CandidateKind.DOC_CODE_DRIFT,
                    title="Documentation identifiers not mirrored as Python imports",
                    summary=(
                        "Backticked identifiers in Markdown were not seen as top-level import roots "
                        "in scanned code; may be prose-only or signal missing implementation."
                    ),
                    confidence=0.4,
                    evidence={"identifiers": doc_only[:20]},
                    suggested_actions=[
                        "Confirm whether these identifiers reflect roadmap items vs implemented APIs.",
                        "Align docs with actual modules or add stubs referencing real packages.",
                    ],
                )
            )

        candidates.sort(key=lambda c: (-c.confidence, c.kind.value, c.title))
        return candidates

    def _build_summary(self, candidates: List[DiscoveryCandidate], stages: List[StageResult]) -> Dict[str, Any]:
        by_kind: Dict[str, int] = {}
        for c in candidates:
            by_kind[c.kind.value] = by_kind.get(c.kind.value, 0) + 1
        return {
            "candidate_count": len(candidates),
            "by_kind": by_kind,
            "stage_statuses": {s.stage.value: s.status for s in stages},
        }


def _default_import_probe(top_level: str) -> bool:
    """Return True if ``find_spec`` locates a module (lazy; safe for optional deps)."""
    try:
        import importlib.util
    except ImportError:
        return False
    spec = importlib.util.find_spec(top_level)
    return spec is not None


def format_report_markdown(report: DiscoveryReport) -> str:
    """Render a compact Markdown summary suitable for logs or PR descriptions."""
    lines = [
        "# Tool discovery report",
        "",
        f"- **Generated:** {report.generated_at}",
        f"- **Root:** `{report.root_dir}`",
        "",
        "## Summary",
        "",
    ]
    for k, v in report.summary.items():
        lines.append(f"- **{k}:** {v}")
    lines.extend(["", "## Candidates", ""])
    for c in report.candidates:
        lines.append(f"### [{c.kind.value}] {c.title}")
        lines.append("")
        lines.append(c.summary)
        lines.append("")
        if c.suggested_actions:
            lines.append("**Suggested actions:**")
            for a in c.suggested_actions:
                lines.append(f"- {a}")
            lines.append("")
    lines.append("## Stage notes")
    lines.append("")
    for s in report.stages:
        lines.append(f"### {s.stage.value} ({s.status})")
        if s.notes:
            for n in s.notes:
                lines.append(f"- {n}")
        lines.append("")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Discover tools, APIs, and capabilities by analyzing code and documentation.",
    )
    parser.add_argument(
        "command",
        choices=["discover", "print-md"],
        help="discover: run pipeline and save JSON; print-md: run and print Markdown to stdout",
    )
    parser.add_argument("--root-dir", default=".", help="Repository root path")
    parser.add_argument(
        "--logs-dir",
        default=None,
        help="Directory for JSON output (defaults to <root>/logs)",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    pipeline = ToolDiscoveryPipeline(root_dir=Path(args.root_dir))
    report = pipeline.run()
    logs = Path(args.logs_dir) if args.logs_dir else None
    path = pipeline.save_report(report, logs_dir=logs)

    if args.command == "discover":
        print(f"Wrote {path}")
        print(json.dumps(report.summary, indent=2))
        return 0

    print(format_report_markdown(report))
    print(f"\n<!-- JSON also saved to: {path} -->")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
