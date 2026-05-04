from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


ENV_MEMORY_PATH = "SYNC_OBSIDIAN_MEMORY_PATH"
ENV_VAULT_PATH = "SYNC_OBSIDIAN_VAULT_PATH"
ENV_LOG_DIR = "SYNC_OBSIDIAN_LOG_DIR"
SCAN_DIRS = ("01_Projects", "02_Knowledge", "memory")
IGNORE_DIRS = {".obsidian", "__pycache__"}
DAILY_NOTES_TITLE = "Daily Notes"
SYNC_MARKER_PREFIX = "<!-- obsidian-sync: "
SYNC_MARKER_SUFFIX = " -->"
STALE_PREFIX = "> Sync status: STALE"
HEADING_RE = re.compile(r"^##\s+(?P<title>.+?)\s*$")
ANSI_COLORS = {
    "info": "\033[36m",
    "success": "\033[32m",
    "warning": "\033[33m",
    "error": "\033[31m",
    "reset": "\033[0m",
}


@dataclass
class SyncMeta:
    slug: str
    memory_hash: str = ""
    vault_hash: str = ""
    memory_mtime: str = ""
    vault_mtime: str = ""
    stale: bool = False
    vault_relative_path: str = ""
    last_sync: str = ""

    @classmethod
    def from_line(cls, line: str) -> "SyncMeta | None":
        if not line.startswith(SYNC_MARKER_PREFIX) or not line.endswith(SYNC_MARKER_SUFFIX):
            return None
        payload = line[len(SYNC_MARKER_PREFIX) : -len(SYNC_MARKER_SUFFIX)]
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict) or "slug" not in data:
            return None
        return cls(
            slug=str(data.get("slug", "")),
            memory_hash=str(data.get("memory_hash", "")),
            vault_hash=str(data.get("vault_hash", "")),
            memory_mtime=str(data.get("memory_mtime", "")),
            vault_mtime=str(data.get("vault_mtime", "")),
            stale=bool(data.get("stale", False)),
            vault_relative_path=str(data.get("vault_relative_path", "")),
            last_sync=str(data.get("last_sync", "")),
        )

    def to_line(self) -> str:
        payload = {
            "slug": self.slug,
            "memory_hash": self.memory_hash,
            "vault_hash": self.vault_hash,
            "memory_mtime": self.memory_mtime,
            "vault_mtime": self.vault_mtime,
            "stale": self.stale,
            "vault_relative_path": self.vault_relative_path,
            "last_sync": self.last_sync,
        }
        return f"{SYNC_MARKER_PREFIX}{json.dumps(payload, sort_keys=True)}{SYNC_MARKER_SUFFIX}"


@dataclass
class MemorySection:
    title: str
    body_lines: list[str]
    meta: SyncMeta | None = None
    stale_note: str | None = None

    @property
    def slug(self) -> str:
        if self.meta and self.meta.slug:
            return self.meta.slug
        return slugify(self.title)

    def body_text(self) -> str:
        return normalize_text(self.body_lines)


@dataclass
class MemoryDocument:
    preamble_lines: list[str]
    sections: list[MemorySection]

    def find_section_by_slug(self, slug: str) -> MemorySection | None:
        for section in self.sections:
            if section.slug == slug:
                return section
        return None

    def find_section_by_title(self, title: str) -> MemorySection | None:
        for section in self.sections:
            if section.title == title:
                return section
        return None

    def ensure_daily_notes(self) -> MemorySection:
        section = self.find_section_by_title(DAILY_NOTES_TITLE)
        if section is not None:
            return section
        section = MemorySection(title=DAILY_NOTES_TITLE, body_lines=[])
        self.sections.append(section)
        return section

    def render(self) -> str:
        lines = trim_blank_lines(self.preamble_lines)
        if lines and self.sections:
            lines.append("")
        for index, section in enumerate(self.sections):
            lines.append(f"## {section.title}")
            if section.meta is not None:
                lines.append(section.meta.to_line())
            if section.stale_note:
                lines.append(section.stale_note)
            if section.body_lines:
                lines.append("")
                lines.extend(section.body_lines)
            if index < len(self.sections) - 1:
                lines.append("")
        return "\n".join(lines).rstrip() + "\n"


