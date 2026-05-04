"""Idea-to-PR pipeline utilities for OpenClaw."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


PHASES = ("research", "draft", "review", "pr")


def _utc_now() -> datetime:
    """Return the current UTC timestamp."""
    return datetime.now(timezone.utc)


class IdeaPipeline:
    """Run a simple idea pipeline and persist status by day."""

    def __init__(self, project_root: Path | None = None, now_func=None) -> None:
        self.project_root = Path(project_root or Path.cwd()).resolve()
        self.logs_dir = self.project_root / "logs"
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.now_func = now_func or _utc_now

    def run_phase(self, phase: str, topic: str) -> dict:
        """Execute a pipeline phase for the supplied topic."""
        normalized_phase = self._normalize_phase(phase)
        clean_topic = self._normalize_topic(topic)
        log_data = self._load_log_data()
        entry = self._get_or_create_entry(log_data, clean_topic)

        target_index = PHASES.index(normalized_phase)
        last_result = {}

        for phase_name in PHASES[: target_index + 1]:
            if phase_name in entry["phases"]:
                last_result = entry["phases"][phase_name]
                continue

            method = getattr(self, f"_{phase_name}_phase")
            last_result = method(clean_topic, entry)
            entry["phases"][phase_name] = last_result
            entry["history"].append(
                {
                    "phase": phase_name,
                    "status": "completed",
                    "completed_at": last_result["completed_at"],
                }
            )
            entry["updated_at"] = self._timestamp()

        entry["current_phase"] = normalized_phase
        entry["status"] = self._pipeline_status(entry)
        self._save_log_data(log_data)
        return {
            "topic": clean_topic,
            "phase": normalized_phase,
            "status": entry["status"],
            "result": last_result,
            "log_path": str(self._log_path()),
        }

    def _research_phase(self, topic: str, entry: dict) -> dict:
        words = topic.split()
        keywords = [word.strip(" ,.-").lower() for word in words if word.strip(" ,.-")]
        unique_keywords = list(dict.fromkeys(keywords))
        return {
            "phase": "research",
            "summary": f"Analyzed '{topic}' for OpenClaw implementation opportunities.",
            "questions": [
                f"What operational need does '{topic}' address in OpenClaw?",
                f"Which repository components should change for '{topic}'?",
                f"How should success be validated for '{topic}'?",
            ],
            "gathered_info": {
                "topic_length": len(topic),
                "word_count": len(words),
                "keywords": unique_keywords,
            },
            "outline": [
                "Understand the user goal and constraints.",
                "Identify code, docs, and test touchpoints.",
                "Define validation steps and rollout notes.",
            ],
            "completed_at": self._timestamp(),
        }

    def _draft_phase(self, topic: str, entry: dict) -> dict:
        research = entry["phases"]["research"]
        outline = research["outline"]
        return {
            "phase": "draft",
            "implementation_notes": [
                f"Use research question as acceptance criteria: {research['questions'][0]}",
                "Translate the outline into implementation tasks and supporting docs.",
                "Keep code changes small enough for targeted review.",
            ],
            "proposed_files": [
                "src/ideation/idea_pipeline.py",
                "tests/test_idea_pipeline.py",
                "examples/pipeline_example.yaml",
            ],
            "draft_checklist": [
                f"Draft task 1: {outline[0]}",
                f"Draft task 2: {outline[1]}",
                f"Draft task 3: {outline[2]}",
            ],
            "notes": f"Prepared a draft plan for '{topic}' grounded in research findings.",
            "completed_at": self._timestamp(),
        }

    def _review_phase(self, topic: str, entry: dict) -> dict:
        draft = entry["phases"]["draft"]
        return {
            "phase": "review",
            "checks": [
                "Confirm the drafted files match the requested scope.",
                "Check for missing tests or docs before opening a PR.",
                "Verify that the implementation remains stdlib-only where required.",
            ],
            "issues": [
                "Potential mismatch between requested workflow and existing repository layout.",
                "Need to confirm CLI output is descriptive enough for automation.",
            ],
            "recommendations": [
                "Run targeted tests for the pipeline module.",
                "Document how the daily JSON log should be inspected or cleaned up.",
                f"Review generated file list: {', '.join(draft['proposed_files'])}.",
            ],
            "completed_at": self._timestamp(),
        }

    def _pr_phase(self, topic: str, entry: dict) -> dict:
        review = entry["phases"]["review"]
        git_summary = self._git_summary()
        title = f"Idea pipeline: {topic}"
        body_lines = [
            f"## Summary\n- Deliver the idea pipeline flow for '{topic}'.",
            "## Testing\n- Add or run targeted automated checks for the pipeline phases.",
            "## Documentation\n- Include usage notes for the CLI and pipeline log.",
            "## Self-review\n- " + "\n- ".join(review["checks"]),
        ]
        if git_summary:
            body_lines.append(f"## Git snapshot\n- {git_summary}")

        return {
            "phase": "pr",
            "title": title,
            "description": "\n\n".join(body_lines),
            "tests": [
                "python -m unittest tests.test_idea_pipeline",
            ],
            "docs": [
                "examples/pipeline_example.yaml",
                "Module docstrings in src/ideation/idea_pipeline.py",
            ],
            "git": git_summary,
            "completed_at": self._timestamp(),
        }

    def _normalize_phase(self, phase: str) -> str:
        normalized = phase.strip().lower()
        if normalized not in PHASES:
            choices = ", ".join(PHASES)
            raise ValueError(f"Unknown phase '{phase}'. Expected one of: {choices}.")
        return normalized

    def _normalize_topic(self, topic: str) -> str:
        clean_topic = topic.strip()
        if not clean_topic:
            raise ValueError("Topic must not be empty.")
        return clean_topic

    def _timestamp(self) -> str:
        return self.now_func().isoformat()

    def _log_path(self) -> Path:
        date_key = self.now_func().strftime("%Y%m%d")
        return self.logs_dir / f"idea_pipeline_{date_key}.json"

    def _load_log_data(self) -> dict:
        log_path = self._log_path()
        if not log_path.exists():
            return {"date": self.now_func().strftime("%Y-%m-%d"), "ideas": []}
        with log_path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def _save_log_data(self, log_data: dict) -> None:
        with self._log_path().open("w", encoding="utf-8") as handle:
            json.dump(log_data, handle, indent=2, sort_keys=True)
            handle.write("\n")

    def _get_or_create_entry(self, log_data: dict, topic: str) -> dict:
        for entry in log_data["ideas"]:
            if entry["topic"] == topic:
                return entry

        entry = {
            "topic": topic,
            "created_at": self._timestamp(),
            "updated_at": self._timestamp(),
            "current_phase": "research",
            "status": self._empty_status(),
            "phases": {},
            "history": [],
        }
        log_data["ideas"].append(entry)
        return entry

    def _empty_status(self) -> dict:
        return {
            "state": "not_started",
            "completed_phases": [],
            "last_phase": None,
            "ready_for_pr": False,
        }

    def _pipeline_status(self, entry: dict) -> dict:
        completed_phases = [phase for phase in PHASES if phase in entry["phases"]]
        last_phase = completed_phases[-1] if completed_phases else None
        state = "ready_for_pr" if last_phase == "pr" else (
            f"{last_phase}_complete" if last_phase else "not_started"
        )
        return {
            "state": state,
            "completed_phases": completed_phases,
            "last_phase": last_phase,
            "ready_for_pr": last_phase == "pr",
        }

    def _git_summary(self) -> str:
        try:
            completed = subprocess.run(
                ["git", "status", "--short", "--branch"],
                check=False,
                capture_output=True,
                text=True,
                cwd=self.project_root,
            )
        except OSError:
            return "git status unavailable"

        output = (completed.stdout or "").strip()
        return output or "working tree clean"


def _build_cli_output(result: dict) -> str:
    return json.dumps(result, indent=2, sort_keys=True)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for the idea pipeline."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 2:
        print(
            "Usage: python -m src.ideation.idea_pipeline "
            "research <topic> | draft <topic> | review <topic> | pr <topic>",
            file=sys.stderr,
        )
        return 1

    phase = args[0]
    topic = " ".join(args[1:])
    pipeline = IdeaPipeline()
    try:
        result = pipeline.run_phase(phase, topic)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2

    print(_build_cli_output(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
