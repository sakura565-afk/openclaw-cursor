#!/usr/bin/env python3
"""OpenClaw workspace vector memory health: scan markdown, embeddings JSON, and sqlite stores."""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import re
import sqlite3
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence


_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts import memory_analytics  # noqa: E402

from scripts.memory_cleanup import discover_memory_files, parse_file, semantic_text  # noqa: E402

log = logging.getLogger(__name__)

# Canonical installation path referenced in OpenClaw docs (Windows profile layout).
DOCUMENTED_WORKSPACE = Path(r"C:\Users\user\.openclaw\workspace")

MAX_JSON_SCAN_BYTES = 12 * 1024 * 1024
DEFAULT_STALE_DAYS = 90
DEFAULT_MAX_CHUNK_CHARS = 12_000
EMBEDDING_NORM_WARN = 1e-6
EMBEDDING_VARIANCE_EPS = 1e-12

ISSUE_WEIGHT = {"critical": 4, "high": 3, "medium": 2, "low": 1}


@dataclass
class Issue:
    severity: str
    category: str
    title: str
    detail: str
    recommendation: str
    paths: list[str]
    evidence: dict[str, Any]
    cleanup_hint: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "severity": self.severity,
            "category": self.category,
            "title": self.title,
            "detail": self.detail,
            "recommendation": self.recommendation,
            "paths": self.paths,
            "evidence": self.evidence,
        }
        if self.cleanup_hint is not None:
            d["cleanup_hint"] = self.cleanup_hint
        return d


