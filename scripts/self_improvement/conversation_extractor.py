#!/usr/bin/env python3
"""
Extract learning-worthy patterns from agent session exports.

Parses ``sessions_list`` and ``sessions_history`` shaped JSON or text, finds moments
explicitly tagged as correction / insight / error (plus light heuristics for user
corrections, error recovery, and successful resolutions), and writes markdown entries
under ``.learnings/conversation_patterns/``.

Typical sources:

- JSON list output with ``sessions``, ``recent``, or a top-level array of session rows
  containing ``id`` / ``key`` / ``session_id`` and a path field such as ``path``,
  ``history_path``, ``transcript_path``, or ``file``.
- Session transcript JSON or text compatible with ``scripts.conversation_extractor``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

from scripts.conversation_extractor import parse_session_log
from scripts.optimize_context import read_text, repo_root

LEARNINGS_SUBDIR = Path(".learnings") / "conversation_patterns"

# Explicit tags (line-start or whole-line friendly).
_TAG_LINE = re.compile(
    r"(?im)^\s*(?:"
    r"\[(?P<kind>correction|insight|error|solution|success)\]\s*:?\s*(?P<body>.*)$"
    r"|"
    r"<!--\s*(?P<kind2>correction|insight|error|solution|success)\s*-->\s*(?P<body2>.*)$"
    r"|"
    r"@learning/(?P<kind3>correction|insight|error|solution|success)\s*:?\s*(?P<body3>.*)$"
    r"|"
    r"moment\s*:\s*(?P<kind4>correction|insight|error|solution|success)\s*:?\s*(?P<body4>.*)$"
    r")"
)

_JSON_MOMENT_KEYS = frozenset(
    {
        "moment",
        "moment_type",
        "momentType",
        "learning_tag",
        "learningTag",
        "tag",
        "pattern",
        "kind",
    }
)

_USER_CORRECTION_HINT = re.compile(
    r"(?i)\b("
    r"actually|instead|not what i meant|you('?re)? wrong|that'?s wrong|incorrect|"
    r"i meant|correction:|please fix|rollback|revert|don'?t do that"
    r")\b"
)

_ERRORISH = re.compile(
    r"(?i)(\b(traceback|exception|error|failed|failure|timed?\s*out)\b|"
    r"exit\s*code\s*[1-9]\d*|\[\s*ERROR\s*\])"
)

_SUCCESS_HINT = re.compile(
    r"(?i)\b("
    r"fixed|resolved|works now|tests?\s+pass|all green|solution\s*:|"
    r"successfully|completed successfully|patch applied"
    r")\b"
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def utc_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _relative_to_workspace(workspace: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(workspace.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _norm(s: str) -> str:
    return " ".join(s.strip().lower().split())


def _fingerprint(kind: str, text: str) -> str:
    h = hashlib.sha256(f"{kind}|{_norm(text)[:400]}".encode("utf-8")).hexdigest()
    return h[:16]


@dataclass
class LearningMoment:
    """One extracted learning-worthy moment."""

    kind: str  # correction | insight | error | solution | recovery | user_correction
    summary: str
    detail: str
    session_id: str = ""
    source: str = ""
    turn: int = 0
    role: str | None = None
    evidence_turns: list[int] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    def fingerprint(self) -> str:
        return _fingerprint(self.kind, self.summary + "\n" + self.detail[:800])


def _segments_to_messages(
    segments: list[tuple[int, str | None, str]],
) -> list[dict[str, Any]]:
    """Flatten segments into ordered message-like rows for pairing heuristics."""

    rows: list[dict[str, Any]] = []
    for turn, role, text in segments:
        rows.append({"turn": turn, "role": (role or "").lower() or None, "text": text})
    return rows


def _walk_for_json_moments(obj: Any, out: list[dict[str, str]]) -> None:
    if isinstance(obj, dict):
        tag = None
        body_parts: list[str] = []
        for k in _JSON_MOMENT_KEYS:
            if k not in obj:
                continue
            v = obj[k]
            if isinstance(v, str) and v.strip().lower() in {
                "correction",
                "insight",
                "error",
                "solution",
                "success",
            }:
                tag = v.strip().lower()
            elif isinstance(v, str) and v.strip():
                body_parts.append(v.strip())
        if tag:
            for key in ("text", "body", "content", "message", "note"):
                raw = obj.get(key)
                if isinstance(raw, str) and raw.strip():
                    body_parts.append(raw.strip())
            out.append({"kind": tag, "body": " ".join(body_parts).strip() or "(no body)"})
        for v in obj.values():
            _walk_for_json_moments(v, out)
    elif isinstance(obj, list):
        for item in obj:
            _walk_for_json_moments(item, out)


def extract_tagged_from_plaintext(text: str) -> list[tuple[str, str]]:
    hits: list[tuple[str, str]] = []
    for m in _TAG_LINE.finditer(text):
        kind = (
            m.group("kind")
            or m.group("kind2")
            or m.group("kind3")
            or m.group("kind4")
            or ""
        ).lower()
        body = (
            m.group("body")
            or m.group("body2")
            or m.group("body3")
            or m.group("body4")
            or ""
        ).strip()
        if kind:
            hits.append((kind, body))
    blob: list[dict[str, str]] = []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return hits
    _walk_for_json_moments(data, blob)
    for row in blob:
        hits.append((row["kind"], row["body"]))
    return hits


def extract_moments_from_segments(
    segments: list[tuple[int, str | None, str]],
    *,
    session_id: str,
    source: str,
) -> list[LearningMoment]:
    moments: list[LearningMoment] = []
    combined_text = "\n".join(t for _, _, t in segments)
    for kind, body in extract_tagged_from_plaintext(combined_text):
        summary = body[:240] + ("…" if len(body) > 240 else "")
        turn_guess = 0
        for turn, _role, text in segments:
            if body[:80] in text or text[:120] in body:
                turn_guess = turn
                break
        moments.append(
            LearningMoment(
                kind=kind if kind != "success" else "solution",
                summary=summary or f"tagged {kind}",
                detail=body,
                session_id=session_id,
                source=source,
                turn=turn_guess,
                role=None,
                evidence_turns=[turn_guess] if turn_guess else [],
                meta={"origin": "tag"},
            )
        )

    rows = _segments_to_messages(segments)
    for i, row in enumerate(rows):
        turn = int(row["turn"])
        role = row["role"]
        text = row["text"]
        if role == "user" and _USER_CORRECTION_HINT.search(text):
            moments.append(
                LearningMoment(
                    kind="user_correction",
                    summary=text[:220] + ("…" if len(text) > 220 else ""),
                    detail=text,
                    session_id=session_id,
                    source=source,
                    turn=turn,
                    role="user",
                    evidence_turns=[turn],
                    meta={"origin": "heuristic"},
                )
            )
        if role in {"assistant", "agent", None, ""} and _SUCCESS_HINT.search(text) and len(text.strip()) > 24:
            moments.append(
                LearningMoment(
                    kind="solution",
                    summary=text[:220] + ("…" if len(text) > 220 else ""),
                    detail=text,
                    session_id=session_id,
                    source=source,
                    turn=turn,
                    role=role or "assistant",
                    evidence_turns=[turn],
                    meta={"origin": "heuristic"},
                )
            )

    # Error recovery: error-like segment followed by a calmer assistant reply.
    for i, row in enumerate(rows):
        if not _ERRORISH.search(row["text"]):
            continue
        window = rows[i + 1 : i + 5]
        for j, nxt in enumerate(window, start=1):
            if nxt["role"] not in {"assistant", "agent", None, ""}:
                continue
            if not _ERRORISH.search(nxt["text"]) and len(nxt["text"].strip()) > 80:
                moments.append(
                    LearningMoment(
                        kind="recovery",
                        summary=f"Recovered after error near turn {row['turn']}",
                        detail=f"--- error ---\n{row['text'][:1200]}\n\n--- follow-up ---\n{nxt['text'][:1200]}",
                        session_id=session_id,
                        source=source,
                        turn=int(nxt["turn"]),
                        role=nxt["role"],
                        evidence_turns=sorted({int(row["turn"]), int(nxt["turn"])}),
                        meta={"origin": "heuristic", "gap": j},
                    )
                )
                break

    # Dedupe by fingerprint keeping first.
    seen: set[str] = set()
    uniq: list[LearningMoment] = []
    for m in moments:
        fp = m.fingerprint()
        if fp in seen:
            continue
        seen.add(fp)
        uniq.append(m)
    return uniq


def format_learning_entry(moment: LearningMoment, *, generated_at: str | None = None) -> str:
    """Render a single `.learnings/` markdown entry with YAML front matter."""

    ts = generated_at or utc_now_iso()
    safe_id = moment.fingerprint()
    fm = {
        "id": safe_id,
        "extracted_at": ts,
        "moment_kind": moment.kind,
        "session_id": moment.session_id or None,
        "turn": moment.turn or None,
        "role": moment.role,
        "source": moment.source or None,
        "evidence_turns": moment.evidence_turns or None,
        "meta": moment.meta or {},
    }
    # YAML-ish without PyYAML: only safe scalars.
    lines = ["---"]
    lines.append(json.dumps(fm, indent=2, ensure_ascii=False))
    lines.append("---")
    lines.append("")
    lines.append(f"## {moment.kind.replace('_', ' ').title()}")
    lines.append("")
    lines.append(f"**Summary**: {moment.summary}")
    lines.append("")
    lines.append("### Detail")
    lines.append("")
    lines.append(moment.detail.strip() or "_(empty)_")
    lines.append("")
    return "\n".join(lines) + "\n"


# -- sessions_list -------------------------------------------------------------


def _coerce_session_row(obj: dict[str, Any]) -> dict[str, Any] | None:
    sid = (
        obj.get("id")
        or obj.get("session_id")
        or obj.get("sessionId")
        or obj.get("key")
        or obj.get("chatId")
        or obj.get("chat_id")
    )
    path = (
        obj.get("history_path")
        or obj.get("historyPath")
        or obj.get("transcript_path")
        or obj.get("transcriptPath")
        or obj.get("path")
        or obj.get("file")
        or obj.get("session_path")
        or obj.get("sessionPath")
    )
    if isinstance(sid, str) and sid.strip():
        sid_s = sid.strip()
    elif sid is not None and not isinstance(sid, str):
        sid_s = str(sid)
    else:
        sid_s = ""

    if isinstance(path, str) and path.strip():
        path_s = path.strip()
    elif path is None:
        path_s = ""
    else:
        path_s = str(path)

    if not path_s and not sid_s:
        return None

    updated = (
        obj.get("updated_at")
        or obj.get("updatedAt")
        or obj.get("modified_at")
        or obj.get("mtime")
        or obj.get("lastActivity")
    )
    updated_s = updated if isinstance(updated, str) else ""

    return {"id": sid_s, "path": path_s, "updated_at": updated_s}


def parse_sessions_list(raw: str) -> list[dict[str, Any]]:
    """Parse ``sessions_list``-style JSON or minimal text into uniform session rows."""

    raw = raw.strip()
    if not raw:
        return []

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return _parse_sessions_list_plain(raw)

    sessions: list[Any] | None = None
    if isinstance(data, list):
        sessions = data
    elif isinstance(data, dict):
        sessions = (
            data.get("sessions")
            or data.get("recent")
            or data.get("items")
            or data.get("chats")
        )
        if sessions is None and "session" in data and isinstance(data["session"], dict):
            sessions = [data["session"]]

    if not isinstance(sessions, list):
        return _parse_sessions_list_plain(raw)

    out: list[dict[str, Any]] = []
    for item in sessions:
        if isinstance(item, str) and item.strip():
            p = Path(item.strip())
            out.append({"id": p.stem, "path": item.strip(), "updated_at": ""})
        elif isinstance(item, dict):
            row = _coerce_session_row(item)
            if row:
                out.append(row)
    return out


def _parse_sessions_list_plain(raw: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in raw.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        parts = re.split(r"\s+", s, maxsplit=1)
        if len(parts) == 1:
            p = Path(parts[0])
            rows.append({"id": p.stem, "path": parts[0], "updated_at": ""})
        else:
            sid, path = parts[0], parts[1].strip()
            rows.append({"id": sid, "path": path, "updated_at": ""})
    return rows


def _mtime_key(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def resolve_history_path(entry: dict[str, Any], workspace: Path) -> Path | None:
    """Resolve a session row path against the workspace."""

    p = (entry.get("path") or "").strip()
    if not p:
        return None
    cand = Path(p).expanduser()
    if cand.is_absolute():
        return cand if cand.exists() else None
    rel = (workspace / cand).resolve()
    return rel if rel.exists() else None


def write_moment_files(
    moments: Sequence[LearningMoment],
    dest_dir: Path,
    *,
    generated_at: str | None = None,
) -> list[Path]:
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = utc_compact()
    written: list[Path] = []
    ts = generated_at or utc_now_iso()
    for m in moments:
        fn = f"pattern_{m.fingerprint()}_{stamp}.md"
        path = dest_dir / fn
        path.write_text(format_learning_entry(m, generated_at=ts), encoding="utf-8")
        written.append(path)
    return written


def load_segments_from_path(path: Path) -> list[tuple[int, str | None, str]]:
    return parse_session_log(path.resolve())


def load_segments_from_string(raw: str, *, suffix: str) -> list[tuple[int, str | None, str]]:
    root = repo_root()
    tmp_dir = root / ".learnings"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        suffix=suffix,
        prefix="sessions_history_",
        dir=tmp_dir,
        delete=False,
    ) as fh:
        fh.write(raw)
        name = fh.name
    try:
        return parse_session_log(Path(name).resolve())
    finally:
        try:
            Path(name).unlink(missing_ok=True)  # type: ignore[arg-type]
        except OSError:
            pass


def cmd_extract_patterns(args: argparse.Namespace) -> int:
    workspace: Path = args.workspace.expanduser().resolve()
    dest = workspace / LEARNINGS_SUBDIR

    if args.stdin:
        raw = sys.stdin.read()
        segments = load_segments_from_string(raw, suffix=".json" if raw.strip().startswith("{") else ".txt")
        sid = args.session_id or "stdin"
        src = "<stdin>"
    else:
        path = args.input.expanduser().resolve()
        if not path.exists():
            sys.stderr.write(f"error: file not found: {path}\n")
            return 2
        segments = load_segments_from_path(path)
        sid = args.session_id or path.stem
        src = _relative_to_workspace(workspace, path)

    moments = extract_moments_from_segments(segments, session_id=sid, source=src)
    if args.dry_run:
        print(json.dumps([asdict(m) for m in moments], indent=2, ensure_ascii=False))
        return 0

    paths = write_moment_files(moments, dest)
    for p in paths:
        print(p.as_posix())
    if not paths:
        sys.stderr.write("warning: no learning moments extracted.\n")
    return 0


def cmd_extract_recent(args: argparse.Namespace) -> int:
    workspace: Path = args.workspace.expanduser().resolve()
    list_path: Path = args.sessions_list.expanduser().resolve()
    if not list_path.exists():
        sys.stderr.write(f"error: sessions list not found: {list_path}\n")
        return 2

    raw = read_text(list_path)
    entries = parse_sessions_list(raw)
    if not entries:
        sys.stderr.write("error: no sessions parsed from list file.\n")
        return 2

    # Prefer filesystem recency when paths exist.
    ranked: list[tuple[float, dict[str, Any]]] = []
    for e in entries:
        rp = resolve_history_path(e, workspace)
        if rp is None:
            continue
        ranked.append((_mtime_key(rp), e))
    ranked.sort(key=lambda x: x[0], reverse=True)

    if not ranked:
        sys.stderr.write("error: no resolvable history paths in sessions list.\n")
        return 2

    dest = workspace / LEARNINGS_SUBDIR
    all_written: list[Path] = []
    for _mt, entry in ranked[: max(1, args.limit)]:
        path = resolve_history_path(entry, workspace)
        if path is None:
            continue
        sid = str(entry.get("id") or path.stem)
        src = _relative_to_workspace(workspace, path)
        segments = load_segments_from_path(path)
        moments = extract_moments_from_segments(segments, session_id=sid, source=src)
        if args.dry_run:
            print(json.dumps({"session": sid, "moments": [asdict(m) for m in moments]}, indent=2))
            continue
        all_written.extend(write_moment_files(moments, dest))

    if args.dry_run:
        return 0

    for p in all_written:
        print(p.as_posix())
    if not all_written:
        sys.stderr.write("warning: no learning moments written for recent sessions.\n")
    return 0


def cmd_format_entry(args: argparse.Namespace) -> int:
    if args.stdin:
        raw = sys.stdin.read()
    else:
        path = args.input.expanduser().resolve()
        if not path.exists():
            sys.stderr.write(f"error: file not found: {path}\n")
            return 2
        raw = read_text(path)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        sys.stderr.write(f"error: invalid JSON: {exc}\n")
        return 2

    if not isinstance(payload, dict):
        sys.stderr.write("error: JSON root must be an object.\n")
        return 2

    kind = str(payload.get("moment_kind") or payload.get("kind") or "insight")
    summary = str(payload.get("summary") or payload.get("title") or "").strip() or kind
    detail = str(payload.get("detail") or payload.get("text") or payload.get("body") or "").strip()
    raw_turn = payload.get("turn")
    if isinstance(raw_turn, int):
        turn = raw_turn
    elif isinstance(raw_turn, str) and raw_turn.strip().isdigit():
        turn = int(raw_turn.strip())
    else:
        turn = 0

    evidence_turns: list[int] = []
    ev_raw = payload.get("evidence_turns")
    if isinstance(ev_raw, list):
        for x in ev_raw:
            if isinstance(x, int):
                evidence_turns.append(x)
            elif isinstance(x, str) and x.strip().isdigit():
                evidence_turns.append(int(x.strip()))

    moment = LearningMoment(
        kind=kind,
        summary=summary,
        detail=detail,
        session_id=str(payload.get("session_id") or payload.get("sessionId") or ""),
        source=str(payload.get("source") or ""),
        turn=turn,
        role=payload.get("role") if isinstance(payload.get("role"), str) else None,
        evidence_turns=evidence_turns,
        meta=payload.get("meta") if isinstance(payload.get("meta"), dict) else {},
    )
    text = format_learning_entry(moment, generated_at=payload.get("extracted_at") if isinstance(payload.get("extracted_at"), str) else None)
    if args.output:
        out = args.output.expanduser().resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(out.as_posix())
    else:
        sys.stdout.write(text)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Extract conversation learning patterns into .learnings/conversation_patterns/",
    )
    sub = p.add_subparsers(dest="command", required=True)

    p_recent = sub.add_parser("extract-recent", help="Parse sessions_list JSON/text and extract from recent histories.")
    p_recent.add_argument(
        "--workspace",
        type=Path,
        default=Path("."),
        help="Repository / workspace root (default: current directory).",
    )
    p_recent.add_argument(
        "--sessions-list",
        type=Path,
        required=True,
        help="Path to sessions_list export (JSON or path-per-line text).",
    )
    p_recent.add_argument("--limit", type=int, default=5, help="Max sessions to process (default: 5).")
    p_recent.add_argument(
        "--dry-run",
        action="store_true",
        help="Print JSON only; do not write .learnings/ files.",
    )
    p_recent.set_defaults(func=cmd_extract_recent)

    p_pat = sub.add_parser("extract-patterns", help="Extract patterns from one sessions_history file or stdin.")
    p_pat.add_argument(
        "--workspace",
        type=Path,
        default=Path("."),
        help="Repository / workspace root (default: current directory).",
    )
    p_pat.add_argument("--input", type=Path, help="Path to transcript (.json, .log, .txt).")
    p_pat.add_argument("--stdin", action="store_true", help="Read history body from stdin.")
    p_pat.add_argument("--session-id", type=str, default="", help="Override session id in output metadata.")
    p_pat.add_argument("--dry-run", action="store_true", help="Print JSON only; do not write files.")
    p_pat.set_defaults(func=cmd_extract_patterns)

    p_fmt = sub.add_parser("format-entry", help="Format a JSON moment descriptor as a .learnings markdown entry.")
    g = p_fmt.add_mutually_exclusive_group(required=True)
    g.add_argument("--input", type=Path, help="Path to JSON object describing one moment.")
    g.add_argument("--stdin", action="store_true", help="Read JSON object from stdin.")
    p_fmt.add_argument("-o", "--output", type=Path, default=None, help="Write entry to this path (default: stdout).")
    p_fmt.set_defaults(func=cmd_format_entry)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "extract-patterns":
        if not args.stdin and not args.input:
            sys.stderr.write("error: extract-patterns requires --input or --stdin.\n")
            return 2

    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
