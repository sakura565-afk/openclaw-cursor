"""
Periodic self-reflection over session transcripts and `.learnings/` entries.

Designed for unattended runs (cron on Linux, Task Scheduler on Windows). Uses
only the standard library at runtime except optional `python-frontmatter` for
Markdown front matter in learnings files.

Suggested scheduling:

- Linux/macOS (daily at 06:00, repo root as cwd)::

    0 6 * * * cd /path/to/openclaw-cursor && python -m src.self_improvement.auto_reflection

- Windows Task Scheduler: Action "Start a program" -> ``python`` with arguments
  ``-m src.self_improvement.auto_reflection`` and "Start in" set to the repo root.

Environment overrides (optional): ``AUTO_REFLECTION_ROOT``, ``AUTO_REFLECTION_LOG_DIR``,
``AUTO_REFLECTION_DAYS``, ``AUTO_REFLECTION_TRANSCRIPT_DIRS`` (use the platform path separator, same as ``PATH``).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import tempfile
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

try:
    import frontmatter as _frontmatter  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    _frontmatter = None


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_path_list(raw: str) -> List[Path]:
    parts = [p.strip() for p in raw.split(os.pathsep) if p.strip()]
    return [Path(p).expanduser() for p in parts]


@dataclass
class ReflectionConfig:
    """Runtime configuration for a reflection pass."""

    root_dir: Path
    log_dir: Path
    learnings_dir: Path
    transcript_dirs: Tuple[Path, ...]
    lookback_days: int = 7
    max_bytes_per_file: int = 2_000_000
    transcript_globs: Tuple[str, ...] = (
        "**/*transcript*.{md,txt,log}",
        "**/*session*.{md,txt,json,jsonl,log}",
        "**/*.jsonl",
    )
    min_word_len: int = 4
    top_terms: int = 25

    @classmethod
    def from_env_and_args(
        cls,
        *,
        root_dir: Path,
        log_dir: Optional[Path] = None,
        learnings_dir: Optional[Path] = None,
        transcript_dirs: Optional[Sequence[Path]] = None,
        lookback_days: int = 7,
        max_bytes_per_file: int = 2_000_000,
    ) -> ReflectionConfig:
        root = Path(
            os.environ.get("AUTO_REFLECTION_ROOT", root_dir),
        ).expanduser().resolve()

        log = Path(
            os.environ.get("AUTO_REFLECTION_LOG_DIR", log_dir or (root / "logs")),
        ).expanduser().resolve()

        learnings = Path(
            os.environ.get("AUTO_REFLECTION_LEARNINGS_DIR", learnings_dir or (root / ".learnings")),
        ).expanduser().resolve()

        env_dirs = os.environ.get("AUTO_REFLECTION_TRANSCRIPT_DIRS")
        if env_dirs:
            tdirs = tuple(_parse_path_list(env_dirs))
        elif transcript_dirs:
            tdirs = tuple(Path(p).expanduser().resolve() for p in transcript_dirs)
        else:
            tdirs = (
                learnings / "transcripts",
                root / "logs" / "sessions",
                root / "memory",
                root / ".learnings" / "sessions",
            )

        days_raw = os.environ.get("AUTO_REFLECTION_DAYS")
        days = lookback_days
        if days_raw:
            try:
                days = max(1, int(days_raw))
            except ValueError:
                pass

        max_bytes = max_bytes_per_file
        mb_raw = os.environ.get("AUTO_REFLECTION_MAX_BYTES")
        if mb_raw:
            try:
                max_bytes = max(4096, int(mb_raw))
            except ValueError:
                pass

        return cls(
            root_dir=root,
            log_dir=log,
            learnings_dir=learnings,
            transcript_dirs=tdirs,
            lookback_days=days,
            max_bytes_per_file=max_bytes,
        )


# Heuristic lexicons (case-insensitive). Tuned for agent / dev session logs.
_SUCCESS_PHRASES = re.compile(
    r"\b("
    r"resolved|fixed|succeeded|success|completed|passed|verified|"
    r"works?\s+well|root\s+cause|shipped|merged|green\s+build|"
    r"mitigated|recovered|no\s+errors?"
    r")\b",
    re.IGNORECASE,
)
_FAILURE_PHRASES = re.compile(
    r"\b("
    r"failed|failure|error|exception|timeout|unable\s+to|could\s+not|"
    r"blocked|regression|broken|crash|critical|"
    r"does\s+not\s+work|not\s+working|gave\s+up|"
    r"permission\s+denied|out\s+of\s+memory|oom"
    r")\b",
    re.IGNORECASE,
)
_RISK_PHRASES = re.compile(
    r"\b("
    r"hack|workaround|temporary|fragile|brittle|debt|"
    r"uncertain|unknown|needs?\s+investigation|todo\s*:|FIXME|XXX"
    r")\b",
    re.IGNORECASE,
)

_STOPWORDS = frozenset(
    {
        "that",
        "this",
        "with",
        "from",
        "have",
        "been",
        "were",
        "will",
        "would",
        "could",
        "should",
        "about",
        "which",
        "their",
        "there",
        "these",
        "those",
        "into",
        "than",
        "then",
        "them",
        "some",
        "such",
        "very",
        "what",
        "when",
        "where",
        "while",
        "without",
        "your",
        "also",
        "just",
        "like",
        "only",
        "more",
        "most",
        "other",
        "because",
        "being",
        "here",
        "each",
        "both",
        "make",
        "made",
        "using",
        "used",
        "file",
        "path",
        "code",
        "test",
        "tests",
        "need",
        "same",
        "many",
        "much",
        "even",
        "well",
        "does",
        "doesn",
        "didn",
        "don",
        "isn",
        "wasn",
        "weren",
        "aren",
        "hasn",
        "haven",
        "hadn",
    }
)


@dataclass
class FileInsight:
    path: str
    kind: str  # learning | transcript
    bytes_read: int
    success_hits: int
    failure_hits: int
    risk_hits: int
    mtime_iso: str
    title: str = ""
    tags: Tuple[str, ...] = ()
    error: str = ""


@dataclass
class ReflectionReport:
    generated_at: str
    lookback_days: int
    root_dir: str
    learnings_dir: str
    transcript_dirs: Tuple[str, ...]
    files_scanned: int
    files_skipped: int
    per_file: List[FileInsight] = field(default_factory=list)
    aggregate_success_hits: int = 0
    aggregate_failure_hits: int = 0
    aggregate_risk_hits: int = 0
    top_terms: List[Tuple[str, int]] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["per_file"] = [asdict(f) for f in self.per_file]
        data["top_terms"] = [{"term": t, "count": c} for t, c in self.top_terms]
        return data


def _atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="\n") as handle:
            handle.write(text)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _atomic_write_json(path: Path, payload: Any) -> None:
    text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    _atomic_write_text(path, text)


def read_text_capped(path: Path, max_bytes: int) -> Tuple[str, int, Optional[str]]:
    """
    Read up to ``max_bytes`` from ``path``. Returns (text, bytes_read, error).
    """
    try:
        data = path.read_bytes()
    except OSError as exc:
        return "", 0, str(exc)
    raw = data[:max_bytes]
    try:
        text = raw.decode("utf-8", errors="replace")
    except Exception as exc:  # pragma: no cover
        return "", len(raw), str(exc)
    return text, len(raw), None


def score_text(text: str) -> Tuple[int, int, int]:
    s = len(_SUCCESS_PHRASES.findall(text))
    f = len(_FAILURE_PHRASES.findall(text))
    r = len(_RISK_PHRASES.findall(text))
    return s, f, r


def tokenize_for_terms(text: str, min_len: int) -> Iterator[str]:
    for match in re.finditer(r"[A-Za-z][A-Za-z0-9_\-]{%d,}" % (min_len - 1), text.lower()):
        word = match.group(0)
        if word in _STOPWORDS:
            continue
        yield word


def parse_learning_metadata(path: Path, text: str) -> Tuple[str, Tuple[str, ...], str]:
    title = path.stem.replace("_", " ").replace("-", " ")
    tags: Tuple[str, ...] = ()
    if _frontmatter is not None:
        try:
            post = _frontmatter.loads(text)
            meta = getattr(post, "metadata", {}) or {}
            if isinstance(meta, dict):
                if isinstance(meta.get("title"), str):
                    title = meta["title"].strip() or title
                raw_tags = meta.get("tags")
                if isinstance(raw_tags, list):
                    tags = tuple(str(t).strip() for t in raw_tags if str(t).strip())
                elif isinstance(raw_tags, str) and raw_tags.strip():
                    tags = tuple(t.strip() for t in raw_tags.split(",") if t.strip())
            content = getattr(post, "content", text)
            return title, tags, str(content)
        except Exception:
            pass
    return title, tags, text


def _display_path(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def iter_learning_files(learnings_dir: Path) -> List[Path]:
    if not learnings_dir.is_dir():
        return []
    out: List[Path] = []
    for pattern in ("*.md", "*.markdown", "*.txt", "*.json"):
        out.extend(sorted(learnings_dir.glob(pattern)))
    # De-duplicate (e.g. case-insensitive FS)
    seen = set()
    unique: List[Path] = []
    for p in out:
        key = str(p.resolve())
        if key not in seen:
            seen.add(key)
            unique.append(p)
    return unique


def _expand_brace_globs(pattern: str) -> List[str]:
    """Expand simple ``{a,b}`` brace groups in glob patterns."""
    if "{" not in pattern:
        return [pattern]
    prefix, rest = pattern.split("{", 1)
    if "}" not in rest:
        return [pattern]
    inner, suffix = rest.split("}", 1)
    options = [opt.strip() for opt in inner.split(",") if opt.strip()]
    return [prefix + opt + suffix for opt in options]


def iter_transcript_files(config: ReflectionConfig) -> Tuple[List[Path], List[str]]:
    """
    Collect transcript-like files under configured directories, modified within lookback.
    """
    cutoff = _now_utc() - timedelta(days=config.lookback_days)
    found: Dict[str, Path] = {}
    skip_reasons: List[str] = []

    for base in config.transcript_dirs:
        if not base.exists():
            skip_reasons.append(f"Transcript directory missing (skipped): {base}")
            continue
        if not base.is_dir():
            skip_reasons.append(f"Transcript path is not a directory (skipped): {base}")
            continue
        for pattern in config.transcript_globs:
            for concrete in _expand_brace_globs(pattern):
                try:
                    for path in base.glob(concrete):
                        if not path.is_file():
                            continue
                        try:
                            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
                        except OSError as exc:
                            skip_reasons.append(f"stat failed for {path}: {exc}")
                            continue
                        if mtime < cutoff:
                            continue
                        key = str(path.resolve())
                        found[key] = path
                except OSError as exc:
                    skip_reasons.append(f"glob failed under {base} pattern {concrete!r}: {exc}")

    return sorted(found.values(), key=lambda p: p.stat().st_mtime, reverse=True), skip_reasons


def build_report(config: ReflectionConfig) -> ReflectionReport:
    notes: List[str] = []
    per_file: List[FileInsight] = []
    term_counter: Counter[str] = Counter()
    agg_s = agg_f = agg_r = 0
    scanned = 0
    skipped = 0

    learnings = iter_learning_files(config.learnings_dir)
    if not learnings and not config.learnings_dir.is_dir():
        notes.append(f"Learnings directory does not exist yet: {config.learnings_dir}")

    for path in learnings:
        text, nbytes, err = read_text_capped(path, config.max_bytes_per_file)
        if err:
            skipped += 1
            per_file.append(
                FileInsight(
                    path=_display_path(path, config.root_dir),
                    kind="learning",
                    bytes_read=nbytes,
                    success_hits=0,
                    failure_hits=0,
                    risk_hits=0,
                    mtime_iso=datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(),
                    error=err,
                )
            )
            continue
        title, tags, body = parse_learning_metadata(path, text)
        s, f, r = score_text(body)
        agg_s += s
        agg_f += f
        agg_r += r
        term_counter.update(tokenize_for_terms(body, config.min_word_len))
        scanned += 1
        rel = _display_path(path, config.root_dir)
        per_file.append(
            FileInsight(
                path=rel,
                kind="learning",
                bytes_read=nbytes,
                success_hits=s,
                failure_hits=f,
                risk_hits=r,
                mtime_iso=datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(),
                title=title,
                tags=tags,
            )
        )

    transcripts, tnotes = iter_transcript_files(config)
    notes.extend(tnotes)

    for path in transcripts:
        text, nbytes, err = read_text_capped(path, config.max_bytes_per_file)
        if err:
            skipped += 1
            rel = _display_path(path, config.root_dir)
            per_file.append(
                FileInsight(
                    path=rel,
                    kind="transcript",
                    bytes_read=nbytes,
                    success_hits=0,
                    failure_hits=0,
                    risk_hits=0,
                    mtime_iso="",
                    error=err,
                )
            )
            continue
        s, f, r = score_text(text)
        agg_s += s
        agg_f += f
        agg_r += r
        term_counter.update(tokenize_for_terms(text, config.min_word_len))
        scanned += 1
        try:
            mtime_iso = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
        except OSError:
            mtime_iso = ""
        rel = _display_path(path, config.root_dir)
        per_file.append(
            FileInsight(
                path=rel,
                kind="transcript",
                bytes_read=nbytes,
                success_hits=s,
                failure_hits=f,
                risk_hits=r,
                mtime_iso=mtime_iso,
            )
        )

    top = term_counter.most_common(config.top_terms)

    ratio = (agg_s + 1) / (agg_f + 1)
    if agg_f > agg_s * 2:
        notes.append(
            "Heuristic: failure-oriented language dominates recent transcripts; "
            "prioritize stabilizing flaky steps called out in the highest failure_hits files."
        )
    elif ratio > 2.0 and agg_s > 3:
        notes.append(
            "Heuristic: success-oriented language is frequent; capture concrete practices "
            "from top-scoring learnings into `.learnings/` for retention."
        )

    if not per_file:
        notes.append(
            "No learnings or in-window transcripts were found. Add files under `.learnings/` "
            "or place session exports under `.learnings/transcripts/` or `logs/sessions/`."
        )

    return ReflectionReport(
        generated_at=_now_utc().isoformat(),
        lookback_days=config.lookback_days,
        root_dir=str(config.root_dir),
        learnings_dir=str(config.learnings_dir),
        transcript_dirs=tuple(str(p) for p in config.transcript_dirs),
        files_scanned=scanned,
        files_skipped=skipped,
        per_file=sorted(per_file, key=lambda x: (x.kind, x.path)),
        aggregate_success_hits=agg_s,
        aggregate_failure_hits=agg_f,
        aggregate_risk_hits=agg_r,
        top_terms=top,
        notes=notes,
    )


def report_to_markdown(report: ReflectionReport) -> str:
    lines = [
        "# Self-reflection summary",
        "",
        f"- **Generated (UTC)**: {report.generated_at}",
        f"- **Lookback**: {report.lookback_days} day(s)",
        f"- **Root**: `{report.root_dir}`",
        f"- **Learnings dir**: `{report.learnings_dir}`",
        f"- **Transcript roots**: {', '.join(f'`{p}`' for p in report.transcript_dirs) or '(none)'}",
        f"- **Files scanned**: {report.files_scanned}",
        f"- **Files skipped / errors**: {report.files_skipped}",
        "",
        "## Aggregate signals (heuristic keyword hits)",
        "",
        f"- Success-oriented hits: **{report.aggregate_success_hits}**",
        f"- Failure-oriented hits: **{report.aggregate_failure_hits}**",
        f"- Risk / debt language hits: **{report.aggregate_risk_hits}**",
        "",
        "## Pattern notes",
        "",
    ]
    if report.notes:
        for n in report.notes:
            lines.append(f"- {n}")
    else:
        lines.append("- (none)")
    lines.extend(["", "## Top recurring terms (excluding common stopwords)", ""])
    if report.top_terms:
        for term, count in report.top_terms:
            lines.append(f"- `{term}`: {count}")
    else:
        lines.append("- (insufficient text)")
    lines.extend(["", "## Per file", ""])
    for fi in report.per_file:
        title = f" — *{fi.title}*" if fi.title and fi.kind == "learning" else ""
        tag = f" tags={list(fi.tags)}" if fi.tags else ""
        err = f" **ERROR**: {fi.error}" if fi.error else ""
        lines.append(
            f"- `{fi.path}` ({fi.kind}{title}{tag}) — "
            f"success={fi.success_hits}, failure={fi.failure_hits}, risk={fi.risk_hits}, "
            f"bytes_read={fi.bytes_read}{err}"
        )
    lines.append("")
    return "\n".join(lines)


def write_reflection_artifacts(report: ReflectionReport, log_dir: Path) -> Tuple[Path, Path]:
    day = _now_utc().strftime("%Y%m%d")
    md_path = log_dir / f"self_reflection_{day}.md"
    json_path = log_dir / f"self_reflection_{day}.json"
    _atomic_write_text(md_path, report_to_markdown(report))
    _atomic_write_json(json_path, report.to_dict())
    return md_path, json_path


def setup_logging(verbose: bool, log_file: Optional[Path] = None) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    handlers: List[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
        force=True,
    )


def run_reflection(
    config: ReflectionConfig,
    *,
    logger: Optional[logging.Logger] = None,
) -> ReflectionReport:
    log = logger or logging.getLogger("auto_reflection")
    config.log_dir.mkdir(parents=True, exist_ok=True)
    config.learnings_dir.mkdir(parents=True, exist_ok=True)
    log.info(
        "Starting reflection: root=%s lookback_days=%s learnings=%s transcripts_roots=%s",
        config.root_dir,
        config.lookback_days,
        config.learnings_dir,
        ", ".join(str(p) for p in config.transcript_dirs),
    )
    report = build_report(config)
    md_path, json_path = write_reflection_artifacts(report, config.log_dir)
    log.info("Wrote markdown summary to %s", md_path)
    log.info("Wrote machine-readable report to %s", json_path)
    return report


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Scan learnings and recent transcripts; emit self-reflection logs.",
    )
    p.add_argument(
        "--root-dir",
        default=".",
        help="Repository root (default: current working directory).",
    )
    p.add_argument(
        "--log-dir",
        default=None,
        help="Output directory for reflection artifacts (default: <root>/logs).",
    )
    p.add_argument(
        "--learnings-dir",
        default=None,
        help="Directory of learning notes (default: <root>/.learnings).",
    )
    p.add_argument(
        "--transcript-dir",
        action="append",
        default=None,
        dest="transcript_dirs",
        metavar="PATH",
        help="Extra transcript root (repeatable). Defaults to several roots under the repo.",
    )
    p.add_argument(
        "--days",
        type=int,
        default=7,
        help="Only include transcripts modified within this many days (default: 7).",
    )
    p.add_argument(
        "--max-bytes",
        type=int,
        default=2_000_000,
        help="Maximum bytes read per file (default: 2_000_000).",
    )
    p.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging.",
    )
    p.add_argument(
        "--log-file",
        type=Path,
        default=None,
        help="Append detailed logs to this file in addition to stderr.",
    )
    p.add_argument(
        "--print-markdown",
        action="store_true",
        help="Print the generated Markdown report to stdout after writing files.",
    )
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    setup_logging(args.verbose, args.log_file)

    root = Path(args.root_dir).expanduser().resolve()
    log_dir = Path(args.log_dir).expanduser().resolve() if args.log_dir else None
    learnings = Path(args.learnings_dir).expanduser().resolve() if args.learnings_dir else None
    transcript_dirs = (
        [Path(p).expanduser().resolve() for p in args.transcript_dirs] if args.transcript_dirs else None
    )

    config = ReflectionConfig.from_env_and_args(
        root_dir=root,
        log_dir=log_dir,
        learnings_dir=learnings,
        transcript_dirs=transcript_dirs,
        lookback_days=max(1, int(args.days)),
        max_bytes_per_file=max(4096, int(args.max_bytes)),
    )

    logger = logging.getLogger("auto_reflection")
    try:
        report = run_reflection(config, logger=logger)
    except Exception:
        logger.exception("Reflection run failed")
        return 1

    if args.print_markdown:
        md = report_to_markdown(report)
        sys.stdout.write(md)
        if not md.endswith("\n"):
            sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
