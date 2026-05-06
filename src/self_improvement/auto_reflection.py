from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

STOPWORDS = {
    "the",
    "and",
    "for",
    "that",
    "with",
    "this",
    "from",
    "have",
    "your",
    "about",
    "there",
    "their",
    "they",
    "would",
    "could",
    "should",
    "what",
    "when",
    "where",
    "which",
    "into",
    "after",
    "before",
    "been",
    "were",
    "them",
    "then",
    "than",
    "just",
    "also",
    "need",
    "like",
    "some",
    "more",
    "very",
    "each",
    "most",
    "other",
    "only",
    "over",
    "such",
    "will",
    "into",
    "make",
    "made",
    "using",
}

TRANSCRIPT_PATTERNS = [
    "transcript*.json",
    "transcript*.md",
    "transcript*.txt",
    "openclaw*transcript*.json",
    "openclaw*transcript*.md",
    "openclaw*transcript*.txt",
    "*.transcript.json",
]


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _safe_datetime_from_timestamp(raw: Any) -> Optional[datetime]:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        try:
            return datetime.fromtimestamp(float(raw), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(raw, str):
        value = raw.strip()
        if not value:
            return None
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(value)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            return None
    return None


@dataclass
class ParsedTranscript:
    path: Path
    modified_at: datetime
    user_messages: int
    assistant_messages: int
    tool_messages: int
    error_messages: int
    token_count_estimate: int
    repeated_phrases: Dict[str, int]
    quality_signals: Dict[str, int]


class AutoReflectionEngine:
    def __init__(
        self,
        root_dir: Path | str | None = None,
        transcript_dirs: Optional[Sequence[Path | str]] = None,
    ) -> None:
        self.root_dir = Path(root_dir or Path.cwd())
        self.memory_dir = self.root_dir / "memory"
        self.learnings_dir = self.root_dir / ".learnings"
        self.metrics_path = self.learnings_dir / "quality_metrics.json"
        self.transcript_dirs = (
            [Path(item) for item in transcript_dirs]
            if transcript_dirs
            else self._default_transcript_dirs()
        )
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.learnings_dir.mkdir(parents=True, exist_ok=True)

    def _default_transcript_dirs(self) -> List[Path]:
        home = Path.home()
        candidates = [
            self.root_dir / "logs",
            self.root_dir / "memory",
            home / ".openclaw" / "logs",
            home / ".openclaw" / "workspace" / "logs",
            home / ".openclaw" / "workspace" / "transcripts",
        ]
        unique: List[Path] = []
        seen = set()
        for item in candidates:
            resolved = item.resolve() if item.exists() else item
            key = str(resolved)
            if key in seen:
                continue
            seen.add(key)
            unique.append(item)
        return unique

    def _load_metrics(self) -> Dict[str, Any]:
        if not self.metrics_path.exists():
            return {
                "generated_at": None,
                "runs": 0,
                "totals": {},
                "rolling": {},
                "last_reflection_file": None,
                "history": [],
            }
        try:
            payload = json.loads(self.metrics_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                return payload
        except json.JSONDecodeError:
            pass
        return {
            "generated_at": None,
            "runs": 0,
            "totals": {},
            "rolling": {},
            "last_reflection_file": None,
            "history": [],
        }

    def _save_metrics(self, payload: Dict[str, Any]) -> None:
        self.metrics_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    def _candidate_transcript_files(self) -> Iterable[Path]:
        seen = set()
        for base in self.transcript_dirs:
            if not base.exists() or not base.is_dir():
                continue
            for pattern in TRANSCRIPT_PATTERNS:
                for path in base.glob(pattern):
                    if not path.is_file():
                        continue
                    key = str(path.resolve())
                    if key in seen:
                        continue
                    seen.add(key)
                    yield path

    def _collect_recent_transcript_files(self, days: int) -> List[Path]:
        cutoff = _now_utc() - timedelta(days=max(days, 0))
        recent: List[Path] = []
        for path in self._candidate_transcript_files():
            modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            if modified >= cutoff:
                recent.append(path)
        recent.sort(key=lambda item: item.stat().st_mtime, reverse=True)
        return recent

    def _extract_text_tokens(self, text: str) -> List[str]:
        return re.findall(r"[a-zA-Z]{4,}", text.lower())

    def _extract_repeated_phrases(self, text: str) -> Dict[str, int]:
        lines = [line.strip().lower() for line in text.splitlines() if len(line.strip()) >= 12]
        compact = [re.sub(r"\s+", " ", line) for line in lines]
        counts = Counter(compact)
        return {phrase: count for phrase, count in counts.items() if count >= 2}

    def _parse_json_transcript(self, path: Path, modified_at: datetime) -> Optional[ParsedTranscript]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

        messages: List[Dict[str, Any]] = []
        if isinstance(payload, list):
            messages = [item for item in payload if isinstance(item, dict)]
        elif isinstance(payload, dict):
            for key in ("messages", "transcript", "events"):
                value = payload.get(key)
                if isinstance(value, list):
                    messages = [item for item in value if isinstance(item, dict)]
                    break

        if not messages:
            raw_text = json.dumps(payload)
            repeated = self._extract_repeated_phrases(raw_text)
            tokens = self._extract_text_tokens(raw_text)
            return ParsedTranscript(
                path=path,
                modified_at=modified_at,
                user_messages=0,
                assistant_messages=0,
                tool_messages=0,
                error_messages=0,
                token_count_estimate=len(tokens),
                repeated_phrases=repeated,
                quality_signals={"asks_for_clarification": 0, "contains_next_steps": 0, "contains_tests": 0},
            )

        user_messages = 0
        assistant_messages = 0
        tool_messages = 0
        error_messages = 0
        text_parts: List[str] = []
        clarifications = 0
        next_steps = 0
        tests = 0

        for message in messages:
            role = str(message.get("role", "")).lower()
            content = message.get("content", "")
            if isinstance(content, list):
                content_text = " ".join(str(item) for item in content)
            else:
                content_text = str(content)

            if role == "user":
                user_messages += 1
            elif role in {"assistant", "model"}:
                assistant_messages += 1
            elif role in {"tool", "system"}:
                tool_messages += 1

            lowered = content_text.lower()
            if "error" in lowered or "traceback" in lowered or "failed" in lowered:
                error_messages += 1
            if "clarify" in lowered or "could you share" in lowered:
                clarifications += 1
            if "next step" in lowered or "follow-up" in lowered:
                next_steps += 1
            if "test" in lowered or "pytest" in lowered or "unittest" in lowered:
                tests += 1
            text_parts.append(content_text)

        all_text = "\n".join(text_parts)
        tokens = self._extract_text_tokens(all_text)
        repeated = self._extract_repeated_phrases(all_text)
        return ParsedTranscript(
            path=path,
            modified_at=modified_at,
            user_messages=user_messages,
            assistant_messages=assistant_messages,
            tool_messages=tool_messages,
            error_messages=error_messages,
            token_count_estimate=len(tokens),
            repeated_phrases=repeated,
            quality_signals={
                "asks_for_clarification": clarifications,
                "contains_next_steps": next_steps,
                "contains_tests": tests,
            },
        )

    def _parse_text_transcript(self, path: Path, modified_at: datetime) -> Optional[ParsedTranscript]:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return None
        lowered = text.lower()
        user_messages = len(re.findall(r"(^|\n)\s*(user|human)\s*:", lowered))
        assistant_messages = len(re.findall(r"(^|\n)\s*(assistant|model)\s*:", lowered))
        tool_messages = len(re.findall(r"(^|\n)\s*tool\s*:", lowered))
        error_messages = len(re.findall(r"\berror\b|\btraceback\b|\bfailed\b", lowered))
        tokens = self._extract_text_tokens(text)
        repeated = self._extract_repeated_phrases(text)
        return ParsedTranscript(
            path=path,
            modified_at=modified_at,
            user_messages=user_messages,
            assistant_messages=assistant_messages,
            tool_messages=tool_messages,
            error_messages=error_messages,
            token_count_estimate=len(tokens),
            repeated_phrases=repeated,
            quality_signals={
                "asks_for_clarification": len(re.findall(r"clarify|could you share", lowered)),
                "contains_next_steps": len(re.findall(r"next step|follow-up", lowered)),
                "contains_tests": len(re.findall(r"\btest\b|pytest|unittest", lowered)),
            },
        )

    def _parse_transcript(self, path: Path) -> Optional[ParsedTranscript]:
        modified_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        if path.suffix.lower() == ".json":
            parsed = self._parse_json_transcript(path, modified_at)
            if parsed is not None:
                return parsed
        return self._parse_text_transcript(path, modified_at)

    def analyze(self, days: int) -> Dict[str, Any]:
        files = self._collect_recent_transcript_files(days)
        parsed_items: List[ParsedTranscript] = []
        for path in files:
            parsed = self._parse_transcript(path)
            if parsed is not None:
                parsed_items.append(parsed)

        phrase_counter: Counter[str] = Counter()
        token_counter: Counter[str] = Counter()
        totals = {
            "transcripts_scanned": len(parsed_items),
            "user_messages": 0,
            "assistant_messages": 0,
            "tool_messages": 0,
            "error_messages": 0,
            "token_count_estimate": 0,
            "asks_for_clarification": 0,
            "contains_next_steps": 0,
            "contains_tests": 0,
        }

        for item in parsed_items:
            totals["user_messages"] += item.user_messages
            totals["assistant_messages"] += item.assistant_messages
            totals["tool_messages"] += item.tool_messages
            totals["error_messages"] += item.error_messages
            totals["token_count_estimate"] += item.token_count_estimate
            for key, value in item.quality_signals.items():
                totals[key] += value
            phrase_counter.update(item.repeated_phrases)
            try:
                text = item.path.read_text(encoding="utf-8")
            except OSError:
                text = ""
            token_counter.update(
                token for token in self._extract_text_tokens(text) if token not in STOPWORDS and len(token) >= 5
            )

        top_repeated_patterns = [
            {"pattern": pattern, "count": count}
            for pattern, count in phrase_counter.most_common(8)
        ]
        top_keywords = [{"keyword": key, "count": count} for key, count in token_counter.most_common(10)]
        avg_errors = round(totals["error_messages"] / totals["transcripts_scanned"], 2) if totals["transcripts_scanned"] else 0.0
        quality_score = max(
            0.0,
            round(
                100.0
                - (avg_errors * 10)
                + min(totals["contains_tests"], 10) * 1.5
                + min(totals["contains_next_steps"], 10),
                2,
            ),
        )

        return {
            "generated_at": _now_utc().isoformat(),
            "lookback_days": days,
            "totals": totals,
            "quality_metrics": {
                "average_errors_per_transcript": avg_errors,
                "quality_score": quality_score,
            },
            "top_repeated_patterns": top_repeated_patterns,
            "top_keywords": top_keywords,
            "files": [str(item.path) for item in parsed_items],
        }

    def _render_reflection_markdown(self, analysis: Dict[str, Any]) -> str:
        totals = analysis["totals"]
        quality = analysis["quality_metrics"]
        lines = [
            f"# Auto Reflection ({_now_utc().strftime('%Y-%m-%d')})",
            "",
            f"- Generated: {analysis['generated_at']}",
            f"- Lookback window: {analysis['lookback_days']} day(s)",
            f"- Transcripts scanned: {totals['transcripts_scanned']}",
            f"- Quality score: {quality['quality_score']}",
            f"- Avg errors/transcript: {quality['average_errors_per_transcript']}",
            "",
            "## Repeated patterns",
        ]
        if analysis["top_repeated_patterns"]:
            for item in analysis["top_repeated_patterns"]:
                lines.append(f"- ({item['count']}) {item['pattern']}")
        else:
            lines.append("- No repeated transcript phrases found.")

        lines.extend(["", "## Key quality signals"])
        lines.extend(
            [
                f"- Clarification prompts: {totals['asks_for_clarification']}",
                f"- Messages mentioning tests: {totals['contains_tests']}",
                f"- Next-step cues: {totals['contains_next_steps']}",
                "",
                "## Frequent keywords",
            ]
        )
        if analysis["top_keywords"]:
            for item in analysis["top_keywords"]:
                lines.append(f"- {item['keyword']}: {item['count']}")
        else:
            lines.append("- No keywords extracted.")
        lines.append("")
        return "\n".join(lines)

    def _write_reflection(self, analysis: Dict[str, Any]) -> Path:
        stamp = _now_utc().strftime("%Y%m%d")
        path = self.memory_dir / f"auto_reflection_{stamp}.md"
        path.write_text(self._render_reflection_markdown(analysis), encoding="utf-8")
        return path

    def _update_rolling_metrics(self, analysis: Dict[str, Any], reflection_path: Path) -> Dict[str, Any]:
        payload = self._load_metrics()
        history = payload.get("history", [])
        if not isinstance(history, list):
            history = []

        entry = {
            "generated_at": analysis["generated_at"],
            "lookback_days": analysis["lookback_days"],
            "quality_score": analysis["quality_metrics"]["quality_score"],
            "average_errors_per_transcript": analysis["quality_metrics"]["average_errors_per_transcript"],
            "transcripts_scanned": analysis["totals"]["transcripts_scanned"],
        }
        history.append(entry)
        history = history[-30:]

        quality_values = [float(item.get("quality_score", 0.0)) for item in history]
        error_values = [float(item.get("average_errors_per_transcript", 0.0)) for item in history]
        rolling = {
            "window_size": len(history),
            "quality_score_avg": round(sum(quality_values) / len(quality_values), 2) if quality_values else 0.0,
            "quality_score_min": round(min(quality_values), 2) if quality_values else 0.0,
            "quality_score_max": round(max(quality_values), 2) if quality_values else 0.0,
            "avg_errors_per_transcript": round(sum(error_values) / len(error_values), 2) if error_values else 0.0,
        }

        payload["generated_at"] = analysis["generated_at"]
        payload["runs"] = int(payload.get("runs", 0)) + 1
        payload["totals"] = analysis["totals"]
        payload["rolling"] = rolling
        payload["last_reflection_file"] = str(reflection_path)
        payload["history"] = history
        self._save_metrics(payload)
        return payload

    def run(self, days: int) -> Dict[str, Any]:
        analysis = self.analyze(days)
        reflection = self._write_reflection(analysis)
        metrics = self._update_rolling_metrics(analysis, reflection)
        return {"analysis": analysis, "reflection_file": str(reflection), "metrics": metrics}

    def summary(self, days: int) -> Dict[str, Any]:
        analysis = self.analyze(days)
        latest_metrics = self._load_metrics()
        return {
            "generated_at": analysis["generated_at"],
            "lookback_days": days,
            "totals": analysis["totals"],
            "quality_metrics": analysis["quality_metrics"],
            "rolling_quality": latest_metrics.get("rolling", {}),
            "last_reflection_file": latest_metrics.get("last_reflection_file"),
        }

    def digest(self, days: int) -> str:
        analysis = self.analyze(days)
        totals = analysis["totals"]
        quality = analysis["quality_metrics"]
        lines = [
            "# Auto Reflection Digest",
            "",
            f"Generated: {analysis['generated_at']}",
            f"Lookback: {days} day(s)",
            "",
            "## Overview",
            f"- Transcripts scanned: {totals['transcripts_scanned']}",
            f"- User messages: {totals['user_messages']}",
            f"- Assistant messages: {totals['assistant_messages']}",
            f"- Error mentions: {totals['error_messages']}",
            f"- Quality score: {quality['quality_score']}",
            "",
            "## Top repeated patterns",
        ]
        if analysis["top_repeated_patterns"]:
            for item in analysis["top_repeated_patterns"]:
                lines.append(f"- {item['pattern']} ({item['count']})")
        else:
            lines.append("- None")
        lines.append("")
        return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="OpenClaw self-reflection cron utility")
    parser.add_argument("command", choices=["run", "summary", "digest"], help="Command to execute")
    parser.add_argument("--days", type=int, default=7, help="Look back this many days for transcript scanning")
    parser.add_argument("--root-dir", default=".", help="Project root directory")
    parser.add_argument(
        "--transcript-dir",
        action="append",
        default=[],
        help="Additional transcript directory to scan (can be provided multiple times)",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.days < 0:
        parser.error("--days must be >= 0")

    transcript_dirs = [Path(item) for item in args.transcript_dir] if args.transcript_dir else None
    engine = AutoReflectionEngine(root_dir=Path(args.root_dir), transcript_dirs=transcript_dirs)

    if args.command == "run":
        result = engine.run(args.days)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    if args.command == "summary":
        summary_payload = engine.summary(args.days)
        print(json.dumps(summary_payload, indent=2, sort_keys=True))
        return 0
    print(engine.digest(args.days))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
