#!/usr/bin/env python3
"""
Best-of-N runner for Cursor Cloud Agents.

One task is handed to N cloud agents at once — each with its own model, its own
branch (``cursor/<slug>-variant-<k>``), its own worktree and its own pull
request.  When every variant has finished the results are scored (LLM judge
first, deterministic diff heuristics as a fallback), the winner is picked and a
report is written to ``data/best_of_N/``.

Everything that already exists is reused rather than reimplemented:

* ``scripts/queue_manager.py`` supplies agent launching, the batch poller,
  exponential backoff with jitter and the transient-retry wrappers.
* ``scripts/worktree_orchestrator.py`` (Task 12) is used for worktree isolation
  when present; otherwise :class:`MinimalWorktreeOrchestrator` shells out to
  ``git worktree add`` so this runner works before Task 12 lands.
* ``scripts/llm_call.py`` / ``src/openclaw_orchestration/llm_call.py`` is used as
  the judge transport when present; otherwise the deterministic fallback scorer
  is used and the report is marked ``judge_mode: "fallback"``.
* ``scripts/telegram_sender.py`` sends the digest.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

_REPO_ROOT = Path(__file__).resolve().parent.parent

if __package__ in (None, ""):  # `python scripts/best_of_n.py`
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))

from scripts import queue_manager as _default_queue_manager  # noqa: E402

# --------------------------------------------------------------------------- #
# Configuration

#: Models used when ``--best-of-models`` is not given.  Overridable through the
#: ``OPENCLAW_BEST_OF_MODELS`` env var (space/comma separated) or
#: ``config/best_of_n.json`` (key ``models``).
DEFAULT_VARIANT_MODELS: tuple[str, ...] = (
    "claude-opus-5-thinking-high",
    "gpt-5.6-sol-high",
    "gemini-3.7-flash-high",
)

#: Judge model.  MiniMax-M3.1-Flash is preferred (cheap, fast, JSON-friendly);
#: any Cursor model name works too when the transport routes to Cursor.
DEFAULT_JUDGE_MODEL = "MiniMax-M3.1-Flash"

CONFIG_PATHS = (
    _REPO_ROOT / "config" / "best_of_n.yaml",
    _REPO_ROOT / "config" / "best_of_n.json",
)
REPORT_DIR = Path(
    os.environ.get("OPENCLAW_BEST_OF_REPORT_DIR", str(_REPO_ROOT / "data" / "best_of_N"))
)

BRANCH_PREFIX = "cursor"

#: Hard ceiling on how long :meth:`BestOfNRunner.run` waits for all variants.
DEFAULT_MAX_WAIT_SEC = 3 * 60 * 60.0

# --------------------------------------------------------------------------- #
# Deterministic fallback scoring weights.
#
# Used only when the LLM judge is unavailable or returns unusable output.  The
# four components are normalised to 0..1 independently, multiplied by the
# weights below (which sum to 1.0) and summed, so the final score is always in
# 0..1.  Rationale for the weighting:
#
#   * tests        — a variant that touches ``tests/`` is the strongest signal
#                    that the change is verifiable, so it carries the most
#                    weight (binary: any test file touched or not).
#   * new src      — new modules under ``src/`` mean real implementation work
#                    rather than incidental edits; saturates at 3 files.
#   * files        — breadth of the change; saturates at 8 files so a huge
#                    unfocused diff cannot outrank a tested one.
#   * line volume  — insertions + deletions; saturates at 400 lines, same idea.
FALLBACK_WEIGHT_TESTS = 0.35
FALLBACK_WEIGHT_NEW_SRC = 0.25
FALLBACK_WEIGHT_FILES_CHANGED = 0.20
FALLBACK_WEIGHT_LINE_VOLUME = 0.20

FALLBACK_NEW_SRC_SATURATION = 3
FALLBACK_FILES_CHANGED_SATURATION = 8
FALLBACK_LINE_VOLUME_SATURATION = 400

JUDGE_MODE_LLM = "llm"
JUDGE_MODE_FALLBACK = "fallback"

STATE_COMPLETED = "completed"
STATE_FAILED = "failed"
STATE_TIMEOUT = "timeout"
STATE_NOT_FOUND = "not_found"
STATE_LAUNCH_FAILED = "launch_failed"
STATE_WORKTREE_FAILED = "worktree_failed"
STATE_POLL_FAILED = "poll_failed"
STATE_PENDING = "pending"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def slugify(value: str) -> str:
    """Lowercase, dash-separated slug safe for git branch names."""
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", str(value or "").strip().lower())
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-")
    return cleaned or "task"


def load_config(path: Optional[Path] = None) -> Dict[str, Any]:
    """Read ``config/best_of_n.{yaml,json}`` if present (missing/broken → ``{}``)."""
    candidates = (Path(path),) if path else CONFIG_PATHS
    for cfg_path in candidates:
        try:
            if not cfg_path.exists():
                continue
            text = cfg_path.read_text(encoding="utf-8")
            if cfg_path.suffix in (".yaml", ".yml"):
                import yaml

                data = yaml.safe_load(text)
            else:
                data = json.loads(text)
            if isinstance(data, dict):
                return data
        except Exception:
            continue
    return {}


def _split_model_list(raw: str) -> List[str]:
    return [part for part in re.split(r"[,\s]+", raw.strip()) if part]


def default_models(config: Optional[Dict[str, Any]] = None) -> List[str]:
    """Default variant models: env var → config file → built-in triple."""
    env = os.environ.get("OPENCLAW_BEST_OF_MODELS", "").strip()
    if env:
        return _split_model_list(env)
    cfg = config if config is not None else load_config()
    models = cfg.get("models")
    if isinstance(models, list) and models:
        return [str(m) for m in models]
    if isinstance(models, str) and models.strip():
        return _split_model_list(models)
    return list(DEFAULT_VARIANT_MODELS)


def default_judge_model(config: Optional[Dict[str, Any]] = None) -> str:
    env = os.environ.get("OPENCLAW_BEST_OF_JUDGE_MODEL", "").strip()
    if env:
        return env
    cfg = config if config is not None else load_config()
    judge = cfg.get("judge_model")
    if isinstance(judge, str) and judge.strip():
        return judge.strip()
    return DEFAULT_JUDGE_MODEL


# --------------------------------------------------------------------------- #
# Specs and results


@dataclass
class TaskSpec:
    """One task to fan out, usually loaded from a YAML task spec."""

    slug: str
    prompt: str = ""
    description: Optional[str] = None
    acceptance_criteria: List[str] = field(default_factory=list)
    source_path: Optional[str] = None

    @classmethod
    def from_mapping(
        cls,
        data: Dict[str, Any],
        *,
        default_slug: Optional[str] = None,
        source_path: Optional[str] = None,
    ) -> "TaskSpec":
        if not isinstance(data, dict):
            raise ValueError("Task spec must be a mapping.")
        name = data.get("slug") or data.get("name") or default_slug
        if not name:
            raise ValueError("Task spec is missing 'slug'/'name'.")
        criteria = (
            data.get("acceptance_criteria")
            or data.get("acceptance")
            or data.get("criteria")
            or []
        )
        if isinstance(criteria, str):
            criteria = [line.strip("-• ") for line in criteria.splitlines() if line.strip()]
        prompt = data.get("prompt") or data.get("goal") or data.get("command") or ""
        return cls(
            slug=slugify(name),
            prompt=str(prompt),
            description=(
                str(data["description"]) if data.get("description") is not None else None
            ),
            acceptance_criteria=[str(c) for c in criteria],
            source_path=source_path,
        )

    @classmethod
    def from_yaml(cls, path: os.PathLike[str] | str, *, task_name: Optional[str] = None) -> "TaskSpec":
        """Load a spec from a ``task_runner``-style YAML file."""
        import yaml  # imported lazily: only needed for YAML-backed specs

        spec_path = Path(path)
        content = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
        if isinstance(content, dict) and isinstance(content.get("tasks"), list):
            entries = [e for e in content["tasks"] if isinstance(e, dict)]
            if task_name:
                entries = [e for e in entries if e.get("name") == task_name]
            if not entries:
                raise ValueError(f"No task found in {spec_path} (task_name={task_name!r})")
            data = entries[0]
        elif isinstance(content, dict):
            data = content
        else:
            raise ValueError(f"Task spec {spec_path} must contain a mapping.")
        return cls.from_mapping(
            data,
            default_slug=spec_path.stem,
            source_path=str(spec_path),
        )

    def body(self) -> str:
        return self.prompt or self.description or ""


@dataclass
class VariantSpec:
    """One of the N attempts at a task."""

    index: int
    model: str
    seed: Optional[int] = None
    label: str = ""

    def display_label(self) -> str:
        return self.label or f"variant {self.index} ({self.model})"


@dataclass
class VariantResult:
    index: int
    model: str
    branch: str
    agent_id: Optional[str] = None
    pr_url: Optional[str] = None
    score: Optional[float] = None
    verdict: str = ""
    judge_raw: str = ""
    latency_s: Optional[float] = None
    error: Optional[str] = None
    state: str = STATE_PENDING
    judge_mode: Optional[str] = None
    worktree: Optional[str] = None
    diff_stats: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "index": self.index,
            "model": self.model,
            "branch": self.branch,
            "agent_id": self.agent_id,
            "pr_url": self.pr_url,
            "score": self.score,
            "verdict": self.verdict,
            "latency_s": self.latency_s,
            "state": self.state,
            "error": self.error,
            "judge_mode": self.judge_mode,
            "diff_stats": self.diff_stats,
        }


@dataclass
class BestOfNResult:
    task_slug: str
    variants: List[VariantResult]
    winner: Optional[VariantResult]
    judge_model: str
    created_at: str
    judge_mode: str = JUDGE_MODE_FALLBACK
    report_path: Optional[str] = None
    markdown_path: Optional[str] = None

    @property
    def n(self) -> int:
        return len(self.variants)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_slug": self.task_slug,
            "n": self.n,
            "judge_model": self.judge_model,
            "judge_mode": self.judge_mode,
            "created_at": self.created_at,
            "winner": (
                {
                    "index": self.winner.index,
                    "model": self.winner.model,
                    "pr_url": self.winner.pr_url,
                }
                if self.winner
                else None
            ),
            "variants": [v.to_dict() for v in self.variants],
        }


# --------------------------------------------------------------------------- #
# Diff statistics


@dataclass
class DiffStats:
    files_changed: int = 0
    insertions: int = 0
    deletions: int = 0
    test_files: int = 0
    new_src_files: int = 0
    files: List[str] = field(default_factory=list)
    available: bool = True
    error: Optional[str] = None

    @property
    def touches_tests(self) -> bool:
        return self.test_files > 0

    @property
    def line_volume(self) -> int:
        return self.insertions + self.deletions

    def to_dict(self) -> Dict[str, Any]:
        return {
            "files_changed": self.files_changed,
            "insertions": self.insertions,
            "deletions": self.deletions,
            "test_files": self.test_files,
            "new_src_files": self.new_src_files,
            "available": self.available,
            "error": self.error,
        }

    def summary(self) -> str:
        if not self.available:
            return f"diff unavailable ({self.error or 'unknown reason'})"
        return (
            f"{self.files_changed} file(s) changed, "
            f"+{self.insertions}/-{self.deletions} lines, "
            f"{self.test_files} test file(s), "
            f"{self.new_src_files} new src file(s)"
        )


def _is_test_path(path: str) -> bool:
    norm = path.replace("\\", "/")
    return norm.startswith("tests/") or "/tests/" in norm or re.search(
        r"(^|/)test_[^/]+\.py$", norm
    ) is not None


def _is_src_path(path: str) -> bool:
    norm = path.replace("\\", "/")
    return norm.startswith("src/") or "/src/" in norm


def parse_numstat(numstat: str, added_files: Iterable[str] = ()) -> DiffStats:
    """Turn ``git diff --numstat`` output into :class:`DiffStats`."""
    added = {p.replace("\\", "/") for p in added_files}
    stats = DiffStats()
    for line in numstat.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        ins_raw, del_raw, path = parts[0], parts[1], parts[-1]
        stats.files_changed += 1
        stats.files.append(path)
        if ins_raw.isdigit():
            stats.insertions += int(ins_raw)
        if del_raw.isdigit():
            stats.deletions += int(del_raw)
        if _is_test_path(path):
            stats.test_files += 1
        if _is_src_path(path) and path.replace("\\", "/") in added:
            stats.new_src_files += 1
    return stats


def collect_diff_stats(
    branch: str,
    *,
    repo_root: Optional[Path] = None,
    base_ref: str = "main",
    runner: Callable[..., Any] = subprocess.run,
) -> DiffStats:
    """Collect diff stats for ``branch`` against ``base_ref`` via git."""
    cwd = str(repo_root or _REPO_ROOT)
    rng = f"{base_ref}...{branch}"
    try:
        numstat = runner(
            ["git", "diff", "--numstat", rng],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=cwd,
        )
        if numstat.returncode != 0:
            return DiffStats(
                available=False,
                error=(numstat.stderr or "git diff failed").strip(),
            )
        added = runner(
            ["git", "diff", "--name-only", "--diff-filter=A", rng],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=cwd,
        )
        added_files = (added.stdout or "").splitlines() if added.returncode == 0 else []
        return parse_numstat(numstat.stdout or "", added_files)
    except (OSError, subprocess.SubprocessError) as exc:
        return DiffStats(available=False, error=str(exc))


# --------------------------------------------------------------------------- #
# Judge


@dataclass
class JudgeVerdict:
    score: Optional[float]
    verdict: str = ""
    reasons: List[str] = field(default_factory=list)
    raw: str = ""
    error: Optional[str] = None
    mode: str = JUDGE_MODE_LLM


def parse_judge_response(text: Any) -> JudgeVerdict:
    """
    Parse a judge reply into a :class:`JudgeVerdict`.

    Accepts bare JSON, JSON inside a ```json fence and JSON embedded in prose.
    On unusable output the score is ``None`` and ``error`` is filled in — this
    never raises.
    """
    raw = text if isinstance(text, str) else json.dumps(text, ensure_ascii=False, default=str)
    if isinstance(text, dict):
        payload: Optional[Dict[str, Any]] = text
    else:
        payload = _default_queue_manager._try_parse_json_object(raw or "")

    if not isinstance(payload, dict):
        return JudgeVerdict(
            score=None,
            raw=raw,
            error="judge response contained no JSON object",
        )

    score_raw = payload.get("score")
    if isinstance(score_raw, bool) or not isinstance(score_raw, (int, float, str)):
        return JudgeVerdict(
            score=None,
            verdict=str(payload.get("verdict") or ""),
            reasons=_as_str_list(payload.get("reasons")),
            raw=raw,
            error=f"judge response has no numeric 'score' (got {score_raw!r})",
        )
    try:
        score = float(score_raw)
    except (TypeError, ValueError):
        return JudgeVerdict(
            score=None,
            verdict=str(payload.get("verdict") or ""),
            reasons=_as_str_list(payload.get("reasons")),
            raw=raw,
            error=f"judge 'score' is not a number: {score_raw!r}",
        )
    if score != score or score in (float("inf"), float("-inf")):  # NaN / inf
        return JudgeVerdict(
            score=None,
            raw=raw,
            error=f"judge 'score' is not finite: {score_raw!r}",
        )

    clamped = min(1.0, max(0.0, score))
    return JudgeVerdict(
        score=clamped,
        verdict=str(payload.get("verdict") or ""),
        reasons=_as_str_list(payload.get("reasons")),
        raw=raw,
        error=None,
    )


def _as_str_list(value: Any) -> List[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    return []


def resolve_llm_transport() -> Optional[Callable[..., Any]]:
    """
    Find an existing LLM transport callable, or ``None``.

    Looks for ``scripts.llm_call`` then ``src/openclaw_orchestration/llm_call.py``
    and picks the first of ``llm_call`` / ``call_llm`` / ``complete`` / ``chat``
    it finds.  Returning ``None`` puts the runner in deterministic fallback mode.
    """
    import importlib

    for module_name in (
        "scripts.llm_call",
        "openclaw_orchestration.llm_call",
        "src.openclaw_orchestration.llm_call",
    ):
        try:
            module = importlib.import_module(module_name)
        except Exception:
            continue
        for attr in ("llm_call", "call_llm", "complete", "chat", "run"):
            fn = getattr(module, attr, None)
            if callable(fn):
                return fn
    return None


JUDGE_SYSTEM_INSTRUCTIONS = (
    "You are grading one candidate implementation of a software task. "
    "Answer with a single JSON object and nothing else: "
    '{"score": <float 0..1>, "verdict": "<one sentence>", "reasons": ["<short>", ...]}. '
    "score 1.0 means every acceptance criterion is clearly met; 0.0 means none are."
)


class LLMJudge:
    """LLM-as-a-Judge scorer on top of whatever LLM transport the repo exposes."""

    def __init__(
        self,
        model: Optional[str] = None,
        transport: Optional[Callable[..., Any]] = None,
        *,
        auto_discover: bool = True,
    ) -> None:
        self.model = model or default_judge_model()
        if transport is None and auto_discover:
            transport = resolve_llm_transport()
        self.transport = transport

    @property
    def available(self) -> bool:
        return callable(self.transport)

    def build_prompt(
        self,
        task_spec: TaskSpec,
        variant: VariantResult,
        diff_stats: Optional[DiffStats] = None,
    ) -> str:
        lines = [JUDGE_SYSTEM_INSTRUCTIONS, "", f"Task: {task_spec.slug}"]
        if task_spec.description:
            lines.append(f"Description: {task_spec.description}")
        if task_spec.acceptance_criteria:
            lines.append("")
            lines.append("Acceptance criteria:")
            lines.extend(f"- {c}" for c in task_spec.acceptance_criteria)
        lines += [
            "",
            f"Candidate variant {variant.index} (model {variant.model})",
            f"branch: {variant.branch}",
            f"pull request: {variant.pr_url or 'none'}",
            f"agent state: {variant.state}",
        ]
        if variant.error:
            lines.append(f"agent error: {variant.error}")
        if diff_stats is not None:
            lines.append(f"diff: {diff_stats.summary()}")
            if diff_stats.files:
                lines.append("changed files:")
                lines.extend(f"- {p}" for p in diff_stats.files[:50])
        lines.append("")
        lines.append("Respond with the JSON object only.")
        return "\n".join(lines)

    def evaluate(
        self,
        task_spec: TaskSpec,
        variant: VariantResult,
        diff_stats: Optional[DiffStats] = None,
    ) -> JudgeVerdict:
        if not self.available:
            return JudgeVerdict(
                score=None,
                raw="",
                error="judge transport unavailable",
                mode=JUDGE_MODE_LLM,
            )
        prompt = self.build_prompt(task_spec, variant, diff_stats)
        try:
            reply = self._call(prompt)
        except Exception as exc:  # transport failures must not abort the run
            return JudgeVerdict(score=None, raw="", error=f"judge call failed: {exc}")
        return parse_judge_response(reply)

    def _call(self, prompt: str) -> Any:
        assert self.transport is not None
        try:
            return self.transport(prompt=prompt, model=self.model)
        except TypeError:
            try:
                return self.transport(prompt, model=self.model)
            except TypeError:
                return self.transport(prompt)


def score_variant_fallback(diff_stats: Optional[DiffStats]) -> JudgeVerdict:
    """
    Deterministic, network-free scoring from diff statistics.

    See the ``FALLBACK_WEIGHT_*`` constants for the weighting rationale.
    """
    if diff_stats is None or not diff_stats.available:
        reason = (
            diff_stats.error
            if diff_stats is not None and diff_stats.error
            else "no diff statistics"
        )
        return JudgeVerdict(
            score=0.0,
            verdict="no diff available for fallback scoring",
            reasons=[str(reason)],
            raw="",
            mode=JUDGE_MODE_FALLBACK,
        )

    tests_component = 1.0 if diff_stats.touches_tests else 0.0
    new_src_component = min(
        diff_stats.new_src_files / FALLBACK_NEW_SRC_SATURATION, 1.0
    )
    files_component = min(
        diff_stats.files_changed / FALLBACK_FILES_CHANGED_SATURATION, 1.0
    )
    lines_component = min(
        diff_stats.line_volume / FALLBACK_LINE_VOLUME_SATURATION, 1.0
    )

    score = (
        FALLBACK_WEIGHT_TESTS * tests_component
        + FALLBACK_WEIGHT_NEW_SRC * new_src_component
        + FALLBACK_WEIGHT_FILES_CHANGED * files_component
        + FALLBACK_WEIGHT_LINE_VOLUME * lines_component
    )
    score = round(min(1.0, max(0.0, score)), 4)

    reasons = [
        f"tests touched: {diff_stats.test_files} "
        f"(weight {FALLBACK_WEIGHT_TESTS})",
        f"new src files: {diff_stats.new_src_files} "
        f"(weight {FALLBACK_WEIGHT_NEW_SRC})",
        f"files changed: {diff_stats.files_changed} "
        f"(weight {FALLBACK_WEIGHT_FILES_CHANGED})",
        f"line volume: {diff_stats.line_volume} "
        f"(weight {FALLBACK_WEIGHT_LINE_VOLUME})",
    ]
    return JudgeVerdict(
        score=score,
        verdict=f"fallback diff score {score:.2f}: {diff_stats.summary()}",
        reasons=reasons,
        raw="",
        mode=JUDGE_MODE_FALLBACK,
    )


# --------------------------------------------------------------------------- #
# Worktrees


class MinimalWorktreeOrchestrator:
    """
    Minimal ``git worktree add`` wrapper.

    Placeholder until Task 12's ``scripts/worktree_orchestrator.py`` is on main;
    :class:`BestOfNRunner` prefers that module when it can be imported.
    """

    def __init__(
        self,
        repo_root: Optional[Path] = None,
        worktree_root: Optional[Path] = None,
        *,
        base_ref: str = "main",
        runner: Callable[..., Any] = subprocess.run,
    ) -> None:
        self.repo_root = Path(repo_root or _REPO_ROOT)
        self.worktree_root = Path(
            worktree_root
            or os.environ.get(
                "OPENCLAW_WORKTREE_ROOT", str(self.repo_root.parent / "worktrees")
            )
        )
        self.base_ref = base_ref
        self.runner = runner

    def create_worktree(self, branch: str, base_ref: Optional[str] = None) -> Path:
        path = self.worktree_root / branch.replace("/", "_")
        self.worktree_root.mkdir(parents=True, exist_ok=True)
        proc = self.runner(
            [
                "git",
                "worktree",
                "add",
                "-B",
                branch,
                str(path),
                base_ref or self.base_ref,
            ],
            capture_output=True,
            text=True,
            timeout=180,
            cwd=str(self.repo_root),
        )
        if getattr(proc, "returncode", 1) != 0:
            raise RuntimeError(
                (getattr(proc, "stderr", "") or "git worktree add failed").strip()
            )
        return path

    def remove_worktree(self, branch: str) -> None:
        path = self.worktree_root / branch.replace("/", "_")
        self.runner(
            ["git", "worktree", "remove", "--force", str(path)],
            capture_output=True,
            text=True,
            timeout=180,
            cwd=str(self.repo_root),
        )


def resolve_worktree_orchestrator(base_ref: str = "main") -> Any:
    """Use Task 12's orchestrator when importable, else the minimal fallback."""
    import importlib

    for module_name in ("scripts.worktree_orchestrator", "worktree_orchestrator"):
        try:
            module = importlib.import_module(module_name)
        except Exception:
            continue
        for attr in ("WorktreeOrchestrator", "Orchestrator"):
            cls = getattr(module, attr, None)
            if isinstance(cls, type):
                try:
                    return cls()
                except Exception:
                    continue
    return MinimalWorktreeOrchestrator(base_ref=base_ref)


def create_worktree(orchestrator: Any, branch: str, base_ref: str = "main") -> Optional[str]:
    """Duck-typed worktree creation across orchestrator implementations."""
    if orchestrator is None:
        return None
    for attr in ("create_worktree", "create", "add", "ensure_worktree", "prepare"):
        fn = getattr(orchestrator, attr, None)
        if not callable(fn):
            continue
        try:
            result = fn(branch, base_ref)
        except TypeError:
            try:
                result = fn(branch)
            except TypeError:
                continue
        return str(result) if result is not None else None
    raise AttributeError(
        f"{type(orchestrator).__name__} exposes no worktree creation method"
    )


# --------------------------------------------------------------------------- #
# Winner selection


def select_winner(variants: Sequence[VariantResult]) -> Optional[VariantResult]:
    """
    Highest score wins.  Ties are broken deterministically by the lower
    ``latency_s`` and then by the lower variant index — never randomly.
    """
    scored = [v for v in variants if isinstance(v.score, (int, float))]
    if not scored:
        return None

    def sort_key(v: VariantResult):
        latency = v.latency_s if isinstance(v.latency_s, (int, float)) else float("inf")
        return (-float(v.score), latency, v.index)

    return sorted(scored, key=sort_key)[0]


# --------------------------------------------------------------------------- #
# Reports


def _format_score(score: Optional[float]) -> str:
    return "n/a" if score is None else f"{score:.2f}"


def render_markdown_report(result: BestOfNResult) -> str:
    lines = [
        f"# Best-of-{result.n}: {result.task_slug}",
        "",
        f"- created_at: `{result.created_at}`",
        f"- judge_model: `{result.judge_model}`",
        f"- judge_mode: `{result.judge_mode}`",
    ]
    if result.winner:
        lines.append(
            f"- winner: variant {result.winner.index} "
            f"(`{result.winner.model}`) score {_format_score(result.winner.score)}"
        )
    else:
        lines.append("- winner: none")
    lines += [
        "",
        "| # | model | score | state | latency s | branch | PR |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for v in sorted(result.variants, key=lambda x: x.index):
        latency = "n/a" if v.latency_s is None else f"{v.latency_s:.1f}"
        pr = v.pr_url or "—"
        marker = " 🏆" if result.winner and v.index == result.winner.index else ""
        lines.append(
            f"| {v.index}{marker} | `{v.model}` | {_format_score(v.score)} | "
            f"{v.state} | {latency} | `{v.branch}` | {pr} |"
        )
    lines.append("")
    for v in sorted(result.variants, key=lambda x: x.index):
        lines.append(f"## Variant {v.index} — `{v.model}`")
        lines.append("")
        lines.append(f"- verdict: {v.verdict or '—'}")
        if v.error:
            lines.append(f"- error: {v.error}")
        if v.diff_stats:
            lines.append(f"- diff: `{json.dumps(v.diff_stats, sort_keys=True)}`")
        lines.append("")
    return "\n".join(lines)


def write_report(
    result: BestOfNResult,
    *,
    report_dir: Optional[Path] = None,
    timestamp: Optional[str] = None,
) -> tuple[Path, Path]:
    """Write ``<slug>_<UTCtimestamp>.json`` plus the markdown twin."""
    directory = Path(report_dir or REPORT_DIR)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = timestamp or _utc_timestamp()
    json_path = directory / f"{result.task_slug}_{stamp}.json"
    md_path = directory / f"{result.task_slug}_{stamp}.md"
    json_path.write_text(
        json.dumps(result.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    result.report_path = str(json_path)
    result.markdown_path = str(md_path)
    md_path.write_text(render_markdown_report(result), encoding="utf-8")
    return json_path, md_path


# --------------------------------------------------------------------------- #
# Telegram digest


def build_digest(result: BestOfNResult) -> str:
    """One line per variant, the winner first."""
    winner = result.winner
    header = (
        f"🏆 Best-of-{result.n} {result.task_slug} winner: variant {winner.index} "
        f"({winner.model}) score {_format_score(winner.score)}"
        if winner
        else f"Best-of-{result.n} {result.task_slug}: no winner"
    )
    lines = [header]
    if winner and winner.pr_url:
        lines.append(winner.pr_url)
    for v in sorted(result.variants, key=lambda x: x.index):
        lines.append(
            f"{v.index}. {v.model} — {_format_score(v.score)} — "
            f"{v.pr_url or v.state}"
        )
    lines.append(f"judge: {result.judge_model} ({result.judge_mode})")
    return "\n".join(lines)


def _build_default_telegram_sender() -> Optional[Any]:
    """Build a configured :class:`TelegramSender`, or ``None`` if unconfigured."""
    try:
        from scripts.telegram_sender import TelegramConfig, TelegramSender

        return TelegramSender(TelegramConfig.from_env())
    except Exception:
        return None


def send_telegram_digest(
    result: BestOfNResult,
    *,
    sender: Optional[Any] = None,
    sender_factory: Callable[[], Optional[Any]] = _build_default_telegram_sender,
) -> bool:
    """
    Send the digest, but only for N >= 2 with a winner.

    ``N == 1`` stays silent so single-agent behaviour is unchanged, and an
    unconfigured Telegram bot simply means the report is the only output.
    """
    if result.n < 2 or result.winner is None:
        return False
    client = sender if sender is not None else sender_factory()
    if client is None:
        return False
    try:
        client.send_message(build_digest(result))
    except Exception as exc:
        print(f"⚠️ Telegram digest not sent: {exc}")
        return False
    return True


# --------------------------------------------------------------------------- #
# Runner


def build_variants(
    models: Sequence[str],
    *,
    n: Optional[int] = None,
    seeds: Optional[Sequence[Optional[int]]] = None,
    labels: Optional[Sequence[str]] = None,
) -> List[VariantSpec]:
    """Build 1..N variant specs, cycling the model list when N > len(models)."""
    model_list = [str(m) for m in models if str(m).strip()]
    if not model_list:
        raise ValueError("at least one model is required")
    total = int(n or len(model_list))
    if total < 1:
        raise ValueError("n must be >= 1")
    variants: List[VariantSpec] = []
    for i in range(total):
        seed = seeds[i] if seeds and i < len(seeds) else None
        label = labels[i] if labels and i < len(labels) else ""
        variants.append(
            VariantSpec(
                index=i + 1,
                model=model_list[i % len(model_list)],
                seed=seed,
                label=label,
            )
        )
    return variants


def variant_branch_name(task_slug: str, index: int) -> str:
    """``cursor/<slug>-variant-<k>``."""
    return f"{BRANCH_PREFIX}/{slugify(task_slug)}-variant-{int(index)}"


class BestOfNRunner:
    """
    Fan one task out to N cloud agents, then score and rank the variants.

    ``queue_manager`` only needs to expose ``launch_agent_prompt`` and
    ``poll_running_agents_batch`` (plus the polling constants), so tests can
    pass a stub in place of the real module.
    """

    def __init__(
        self,
        queue_manager: Any = None,
        worktree_orchestrator: Any = None,
        judge: Any = None,
        *,
        diff_stats_fn: Optional[Callable[..., DiffStats]] = None,
        report_dir: Optional[Path] = None,
        telegram_sender: Optional[Any] = None,
        base_ref: Optional[str] = None,
        sleeper: Optional[Callable[[float], None]] = None,
        clock: Callable[[], float] = time.monotonic,
        max_wait_s: float = DEFAULT_MAX_WAIT_SEC,
        notify: bool = True,
    ) -> None:
        self.queue_manager = queue_manager or _default_queue_manager
        self.base_ref = base_ref or getattr(self.queue_manager, "REF", "main")
        self.worktree_orchestrator = (
            worktree_orchestrator
            if worktree_orchestrator is not None
            else resolve_worktree_orchestrator(self.base_ref)
        )
        self.judge = judge if judge is not None else LLMJudge()
        self.diff_stats_fn = diff_stats_fn or collect_diff_stats
        self.report_dir = Path(report_dir or REPORT_DIR)
        self.telegram_sender = telegram_sender
        self.clock = clock
        self.max_wait_s = float(max_wait_s)
        self.notify = notify
        self._sleeper = sleeper

    # -- prompt ----------------------------------------------------------- #

    def build_variant_prompt(
        self, task_spec: TaskSpec, variant: VariantSpec, total: int, branch: str
    ) -> str:
        lines = [
            f"variant: {variant.index}/{total}",
            f"model: {variant.model}",
        ]
        if variant.label:
            lines.append(f"label: {variant.label}")
        if variant.seed is not None:
            lines.append(f"seed: {variant.seed}")
        lines += [
            f"branch: {branch}",
            "",
            f"Task: {task_spec.slug}",
        ]
        if task_spec.description:
            lines.append(task_spec.description)
        body = task_spec.body()
        if body:
            lines += ["", body]
        if task_spec.acceptance_criteria:
            lines += ["", "Acceptance criteria:"]
            lines += [f"- {c}" for c in task_spec.acceptance_criteria]
        lines += [
            "",
            f"You are variant {variant.index} of {total} competing attempts at this "
            "task; work independently and do not reference the other variants.",
            f"Commit your work on branch {branch} and open a pull request. "
            "Reply with the PR URL.",
        ]
        return "\n".join(lines)

    # -- orchestration ---------------------------------------------------- #

    def run(self, task_spec: TaskSpec, variants: Sequence[VariantSpec]) -> BestOfNResult:
        if not variants:
            raise ValueError("at least one variant is required")
        indexes = [v.index for v in variants]
        if len(set(indexes)) != len(indexes):
            raise ValueError(f"variant indexes must be unique, got {indexes}")

        total = len(variants)
        results: Dict[int, VariantResult] = {}
        prompts: Dict[int, str] = {}
        launch_queue: List[VariantSpec] = []

        for variant in variants:
            branch = variant_branch_name(task_spec.slug, variant.index)
            result = VariantResult(
                index=variant.index,
                model=variant.model,
                branch=branch,
                state=STATE_PENDING,
            )
            results[variant.index] = result
            prompts[variant.index] = self.build_variant_prompt(
                task_spec, variant, total, branch
            )
            try:
                result.worktree = create_worktree(
                    self.worktree_orchestrator, branch, self.base_ref
                )
            except Exception as exc:
                result.state = STATE_WORKTREE_FAILED
                result.error = f"worktree setup failed: {exc}"
                print(f"❌ {branch}: {result.error}")
                continue
            launch_queue.append(variant)

        self._drive_agents(launch_queue, results, prompts)
        self._score_variants(task_spec, results)

        ordered = [results[i] for i in sorted(results)]
        winner = select_winner(ordered)
        modes = {r.judge_mode for r in ordered if r.judge_mode}
        judge_mode = JUDGE_MODE_LLM if JUDGE_MODE_LLM in modes else JUDGE_MODE_FALLBACK

        result = BestOfNResult(
            task_slug=task_spec.slug,
            variants=ordered,
            winner=winner,
            judge_model=getattr(self.judge, "model", DEFAULT_JUDGE_MODEL),
            created_at=_utc_now_iso(),
            judge_mode=judge_mode,
        )
        write_report(result, report_dir=self.report_dir)
        if self.notify:
            send_telegram_digest(result, sender=self.telegram_sender)
        return result

    def _sleep(self, seconds: float) -> None:
        if self._sleeper is not None:
            self._sleeper(seconds)
            return
        jitter = getattr(self.queue_manager, "_sleep_with_jitter", None)
        if callable(jitter):
            jitter(seconds)
        else:
            time.sleep(seconds)

    def _launch(self, branch: str, prompt: str, model: str) -> Dict[str, Any]:
        launcher = getattr(self.queue_manager, "launch_agent_prompt", None)
        if not callable(launcher):
            raise AttributeError(
                "queue_manager must expose launch_agent_prompt(branch, prompt, model=...)"
            )
        try:
            return launcher(branch, prompt, model=model) or {}
        except TypeError:
            return launcher(branch, prompt) or {}

    def _drive_agents(
        self,
        launch_queue: List[VariantSpec],
        results: Dict[int, VariantResult],
        prompts: Dict[int, str],
    ) -> None:
        """
        Launch the variants through queue_manager's existing batch mechanism and
        poll them with its batch poller / exponential backoff.
        """
        qm = self.queue_manager
        batch_size = int(getattr(qm, "BATCH_SIZE", 5) or 5)
        poll_initial = float(getattr(qm, "POLL_INTERVAL_INITIAL", 10.0))
        poll_max = float(getattr(qm, "POLL_INTERVAL_MAX", 300.0))
        backoff = float(getattr(qm, "POLL_BACKOFF_MULTIPLIER", 2.0))
        poll_batch = getattr(qm, "poll_running_agents_batch", None)

        pending = list(launch_queue)
        running: Dict[str, int] = {}
        agent_tracker: Dict[str, Dict[str, Any]] = {}
        started_at: Dict[int, float] = {}
        poll_interval = poll_initial
        deadline = self.clock() + self.max_wait_s

        while pending or running:
            while pending and len(running) < batch_size:
                variant = pending.pop(0)
                result = results[variant.index]
                started_at[variant.index] = self.clock()
                launched = self._launch(
                    result.branch, prompts[variant.index], variant.model
                )
                agent_id = launched.get("agent_id")
                if not agent_id:
                    result.state = STATE_LAUNCH_FAILED
                    result.error = str(launched.get("status") or "launch failed")
                    result.latency_s = round(self.clock() - started_at[variant.index], 3)
                    print(f"❌ Launch failed for {result.branch}: {result.error}")
                    continue
                agent_id = str(agent_id)
                result.agent_id = agent_id
                running[agent_id] = variant.index
                agent_tracker.setdefault(
                    agent_id,
                    {"poll_count": 0, "started_ts": time.time(), "last_poll_ts": 0.0},
                )
                poll_interval = poll_initial
                print(f"✅ Launched variant {variant.index} ({variant.model}): {agent_id}")

            if not running:
                continue

            if not callable(poll_batch):
                raise AttributeError(
                    "queue_manager must expose poll_running_agents_batch(running, tracker)"
                )
            # queue_manager's poller expects agent_id -> branch
            branch_map = {aid: results[idx].branch for aid, idx in running.items()}
            batch = poll_batch(branch_map, agent_tracker) or {}

            finished_this_round = False
            for agent_id in list(running):
                poll = batch.get(agent_id)
                if poll is None:
                    continue
                status = getattr(poll, "status", "PROCESSING")
                if status in ("RUNNING", "PROCESSING"):
                    continue

                index = running.pop(agent_id)
                result = results[index]
                finished_this_round = True
                pr_urls = list(getattr(poll, "pr_urls", []) or [])
                if pr_urls:
                    result.pr_url = pr_urls[0]
                result.latency_s = round(
                    self.clock() - started_at.get(index, self.clock()), 3
                )
                result.state = {
                    "FINISHED": STATE_COMPLETED,
                    "FAILED": STATE_FAILED,
                    "TIMEOUT": STATE_TIMEOUT,
                    "NOT_FOUND": STATE_NOT_FOUND,
                    "ERROR": STATE_POLL_FAILED,
                }.get(status, STATE_FAILED)
                if result.state != STATE_COMPLETED:
                    result.error = (
                        getattr(poll, "error_message", None) or f"agent status {status}"
                    )
                print(f"• variant {index}: {status} ({result.state})")

            if running:
                if self.clock() > deadline:
                    for agent_id, index in list(running.items()):
                        result = results[index]
                        result.state = STATE_TIMEOUT
                        result.error = "best-of-N wait budget exhausted"
                        result.latency_s = round(
                            self.clock() - started_at.get(index, self.clock()), 3
                        )
                    running.clear()
                    break
                poll_interval = (
                    poll_initial
                    if finished_this_round
                    else min(poll_interval * backoff, poll_max)
                )
                self._sleep(poll_interval)

    def _score_variants(
        self, task_spec: TaskSpec, results: Dict[int, VariantResult]
    ) -> None:
        for index in sorted(results):
            result = results[index]
            stats: Optional[DiffStats] = None
            try:
                stats = self.diff_stats_fn(result.branch, base_ref=self.base_ref)
            except TypeError:
                try:
                    stats = self.diff_stats_fn(result.branch)
                except Exception as exc:
                    stats = DiffStats(available=False, error=str(exc))
            except Exception as exc:
                stats = DiffStats(available=False, error=str(exc))
            if stats is not None:
                result.diff_stats = stats.to_dict()

            verdict: Optional[JudgeVerdict] = None
            judge_error: Optional[str] = None
            evaluate = getattr(self.judge, "evaluate", None)
            if callable(evaluate):
                try:
                    verdict = evaluate(task_spec, result, stats)
                except Exception as exc:
                    judge_error = f"judge raised: {exc}"
            else:
                judge_error = "judge has no evaluate()"

            if verdict is not None and verdict.score is not None:
                result.score = float(verdict.score)
                result.verdict = verdict.verdict
                result.judge_raw = verdict.raw
                result.judge_mode = verdict.mode or JUDGE_MODE_LLM
                continue

            judge_error = judge_error or (
                verdict.error if verdict is not None else "judge unavailable"
            )
            fallback = score_variant_fallback(stats)
            result.score = fallback.score
            result.verdict = fallback.verdict
            result.judge_raw = verdict.raw if verdict is not None else ""
            result.judge_mode = JUDGE_MODE_FALLBACK
            if result.error:
                result.error = f"{result.error}; {judge_error}"
            else:
                result.error = judge_error


# --------------------------------------------------------------------------- #
# CLI glue (also used by scripts/queue_manager.py)


def resolve_task_spec(
    *,
    task_spec_path: Optional[os.PathLike[str] | str] = None,
    task_name: Optional[str] = None,
    task_slug: Optional[str] = None,
    task_prompt: Optional[str] = None,
) -> TaskSpec:
    if task_spec_path:
        spec = TaskSpec.from_yaml(task_spec_path, task_name=task_name)
        if task_slug:
            spec.slug = slugify(task_slug)
        if task_prompt:
            spec.prompt = task_prompt
        return spec
    if not task_slug:
        raise ValueError("--task-slug is required when --task-spec is not given")
    return TaskSpec(slug=slugify(task_slug), prompt=task_prompt or "")


def run_best_of_n(
    *,
    n: int,
    task_spec_path: Optional[os.PathLike[str] | str] = None,
    task_name: Optional[str] = None,
    task_slug: Optional[str] = None,
    task_prompt: Optional[str] = None,
    models: Optional[Sequence[str]] = None,
    judge_model: Optional[str] = None,
    report_dir: Optional[Path] = None,
    notify: bool = True,
    runner: Optional[BestOfNRunner] = None,
) -> BestOfNResult:
    """Entry point used by ``queue_manager.py --best-of N``."""
    if int(n) < 2:
        raise ValueError("--best-of requires N >= 2")
    spec = resolve_task_spec(
        task_spec_path=task_spec_path,
        task_name=task_name,
        task_slug=task_slug,
        task_prompt=task_prompt,
    )
    variant_models = list(models) if models else default_models()
    variants = build_variants(variant_models, n=int(n))
    active = runner or BestOfNRunner(
        judge=LLMJudge(judge_model or default_judge_model()),
        report_dir=report_dir,
        notify=notify,
    )
    result = active.run(spec, variants)
    print(
        f"\n🏁 best-of-{result.n} {result.task_slug}: "
        f"winner={result.winner.index if result.winner else 'none'} "
        f"judge_mode={result.judge_mode}"
    )
    if result.report_path:
        print(f"📄 {result.report_path}")
    return result


def add_cli_arguments(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """Attach the best-of-N flags (shared with queue_manager's parser)."""
    parser.add_argument(
        "--best-of",
        type=int,
        default=1,
        metavar="N",
        help=(
            "Run N competing cloud agents (different models) on one task, each "
            "in its own worktree/branch/PR, then judge and rank them. "
            "N < 2 keeps the classic one-agent-per-branch behaviour."
        ),
    )
    parser.add_argument(
        "--best-of-models",
        nargs="+",
        metavar="MODEL",
        help=(
            "Explicit model list for best-of-N variants "
            f"(default: {' '.join(DEFAULT_VARIANT_MODELS)})"
        ),
    )
    parser.add_argument(
        "--best-of-judge-model",
        metavar="MODEL",
        help=f"Model used for LLM-as-a-Judge scoring (default: {DEFAULT_JUDGE_MODEL})",
    )
    parser.add_argument(
        "--task-spec",
        type=Path,
        metavar="YAML",
        help="YAML task spec (task_runner format) providing prompt + acceptance criteria",
    )
    parser.add_argument(
        "--task-name",
        help="Task name to pick when --task-spec contains a 'tasks' list",
    )
    parser.add_argument("--task-slug", help="Slug used for branches and report names")
    parser.add_argument("--task-prompt", help="Inline task prompt (instead of --task-spec)")
    parser.add_argument(
        "--best-of-no-telegram",
        action="store_true",
        help="Write the report but skip the Telegram digest",
    )
    return parser


def run_from_args(args: argparse.Namespace) -> int:
    """Dispatch a parsed namespace (from queue_manager or this module)."""
    try:
        run_best_of_n(
            n=int(getattr(args, "best_of", 0) or 0),
            task_spec_path=getattr(args, "task_spec", None),
            task_name=getattr(args, "task_name", None),
            task_slug=getattr(args, "task_slug", None),
            task_prompt=getattr(args, "task_prompt", None),
            models=getattr(args, "best_of_models", None),
            judge_model=getattr(args, "best_of_judge_model", None),
            notify=not getattr(args, "best_of_no_telegram", False),
        )
    except ValueError as exc:
        print(f"Error: {exc}")
        return 2
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Best-of-N runner for Cursor Cloud Agents"
    )
    parser.add_argument("action", choices=["run"], nargs="?", default="run")
    add_cli_arguments(parser)
    args = parser.parse_args(list(argv) if argv is not None else None)
    if int(args.best_of or 0) < 2:
        args.best_of = max(2, len(args.best_of_models or default_models()))
    return run_from_args(args)


if __name__ == "__main__":
    raise SystemExit(main())
