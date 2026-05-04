from pathlib import Path
import json
from datetime import datetime, UTC
import re
import sys


STATUS_FLOW = ("idea", "planning", "implementing", "done")
ACTIVE_STATUSES = {"idea", "planning", "implementing"}
CONVERSATION_PATTERN = re.compile(
    r"\b(?:we\s+)?(?:should|could|need to|idea|dream|research|implement)\b[:\-\s]*(.+)",
    re.IGNORECASE,
)
TODO_PATTERN = re.compile(
    r"^\s*(?:[#/*-]+\s*)?(?:TODO|FIXME|HACK)\b[:\-\s]*(.+)",
    re.IGNORECASE,
)


class DreamTracker:
    def __init__(self, repo_root=None):
        self.repo_root = Path(repo_root or Path.cwd()).resolve()
        self.dream_root = self.repo_root / "dreams"
        self.active_dir = self.dream_root / "active"
        self.implemented_dir = self.dream_root / "implemented"
        self.archived_dir = self.dream_root / "archived"
        self.examples_dir = self.dream_root / "examples"
        self.index_path = self.dream_root / "dream_index.json"
        self._ensure_structure()

    def _ensure_structure(self):
        for directory in (
            self.dream_root,
            self.active_dir,
            self.implemented_dir,
            self.archived_dir,
            self.examples_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

        if not self.index_path.exists():
            self._save_index({"version": 1, "dreams": {}})

    def _load_index(self):
        if not self.index_path.exists():
            return {"version": 1, "dreams": {}}

        content = self.index_path.read_text(encoding="utf-8").strip()
        if not content:
            return {"version": 1, "dreams": {}}

        data = json.loads(content)
        if "dreams" not in data or not isinstance(data["dreams"], dict):
            data["dreams"] = {}
        if "version" not in data:
            data["version"] = 1
        return data

    def _save_index(self, data):
        self.index_path.write_text(
            json.dumps(data, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def _now(self):
        return datetime.now(UTC).replace(microsecond=0).isoformat()

    def _slugify(self, value):
        slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
        return slug or "dream"

    def _title_key(self, value):
        return self._slugify(value)

    def _make_id(self, title):
        timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
        return f"{timestamp}-{self._slugify(title)[:40]}"

    def _dream_location(self, dream):
        if dream["status"] == "done":
            return self.implemented_dir / f"{dream['id']}.md"
        if dream["status"] == "archived":
            return self.archived_dir / f"{dream['id']}.md"
        return self.active_dir / f"{dream['id']}.md"

    def _write_markdown(self, dream):
        target = self._dream_location(dream)
        for directory in (self.active_dir, self.implemented_dir, self.archived_dir):
            candidate = directory / f"{dream['id']}.md"
            if candidate != target and candidate.exists():
                candidate.unlink()

        history_lines = "\n".join(
            f"- {entry['timestamp']}: {entry['status']} - {entry['note']}"
            for entry in dream.get("history", [])
        )
        content = (
            f"# {dream['title']}\n\n"
            f"- id: {dream['id']}\n"
            f"- status: {dream['status']}\n"
            f"- source: {dream['source']}\n"
            f"- created_at: {dream['created_at']}\n"
            f"- updated_at: {dream['updated_at']}\n\n"
            "## Summary\n\n"
            f"{dream['description']}\n\n"
            "## Notes\n\n"
            f"{dream.get('notes', '').strip() or 'No notes yet.'}\n\n"
            "## History\n\n"
            f"{history_lines or '- No transitions yet.'}\n"
        )
        target.write_text(content, encoding="utf-8")
        dream["path"] = str(target.relative_to(self.repo_root))

    def _store_dream(self, dream):
        data = self._load_index()
        data["dreams"][dream["id"]] = dream
        self._write_markdown(dream)
        self._save_index(data)
        return dream

    def _find_existing_title(self, title):
        title_key = self._title_key(title)
        for dream in self._load_index()["dreams"].values():
            if self._title_key(dream["title"]) == title_key:
                return dream
        return None

    def create_dream(self, title, description="", source="manual"):
        title = re.sub(r"\s+", " ", title).strip()
        if not title:
            raise ValueError("Dream title cannot be empty.")

        existing = self._find_existing_title(title)
        if existing:
            return existing

        created_at = self._now()
        dream = {
            "id": self._make_id(title),
            "title": title,
            "description": description.strip()
            or f"{title} for OpenClaw's evolving dream-mode workflow.",
            "status": "idea",
            "source": source,
            "created_at": created_at,
            "updated_at": created_at,
            "notes": "",
            "history": [
                {
                    "timestamp": created_at,
                    "status": "idea",
                    "note": "Dream created.",
                }
            ],
            "path": "",
        }
        return self._store_dream(dream)

    def list_dreams(self):
        self.auto_generate_dreams()
        dreams = list(self._load_index()["dreams"].values())
        dreams.sort(key=lambda item: (item["created_at"], item["id"]), reverse=True)
        return dreams

    def get_dream(self, dream_id):
        self.auto_generate_dreams()
        dream = self._load_index()["dreams"].get(dream_id)
        if not dream:
            raise KeyError(f"Unknown dream id: {dream_id}")
        return dream

    def status(self, dream_id):
        dream = self.get_dream(dream_id)
        dream["path"] = str(self._dream_location(dream).relative_to(self.repo_root))
        return dream

    def _append_note(self, dream, note):
        existing = dream.get("notes", "").strip()
        dream["notes"] = f"{existing}\n\n{note}".strip() if existing else note

    def _transition(self, dream_id, new_status, note):
        if new_status not in STATUS_FLOW and new_status != "archived":
            raise ValueError(f"Unsupported status: {new_status}")

        data = self._load_index()
        dream = data["dreams"].get(dream_id)
        if not dream:
            raise KeyError(f"Unknown dream id: {dream_id}")

        dream["status"] = new_status
        dream["updated_at"] = self._now()
        dream.setdefault("history", []).append(
            {
                "timestamp": dream["updated_at"],
                "status": new_status,
                "note": note,
            }
        )
        self._append_note(dream, note)
        data["dreams"][dream_id] = dream
        self._write_markdown(dream)
        self._save_index(data)
        return dream

    def research_dream(self, dream_id):
        dream = self.get_dream(dream_id)
        if dream["status"] == "done":
            return dream

        note = self._build_research_summary(dream)
        return self._transition(dream_id, "planning", note)

    def implement_dream(self, dream_id):
        dream = self.get_dream(dream_id)
        if dream["status"] == "done":
            return dream

        if dream["status"] == "idea":
            self.research_dream(dream_id)
            dream = self.get_dream(dream_id)

        if dream["status"] == "planning":
            note = self._build_implementation_notes(dream)
            return self._transition(dream_id, "implementing", note)

        if dream["status"] == "implementing":
            completion_note = (
                f"Implementation review closed on {self._now()}. "
                "Dream moved to the implemented directory."
            )
            return self._transition(dream_id, "done", completion_note)

        return dream

    def archive_dream(self, dream_id, reason="Dream archived."):
        return self._transition(dream_id, "archived", reason)

    def auto_generate_dreams(self):
        candidates = []
        candidates.extend(self._detect_system_patterns())
        candidates.extend(self._detect_conversation_patterns())

        created = []
        seen = set()
        for candidate in candidates:
            key = self._title_key(candidate["title"])
            if key in seen:
                continue
            seen.add(key)
            created.append(
                self.create_dream(
                    candidate["title"],
                    description=candidate["description"],
                    source=candidate["source"],
                )
            )
        return created

    def _detect_system_patterns(self):
        candidates = []
        readme = self.repo_root / "README.md"
        if readme.exists():
            readme_text = readme.read_text(encoding="utf-8", errors="ignore")
            if "openclaw" in readme_text.lower():
                candidates.append(
                    {
                        "title": "Dream: Richer OpenClaw orchestration visibility",
                        "description": (
                            "README mentions OpenClaw orchestration. Explore visibility, "
                            "tracking, and operational dream workflows around that system."
                        ),
                        "source": "auto-system-pattern",
                    }
                )

        for path in self._iter_text_files(mode="system"):
            relative_path = str(path.relative_to(self.repo_root))
            text = path.read_text(encoding="utf-8", errors="ignore")
            for line in text.splitlines():
                match = TODO_PATTERN.search(line)
                if not match:
                    continue
                snippet = self._clean_phrase(match.group(1))
                if not snippet:
                    continue
                candidates.append(
                    {
                        "title": f"Dream: Resolve note from {path.stem}",
                        "description": (
                            f"System pattern spotted in {relative_path}: {snippet}. "
                            "Research how this note can become a concrete OpenClaw improvement."
                        ),
                        "source": "auto-system-pattern",
                    }
                )
                break
        return candidates

    def _detect_conversation_patterns(self):
        candidates = []
        for path in self._iter_text_files(mode="conversation"):
            relative_path = str(path.relative_to(self.repo_root))
            text = path.read_text(encoding="utf-8", errors="ignore")
            for line in text.splitlines():
                match = CONVERSATION_PATTERN.search(line)
                if not match:
                    continue
                phrase = self._clean_phrase(match.group(1))
                if not phrase:
                    continue
                title = self._title_from_phrase(phrase)
                if not title:
                    continue
                candidates.append(
                    {
                        "title": title,
                        "description": (
                            f"Conversation-style pattern discovered in {relative_path}: "
                            f"{phrase}. Capture it as a tracked dream for follow-up."
                        ),
                        "source": "auto-conversation-analysis",
                    }
                )
                break
        return candidates

    def _iter_text_files(self, mode="system"):
        if mode == "conversation":
            allowed_suffixes = {".md", ".txt", ".json"}
        else:
            allowed_suffixes = {".md", ".txt", ".py", ".json"}

        for path in sorted(self.repo_root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in allowed_suffixes:
                continue
            if self.dream_root in path.parents:
                continue
            yield path

    def _clean_phrase(self, text):
        cleaned = re.sub(r"^[\-\*\d\.\)\s]+", "", text.strip())
        cleaned = re.sub(
            r"^(?:research|implement|build|create|add|track|plan)\s+",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" .:-")
        return cleaned

    def _title_from_phrase(self, phrase):
        words = re.findall(r"[A-Za-z0-9']+", phrase)
        if not words:
            return None
        title_words = words[:8]
        title = " ".join(title_words)
        return f"Dream: {title[:1].upper() + title[1:]}"

    def _related_files(self, dream):
        keywords = [
            word
            for word in re.findall(r"[a-z0-9]{4,}", dream["title"].lower())
            if word not in {"dream", "openclaw", "with", "from", "that"}
        ]
        matches = []
        for path in self._iter_text_files(mode="system"):
            haystack = path.read_text(encoding="utf-8", errors="ignore").lower()
            if any(keyword in haystack for keyword in keywords):
                matches.append(str(path.relative_to(self.repo_root)))
            if len(matches) == 5:
                break
        return matches

    def _build_research_summary(self, dream):
        related_files = self._related_files(dream)
        related_block = ", ".join(related_files) if related_files else "No direct file hits."
        return (
            f"Research summary generated at {self._now()}.\n"
            f"- Current status reviewed for '{dream['title']}'.\n"
            f"- Related files: {related_block}\n"
            "- Recommended next step: define scope, evidence, and implementation guardrails."
        )

    def _build_implementation_notes(self, dream):
        related_files = self._related_files(dream)
        if related_files:
            file_lines = "\n".join(f"- {path}" for path in related_files)
        else:
            file_lines = "- Start by identifying the best integration point in the repository."
        return (
            f"Implementation outline generated at {self._now()}.\n"
            "- Promote the idea into a concrete workstream.\n"
            "- Touchpoints worth reviewing:\n"
            f"{file_lines}\n"
            "- Exit criteria: updated behavior, verification, and dream cleanup."
        )


def _format_dream_line(dream):
    return f"{dream['id']} | {dream['status']} | {dream['title']} | {dream['source']}"


def _usage():
    return (
        "Usage: python -m src.dreams.dream_tracker "
        "list|create <title>|status <id>|research <id>|implement <id>"
    )


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    tracker = DreamTracker()

    if not argv or argv[0] in {"-h", "--help"}:
        print(_usage())
        return 0

    command = argv[0]

    if command == "list":
        dreams = tracker.list_dreams()
        if not dreams:
            print("No dreams tracked yet.")
            return 0
        for dream in dreams:
            print(_format_dream_line(dream))
        return 0

    if command == "create":
        title = " ".join(argv[1:]).strip()
        if not title:
            print("create requires a title", file=sys.stderr)
            return 1
        dream = tracker.create_dream(title)
        print(_format_dream_line(dream))
        return 0

    if command == "status":
        if len(argv) < 2:
            print("status requires an id", file=sys.stderr)
            return 1
        try:
            dream = tracker.status(argv[1])
        except KeyError as error:
            print(str(error), file=sys.stderr)
            return 1
        print(json.dumps(dream, indent=2, sort_keys=True))
        return 0

    if command == "research":
        if len(argv) < 2:
            print("research requires an id", file=sys.stderr)
            return 1
        try:
            dream = tracker.research_dream(argv[1])
        except KeyError as error:
            print(str(error), file=sys.stderr)
            return 1
        print(_format_dream_line(dream))
        return 0

    if command == "implement":
        if len(argv) < 2:
            print("implement requires an id", file=sys.stderr)
            return 1
        try:
            dream = tracker.implement_dream(argv[1])
        except KeyError as error:
            print(str(error), file=sys.stderr)
            return 1
        print(_format_dream_line(dream))
        return 0

    print(_usage(), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