def slugify(value: str) -> str:
    lowered = value.strip().lower()
    lowered = re.sub(r"[^a-z0-9]+", "-", lowered)
    return lowered.strip("-") or "untitled"


def trim_blank_lines(lines: list[str]) -> list[str]:
    trimmed = list(lines)
    while trimmed and not trimmed[0].strip():
        trimmed.pop(0)
    while trimmed and not trimmed[-1].strip():
        trimmed.pop()
    return trimmed


def normalize_text(lines: list[str]) -> str:
    text = "\n".join(lines).rstrip()
    return text + ("\n" if text else "")


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def isoformat_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def path_mtime_iso(path: Path) -> str:
    return isoformat_utc(datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc))


def report_path_for(log_dir: Path, current_time: datetime | None = None) -> Path:
    current_time = current_time or now_utc()
    return log_dir / f"sync_obsidian_{current_time.strftime('%Y%m%d')}.json"


def colorize(level: str, message: str) -> str:
    color = ANSI_COLORS.get(level, "")
    reset = ANSI_COLORS["reset"] if color else ""
    return f"{color}{message}{reset}"


def emit(level: str, message: str) -> None:
    print(colorize(level, message))


def parse_memory_document(text: str) -> MemoryDocument:
    lines = text.splitlines()
    heading_indexes = [index for index, line in enumerate(lines) if HEADING_RE.match(line)]
    if not heading_indexes:
        return MemoryDocument(preamble_lines=lines, sections=[])

    preamble_lines = trim_blank_lines(lines[: heading_indexes[0]])
    sections: list[MemorySection] = []

    for index, heading_start in enumerate(heading_indexes):
        heading_end = heading_indexes[index + 1] if index + 1 < len(heading_indexes) else len(lines)
        raw_section_lines = lines[heading_start:heading_end]
        match = HEADING_RE.match(raw_section_lines[0])
        if match is None:
            continue
        title = match.group("title").strip()
        cursor = 1
        meta = None
        stale_note = None

        if cursor < len(raw_section_lines):
            meta = SyncMeta.from_line(raw_section_lines[cursor])
            if meta is not None:
                cursor += 1
        if cursor < len(raw_section_lines) and raw_section_lines[cursor].startswith(STALE_PREFIX):
            stale_note = raw_section_lines[cursor]
            cursor += 1

        body_lines = trim_blank_lines(raw_section_lines[cursor:])
        sections.append(MemorySection(title=title, body_lines=body_lines, meta=meta, stale_note=stale_note))

    return MemoryDocument(preamble_lines=preamble_lines, sections=sections)


def scan_vault(vault_path: Path) -> dict[str, list[Path]]:
    scanned: dict[str, list[Path]] = {}
    for directory in SCAN_DIRS:
        root = vault_path / directory
        results: list[Path] = []
        if root.exists():
            for path in sorted(root.rglob("*.md")):
                if any(part in IGNORE_DIRS for part in path.parts):
                    continue
                results.append(path)
        scanned[directory] = results
    return scanned


def default_vault_path_for_section(section: MemorySection, vault_path: Path) -> Path:
    relative = section.meta.vault_relative_path if section.meta and section.meta.vault_relative_path else f"memory/{section.slug}.md"
    return vault_path / relative


def render_vault_reference(section: MemorySection, generated_at: str) -> str:
    body = section.body_text().rstrip()
    content_lines = [
        f"{SYNC_MARKER_PREFIX}{json.dumps({'slug': section.slug, 'memory_section': section.title, 'generated_at': generated_at}, sort_keys=True)}{SYNC_MARKER_SUFFIX}",
        f"# {section.title}",
        "",
        f"Source: [[MEMORY#{section.title}]]",
        "",
        "## Synced Content",
    ]
    if body:
        content_lines.extend(["", *body.splitlines()])
    return "\n".join(content_lines).rstrip() + "\n"


