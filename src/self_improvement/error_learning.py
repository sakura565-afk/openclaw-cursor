from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


@dataclass
class ErrorEntry:
    category: str
    error: str
    lesson: str
    resolved: bool = False
    occurrences: int = 1
    first_seen: str = ""
    last_seen: str = ""

    def as_dict(self) -> Dict[str, object]:
        return {
            "category": self.category,
            "error": self.error,
            "lesson": self.lesson,
            "resolved": self.resolved,
            "occurrences": self.occurrences,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
        }


class ErrorLearningSystem:
    def __init__(
        self,
        root_dir: Path | str | None = None,
        session_log_dir: Path | str | None = None,
        learnings_file: Path | str | None = None,
    ) -> None:
        self.root_dir = Path(root_dir or Path.cwd())
        self.session_log_dir = Path(session_log_dir or self.root_dir / "logs")
        self.learnings_file = Path(learnings_file or self.root_dir / ".learnings" / "error_log.json")
        self.learnings_file.parent.mkdir(parents=True, exist_ok=True)

    def load_entries(self) -> List[ErrorEntry]:
        if not self.learnings_file.exists():
            return []
        try:
            payload = json.loads(self.learnings_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []
        if not isinstance(payload, list):
            return []
        entries: List[ErrorEntry] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            entry = ErrorEntry(
                category=str(item.get("category", "runtime")),
                error=str(item.get("error", "")).strip(),
                lesson=str(item.get("lesson", "")).strip(),
                resolved=bool(item.get("resolved", False)),
                occurrences=max(1, int(item.get("occurrences", 1) or 1)),
                first_seen=str(item.get("first_seen", "")),
                last_seen=str(item.get("last_seen", "")),
            )
            if entry.error:
                entries.append(entry)
        return entries

    def save_entries(self, entries: Sequence[ErrorEntry]) -> None:
        payload = [entry.as_dict() for entry in entries]
        self.learnings_file.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    def _normalize_error(self, error: str) -> str:
        normalized = error.lower()
        normalized = re.sub(r"0x[0-9a-f]+", "<hex>", normalized)
        normalized = re.sub(r"\b\d+\b", "<num>", normalized)
        normalized = re.sub(r"'[^']*'|\"[^\"]*\"", "<str>", normalized)
        normalized = re.sub(r"\s+", " ", normalized)
        return normalized.strip()

    def _find_similar_entry(self, entries: Sequence[ErrorEntry], error: str) -> Optional[ErrorEntry]:
        target = self._normalize_error(error)
        best: Optional[ErrorEntry] = None
        best_score = 0.0
        for entry in entries:
            existing = self._normalize_error(entry.error)
            if existing == target:
                return entry
            score = SequenceMatcher(a=existing, b=target).ratio()
            if score > best_score:
                best = entry
                best_score = score
        if best is not None and best_score >= 0.9:
            return best
        return None

    def _categorize_error(self, error: str) -> str:
        lowered = error.lower()
        if any(token in lowered for token in ("timeout", "timed out", "connection", "network", "dns")):
            return "network"
        if any(token in lowered for token in ("permission denied", "unauthorized", "forbidden")):
            return "permission"
        if any(token in lowered for token in ("out of memory", "cuda out of memory", "memoryerror")):
            return "resource"
        if any(token in lowered for token in ("modulenotfounderror", "importerror")):
            return "dependency"
        if any(token in lowered for token in ("filenotfounderror", "no such file or directory")):
            return "io"
        if "valueerror" in lowered or "typeerror" in lowered:
            return "validation"
        return "runtime"

    def _default_lesson(self, category: str) -> str:
        return (
            f"Investigate {category} failures in OpenClaw session setup and add guardrails to prevent recurrence."
        )

    def add_entry(
        self,
        *,
        category: str,
        error: str,
        lesson: str,
        resolved: bool = False,
        increment: int = 1,
    ) -> ErrorEntry:
        entries = self.load_entries()
        now = _now_utc().isoformat()
        similar = self._find_similar_entry(entries, error)
        if similar is not None:
            similar.occurrences += max(1, increment)
            similar.last_seen = now
            if lesson and not similar.lesson:
                similar.lesson = lesson
            similar.resolved = bool(resolved if resolved else similar.resolved)
            self.save_entries(entries)
            return similar

        entry = ErrorEntry(
            category=category,
            error=error.strip(),
            lesson=lesson.strip(),
            resolved=resolved,
            occurrences=max(1, increment),
            first_seen=now,
            last_seen=now,
        )
        entries.append(entry)
        self.save_entries(entries)
        return entry

    def list_entries(self, *, include_resolved: bool = True) -> List[ErrorEntry]:
        entries = self.load_entries()
        if include_resolved:
            return entries
        return [entry for entry in entries if not entry.resolved]

    def search_entries(self, query: str, *, include_resolved: bool = True) -> List[ErrorEntry]:
        needle = query.strip().lower()
        if not needle:
            return self.list_entries(include_resolved=include_resolved)
        return [
            entry
            for entry in self.list_entries(include_resolved=include_resolved)
            if needle in entry.error.lower()
            or needle in entry.lesson.lower()
            or needle in entry.category.lower()
        ]

    def mark_resolved(self, query: str, resolved: bool = True) -> int:
        entries = self.load_entries()
        matches = self.search_entries(query, include_resolved=True)
        if not matches:
            return 0
        match_ids = {_slugify(item.error) for item in matches}
        updated = 0
        for entry in entries:
            if _slugify(entry.error) in match_ids:
                entry.resolved = resolved
                entry.last_seen = _now_utc().isoformat()
                updated += 1
        if updated:
            self.save_entries(entries)
        return updated

    def _iter_session_log_files(self) -> Iterable[Path]:
        if not self.session_log_dir.exists():
            return []
        candidates = sorted(self.session_log_dir.glob("*session*.log"))
        if not candidates:
            candidates = sorted(self.session_log_dir.glob("*openclaw*.log"))
        if not candidates:
            candidates = sorted(self.session_log_dir.glob("*.log"))
        return [path for path in candidates if path.is_file()]

    def _extract_errors_from_text(self, text: str) -> List[str]:
        errors: List[str] = []
        lines = text.splitlines()
        marker_pattern = re.compile(r"(error|exception|traceback)", re.IGNORECASE)
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            if marker_pattern.search(stripped):
                errors.append(stripped)
        return errors

    def capture_recurring_errors(self, min_occurrences: int = 2) -> int:
        buckets: Dict[str, Dict[str, object]] = {}
        for log_path in self._iter_session_log_files():
            try:
                text = log_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for error_line in self._extract_errors_from_text(text):
                normalized = self._normalize_error(error_line)
                if normalized not in buckets:
                    buckets[normalized] = {"raw": error_line, "count": 0}
                buckets[normalized]["count"] = int(buckets[normalized]["count"]) + 1

        created_or_updated = 0
        for item in buckets.values():
            count = int(item["count"])
            if count < min_occurrences:
                continue
            error = str(item["raw"])
            category = self._categorize_error(error)
            lesson = self._default_lesson(category)
            self.add_entry(
                category=category,
                error=error,
                lesson=lesson,
                resolved=False,
                increment=count,
            )
            created_or_updated += 1
        return created_or_updated


def _print_entries(entries: Sequence[ErrorEntry]) -> None:
    if not entries:
        print("No error learnings found.")
        return
    for entry in entries:
        state = "resolved" if entry.resolved else "open"
        print(f"- [{state}] {entry.category} | {entry.error}")
        print(f"  lesson: {entry.lesson}")
        print(f"  occurrences: {entry.occurrences}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="OpenClaw error learning system")
    parser.add_argument("--root-dir", default=".", help="Repository root directory")
    parser.add_argument("--session-log-dir", default=None, help="Directory containing OpenClaw session logs")
    parser.add_argument("--learnings-file", default=None, help="Override path for .learnings/error_log.json")

    subparsers = parser.add_subparsers(dest="command", required=True)

    capture = subparsers.add_parser("capture", help="Capture recurring errors from session logs")
    capture.add_argument("--min-occurrences", type=int, default=2, help="Minimum repeated count to persist")

    add = subparsers.add_parser("add", help="Add an error learning entry")
    add.add_argument("--category", required=True, help="Error category")
    add.add_argument("--error", required=True, help="Error message")
    add.add_argument("--lesson", required=True, help="Lesson learned")
    add.add_argument("--resolved", action="store_true", help="Mark as resolved at creation")

    list_cmd = subparsers.add_parser("list", help="List stored error learnings")
    list_cmd.add_argument("--open-only", action="store_true", help="Show unresolved entries only")

    search = subparsers.add_parser("search", help="Search stored error learnings")
    search.add_argument("query", help="Search query")
    search.add_argument("--open-only", action="store_true", help="Show unresolved entries only")

    resolve = subparsers.add_parser("resolve", help="Mark matched errors as resolved")
    resolve.add_argument("query", help="Query for matching entries")
    resolve.add_argument("--reopen", action="store_true", help="Reopen matched entries instead of resolving")

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    system = ErrorLearningSystem(
        root_dir=Path(args.root_dir),
        session_log_dir=Path(args.session_log_dir) if args.session_log_dir else None,
        learnings_file=Path(args.learnings_file) if args.learnings_file else None,
    )

    if args.command == "capture":
        count = system.capture_recurring_errors(min_occurrences=max(1, args.min_occurrences))
        print(f"Captured {count} recurring error pattern(s).")
        return 0

    if args.command == "add":
        entry = system.add_entry(
            category=args.category,
            error=args.error,
            lesson=args.lesson,
            resolved=args.resolved,
        )
        print(f"Saved learning: {entry.category} | {entry.error}")
        return 0

    if args.command == "list":
        entries = system.list_entries(include_resolved=not args.open_only)
        _print_entries(entries)
        return 0

    if args.command == "search":
        entries = system.search_entries(args.query, include_resolved=not args.open_only)
        _print_entries(entries)
        return 0

    if args.command == "resolve":
        updated = system.mark_resolved(args.query, resolved=not args.reopen)
        if args.reopen:
            print(f"Reopened {updated} entrie(s).")
        else:
            print(f"Resolved {updated} entrie(s).")
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
