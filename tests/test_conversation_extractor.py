"""Tests for scripts/conversation_extractor.py."""

from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "conversation_extractor.py"
SPEC = importlib.util.spec_from_file_location("conversation_extractor", MODULE_PATH)
conversation_extractor = importlib.util.module_from_spec(SPEC)
sys.modules["conversation_extractor"] = conversation_extractor
assert SPEC.loader is not None
SPEC.loader.exec_module(conversation_extractor)


SAMPLE_JSON_SESSION = {
    "session_id": "session-001",
    "started_at": "2026-05-01T10:00:00Z",
    "ended_at": "2026-05-01T10:05:00Z",
    "model": "claude",
    "messages": [
        {
            "role": "user",
            "content": "Please run the tests for the project.",
            "timestamp": "2026-05-01T10:00:01Z",
        },
        {
            "role": "assistant",
            "content": "I'll run the tests using pytest.",
            "tool_calls": [
                {
                    "id": "call_1",
                    "tool_name": "shell",
                    "arguments": {"command": "pytest"},
                    "result": "All tests passed",
                    "status": "success",
                    "duration_ms": 1234,
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_1",
            "tool_name": "shell",
            "content": "All tests passed",
            "status": "success",
        },
        {
            "role": "assistant",
            "content": (
                "Tests passed successfully. Lesson learned: always pin "
                "dependency versions to avoid breakage."
            ),
        },
    ],
}


SAMPLE_JSONL_SESSION = [
    {
        "role": "user",
        "content": "Deploy the service",
        "timestamp": "2026-05-02T12:00:00Z",
    },
    {
        "role": "assistant",
        "content": "Trying to deploy now.",
        "tool_calls": [
            {
                "tool_name": "deploy",
                "arguments": {"env": "prod"},
                "status": "error",
                "error": "Permission denied: missing IAM role",
            }
        ],
    },
    {
        "role": "tool",
        "tool_name": "deploy",
        "content": "Permission denied: missing IAM role",
        "status": "error",
    },
    {
        "role": "assistant",
        "content": "Deployment failed. Next time, verify IAM policies before deploying.",
    },
]


def write_json_session(directory: Path, name: str, payload) -> Path:
    target = directory / name
    target.write_text(json.dumps(payload), encoding="utf-8")
    return target


def write_jsonl_session(directory: Path, name: str, records) -> Path:
    target = directory / name
    target.write_text(
        "\n".join(json.dumps(record) for record in records) + "\n",
        encoding="utf-8",
    )
    return target


class ParseSessionFileTest(unittest.TestCase):
    def test_parses_json_session_with_tool_calls(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            session_path = write_json_session(Path(tmp_dir), "session-001.json", SAMPLE_JSON_SESSION)

            session = conversation_extractor.parse_session_file(session_path)

            self.assertEqual(session.session_id, "session-001")
            self.assertEqual(len(session.turns), 4)
            roles = [turn.role for turn in session.turns]
            self.assertEqual(roles, ["user", "assistant", "tool", "assistant"])
            assistant_call = session.turns[1].tool_calls[0]
            self.assertEqual(assistant_call.tool_name, "shell")
            self.assertEqual(assistant_call.status, "success")
            self.assertEqual(assistant_call.duration_ms, 1234.0)
            tool_turn = session.turns[2]
            self.assertEqual(tool_turn.outcome, "success")
            self.assertEqual(session.metadata.get("model"), "claude")

    def test_parses_jsonl_session_with_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            session_path = write_jsonl_session(Path(tmp_dir), "session-002.jsonl", SAMPLE_JSONL_SESSION)

            session = conversation_extractor.parse_session_file(session_path)

            self.assertEqual(session.session_id, "session-002")
            self.assertEqual(len(session.turns), 4)
            error_call = session.turns[1].tool_calls[0]
            self.assertEqual(error_call.status, "error")
            self.assertIn("Permission denied", error_call.error_message or "")
            self.assertEqual(session.turns[2].outcome, "error")

    def test_handles_top_level_message_array(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            session_path = write_json_session(
                Path(tmp_dir),
                "array.json",
                [
                    {"role": "user", "content": "hi"},
                    {"role": "assistant", "content": "hello"},
                ],
            )

            session = conversation_extractor.parse_session_file(session_path)

            self.assertEqual(len(session.turns), 2)
            self.assertEqual(session.turns[0].role, "user")

    def test_records_warning_for_empty_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            session_path = Path(tmp_dir) / "empty.json"
            session_path.write_text("", encoding="utf-8")

            session = conversation_extractor.parse_session_file(session_path)

            self.assertEqual(session.turns, [])
            self.assertTrue(session.parse_warnings)


class DiscoveryTest(unittest.TestCase):
    def test_discover_session_files_returns_files_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            write_json_session(base, "a.json", SAMPLE_JSON_SESSION)
            (base / "ignored.txt").write_text("noop", encoding="utf-8")
            nested = base / "nested"
            nested.mkdir()
            write_jsonl_session(nested, "b.jsonl", SAMPLE_JSONL_SESSION)

            files = conversation_extractor.discover_session_files(base)

            names = sorted(path.name for path in files)
            self.assertEqual(names, ["a.json", "b.jsonl"])

    def test_discover_handles_missing_directory(self) -> None:
        files = conversation_extractor.discover_session_files(Path("/nonexistent/path/abc123"))
        self.assertEqual(files, [])


class ReportingTest(unittest.TestCase):
    def _build_sessions(self, tmp_dir: Path):
        path_a = write_json_session(tmp_dir, "session-001.json", SAMPLE_JSON_SESSION)
        path_b = write_jsonl_session(tmp_dir, "session-002.jsonl", SAMPLE_JSONL_SESSION)
        return conversation_extractor.parse_sessions([path_a, path_b])

    def test_build_report_aggregates_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            sessions = self._build_sessions(Path(tmp_dir))

            report = conversation_extractor.build_report(sessions)

            self.assertEqual(report["session_count"], 2)
            self.assertEqual(report["totals"]["user_turns"], 2)
            self.assertEqual(report["totals"]["assistant_turns"], 4)
            self.assertEqual(report["totals"]["tool_calls"], 4)
            self.assertEqual(report["totals"]["tool_call_success"], 2)
            self.assertEqual(report["totals"]["tool_call_errors"], 2)

    def test_detect_error_patterns_clusters_by_signature(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            sessions = self._build_sessions(Path(tmp_dir))

            patterns = conversation_extractor.detect_error_patterns(sessions)

            self.assertTrue(patterns)
            top = patterns[0]
            self.assertEqual(top["tool_name"], "deploy")
            self.assertIn("Permission denied", top["error_signature"])

    def test_detect_success_patterns_includes_tool_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            sessions = self._build_sessions(Path(tmp_dir))

            patterns = conversation_extractor.detect_success_patterns(sessions)

            tool_counts = [p for p in patterns if p["kind"] == "tool_success_count"]
            self.assertTrue(tool_counts)
            self.assertEqual(tool_counts[0]["tool_name"], "shell")

    def test_extract_learnings_finds_lessons(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            sessions = self._build_sessions(Path(tmp_dir))

            learnings = conversation_extractor.extract_learnings(sessions)

            joined = " | ".join(item["learning"].lower() for item in learnings)
            self.assertIn("pin", joined)
            self.assertIn("verify iam policies", joined)


class CLITest(unittest.TestCase):
    def test_cli_writes_report_and_turns_dump(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            sessions_dir = base / "sessions"
            sessions_dir.mkdir()
            write_json_session(sessions_dir, "session-001.json", SAMPLE_JSON_SESSION)
            write_jsonl_session(sessions_dir, "session-002.jsonl", SAMPLE_JSONL_SESSION)

            output_path = base / "out" / "report.json"
            turns_path = base / "out" / "turns.jsonl"

            buffer = io.StringIO()
            with redirect_stdout(buffer):
                exit_code = conversation_extractor.main(
                    [
                        "--sessions-dir",
                        str(sessions_dir),
                        "--output",
                        str(output_path),
                        "--turns-output",
                        str(turns_path),
                        "--quiet",
                        "--print-summary",
                    ]
                )

            self.assertEqual(exit_code, 0)
            self.assertTrue(output_path.exists())
            self.assertTrue(turns_path.exists())
            data = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(data["session_count"], 2)
            jsonl_lines = turns_path.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(jsonl_lines), 8)
            self.assertIn("Sessions parsed", buffer.getvalue())

    def test_cli_returns_error_when_no_sessions_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            empty_dir = Path(tmp_dir) / "empty"
            empty_dir.mkdir()
            output_path = Path(tmp_dir) / "report.json"

            exit_code = conversation_extractor.main(
                [
                    "--sessions-dir",
                    str(empty_dir),
                    "--output",
                    str(output_path),
                    "--quiet",
                ]
            )

            self.assertEqual(exit_code, 1)
            self.assertFalse(output_path.exists())


if __name__ == "__main__":
    unittest.main()
