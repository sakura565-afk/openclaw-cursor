#!/usr/bin/env python3
"""Optimize OpenClaw session context and suggest reductions."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_STALE_TURNS = 5
LARGE_FILE_THRESHOLD = 10 * 1024
PATH_PATTERN = re.compile(r"(?P<path>(?:/)?(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.[A-Za-z0-9_.-]+)")
TURN_PATTERN = re.compile(r"(?i)\b(?:turn|step|message)\s*[:#-]?\s*(\d+)\b")
SYMBOL_PATTERN = re.compile(r"^\s*(?:def|class)\s+([A-Za-z_][A-Za-z0-9_]*)", re.MULTILINE)
HEADING_PATTERN = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*$", re.MULTILINE)
ANSI = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "blue": "\033[34m",
    "cyan": "\033[36m",
}


def estimate_tokens(text: str) -> int:
    """Estimate token count at roughly four characters per token."""
    if not text:
        return 0
    return (len(text) + 3) // 4


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def normalize_content(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def truncate(text: str, limit: int = 90) -> str:
    compact = normalize_content(text)
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."


def color(text: str, name: str, use_color: bool = True) -> str:
    if not use_color:
        return text
    return ANSI[name] + text + ANSI["reset"]


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def resolve_candidate_path(candidate: str, workspace_root: Path, session_log_path: Path | None) -> Path | None:
    cleaned = candidate.strip().strip("'\"`").rstrip(".,:;)")
    if not cleaned or cleaned.startswith("http://") or cleaned.startswith("https://"):
        return None

    candidate_path = Path(cleaned)
    search_roots = []
    if session_log_path is not None:
        search_roots.append(session_log_path.parent)
    search_roots.append(workspace_root)

    if candidate_path.is_absolute():
        return candidate_path

    for base in search_roots:
        resolved = (base / candidate_path).resolve()
        if resolved.exists():
            return resolved

    return (workspace_root / candidate_path).resolve()


def update_ref(
    refs: dict[str, dict],
    path_text: str,
    turn: int,
    workspace_root: Path,
    session_log_path: Path | None,
    embedded_content: str | None = None,
) -> None:
    resolved = resolve_candidate_path(path_text, workspace_root, session_log_path)
    if resolved is None:
        return

    key = str(resolved)
    entry = refs.setdefault(
        key,
        {
            "path": key,
            "display_path": resolved.relative_to(workspace_root).as_posix()
            if resolved.is_relative_to(workspace_root)
            else resolved.as_posix(),
            "last_access_turn": turn,
            "mentions": 0,
            "embedded_content": None,
        },
    )
    entry["last_access_turn"] = max(entry["last_access_turn"], turn)
    entry["mentions"] += 1
    if embedded_content and not entry["embedded_content"]:
        entry["embedded_content"] = embedded_content


def extract_paths_from_text(
    text: str,
    turn: int,
    refs: dict[str, dict],
    workspace_root: Path,
    session_log_path: Path | None,
) -> None:
    for match in PATH_PATTERN.finditer(text):
        update_ref(refs, match.group("path"), turn, workspace_root, session_log_path)


def extract_from_json(
    value,
    refs: dict[str, dict],
    workspace_root: Path,
    session_log_path: Path | None,
    current_turn: int = 1,
) -> int:
    if isinstance(value, dict):
        turn = current_turn
        for key in ("turn", "step", "message_index", "message", "index"):
            raw = value.get(key)
            if isinstance(raw, int):
                turn = max(turn, raw)
            elif isinstance(raw, str) and raw.isdigit():
                turn = max(turn, int(raw))

        embedded_content = None
        for content_key in ("content", "text", "body", "snippet"):
            raw = value.get(content_key)
            if isinstance(raw, str) and raw.strip():
                embedded_content = raw
                break

        for key, raw in value.items():
            key_lower = str(key).lower()
            if isinstance(raw, str):
                if "path" in key_lower or key_lower.endswith("file") or "file_" in key_lower:
                    update_ref(
                        refs,
                        raw,
                        turn,
                        workspace_root,
                        session_log_path,
                        embedded_content=embedded_content,
                    )
                extract_paths_from_text(raw, turn, refs, workspace_root, session_log_path)
            elif isinstance(raw, list) and ("files" in key_lower or "paths" in key_lower):
                for item in raw:
                    if isinstance(item, str):
                        update_ref(refs, item, turn, workspace_root, session_log_path)
                    elif isinstance(item, dict):
                        path_value = (
                            item.get("path")
                            or item.get("file")
                            or item.get("file_path")
                            or item.get("filepath")
                        )
                        content_value = item.get("content") if isinstance(item.get("content"), str) else None
                        if isinstance(path_value, str):
                            update_ref(
                                refs,
                                path_value,
                                turn,
                                workspace_root,
                                session_log_path,
                                embedded_content=content_value,
                            )

        deepest_turn = turn
        for raw in value.values():
            deepest_turn = max(
                deepest_turn,
                extract_from_json(raw, refs, workspace_root, session_log_path, turn),
            )
        return deepest_turn

    if isinstance(value, list):
        deepest_turn = current_turn
        for item in value:
            deepest_turn = max(
                deepest_turn,
                extract_from_json(item, refs, workspace_root, session_log_path, current_turn),
            )
        return deepest_turn

    if isinstance(value, str):
        extract_paths_from_text(value, current_turn, refs, workspace_root, session_log_path)
    return current_turn


def parse_text_session_log(session_log_path: Path, workspace_root: Path) -> tuple[dict[str, dict], int]:
    refs: dict[str, dict] = {}
    current_turn = 1
    max_turn = 1
    for line_number, line in enumerate(read_text(session_log_path).splitlines(), start=1):
        match = TURN_PATTERN.search(line)
        if match:
            current_turn = int(match.group(1))
        else:
            current_turn = max(current_turn, line_number)
        max_turn = max(max_turn, current_turn)
        extract_paths_from_text(line, current_turn, refs, workspace_root, session_log_path)
    return refs, max_turn


def parse_session_log(session_log_path: Path | None, workspace_root: Path) -> tuple[dict[str, dict], int]:
    if session_log_path is None or not session_log_path.exists():
        return {}, 1

    suffix = session_log_path.suffix.lower()
    if suffix == ".json":
        refs: dict[str, dict] = {}
        try:
            data = json.loads(read_text(session_log_path))
        except json.JSONDecodeError:
            return parse_text_session_log(session_log_path, workspace_root)
        max_turn = extract_from_json(data, refs, workspace_root, session_log_path, 1)
        return refs, max_turn

    return parse_text_session_log(session_log_path, workspace_root)


def content_fingerprint(text: str) -> str:
    normalized = normalize_content(text)
    if len(normalized) > 3000:
        return normalized[:1500] + "::" + normalized[-1500:]
    return normalized


def relative_display(path: Path, workspace_root: Path) -> str:
    return path.relative_to(workspace_root).as_posix() if path.is_relative_to(workspace_root) else path.as_posix()


def build_file_metrics(ref: dict, workspace_root: Path, current_turn: int) -> dict:
    path = Path(ref["path"])
    exists = path.exists()
    text = read_text(path) if exists else (ref.get("embedded_content") or "")
    size_bytes = len(text.encode("utf-8")) if text else (path.stat().st_size if exists else 0)
    token_estimate = estimate_tokens(text) if text else max(0, size_bytes // 4)
    last_access_turn = ref["last_access_turn"]
    turns_since_access = max(0, current_turn - last_access_turn)
    return {
        "path": ref["path"],
        "display_path": ref["display_path"],
        "exists": exists,
        "size_bytes": size_bytes,
        "token_estimate": token_estimate,
        "last_access_turn": last_access_turn,
        "turns_since_access": turns_since_access,
        "mentions": ref["mentions"],
        "content": text,
    }


def priority_label(value: int, size_bytes: int, stale_threshold: int, reverse: bool = False) -> str:
    if reverse:
        if value <= 1 and size_bytes <= LARGE_FILE_THRESHOLD:
            return "high"
        if value <= stale_threshold:
            return "medium"
        return "low"

    if value >= stale_threshold * 2 or size_bytes > LARGE_FILE_THRESHOLD:
        return "high"
    if value >= stale_threshold:
        return "medium"
    return "low"


def summarize_content(path: str, content: str) -> str:
    names = SYMBOL_PATTERN.findall(content)
    if names:
        keep = ", ".join(names[:4])
        return f"{path}: retain only interfaces for {keep}; reload full implementation only while editing internals."

    headings = HEADING_PATTERN.findall(content)
    if headings:
        keep = ", ".join(truncate(item, 32) for item in headings[:3])
        return f"{path}: keep the heading-level goals ({keep}) and drop the full prose until needed."

    snippets = [truncate(line, 48) for line in content.splitlines() if line.strip()]
    if snippets:
        keep = "; ".join(snippets[:2])
        return f"{path}: compress to a short reminder - {keep}."
    return f"{path}: keep only the filename and purpose in context until it becomes active again."


def detect_memory_bloat(workspace_root: Path) -> dict:
    memory_dir = workspace_root / "memory"
    if not memory_dir.exists():
        return {
            "files": [],
            "large_files": [],
            "duplicate_groups": [],
            "near_duplicates": [],
            "bloat_token_penalty": 0,
        }

    memory_files = []
    for path in sorted(memory_dir.rglob("*")):
        if not path.is_file():
            continue
        text = read_text(path)
        size_bytes = len(text.encode("utf-8"))
        token_estimate = estimate_tokens(text)
        memory_files.append(
            {
                "path": path.as_posix(),
                "display_path": relative_display(path, workspace_root),
                "size_bytes": size_bytes,
                "token_estimate": token_estimate,
                "content": text,
                "fingerprint": content_fingerprint(text),
            }
        )

    duplicates: dict[str, list[dict]] = {}
    for item in memory_files:
        if item["fingerprint"]:
            duplicates.setdefault(item["fingerprint"], []).append(item)

    duplicate_groups = []
    duplicate_penalty = 0
    for group in duplicates.values():
        if len(group) < 2:
            continue
        sorted_group = sorted(group, key=lambda item: item["display_path"])
        duplicate_groups.append(
            {
                "paths": [item["display_path"] for item in sorted_group],
                "shared_tokens": min(item["token_estimate"] for item in sorted_group),
            }
        )
        duplicate_penalty += sum(item["token_estimate"] for item in sorted_group[1:])

    near_duplicates = []
    if len(memory_files) <= 30:
        for index, first in enumerate(memory_files):
            first_text = normalize_content(first["content"])
            if len(first_text) < 200:
                continue
            for second in memory_files[index + 1 :]:
                second_text = normalize_content(second["content"])
                if len(second_text) < 200 or first["fingerprint"] == second["fingerprint"]:
                    continue
                shorter, longer = (first, second) if len(first_text) <= len(second_text) else (second, first)
                shorter_text = normalize_content(shorter["content"])
                longer_text = normalize_content(longer["content"])
                if len(shorter_text) / max(1, len(longer_text)) < 0.75:
                    continue
                if shorter_text and shorter_text in longer_text:
                    near_duplicates.append(
                        {
                            "paths": [shorter["display_path"], longer["display_path"]],
                            "shared_tokens": shorter["token_estimate"],
                        }
                    )

    large_files = [
        {
            "path": item["display_path"],
            "size_bytes": item["size_bytes"],
            "token_estimate": item["token_estimate"],
        }
        for item in sorted(memory_files, key=lambda entry: entry["size_bytes"], reverse=True)
        if item["size_bytes"] > LARGE_FILE_THRESHOLD
    ]

    large_penalty = sum(item["token_estimate"] for item in large_files)
    near_duplicate_penalty = sum(item["shared_tokens"] for item in near_duplicates)
    return {
        "files": [
            {
                "path": item["display_path"],
                "size_bytes": item["size_bytes"],
                "token_estimate": item["token_estimate"],
            }
            for item in memory_files
        ],
        "large_files": large_files,
        "duplicate_groups": duplicate_groups,
        "near_duplicates": near_duplicates,
        "bloat_token_penalty": duplicate_penalty + near_duplicate_penalty + large_penalty,
    }


def analyze_session(workspace_root: Path, session_log_path: Path | None = None, stale_turns: int = DEFAULT_STALE_TURNS) -> dict:
    refs, current_turn = parse_session_log(session_log_path, workspace_root)
    files = [
        build_file_metrics(ref, workspace_root, current_turn)
        for ref in sorted(refs.values(), key=lambda item: item["display_path"])
    ]

    total_tokens = sum(item["token_estimate"] for item in files)
    stale_candidates = [
        {
            "path": item["display_path"],
            "token_estimate": item["token_estimate"],
            "size_bytes": item["size_bytes"],
            "turns_since_access": item["turns_since_access"],
            "priority": priority_label(item["turns_since_access"], item["size_bytes"], stale_turns),
            "reason": f"Inactive for {item['turns_since_access']} turns",
        }
        for item in sorted(
            files,
            key=lambda entry: (entry["turns_since_access"], entry["token_estimate"]),
            reverse=True,
        )
        if item["turns_since_access"] >= stale_turns
    ]

    load_priorities = [
        {
            "path": item["display_path"],
            "token_estimate": item["token_estimate"],
            "turns_since_access": item["turns_since_access"],
            "priority": priority_label(
                item["turns_since_access"],
                item["size_bytes"],
                stale_turns,
                reverse=True,
            ),
        }
        for item in sorted(
            files,
            key=lambda entry: (entry["turns_since_access"], -entry["mentions"], entry["token_estimate"]),
        )
    ]

    largest_in_context = [
        {
            "path": item["display_path"],
            "size_bytes": item["size_bytes"],
            "token_estimate": item["token_estimate"],
        }
        for item in sorted(files, key=lambda entry: entry["size_bytes"], reverse=True)
        if item["size_bytes"] > LARGE_FILE_THRESHOLD
    ]

    memory_bloat = detect_memory_bloat(workspace_root)
    stale_tokens = sum(item["token_estimate"] for item in stale_candidates)
    useful_tokens = max(0, total_tokens - stale_tokens)
    penalty_tokens = stale_tokens + memory_bloat["bloat_token_penalty"]
    denominator = max(1, total_tokens + memory_bloat["bloat_token_penalty"])
    efficiency_score = max(0, min(100, round((useful_tokens / denominator) * 100)))

    reminders = []
    reminder_source = sorted(
        files,
        key=lambda item: (item["turns_since_access"] >= stale_turns, item["token_estimate"]),
        reverse=True,
    )
    for item in reminder_source[:5]:
        reminders.append(
            {
                "path": item["display_path"],
                "summary": summarize_content(item["display_path"], item["content"]),
            }
        )

    optimization_hints = []
    if stale_candidates:
        first = stale_candidates[0]
        optimization_hints.append(
            f"Unload {first['path']} first; it has been idle for {first['turns_since_access']} turns."
        )
    if largest_in_context:
        first = largest_in_context[0]
        optimization_hints.append(
            f"Replace the full contents of {first['path']} with a reminder because it exceeds 10 KB."
        )
    if memory_bloat["duplicate_groups"]:
        duplicate = memory_bloat["duplicate_groups"][0]
        optimization_hints.append(
            f"Deduplicate memory files: {', '.join(duplicate['paths'][:3])} contain the same normalized content."
        )
    if load_priorities:
        keep = load_priorities[0]
        optimization_hints.append(
            f"Keep {keep['path']} loaded first; it is the freshest context with a manageable token cost."
        )
    if not optimization_hints:
        optimization_hints.append("Context looks lean; no urgent unload actions were detected.")

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "workspace_root": workspace_root.as_posix(),
        "session_log": session_log_path.as_posix() if session_log_path else None,
        "stale_turn_window": stale_turns,
        "current_turn": current_turn,
        "summary": {
            "loaded_file_count": len(files),
            "total_estimated_tokens": total_tokens,
            "stale_candidate_count": len(stale_candidates),
            "memory_file_count": len(memory_bloat["files"]),
            "efficiency_score": efficiency_score,
        },
        "files": [
            {
                "path": item["display_path"],
                "exists": item["exists"],
                "size_bytes": item["size_bytes"],
                "token_estimate": item["token_estimate"],
                "last_access_turn": item["last_access_turn"],
                "turns_since_access": item["turns_since_access"],
                "mentions": item["mentions"],
            }
            for item in files
        ],
        "largest_in_context": largest_in_context,
        "stale_candidates": stale_candidates,
        "load_priorities": load_priorities[:10],
        "memory_bloat": memory_bloat,
        "reminders": reminders,
        "optimization_hints": optimization_hints,
    }


def render_markdown(report: dict) -> str:
    lines = [
        "# Context Optimization Hints",
        "",
        f"- Efficiency score: {report['summary']['efficiency_score']}%",
        f"- Loaded files: {report['summary']['loaded_file_count']}",
        f"- Estimated tokens: {report['summary']['total_estimated_tokens']}",
        f"- Stale candidates: {report['summary']['stale_candidate_count']}",
        "",
        "## Unload Priorities",
    ]

    if report["stale_candidates"]:
        for item in report["stale_candidates"][:8]:
            lines.append(
                f"- [{item['priority']}] {item['path']} - {item['token_estimate']} tokens, idle {item['turns_since_access']} turns"
            )
    else:
        lines.append("- None")

    lines.extend(["", "## Load First", ""])
    if report["load_priorities"]:
        for item in report["load_priorities"][:5]:
            lines.append(
                f"- [{item['priority']}] {item['path']} - {item['token_estimate']} tokens, last used {item['turns_since_access']} turns ago"
            )
    else:
        lines.append("- No active files were detected.")

    lines.extend(["", "## Compact Reminders", ""])
    for item in report["reminders"]:
        lines.append(f"- {item['summary']}")

    lines.extend(["", "## Optimization Hints", ""])
    for hint in report["optimization_hints"]:
        lines.append(f"- {hint}")
    return "\n".join(lines) + "\n"


def render_summary(report: dict, use_color: bool = True) -> str:
    summary = report["summary"]
    lines = [
        color("OpenClaw Context Optimization", "bold", use_color),
        f"Loaded files: {color(str(summary['loaded_file_count']), 'cyan', use_color)}",
        f"Estimated tokens: {color(str(summary['total_estimated_tokens']), 'cyan', use_color)}",
        f"Efficiency score: {color(str(summary['efficiency_score']) + '%', 'green' if summary['efficiency_score'] >= 70 else 'yellow' if summary['efficiency_score'] >= 40 else 'red', use_color)}",
        "",
        color("Unload priorities", "blue", use_color),
    ]

    if report["stale_candidates"]:
        for item in report["stale_candidates"][:5]:
            lines.append(
                f"  - {color(item['priority'].upper(), 'red' if item['priority'] == 'high' else 'yellow', use_color)} "
                f"{item['path']} ({item['token_estimate']} tokens, idle {item['turns_since_access']} turns)"
            )
    else:
        lines.append("  - No stale files detected.")

    lines.extend(["", color("Largest files in context (>10 KB)", "blue", use_color)])
    if report["largest_in_context"]:
        for item in report["largest_in_context"][:5]:
            lines.append(
                f"  - {item['path']} ({item['size_bytes']} bytes, ~{item['token_estimate']} tokens)"
            )
    else:
        lines.append("  - None")

    lines.extend(["", color("Memory bloat", "blue", use_color)])
    memory_bloat = report["memory_bloat"]
    if memory_bloat["large_files"]:
        lines.append(f"  - Large memory files: {len(memory_bloat['large_files'])}")
    if memory_bloat["duplicate_groups"]:
        lines.append(f"  - Redundant memory groups: {len(memory_bloat['duplicate_groups'])}")
    if memory_bloat["near_duplicates"]:
        lines.append(f"  - Near-duplicate memory pairs: {len(memory_bloat['near_duplicates'])}")
    if not (
        memory_bloat["large_files"] or memory_bloat["duplicate_groups"] or memory_bloat["near_duplicates"]
    ):
        lines.append("  - No memory bloat detected.")

    lines.extend(["", color("Compact reminders", "blue", use_color)])
    if report["reminders"]:
        for item in report["reminders"][:4]:
            lines.append(f"  - {item['summary']}")
    else:
        lines.append("  - No reminders generated.")

    lines.extend(["", color("Optimization hints", "blue", use_color)])
    for hint in report["optimization_hints"]:
        lines.append(f"  - {hint}")

    return "\n".join(lines) + "\n"


def write_report(report: dict, workspace_root: Path, output_path: Path | None = None) -> tuple[Path, Path | None]:
    logs_dir = workspace_root / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    json_path = logs_dir / f"context_optimization_{datetime.now().strftime('%Y%m%d')}.json"
    json_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    written_output = None
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(render_markdown(report), encoding="utf-8")
        written_output = output_path

    return json_path, written_output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Optimize OpenClaw session context.")
    parser.add_argument("--session-log", type=Path, help="Path to a session log (.json or text).")
    parser.add_argument("--output", type=Path, help="Optional markdown file for optimization hints.")
    args = parser.parse_args(argv)

    workspace_root = repo_root()
    session_log_path = args.session_log.resolve() if args.session_log else None
    output_path = args.output.resolve() if args.output else None

    report = analyze_session(workspace_root, session_log_path=session_log_path)
    json_path, written_output = write_report(report, workspace_root, output_path=output_path)

    sys.stdout.write(render_summary(report, use_color=True))
    sys.stdout.write(f"\nJSON log: {json_path}\n")
    if written_output is not None:
        sys.stdout.write(f"Hints markdown: {written_output}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