def extract_vault_body(text: str) -> str:
    lines = text.splitlines()
    try:
        heading_index = lines.index("## Synced Content")
    except ValueError:
        return text.rstrip() + ("\n" if text.strip() else "")
    body_lines = trim_blank_lines(lines[heading_index + 1 :])
    return normalize_text(body_lines)


def build_diff(before: str, after: str, from_label: str, to_label: str, limit: int = 40) -> list[str]:
    diff = list(
        difflib.unified_diff(
            before.splitlines(),
            after.splitlines(),
            fromfile=from_label,
            tofile=to_label,
            lineterm="",
        )
    )
    return diff[:limit]


def add_daily_note(document: MemoryDocument, note_text: str) -> bool:
    daily_notes = document.ensure_daily_notes()
    existing = "\n".join(daily_notes.body_lines)
    if note_text in existing:
        return False
    if daily_notes.body_lines:
        daily_notes.body_lines.append(note_text)
    else:
        daily_notes.body_lines = [note_text]
    return True


def ensure_meta(section: MemorySection) -> SyncMeta:
    if section.meta is None:
        section.meta = SyncMeta(slug=slugify(section.title))
    elif not section.meta.slug:
        section.meta.slug = slugify(section.title)
    return section.meta


def sync(memory_path: Path, vault_path: Path, log_dir: Path, dry_run: bool = False) -> dict[str, object]:
    if not memory_path.exists():
        raise FileNotFoundError(f"MEMORY file not found: {memory_path}")
    if not vault_path.exists():
        raise FileNotFoundError(f"Vault path not found: {vault_path}")

    started_at = now_utc()
    memory_original = memory_path.read_text(encoding="utf-8")
    document = parse_memory_document(memory_original)
    scanned = scan_vault(vault_path)
    memory_files = scanned.get("memory", [])
    notes_added: list[dict[str, object]] = []
    actions: list[dict[str, object]] = []
    conflicts: list[dict[str, object]] = []
    touched_vault_files: set[Path] = set()

    memory_file_mtime = path_mtime_iso(memory_path)
    memory_file_dt = datetime.fromtimestamp(memory_path.stat().st_mtime, tz=timezone.utc)

    for section in document.sections:
        if section.title == DAILY_NOTES_TITLE:
            continue

        meta = ensure_meta(section)
        memory_body = section.body_text()
        memory_hash = sha256_text(memory_body)
        vault_file = default_vault_path_for_section(section, vault_path)
        meta.vault_relative_path = str(vault_file.relative_to(vault_path))
        touched_vault_files.add(vault_file)

        vault_exists = vault_file.exists()
        vault_text = vault_file.read_text(encoding="utf-8") if vault_exists else ""
        vault_body = extract_vault_body(vault_text) if vault_exists else ""
        vault_hash = sha256_text(vault_body) if vault_exists else ""
        vault_dt = datetime.fromtimestamp(vault_file.stat().st_mtime, tz=timezone.utc) if vault_exists else None
        vault_mtime = path_mtime_iso(vault_file) if vault_exists else ""

        memory_changed = not meta.memory_hash or meta.memory_hash != memory_hash
        vault_changed = vault_exists and (not meta.vault_hash or meta.vault_hash != vault_hash)
        sync_time = isoformat_utc(started_at)
        desired_vault_text = render_vault_reference(section, sync_time)

        if not vault_exists:
            action = {
                "type": "vault_created",
                "section": section.title,
                "vault_path": str(vault_file),
                "dry_run": dry_run,
                "diff": build_diff("", desired_vault_text, "missing", str(vault_file)),
            }
            actions.append(action)
            emit("success", f"Create vault reference for '{section.title}' -> {vault_file}")
            if not dry_run:
                vault_file.parent.mkdir(parents=True, exist_ok=True)
                vault_file.write_text(desired_vault_text, encoding="utf-8")
                vault_mtime = path_mtime_iso(vault_file)
                written_vault_hash = sha256_text(extract_vault_body(desired_vault_text))
                meta.memory_hash = memory_hash
                meta.vault_hash = written_vault_hash
                meta.memory_mtime = memory_file_mtime
                meta.vault_mtime = vault_mtime
                meta.last_sync = sync_time
                meta.stale = False
                section.stale_note = None
            continue

        if vault_hash == memory_hash and not meta.memory_hash and not meta.vault_hash:
            emit("info", f"Bootstrap sync metadata for '{section.title}'")
            meta.memory_hash = memory_hash
            meta.vault_hash = vault_hash
            meta.memory_mtime = memory_file_mtime
            meta.vault_mtime = vault_mtime
            meta.last_sync = sync_time
            meta.stale = False
            section.stale_note = None
            continue

        if memory_changed and vault_changed and memory_hash != vault_hash:
            resolved_to = "memory" if vault_dt is None or memory_file_dt >= vault_dt else "vault"
            conflict = {
                "section": section.title,
                "vault_path": str(vault_file),
                "resolved_to": resolved_to,
                "memory_mtime": memory_file_mtime,
                "vault_mtime": vault_mtime,
                "diff": build_diff(memory_body, vault_body, "MEMORY.md", str(vault_file)),
            }
            conflicts.append(conflict)
            emit("warning", f"Conflict for '{section.title}' resolved to newer {resolved_to} timestamp")
            if resolved_to == "memory":
                action = {
                    "type": "vault_updated",
                    "section": section.title,
                    "vault_path": str(vault_file),
                    "dry_run": dry_run,
                    "reason": "memory_newer_conflict",
                    "diff": build_diff(vault_text, desired_vault_text, str(vault_file), "memory->vault"),
                }
                actions.append(action)
                if not dry_run:
                    vault_file.write_text(desired_vault_text, encoding="utf-8")
                    meta.memory_hash = memory_hash
                    meta.vault_hash = sha256_text(extract_vault_body(desired_vault_text))
                    meta.memory_mtime = memory_file_mtime
                    meta.vault_mtime = path_mtime_iso(vault_file)
                    meta.last_sync = sync_time
                    meta.stale = False
                    section.stale_note = None
            else:
                section.stale_note = f"{STALE_PREFIX} - review {meta.vault_relative_path} (updated {vault_mtime})"
                meta.memory_hash = memory_hash
                meta.vault_hash = vault_hash
                meta.memory_mtime = memory_file_mtime
                meta.vault_mtime = vault_mtime
                meta.last_sync = sync_time
                meta.stale = True
                action = {
                    "type": "memory_marked_stale",
                    "section": section.title,
                    "vault_path": str(vault_file),
                    "dry_run": dry_run,
                    "reason": "vault_newer_conflict",
                }
                actions.append(action)
            continue

        if memory_changed and memory_hash != vault_hash:
            action = {
                "type": "vault_updated",
                "section": section.title,
                "vault_path": str(vault_file),
                "dry_run": dry_run,
                "reason": "memory_changed",
                "diff": build_diff(vault_text, desired_vault_text, str(vault_file), "memory->vault"),
            }
            actions.append(action)
            emit("success", f"Update vault reference from MEMORY for '{section.title}'")
            if not dry_run:
                vault_file.write_text(desired_vault_text, encoding="utf-8")
                meta.memory_hash = memory_hash
                meta.vault_hash = sha256_text(extract_vault_body(desired_vault_text))
                meta.memory_mtime = memory_file_mtime
                meta.vault_mtime = path_mtime_iso(vault_file)
                meta.last_sync = sync_time
                meta.stale = False
                section.stale_note = None
            continue

        if vault_changed and memory_hash != vault_hash:
            section.stale_note = f"{STALE_PREFIX} - review {meta.vault_relative_path} (updated {vault_mtime})"
            meta.memory_hash = memory_hash
            meta.vault_hash = vault_hash
            meta.memory_mtime = memory_file_mtime
            meta.vault_mtime = vault_mtime
            meta.last_sync = sync_time
            meta.stale = True
            action = {
                "type": "memory_marked_stale",
                "section": section.title,
                "vault_path": str(vault_file),
                "dry_run": dry_run,
                "reason": "vault_changed",
                "diff": build_diff(memory_body, vault_body, "MEMORY.md", str(vault_file)),
            }
            actions.append(action)
            emit("warning", f"Vault note is newer for '{section.title}'; MEMORY marked stale")
            continue

        meta.memory_hash = memory_hash
        meta.vault_hash = vault_hash
        meta.memory_mtime = memory_file_mtime
        meta.vault_mtime = vault_mtime
        meta.last_sync = sync_time
        if meta.stale and section.stale_note:
            continue
        meta.stale = False
        section.stale_note = None

    for vault_file in memory_files:
        if vault_file in touched_vault_files:
            continue
        slug = slugify(vault_file.stem)
        if document.find_section_by_slug(slug) is not None:
            continue
        note_title = extract_note_title(vault_file)
        note_entry = f"- {started_at.strftime('%Y-%m-%d')}: added vault note {vault_file.relative_to(vault_path)} ({note_title})"
        if add_daily_note(document, note_entry):
            note_action = {
                "type": "daily_note_added",
                "vault_path": str(vault_file),
                "entry": note_entry,
                "dry_run": dry_run,
            }
            notes_added.append(note_action)
            emit("info", f"Add daily note for unmatched vault file {vault_file.relative_to(vault_path)}")

    memory_updated = document.render()
    memory_changed = memory_updated != memory_original
    if memory_changed and not dry_run:
        memory_path.write_text(memory_updated, encoding="utf-8")

    log_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "started_at": isoformat_utc(started_at),
        "finished_at": isoformat_utc(now_utc()),
        "dry_run": dry_run,
        "memory_path": str(memory_path),
        "vault_path": str(vault_path),
        "scanned": {key: [str(path) for path in value] for key, value in scanned.items()},
        "actions": actions,
        "notes_added": notes_added,
        "conflicts": conflicts,
        "memory_changed": memory_changed,
    }
    report_path = report_path_for(log_dir, started_at)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    emit("info", f"Wrote sync report to {report_path}")
    emit(
        "info",
        f"Summary: {len(actions)} file actions, {len(notes_added)} daily-note updates, {len(conflicts)} conflicts",
    )
    report["report_path"] = str(report_path)
    return report