def default_workspace() -> Path:
    """Resolve OpenClaw workspace (vector + markdown memory root)."""

    raw = os.environ.get("OPENCLAW_WORKSPACE", "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".openclaw" / "workspace"


def configure_logging(verbose: bool, quiet: bool) -> None:
    if quiet:
        level = logging.WARNING
    elif verbose:
        level = logging.DEBUG
    else:
        level = logging.INFO
    logging.basicConfig(level=level, format="%(levelname)s: %(message)s")


def _guess_token_budget(text: str) -> int:
    """Cheap proxy: ~4 chars per token for Latin text."""

    return max(1, len(text) // 4)


def _iter_candidate_json_files(workspace: Path) -> Iterable[Path]:
    interesting_parent = frozenset(
        {"embeddings", "vectors", ".vector_store", "vector_store", "memory", ".openclaw"}
    )
    for path in workspace.rglob("*.json"):
        if not path.is_file():
            continue
        try:
            if path.stat().st_size > MAX_JSON_SCAN_BYTES or path.stat().st_size == 0:
                continue
        except OSError:
            continue
        name_l = path.name.lower()
        parent_l = path.parent.name.lower()
        if (
            "embedding" in name_l
            or "vector" in name_l
            or parent_l in interesting_parent
            or ".embedding" in name_l
        ):
            yield path


def _extract_number_lists(obj: Any, depth: int = 0, out: list[list[float]] | None = None) -> list[list[float]]:
    if out is None:
        out = []
    if depth > 8:
        return out
    if isinstance(obj, list):
        if obj and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in obj):
            out.append([float(x) for x in obj])
        else:
            for item in obj:
                _extract_number_lists(item, depth + 1, out)
    elif isinstance(obj, dict):
        for value in obj.values():
            _extract_number_lists(value, depth + 1, out)
    return out


def _vector_quality_flags(values: Sequence[float]) -> list[str]:
    flags: list[str] = []
    if not values:
        flags.append("empty_vector")
        return flags
    if any(math.isnan(x) or math.isinf(x) for x in values):
        flags.append("non_finite")
    mean = sum(values) / len(values)
    var = sum((x - mean) ** 2 for x in values) / len(values)
    norm = math.sqrt(sum(x * x for x in values))
    if norm < EMBEDDING_NORM_WARN:
        flags.append("near_zero_norm")
    if var < EMBEDDING_VARIANCE_EPS and len(values) > 1:
        flags.append("near_constant")
    return flags


def _scan_embedding_json(path: Path) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        findings.append(
            {
                "path": path.as_posix(),
                "error": str(exc),
            }
        )
        return findings

    vectors = _extract_number_lists(data)
    if not vectors:
        findings.append({"path": path.as_posix(), "note": "no_numeric_embedding_arrays"})
        return findings

    bad: list[dict[str, Any]] = []
    for index, vec in enumerate(vectors[:200]):
        flags = _vector_quality_flags(vec)
        if flags:
            bad.append({"index": index, "dim": len(vec), "flags": flags})
    if bad:
        findings.append({"path": path.as_posix(), "low_quality_vectors": bad})
    return findings


def _safe_connect_sqlite(path: Path) -> sqlite3.Connection | None:
    try:
        uri = f"file:{path.as_posix()}?mode=ro"
        return sqlite3.connect(uri, uri=True)
    except sqlite3.Error:
        return None


def _scan_sqlite(path: Path) -> dict[str, Any]:
    out: dict[str, Any] = {"path": path.as_posix(), "tables": [], "orphan_path_refs": [], "notes": []}
    conn = _safe_connect_sqlite(path)
    if conn is None:
        out["notes"].append("open_failed")
        return out
    try:
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        tables = [row[0] for row in cur.fetchall()]
        out["tables"] = tables
        workspace = path.parent
        for table in tables:
            try:
                info = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
            except sqlite3.Error:
                continue
            col_names = [row[1] for row in info]
            path_like = [c for c in col_names if re.search(r"path|file|source|uri", c, re.I)]
            for col in path_like:
                try:
                    rows = conn.execute(f'SELECT "{col}" FROM "{table}" WHERE "{col}" IS NOT NULL LIMIT 500').fetchall()
                except sqlite3.Error:
                    continue
                for (cell,) in rows:
                    if not isinstance(cell, str) or not cell.strip():
                        continue
                    p = Path(cell)
                    if p.is_absolute() and not p.exists():
                        out["orphan_path_refs"].append(
                            {"table": table, "column": col, "missing_path": cell[:500]}
                        )
                    elif not p.is_absolute():
                        candidate = (workspace / p).resolve()
                        if not candidate.exists() and not (workspace / cell).exists():
                            out["orphan_path_refs"].append(
                                {"table": table, "column": col, "missing_path": cell[:500]}
                            )
    finally:
        conn.close()
    return out


def _markdown_bloat_issues(
    workspace: Path,
    max_chunk_chars: int,
    max_estimated_tokens: int,
) -> list[Issue]:
    issues: list[Issue] = []
    main_md, daily = discover_memory_files(workspace)
    for path in [p for p in [main_md, *daily] if p is not None]:
        parsed = parse_file(path)
        for entry in parsed.entries:
            body_len = len(entry.body_text)
            tok = _guess_token_budget(entry.body_text)
            if body_len > max_chunk_chars or tok > max_estimated_tokens:
                issues.append(
                    Issue(
                        severity="medium",
                        category="bloated_chunk",
                        title="Oversized memory chunk for retrieval",
                        detail=(
                            f"Section body is ~{tok} pseudo-tokens ({body_len} chars); "
                            f"prefer smaller chunks for vector recall."
                        ),
                        recommendation=(
                            "Split the section into focused subsections or migrate details to dated daily files "
                            "so each embedding captures one topic."
                        ),
                        paths=[path.as_posix()],
                        evidence={
                            "entry_id": entry.entry_id,
                            "heading": entry.heading_line.strip(),
                            "chars": body_len,
                            "pseudo_tokens": tok,
                        },
                        cleanup_hint=None,
                    )
                )

            semi = semantic_text(entry.body_text or entry.raw_text)
            if semi and len(semi) < 24:
                issues.append(
                    Issue(
                        severity="low",
                        category="low_signal_chunk",
                        title="Thin semantic payload",
                        detail="After stripping headings and dates the chunk has almost no lexical signal.",
                        recommendation="Merge with a richer note or add concrete facts/checklists before indexing.",
                        paths=[path.as_posix()],
                        evidence={"entry_id": entry.entry_id, "semantic_preview": semi[:120]},
                        cleanup_hint=None,
                    )
                )
    return issues


def _analyze_main_memory(workspace: Path, stale_days: int, reference_date: date) -> tuple[list[Issue], dict[str, Any]]:
    issues: list[Issue] = []
    stats: dict[str, Any] = {}

    candidates = []
    mp = workspace / "MEMORY.md"
    np = workspace / "memory" / "MEMORY.md"
    if mp.exists():
        candidates.append(mp)
    elif np.exists():
        candidates.append(np)

    for mem_path in candidates:
        parsed = memory_analytics.parse_memory_file(mem_path)
        stale = memory_analytics.find_stale_entries(parsed["entries"], reference_date, stale_days)
        duplicates = memory_analytics.find_duplicate_entries(parsed["entries"])
        broken = memory_analytics.find_missing_cross_references(parsed)

        stats[mem_path.name] = {
            "anchors": len(parsed["anchors"]),
            "entries": len(parsed["entries"]),
            "internal_links": len(parsed["internal_links"]),
            "stale_count": len(stale),
            "duplicate_pairs": len(duplicates),
            "broken_anchor_refs": len(broken),
        }

        for item in stale:
            issues.append(
                Issue(
                    severity="medium",
                    category="outdated_fact",
                    title="Stale dated entry relative to MEMORY.md freshness window",
                    detail=f"Last in-text date mention is {item['last_mention']} ({item['age_days']} days before reference date).",
                    recommendation="Archive or rewrite with a confirmed current timestamp; embeddings may mis-rank stale text.",
                    paths=[mem_path.as_posix()],
                    evidence=item,
                    cleanup_hint={
                        "action": "run_memory_cleanup",
                        "kwargs": {"days": stale_days},
                    },
                )
            )

        for item in duplicates:
            issues.append(
                Issue(
                    severity="low",
                    category="duplicate_fact",
                    title="Highly similar MEMORY.md entries",
                    detail=f"Entries {item['entry_id_a']} and {item['entry_id_b']} match at {float(item['similarity']):.0%} similarity.",
                    recommendation="Dedupe similar bullets so retrieval does not oscillate between near-identical chunks.",
                    paths=[mem_path.as_posix()],
                    evidence=item,
                    cleanup_hint={"action": "run_memory_cleanup", "kwargs": {"days": stale_days}},
                )
            )

        for item in broken:
            issues.append(
                Issue(
                    severity="high",
                    category="orphaned_entry",
                    title="Internal anchor link target missing",
                    detail=f"Link to #{item['target']} on line {item['line_number']} has no matching heading or explicit anchor.",
                    recommendation="Fix the markdown anchor, add the missing heading, or remove the dead link.",
                    paths=[mem_path.as_posix()],
                    evidence=item,
                    cleanup_hint=None,
                )
            )

    return issues, {"main_memory_stats": stats}


def _sqlite_and_json_issues(workspace: Path) -> list[Issue]:
    issues: list[Issue] = []
    for db_path in list(workspace.rglob("*.db")) + list(workspace.rglob("*.sqlite3")):
        if not db_path.is_file():
            continue
        scan = _scan_sqlite(db_path)
        if scan.get("notes") == ["open_failed"]:
            issues.append(
                Issue(
                    severity="low",
                    category="vector_store_io",
                    title="Could not open sqlite database read-only",
                    detail="The file may be locked, corrupt, or not a sqlite DB.",
                    recommendation="Close writers and retry; verify the store with `sqlite3 .schema`.",
                    paths=[db_path.as_posix()],
                    evidence=scan,
                    cleanup_hint=None,
                )
            )
            continue
        for ref in scan.get("orphan_path_refs", [])[:50]:
            issues.append(
                Issue(
                    severity="high",
                    category="orphaned_entry",
                    title="Vector store references missing filesystem path",
                    detail=f"Table {ref['table']}.{ref['column']} points to a path that does not exist.",
                    recommendation="Re-index after restoring files, or purge orphan rows from the vector store.",
                    paths=[db_path.as_posix()],
                    evidence=ref,
                    cleanup_hint=None,
                )
            )

    for jpath in _iter_candidate_json_files(workspace):
        for finding in _scan_embedding_json(jpath):
            if "error" in finding:
                issues.append(
                    Issue(
                        severity="medium",
                        category="low_quality_embedding",
                        title="Embedding JSON could not be parsed",
                        detail=str(finding.get("error")),
                        recommendation="Repair JSON or regenerate embeddings from the stable source text.",
                        paths=[finding["path"]],
                        evidence=finding,
                        cleanup_hint=None,
                    )
                )
                continue
            if "note" in finding and finding["note"] == "no_numeric_embedding_arrays":
                issues.append(
                    Issue(
                        severity="low",
                        category="low_quality_embedding",
                        title="Candidate embedding file has no numeric vector payload",
                        detail="No float arrays were discovered; it may be metadata only.",
                        recommendation="Confirm this file is still required; remove if superseded.",
                        paths=[finding["path"]],
                        evidence=finding,
                        cleanup_hint=None,
                    )
                )
                continue
            for row in finding.get("low_quality_vectors", [])[:20]:
                issues.append(
                    Issue(
                        severity="high",
                        category="low_quality_embedding",
                        title="Suspicious embedding vector statistics",
                        detail=f"Vector slot {row['index']} (dim {row['dim']}): {', '.join(row['flags'])}.",
                        recommendation="Regenerate embeddings with a known-good model; drop zeroed or constant rows.",
                        paths=[finding["path"]],
                        evidence=row,
                        cleanup_hint=None,
                    )
                )
    return issues


def build_report(
    workspace: Path,
    *,
    stale_days: int = DEFAULT_STALE_DAYS,
    max_chunk_chars: int = DEFAULT_MAX_CHUNK_CHARS,
    max_estimated_tokens: int = 4000,
    reference_date: date | None = None,
) -> dict[str, Any]:
    """Scan workspace and return a JSON-serializable health report."""

    ref = reference_date or date.today()
    issues: list[Issue] = []
    issues.extend(_markdown_bloat_issues(workspace, max_chunk_chars, max_estimated_tokens))
    mem_issues, mem_meta = _analyze_main_memory(workspace, stale_days, ref)
    issues.extend(mem_issues)
    issues.extend(_sqlite_and_json_issues(workspace))

    score = 100
    for issue in issues:
        w = ISSUE_WEIGHT.get(issue.severity, 1)
        score -= min(15, w * 3)
    score = max(0, score)

    cleanup_hints: list[dict[str, Any]] = [
        {
            "action": "run_memory_cleanup",
            "kwargs": {"days": stale_days, "backup": True},
            "reason": "Align markdown pruning with detected staleness and duplicates.",
        }
    ]
    if any(i.category == "orphaned_entry" for i in issues):
        cleanup_hints.append(
            {
                "action": "manual_review",
                "kwargs": {},
                "reason": "Fix broken anchors or sqlite path references before automated deletion.",
            }
        )

    report = {
        "schema_version": 1,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "reference_date": ref.isoformat(),
        "workspace": workspace.resolve().as_posix(),
        "documented_example_path": DOCUMENTED_WORKSPACE.as_posix(),
        "parameters": {
            "stale_days": stale_days,
            "max_chunk_chars": max_chunk_chars,
            "max_estimated_tokens": max_estimated_tokens,
        },
        "summary": {
            "issue_count": len(issues),
            "by_severity": {
                sev: sum(1 for i in issues if i.severity == sev)
                for sev in ("critical", "high", "medium", "low")
            },
            "by_category": {},
            "health_score": score,
        },
        "statistics": {
            "main_memory": mem_meta.get("main_memory_stats", {}),
        },
        "issues": [i.as_dict() for i in issues],
        "recommendations": _global_recommendations(issues),
        "cleanup_hints": cleanup_hints,
    }
    by_cat: dict[str, int] = {}
    for i in issues:
        by_cat[i.category] = by_cat.get(i.category, 0) + 1
    report["summary"]["by_category"] = dict(sorted(by_cat.items(), key=lambda kv: kv[0]))
    return report


def _global_recommendations(issues: Sequence[Issue]) -> list[str]:
    recs: list[str] = []
    cats = {i.category for i in issues}
    if "bloated_chunk" in cats:
        recs.append("Split oversized markdown chunks so each embedding encodes one decision or fact.")
    if "low_quality_embedding" in cats:
        recs.append("Regenerate embeddings after fixing upstream text quality issues.")
    if "orphaned_entry" in cats:
        recs.append("Repair anchors and dangling sqlite path rows before trusting retrieval.")
    if "outdated_fact" in cats:
        recs.append("Schedule regular archival for facts older than the configured stale window.")
    if "duplicate_fact" in cats:
        recs.append("Run memory cleanup deduplication weekly to shrink redundant vector payloads.")
    if not recs:
        recs.append("No major risks detected using static heuristics; spot-check embeddings after model upgrades.")
    return recs


def write_report(report: dict[str, Any], out_path: Path | None, json_dump: Callable[[dict], str]) -> str | None:
    text = json_dump(report)
    if out_path is None:
        return None
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")
    return out_path.as_posix()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Analyze OpenClaw vector memory under the workspace folder "
            f"(often {DOCUMENTED_WORKSPACE} or ~/.openclaw/workspace)."
        )
    )
    p.add_argument(
        "--workspace",
        type=Path,
        default=None,
        help="OpenClaw workspace root (defaults to OPENCLAW_WORKSPACE or ~/.openclaw/workspace).",
    )
    p.add_argument("--output", type=Path, default=None, help="Write JSON report to this path (default: stdout only).")
    p.add_argument("--stale-days", type=int, default=DEFAULT_STALE_DAYS)
    p.add_argument("--max-chunk-chars", type=int, default=DEFAULT_MAX_CHUNK_CHARS)
    p.add_argument("--max-estimated-tokens", type=int, default=4000)
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("-q", "--quiet", action="store_true")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging(verbose=args.verbose, quiet=args.quiet)

    ws = Path(args.workspace) if args.workspace is not None else default_workspace()
    if not ws.exists():
        logging.error("Workspace does not exist: %s", ws)
        logging.info(
            "On Windows this is commonly %s (adjust user name); set OPENCLAW_WORKSPACE to override.",
            DOCUMENTED_WORKSPACE,
        )
        return 1

    log.info("Scanning workspace %s", ws.resolve())
    report = build_report(
        ws,
        stale_days=args.stale_days,
        max_chunk_chars=args.max_chunk_chars,
        max_estimated_tokens=args.max_estimated_tokens,
    )

    def _dump(obj: dict) -> str:
        return json.dumps(obj, indent=2, sort_keys=True)

    saved = write_report(report, args.output, _dump)

    summary = report["summary"]
    if not args.quiet:
        print(json.dumps(summary, indent=2, sort_keys=True))
        if saved:
            print(f"Report written to {saved}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
