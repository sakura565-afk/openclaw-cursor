#!/usr/bin/env python3
"""Capture, classify, and learn from agent execution errors."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_STORE_DIR = Path("logs/error_learning")
DEFAULT_EVENTS_FILE = DEFAULT_STORE_DIR / "errors.jsonl"
DEFAULT_LESSONS_INDEX = Path(".learnings/lessons.md")

SEVERITY_BY_CATEGORY = {
    "dependency": "high",
    "environment": "high",
    "filesystem": "medium",
    "network": "medium",
    "permission": "high",
    "timeout": "medium",
    "resource": "high",
    "logic": "high",
    "external_service": "medium",
    "unknown": "low",
}


@dataclass
class ErrorEvent:
    """A single normalized error event."""

    timestamp: str
    category: str
    severity: str
    signature: str
    fingerprint: str
    recurrence: int
    message: str
    command: str
    stacktrace: str
    context: dict[str, Any]
    tags: list[str]
    suggestions: list[str]

    def to_json(self) -> str:
        payload = {
            "timestamp": self.timestamp,
            "category": self.category,
            "severity": self.severity,
            "signature": self.signature,
            "fingerprint": self.fingerprint,
            "recurrence": self.recurrence,
            "message": self.message,
            "command": self.command,
            "stacktrace": self.stacktrace,
            "context": self.context,
            "tags": self.tags,
            "suggestions": self.suggestions,
        }
        return json.dumps(payload, sort_keys=True)


def utc_now_iso() -> str:
    """Return current UTC timestamp in ISO format."""

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_message(message: str) -> str:
    """Normalize unstable fields to improve signature recurrence detection."""

    normalized = message.strip().lower()
    normalized = re.sub(r"\b0x[0-9a-f]+\b", "<hex>", normalized)
    normalized = re.sub(r"\b\d+\b", "<num>", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized


def classify_error(message: str, stacktrace: str, command: str) -> str:
    """Classify an error by matching known patterns."""

    full_text = " ".join([message, stacktrace, command]).lower()
    patterns = [
        ("dependency", r"(modulenotfounderror|no module named|importerror|cannot find package)"),
        ("permission", r"(permission denied|operation not permitted|eacces|eperm)"),
        ("filesystem", r"(filenotfounderror|enoent|no such file or directory|is a directory)"),
        ("timeout", r"(timeout|timed out|deadline exceeded)"),
        ("network", r"(connection reset|dns|name resolution|connection refused|ssl|http error|502|503|504)"),
        ("resource", r"(out of memory|oom|disk full|no space left|too many open files)"),
        ("environment", r"(keyerror: 'path'|missing env|environment variable|not set)"),
        ("external_service", r"(api error|rate limit|429|unauthorized|forbidden|service unavailable)"),
        ("logic", r"(assertionerror|typeerror|valueerror|indexerror|keyerror|nullpointer|attributeerror)"),
    ]
    for category, pattern in patterns:
        if re.search(pattern, full_text):
            return category
    return "unknown"


def build_signature(category: str, message: str) -> str:
    """Generate a stable signature string for recurrence tracking."""

    return f"{category}:{normalize_message(message)}"


def build_fingerprint(signature: str) -> str:
    """Build a compact hash fingerprint for signature lookup."""

    return hashlib.sha256(signature.encode("utf-8")).hexdigest()[:16]


def suggest_fixes(category: str, message: str, command: str) -> list[str]:
    """Generate practical remediation suggestions by category."""

    message_l = message.lower()
    suggestions: list[str] = []
    if category == "dependency":
        suggestions.extend(
            [
                "Install the missing dependency using the project package manager.",
                "Verify the active virtual environment or runtime image has required packages.",
                "Pin or update dependency constraints to prevent version drift.",
            ]
        )
    elif category == "permission":
        suggestions.extend(
            [
                "Check file and directory permissions for the target path.",
                "Run the command with a user that has required access.",
                "Avoid writing into protected locations; prefer workspace-local paths.",
            ]
        )
    elif category == "filesystem":
        suggestions.extend(
            [
                "Validate that the referenced files and directories exist before running.",
                "Create parent directories as part of setup if needed.",
                "Use absolute paths or explicit working_directory to avoid path confusion.",
            ]
        )
    elif category == "timeout":
        suggestions.extend(
            [
                "Increase timeout limits for long-running commands.",
                "Add retries with bounded exponential backoff for flaky operations.",
                "Profile command runtime and split work into smaller units.",
            ]
        )
    elif category == "network":
        suggestions.extend(
            [
                "Retry transient network requests with exponential backoff.",
                "Verify DNS, proxy, and outbound connectivity settings.",
                "Add fallback mirrors or cached artifacts for critical dependencies.",
            ]
        )
    elif category == "resource":
        suggestions.extend(
            [
                "Reduce memory footprint or batch size for the failing operation.",
                "Clean temporary files and confirm sufficient disk space.",
                "Close leaked handles/processes and re-run with resource monitoring.",
            ]
        )
    elif category == "environment":
        suggestions.extend(
            [
                "Document required environment variables and defaults.",
                "Add startup validation that fails fast with actionable messages.",
                "Provide a setup script or example env file for reproducibility.",
            ]
        )
    elif category == "external_service":
        suggestions.extend(
            [
                "Check API credentials, scopes, and endpoint health.",
                "Implement retries and circuit breaking for service instability.",
                "Cache responses where possible to reduce rate-limit pressure.",
            ]
        )
    elif category == "logic":
        suggestions.extend(
            [
                "Add targeted unit tests for this failure mode.",
                "Strengthen input validation and guard clauses around edge cases.",
                "Capture richer debug context around the failing branch.",
            ]
        )
    else:
        suggestions.extend(
            [
                "Capture full stacktrace and command context for improved triage.",
                "Add a specific classification rule once this pattern is understood.",
                "Record the workaround in project learnings for future runs.",
            ]
        )

    if "pip install" in command.lower() and category in {"network", "dependency"}:
        suggestions.append("Consider preloading dependencies in the base image for faster, stable setup.")
    if "no such file" in message_l:
        suggestions.append("Add a preflight path existence check before command execution.")
    return suggestions


def parse_context(raw_context: str | None) -> dict[str, Any]:
    """Parse user-provided JSON context safely."""

    if not raw_context:
        return {}
    try:
        parsed = json.loads(raw_context)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON for --context: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("--context JSON must be an object.")
    return parsed


def ensure_parent(path: Path) -> None:
    """Create parent directory for a file path."""

    path.parent.mkdir(parents=True, exist_ok=True)


def load_events(events_file: Path) -> list[dict[str, Any]]:
    """Read JSONL events from disk."""

    if not events_file.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in events_file.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            events.append(payload)
    return events


def append_event(events_file: Path, event: ErrorEvent) -> None:
    """Append one normalized event to JSONL storage."""

    ensure_parent(events_file)
    with events_file.open("a", encoding="utf-8") as handle:
        handle.write(event.to_json() + "\n")


def compute_recurrence(events: list[dict[str, Any]], fingerprint: str) -> int:
    """Count recurrence for fingerprint including current event."""

    seen = sum(1 for item in events if item.get("fingerprint") == fingerprint)
    return seen + 1


def summarize(events: list[dict[str, Any]], top: int = 5) -> dict[str, Any]:
    """Create summary stats used by CLI output."""

    by_category = Counter(item.get("category", "unknown") for item in events)
    by_fingerprint = Counter(item.get("fingerprint", "") for item in events if item.get("fingerprint"))
    recurring = by_fingerprint.most_common(top)
    return {
        "total_events": len(events),
        "by_category": dict(sorted(by_category.items(), key=lambda item: item[0])),
        "recurring_signatures": recurring,
    }


def format_summary(summary: dict[str, Any]) -> str:
    """Render a compact markdown summary."""

    lines = [
        "# Error Learning Summary",
        "",
        f"- Total logged events: {summary['total_events']}",
        "",
        "## By Category",
        "",
    ]
    if summary["by_category"]:
        for category, count in summary["by_category"].items():
            lines.append(f"- {category}: {count}")
    else:
        lines.append("- No events logged yet.")
    lines.extend(["", "## Recurring Signatures", ""])
    if summary["recurring_signatures"]:
        for fingerprint, count in summary["recurring_signatures"]:
            lines.append(f"- `{fingerprint}` -> {count} occurrences")
    else:
        lines.append("- No recurring signatures yet.")
    lines.append("")
    return "\n".join(lines)


def choose_lessons_path(explicit_path: str | None) -> Path:
    """Resolve lessons target: explicit path, AGENTS.md, or .learnings/."""

    if explicit_path:
        return Path(explicit_path)
    agents_path = Path("AGENTS.md")
    if agents_path.exists():
        return agents_path
    return DEFAULT_LESSONS_INDEX


def update_lessons_file(lessons_path: Path, event: ErrorEvent, dry_run: bool) -> str:
    """Append a lesson entry to AGENTS.md or .learnings markdown."""

    ensure_parent(lessons_path)
    if lessons_path.exists():
        current = lessons_path.read_text(encoding="utf-8")
    else:
        current = ""
    heading = "## Error Learnings"
    lesson_block = "\n".join(
        [
            f"- {event.timestamp} | `{event.fingerprint}` | {event.category} ({event.severity})",
            f"  - Message: {event.message}",
            f"  - Command: `{event.command or 'n/a'}`",
            f"  - Recurrence: {event.recurrence}",
            f"  - Suggested fix: {event.suggestions[0] if event.suggestions else 'n/a'}",
        ]
    )
    if heading in current:
        updated = current.rstrip() + "\n" + lesson_block + "\n"
    elif current.strip():
        updated = current.rstrip() + "\n\n" + heading + "\n\n" + lesson_block + "\n"
    else:
        updated = "# Agent Learnings\n\n" + heading + "\n\n" + lesson_block + "\n"

    if not dry_run:
        lessons_path.write_text(updated, encoding="utf-8")
    return str(lessons_path)


def cmd_log(args: argparse.Namespace) -> int:
    """Log an error event and optionally persist a lesson."""

    try:
        context = parse_context(args.context)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    events_file = Path(args.events_file)
    prior_events = load_events(events_file)
    category = args.category or classify_error(args.message, args.stacktrace or "", args.command or "")
    signature = build_signature(category, args.message)
    fingerprint = build_fingerprint(signature)
    recurrence = compute_recurrence(prior_events, fingerprint)
    suggestions = suggest_fixes(category, args.message, args.command or "")
    tags = sorted(set(args.tags or []))
    event = ErrorEvent(
        timestamp=utc_now_iso(),
        category=category,
        severity=args.severity or SEVERITY_BY_CATEGORY.get(category, "low"),
        signature=signature,
        fingerprint=fingerprint,
        recurrence=recurrence,
        message=args.message,
        command=args.command or "",
        stacktrace=args.stacktrace or "",
        context=context,
        tags=tags,
        suggestions=suggestions,
    )

    if not args.dry_run:
        append_event(events_file, event)

    lessons_path = choose_lessons_path(args.lessons_path)
    if args.update_lessons:
        touched = update_lessons_file(lessons_path, event, dry_run=args.dry_run)
        print(f"Lesson updated: {touched}")

    print(f"Logged error fingerprint: {event.fingerprint}")
    print(f"Category: {event.category} | Severity: {event.severity} | Recurrence: {event.recurrence}")
    print("Suggested fixes:")
    for item in event.suggestions:
        print(f"- {item}")
    return 0


def cmd_summary(args: argparse.Namespace) -> int:
    """Show a summary of logged errors."""

    events = load_events(Path(args.events_file))
    summary = summarize(events, top=args.top)
    output = format_summary(summary)
    if args.output:
        output_path = Path(args.output)
        ensure_parent(output_path)
        output_path.write_text(output, encoding="utf-8")
        print(f"Summary written to: {output_path}")
    else:
        print(output)
    return 0


def cmd_suggest(args: argparse.Namespace) -> int:
    """Produce suggestions for an ad-hoc error message."""

    category = args.category or classify_error(args.message, args.stacktrace or "", args.command or "")
    severity = args.severity or SEVERITY_BY_CATEGORY.get(category, "low")
    suggestions = suggest_fixes(category, args.message, args.command or "")
    print(f"Category: {category}")
    print(f"Severity: {severity}")
    print("Suggestions:")
    for item in suggestions:
        print(f"- {item}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build top-level CLI parser."""

    parser = argparse.ArgumentParser(
        description="Capture, classify, and learn from agent execution errors."
    )
    parser.add_argument(
        "--events-file",
        default=str(DEFAULT_EVENTS_FILE),
        help="Path to JSONL event store.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    log_cmd = subparsers.add_parser("log", help="Log a new error event.")
    log_cmd.add_argument("--message", required=True, help="Error message.")
    log_cmd.add_argument("--command", default="", help="Command that produced the error.")
    log_cmd.add_argument("--stacktrace", default="", help="Optional stacktrace text.")
    log_cmd.add_argument("--context", help='JSON object with extra context, e.g. \'{"step":"install"}\'.')
    log_cmd.add_argument("--category", help="Optional manual category override.")
    log_cmd.add_argument("--severity", help="Optional manual severity override.")
    log_cmd.add_argument("--tags", nargs="*", help="Optional tags.")
    log_cmd.add_argument(
        "--update-lessons",
        action="store_true",
        help="Append learned lesson to AGENTS.md or .learnings/.",
    )
    log_cmd.add_argument(
        "--lessons-path",
        help="Optional explicit lessons file path.",
    )
    log_cmd.add_argument("--dry-run", action="store_true", help="Print without writing files.")
    log_cmd.set_defaults(handler=cmd_log)

    summary_cmd = subparsers.add_parser("summary", help="Summarize historical errors.")
    summary_cmd.add_argument("--top", type=int, default=5, help="Top recurring signatures to show.")
    summary_cmd.add_argument("--output", help="Optional markdown output path.")
    summary_cmd.set_defaults(handler=cmd_summary)

    suggest_cmd = subparsers.add_parser("suggest", help="Suggest fixes for one error string.")
    suggest_cmd.add_argument("--message", required=True, help="Error message.")
    suggest_cmd.add_argument("--command", default="", help="Command context.")
    suggest_cmd.add_argument("--stacktrace", default="", help="Optional stacktrace.")
    suggest_cmd.add_argument("--category", help="Optional manual category.")
    suggest_cmd.add_argument("--severity", help="Optional manual severity.")
    suggest_cmd.set_defaults(handler=cmd_suggest)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run CLI command."""

    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
