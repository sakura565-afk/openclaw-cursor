from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence


TURN_ROLE_PATTERN = re.compile(
    r"^\s*(?:\[(?P<ts>[^\]]+)\]\s*)?(?P<role>user|assistant|system|developer|tool|human|ai)\s*(?:[:>|-]|\])\s*(?P<content>.*)$",
    re.IGNORECASE,
)
ROLE_TAG_PATTERN = re.compile(r"^\s*<(?P<role>user|assistant|system|developer|tool)[^>]*>\s*$", re.IGNORECASE)
ERROR_PATTERN = re.compile(
    r"\b(error|exception|traceback|failed|failure|timeout|timed out|non[-\s]?zero|crash)\b",
    re.IGNORECASE,
)
DECISION_PATTERN = re.compile(
    r"\b(decision|decided|we will|agreed|chosen|choose to|going with|selected)\b",
    re.IGNORECASE,
)
FOLLOW_UP_PATTERN = re.compile(
    r"\b(todo|follow[- ]?up|next step|action item|pending|need to|should)\b",
    re.IGNORECASE,
)
TOOL_PATTERN = re.compile(
    r"\b(Shell|ReadFile|ApplyPatch|rg|Glob|Subagent|WebSearch|WebFetch|ManagePullRequest|EditNotebook|TodoWrite|functions\.[A-Za-z_]+)\b"
)


@dataclass
class Turn:
    index: int
    role: str
    content: str
    timestamp: Optional[str] = None
    tool_calls: Optional[List[str]] = None
    metadata: Optional[Dict[str, Any]] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "index": self.index,
            "role": self.role,
            "timestamp": self.timestamp,
            "content": self.content,
            "tool_calls": self.tool_calls or [],
            "metadata": self.metadata or {},
        }


