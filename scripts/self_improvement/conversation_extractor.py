#!/usr/bin/env python3
"""
Scan OpenClaw-style session transcripts and emit structured insights for memory
and training pipelines.

Reuses the session parsers in ``scripts.conversation_extractor`` (JSON envelopes
``messages`` / ``conversation`` / ``transcript`` / ``history``, Anthropic-style
``tool_use`` blocks, and line-oriented logs). On top of that baseline digest,
this module labels **key moments**: user corrections, agent-side failures,
successful resolution strategies, and lightweight correction / affirmation pairs
suitable for preference or distillation workflows.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

from scripts.conversation_extractor import (
    ConversationDigest,
    analyze_segments,
    digest_to_dict,
    parse_session_log,
    repo_root,
    utc_stamp,
    write_digest,
)

ARTIFACT_TYPE = "openclaw_session_insights"
SCHEMA_VERSION = 1

# User pushback / steering (often follows a weak assistant reply)
USER_CORRECTION_HINTS = re.compile(
    r"(?i)\b("
    r"no[,.\s]|actually,|actually\s|you('?re)?\s+wrong|that's\s+wrong|not\s+quite|"
    r"incorrect|should\s+be|instead\s+of|i\s+meant|correction|fix\s+this|"
    r"don'?t\s+(do|use|run|call)|revert|rollback|undo|misunderstood|misread|"
    r"that\s+wasn'?t|not\s+what\s+i|stop\s+and|try\s+again|"
    r"wrong\s+(file|path|command|approach)|use\s+\S+\s+instead"
    r")\b"
)

# Agent / tool failure surfaces
AGENT_ERROR_HINTS = re.compile(
    r"(?i)(\btraceback\b|exception\s*:|error\s*:|errno\s|"
    r"failed\s+to|failure:|timed?\s*out|connection\s+refused|"
    r"\b5\d{2}\b|exit\s+code\s*[1-9]|"
    r"i\s+apologize|my\s+mistake|i\s+was\s+wrong|i\s+incorrectly|"
    r"bug\s+in|not\s+supported|permission\s+denied|command\s+not\s+found)"
)

# Outcomes that read as resolved wins (assistant or tool narration)
SUCCESS_STRATEGY_HINTS = re.compile(
    r"(?i)\b("
    r"fixed|resolved|tests?\s+pass|all\s+tests\s+pass|all\s+green|"
    r"success(?:fully)?|completed|deployed|merged|verified|"
    r"works\s+now|that\s+worked|unblocked|shipped|root\s+cause\s+was|"
    r"the\s+issue\s+was|patch\s+applied|commit\s+pushed"
    r")\b"
)

USER_AFFIRMATION_HINTS = re.compile(
    r"(?i)\b("
    r"thanks|thank\s+you|perfect|great|that\s+works|lgtm|exactly|"
    r"got\s+it|makes\s+sense|solved|nice|awesome|that'?s\s+correct"
    r")\b"
)

ROLE_ASSISTANT = frozenset({"assistant", "agent", ""})
ROLE_USER = frozenset({"user", "human"})


def _norm_role(role: str | None) -> str:
    if not role:
        return "unknown"
    r = role.strip().lower()
    return {"human": "user", "agent": "assistant"}.get(r, r)


def _clip(text: str, limit: int = 480) -> str:
    t = " ".join(text.split())
    if len(t) <= limit:
        return t
    return t[: max(0, limit - 3)] + "..."


def _neighbor_roles(segments: list[tuple[int, str | None, str]], idx: int) -> tuple[str | None, str | None]:
    prev_r = _norm_role(segments[idx - 1][1]) if idx > 0 else None
    next_r = _norm_role(segments[idx + 1][1]) if idx + 1 < len(segments) else None
    return prev_r, next_r


@dataclass
class LabeledMoment:
    """One salient span inside a session."""

    kind: str  # user_correction | agent_error | success_strategy | user_affirmation
    turn: int
    role: str
    evidence: str
    prev_role: str | None = None
    next_role: str | None = None


@dataclass
class TrainingSnippet:
    """Compact pair or span for distillation / preference datasets."""

    snippet_type: str
    turn: int
    fields: dict[str, Any] = field(default_factory=dict)


def _label_segment(
    segments: list[tuple[int, str | None, str]], idx: int
) -> tuple[list[LabeledMoment], list[TrainingSnippet]]:
    """Derive moments and optional training-oriented snippets for one segment index."""

    turn, role, text = segments[idx]
    rl = _norm_role(role)
    stripped = text.strip()
    if len(stripped) < 8:
        return [], []

    moments: list[LabeledMoment] = []
    snippets: list[TrainingSnippet] = []
    prev_r, next_r = _neighbor_roles(segments, idx)

    if rl in ROLE_USER and USER_CORRECTION_HINTS.search(stripped):
        moments.append(
            LabeledMoment(
                kind="user_correction",
                turn=turn,
                role=rl,
                evidence=_clip(stripped),
                prev_role=prev_r,
                next_role=next_r,
            )
        )
        if prev_r in ROLE_ASSISTANT | {"unknown", "tool_output"} and idx > 0:
            _pturn, _pr, prev_text = segments[idx - 1]
            snippets.append(
                TrainingSnippet(
                    snippet_type="correction_pair",
                    turn=turn,
                    fields={
                        "prior_assistant_excerpt": _clip(prev_text),
                        "user_correction": _clip(stripped),
                    },
                )
            )

    if rl in ROLE_USER and USER_AFFIRMATION_HINTS.search(stripped):
        moments.append(
            LabeledMoment(
                kind="user_affirmation",
                turn=turn,
                role=rl,
                evidence=_clip(stripped),
                prev_role=prev_r,
                next_role=next_r,
            )
        )
        if prev_r in ROLE_ASSISTANT and idx > 0:
            _pturn, _pr, prev_text = segments[idx - 1]
            snippets.append(
                TrainingSnippet(
                    snippet_type="positive_feedback_pair",
                    turn=turn,
                    fields={
                        "assistant_excerpt": _clip(prev_text),
                        "user_reaction": _clip(stripped),
                    },
                )
            )

    if rl in ROLE_ASSISTANT | {"unknown"} and SUCCESS_STRATEGY_HINTS.search(stripped):
        moments.append(
            LabeledMoment(
                kind="success_strategy",
                turn=turn,
                role=rl,
                evidence=_clip(stripped),
                prev_role=prev_r,
                next_role=next_r,
            )
        )

    if rl in ROLE_ASSISTANT | {"unknown", "tool", "tool_output"} and AGENT_ERROR_HINTS.search(stripped):
        moments.append(
            LabeledMoment(
                kind="agent_error",
                turn=turn,
                role=rl,
                evidence=_clip(stripped),
                prev_role=prev_r,
                next_role=next_r,
            )
        )

    return moments, snippets


def extract_labeled_moments(
    segments: list[tuple[int, str | None, str]],
) -> tuple[list[LabeledMoment], list[TrainingSnippet]]:
    all_moments: list[LabeledMoment] = []
    all_snippets: list[TrainingSnippet] = []
    for i in range(len(segments)):
        m, s = _label_segment(segments, i)
        all_moments.extend(m)
        all_snippets.extend(s)
    return all_moments, all_snippets


def moments_summary(moments: Iterable[LabeledMoment]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for m in moments:
        counts[m.kind] = counts.get(m.kind, 0) + 1
    return counts


def build_insights_payload(
    source_display: str,
    digest: ConversationDigest,
    moments: list[LabeledMoment],
    snippets: list[TrainingSnippet],
) -> dict[str, Any]:
    """Single JSON document for memory / training consumers."""

    return {
        "artifact_type": ARTIFACT_TYPE,
        "schema_version": SCHEMA_VERSION,
        "source": source_display,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "counts": {
            **digest_to_dict(digest)["counts"],
            "key_moments": len(moments),
            "training_snippets": len(snippets),
            "moments_by_kind": moments_summary(moments),
        },
        "session_digest": digest_to_dict(digest),
        "key_moments": [asdict(x) for x in moments],
        "training_snippets": [{"type": x.snippet_type, "turn": x.turn, **x.fields} for x in snippets],
        "memory_integration": {
            "suggested_tags": _suggested_insight_tags(digest, moments),
            "headlines": _headlines(digest, moments),
        },
    }


def _suggested_insight_tags(digest: ConversationDigest, moments: list[LabeledMoment]) -> list[str]:
    tags = list(dict.fromkeys(digest_to_dict(digest).get("memory_integration", {}).get("suggested_tags", [])))
    kind_map = moments_summary(moments)
    for k in ("user_correction", "agent_error", "success_strategy", "user_affirmation"):
        if kind_map.get(k):
            tags.append(k)
    return list(dict.fromkeys(tags))


def _headlines(digest: ConversationDigest, moments: list[LabeledMoment]) -> list[str]:
    lines: list[str] = []
    if digest.decisions:
        lines.append(f"Decisions: {len(digest.decisions)}")
    if digest.learnings:
        lines.append(f"Explicit learnings: {len(digest.learnings)}")
    ms = moments_summary(moments)
    if ms.get("user_correction"):
        lines.append(f"User corrections detected: {ms['user_correction']}")
    if ms.get("agent_error"):
        lines.append(f"Agent error signals: {ms['agent_error']}")
    if ms.get("success_strategy"):
        lines.append(f"Success / resolution cues: {ms['success_strategy']}")
    return lines


def render_insights_markdown(payload: dict[str, Any]) -> str:
    """Human-readable view alongside JSON."""

    lines = [
        "# Session insights (self-improvement)",
        "",
        f"- **Source**: `{payload['source']}`",
        f"- **Generated (UTC)**: {payload['generated_at_utc']}",
        f"- **Schema**: v{payload['schema_version']} (`{payload['artifact_type']}`)",
        "",
        "## Summary counts",
        "",
        "```json",
        json.dumps(payload.get("counts", {}), indent=2, ensure_ascii=False),
        "```",
        "",
        "## Key moments",
    ]
    km = payload.get("key_moments") or []
    if not km:
        lines.append("- *(none detected with current heuristics)*")
    else:
        for item in km:
            lines.append(
                f"- **{item['kind']}** (turn {item['turn']}, role `{item['role']}`): "
                f"{item['evidence']}"
            )

    lines.extend(["", "## Training-oriented snippets", ""])
    sn = payload.get("training_snippets") or []
    if not sn:
        lines.append("- *(no correction / affirmation pairs extracted)*")
    else:
        for item in sn:
            t = item.get("type", "snippet")
            body = {k: v for k, v in item.items() if k not in {"type", "turn"}}
            lines.append(f"### {t} (turn {item.get('turn')})")
            lines.append("```json")
            lines.append(json.dumps(body, indent=2, ensure_ascii=False))
            lines.append("```")
            lines.append("")

    lines.extend(
        [
            "## Digest cross-reference",
            "",
            "See `session_digest` in the JSON for decisions, learnings, patterns, and tool ranks.",
            "",
            "---",
            "*scripts.self_improvement.conversation_extractor — OpenClaw session insights.*",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


# When a directory is passed, only pick likely transcripts (not every package JSON).
_DIR_GLOBS = ("**/session.json", "**/conversation*.json", "**/*transcript*.json", "**/*.log")


def iter_session_paths(paths: Iterable[Path]) -> Iterator[Path]:
    for p in paths:
        rp = p.expanduser().resolve()
        if rp.is_file():
            yield rp
        elif rp.is_dir():
            seen: set[Path] = set()
            for pattern in _DIR_GLOBS:
                for child in rp.glob(pattern):
                    if child.is_file():
                        c = child.resolve()
                        if c not in seen:
                            seen.add(c)
                            yield c


def write_insights_artifacts(
    payload: dict[str, Any],
    memory_dir: Path,
    stem: str,
) -> tuple[Path, Path]:
    memory_dir.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^\w\-_.]+", "_", stem).strip("_") or "session"
    tag = utc_stamp()
    md_path = memory_dir / f"session_insights_{safe}_{tag}.md"
    js_path = memory_dir / f"session_insights_{safe}_{tag}.json"
    md_path.write_text(render_insights_markdown(payload), encoding="utf-8")
    js_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return md_path, js_path


def run_on_session(
    session_path: Path,
    memory_dir: Path,
    workspace_root: Path,
    *,
    write_base_digest: bool,
) -> tuple[Path, Path]:
    """Parse one transcript, enrich with moments, write JSON + Markdown."""

    abs_path = session_path.resolve()
    segments = parse_session_log(abs_path)
    rel = abs_path.as_posix()
    ws_posix = workspace_root.resolve().as_posix()
    if rel.startswith(ws_posix):
        rel = Path(rel[len(ws_posix) :].lstrip("/")).as_posix()

    digest = analyze_segments(segments, rel)
    moments, snippets = extract_labeled_moments(segments)
    payload = build_insights_payload(rel, digest, moments, snippets)

    stem = session_path.stem
    parent = session_path.parent.name
    if parent and parent not in {".", ""}:
        stem = f"{parent}__{session_path.stem}"

    md_path, js_path = write_insights_artifacts(payload, memory_dir, stem)

    if write_base_digest:
        write_digest(digest, memory_dir, stem)

    return md_path, js_path


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Extract OpenClaw session insights (patterns, corrections, errors, wins) for memory/training."
    )
    p.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="Session files (.json, .log, text) and/or directories to scan (rglob session.json, *.json, *.log).",
    )
    p.add_argument(
        "--memory-dir",
        type=Path,
        default=None,
        help="Output directory (default: <repo>/memory).",
    )
    p.add_argument(
        "--workspace-root",
        type=Path,
        default=None,
        help="Repo root for relative paths in output (default: repo root).",
    )
    p.add_argument(
        "--also-base-extract",
        action="store_true",
        help="Also write conversation_extract_* from scripts.conversation_extractor alongside session_insights_*.",
    )
    p.add_argument(
        "--stdout-json",
        action="store_true",
        help="Print combined JSON for all inputs to stdout instead of writing files.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    ws = (args.workspace_root or repo_root()).resolve()
    memory_dir = (args.memory_dir or ws / "memory").resolve()

    inputs: list[Path] = list(args.paths) if args.paths else []
    if not inputs:
        sys.stderr.write(
            "error: pass at least one session file or directory (see --help).\n"
        )
        return 2

    targets = list(dict.fromkeys(iter_session_paths(inputs)))
    if not targets:
        sys.stderr.write("error: no session files found under the given paths.\n")
        return 2

    combined: list[dict[str, Any]] = []
    for path in targets:
        if args.stdout_json:
            segments = parse_session_log(path)
            rel = path.resolve().as_posix()
            if rel.startswith(ws.as_posix()):
                rel = Path(rel[len(ws.as_posix()) :].lstrip("/")).as_posix()
            digest = analyze_segments(segments, rel)
            moments, snippets = extract_labeled_moments(segments)
            combined.append(build_insights_payload(rel, digest, moments, snippets))
            continue

        md_path, js_path = run_on_session(path, memory_dir, ws, write_base_digest=args.also_base_extract)
        print(f"{path.as_posix()} -> {md_path.as_posix()}")
        print(f"{path.as_posix()} -> {js_path.as_posix()}")

    if args.stdout_json:
        if len(combined) == 1:
            sys.stdout.write(json.dumps(combined[0], indent=2, ensure_ascii=False) + "\n")
        else:
            sys.stdout.write(json.dumps(combined, indent=2, ensure_ascii=False) + "\n")
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