def extract_note_title(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return path.stem.replace("-", " ").replace("_", " ").strip().title() or path.stem


def resolve_memory_path(raw_value: str | None) -> Path:
    value = raw_value or os.environ.get(ENV_MEMORY_PATH) or "MEMORY.md"
    return Path(value).expanduser().resolve()


def resolve_vault_path(raw_value: str | None) -> Path:
    value = raw_value or os.environ.get(ENV_VAULT_PATH) or "vault"
    return Path(value).expanduser().resolve()


def resolve_log_dir() -> Path:
    value = os.environ.get(ENV_LOG_DIR) or "logs"
    return Path(value).expanduser().resolve()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Bidirectional sync for MEMORY.md and an Obsidian vault.")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without writing MEMORY or vault files.")
    parser.add_argument("--memory", help=f"Path to MEMORY.md (default: ${ENV_MEMORY_PATH} or ./MEMORY.md)")
    parser.add_argument("--vault", help=f"Path to Obsidian vault (default: ${ENV_VAULT_PATH} or ./vault)")
    parser.add_argument("--log-dir", help=f"Directory for sync reports (default: ${ENV_LOG_DIR} or ./logs)")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        sync(
            memory_path=resolve_memory_path(args.memory),
            vault_path=resolve_vault_path(args.vault),
            log_dir=Path(args.log_dir).expanduser().resolve() if args.log_dir else resolve_log_dir(),
            dry_run=args.dry_run,
        )
    except FileNotFoundError as exc:
        emit("error", str(exc))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