class ConversationExtractor:
    def __init__(
        self,
        input_dirs: Optional[Sequence[Path | str]] = None,
        output_dir: Path | str = Path("memory") / "extracted_conversations",
    ) -> None:
        self.input_dirs = [Path(p) for p in (input_dirs or [Path("logs"), Path("memory")])]
        self.output_dir = Path(output_dir)

    def discover_session_files(self) -> List[Path]:
        patterns = ("*.log", "*.txt", "*.md", "*.json", "*.jsonl")
        files: List[Path] = []
        for directory in self.input_dirs:
            if not directory.exists():
                continue
            for pattern in patterns:
                files.extend(path for path in directory.rglob(pattern) if path.is_file())
        # Avoid re-processing generated extraction output.
        return sorted(
            [
                path
                for path in files
                if "extracted_conversations" not in path.parts
                and path.name != "conversation_schema.json"
            ]
        )

    def extract_all(self) -> List[Path]:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        written_paths: List[Path] = []
        for path in self.discover_session_files():
            extracted = self.extract_file(path)
            if extracted is None:
                continue
            output_path = self.output_dir / f"{extracted['session_id']}.json"
            output_path.write_text(json.dumps(extracted, indent=2, sort_keys=True), encoding="utf-8")
            written_paths.append(output_path)
        return written_paths

    def extract_file(self, path: Path) -> Optional[Dict[str, Any]]:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
        if not text:
            return None
        turns = self._parse_turns(text)
        if not turns:
            return None
        key_decisions = self._collect_items(turns, DECISION_PATTERN)
        errors = self._collect_items(turns, ERROR_PATTERN)
        follow_ups = self._collect_items(turns, FOLLOW_UP_PATTERN)
        tools = sorted({tool for turn in turns for tool in (turn.tool_calls or [])})
        return {
            "session_id": self._session_id_for(path),
            "source_file": str(path),
            "summary": {
                "total_turns": len(turns),
                "roles_present": sorted({turn.role for turn in turns}),
                "tools_used_count": len(tools),
                "decisions_count": len(key_decisions),
                "errors_count": len(errors),
                "follow_ups_count": len(follow_ups),
            },
            "turns": [turn.as_dict() for turn in turns],
            "key_decisions": key_decisions,
            "tools_used": tools,
            "errors_encountered": errors,
            "follow_up_items": follow_ups,
        }

    def _parse_turns(self, text: str) -> List[Turn]:
        json_turns = self._parse_json_turns(text)
        if json_turns:
            return self._normalize_turns(json_turns)
        jsonl_turns = self._parse_jsonl_turns(text)
        if jsonl_turns:
            return self._normalize_turns(jsonl_turns)
        plain_turns = self._parse_plaintext_turns(text)
        return self._normalize_turns(plain_turns)

    def _parse_json_turns(self, text: str) -> List[Dict[str, Any]]:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return []
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            for key in ("turns", "messages", "conversation", "events", "entries"):
                candidate = payload.get(key)
                if isinstance(candidate, list):
                    return [item for item in candidate if isinstance(item, dict)]
        return []

    def _parse_jsonl_turns(self, text: str) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or not line.startswith("{"):
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                rows.append(item)
        return rows

    def _parse_plaintext_turns(self, text: str) -> List[Dict[str, Any]]:
        turns: List[Dict[str, Any]] = []
        current_role = "unknown"
        current_timestamp: Optional[str] = None
        buffer: List[str] = []

        def flush() -> None:
            if not buffer:
                return
            turns.append(
                {
                    "role": current_role,
                    "timestamp": current_timestamp,
                    "content": "\n".join(buffer).strip(),
                }
            )

        for line in text.splitlines():
            marker = ROLE_TAG_PATTERN.match(line)
            if marker:
                flush()
                buffer = []
                current_role = marker.group("role").lower()
                current_timestamp = None
                continue

            matched = TURN_ROLE_PATTERN.match(line)
            if matched:
                flush()
                buffer = []
                role = matched.group("role").lower()
                if role == "human":
                    role = "user"
                if role == "ai":
                    role = "assistant"
                current_role = role
                current_timestamp = matched.group("ts")
                initial = matched.group("content")
                if initial:
                    buffer.append(initial)
                continue

            if line.strip() == "---" and buffer:
                flush()
                buffer = []
                current_role = "unknown"
                current_timestamp = None
                continue
            buffer.append(line)

        flush()
        return turns

    def _normalize_turns(self, items: Iterable[Dict[str, Any]]) -> List[Turn]:
        turns: List[Turn] = []
        for idx, raw in enumerate(items):
            role = str(raw.get("role") or raw.get("speaker") or raw.get("author") or "unknown").lower()
            if role == "human":
                role = "user"
            if role == "ai":
                role = "assistant"
            content = raw.get("content") or raw.get("text") or raw.get("message") or raw.get("body") or ""
            content_text = str(content).strip()
            if not content_text:
                continue

            metadata = {
                key: value
                for key, value in raw.items()
                if key not in {"role", "speaker", "author", "content", "text", "message", "body"}
            }
            tool_calls = self._extract_tools(content_text, raw)
            turns.append(
                Turn(
                    index=len(turns),
                    role=role,
                    timestamp=str(raw.get("timestamp") or raw.get("time") or "") or None,
                    content=content_text,
                    tool_calls=tool_calls,
                    metadata=metadata,
                )
            )
        return turns

    def _extract_tools(self, content: str, raw: Dict[str, Any]) -> List[str]:
        found = {match.group(1).replace("functions.", "") for match in TOOL_PATTERN.finditer(content)}
        for key in ("tool", "tool_name", "recipient_name"):
            value = raw.get(key)
            if isinstance(value, str) and value:
                found.add(value.replace("functions.", ""))
        tools = raw.get("tools")
        if isinstance(tools, list):
            for item in tools:
                if isinstance(item, str):
                    found.add(item.replace("functions.", ""))
        return sorted(found)

    def _collect_items(self, turns: List[Turn], pattern: re.Pattern[str]) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        for turn in turns:
            sentences = re.split(r"(?<=[.!?])\s+|\n+", turn.content)
            for sentence in sentences:
                snippet = sentence.strip()
                if snippet and pattern.search(snippet):
                    items.append(
                        {
                            "turn_index": turn.index,
                            "role": turn.role,
                            "timestamp": turn.timestamp,
                            "evidence": snippet,
                        }
                    )
        return items

    def _session_id_for(self, path: Path) -> str:
        parent = path.parent.name.lower().replace(" ", "_")
        stem = path.stem.lower().replace(" ", "_")
        slug = re.sub(r"[^a-z0-9_.-]+", "-", f"{parent}-{stem}").strip("-")
        return slug or "conversation"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extract structured conversation sessions from logs to JSON."
    )
    parser.add_argument(
        "--input-dir",
        action="append",
        default=[],
        help="Input directory containing conversation logs. Can be used multiple times.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(Path("memory") / "extracted_conversations"),
        help="Output directory for extracted JSON sessions.",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    input_dirs = [Path(item) for item in args.input_dir] if args.input_dir else None
    extractor = ConversationExtractor(input_dirs=input_dirs, output_dir=Path(args.output_dir))
    extracted_files = extractor.extract_all()
    print(f"Extracted {len(extracted_files)} conversations into {extractor.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
