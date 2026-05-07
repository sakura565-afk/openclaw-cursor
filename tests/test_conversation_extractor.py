import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "conversation_extractor.py"
SPEC = importlib.util.spec_from_file_location("conversation_extractor", MODULE_PATH)
conversation_extractor = importlib.util.module_from_spec(SPEC)
sys.modules["conversation_extractor"] = conversation_extractor
assert SPEC.loader is not None
SPEC.loader.exec_module(conversation_extractor)


SAMPLE_SESSION = {
    "session_id": "demo-session",
    "model": "openclaw-1",
    "started_at": "2026-05-01T10:00:00Z",
    "messages": [
        {
            "role": "user",
            "content": "Please run the failing test and explain the error.",
            "timestamp": "2026-05-01T10:00:01Z",
        },
        {
            "role": "assistant",
            "timestamp": "2026-05-01T10:00:02Z",
            "content": [
                {"type": "text", "text": "I'll run the tests now."},
                {
                    "type": "tool_use",
                    "id": "call-1",
                    "name": "Shell",
                    "input": {"command": "pytest -q"},
                },
            ],
        },
        {
            "role": "tool",
            "timestamp": "2026-05-01T10:00:05Z",
            "tool_call_id": "call-1",
            "name": "Shell",
            "exit_code": 1,
            "content": (
                "Traceback (most recent call last):\n"
                "  File 'app.py', line 10, in main\n"
                "    raise ValueError('boom')\n"
                "ValueError: boom\n"
                "1 failed in 0.12s"
            ),
        },
        {
            "role": "assistant",
            "timestamp": "2026-05-01T10:01:00Z",
            "content": (
                "Root cause: the input was None. The fix was to add a guard. "
                "Note to self: validate inputs before dispatching. Next time, add a regression test."
            ),
        },
        {
            "role": "tool",
            "timestamp": "2026-05-01T10:02:00Z",
            "name": "Shell",
            "exit_code": 0,
            "content": "5 passed, 0 failed in 0.34s\nBuild succeeded",
        },
    ],
}


JSONL_SESSION = "\n".join(
    json.dumps(record)
    for record in [
        {"role": "user", "content": "hi", "timestamp": "2026-05-02T08:00:00Z"},
        {"role": "assistant", "content": "hello", "timestamp": "2026-05-02T08:00:01Z"},
    ]
)


class DiscoveryTests(unittest.TestCase):
    def test_default_sessions_dir_uses_env_override(self) -> None:
        with mock.patch.dict(os.environ, {"OPENCLAW_SESSIONS_DIR": "/tmp/custom"}, clear=False):
            self.assertEqual(
                conversation_extractor.default_sessions_dir(),
                Path("/tmp/custom"),
            )

    def test_default_sessions_dir_falls_back_to_home(self) -> None:
        env = {key: value for key, value in os.environ.items() if key != "OPENCLAW_SESSIONS_DIR"}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch("os.name", "posix"):
                expected = Path.home() / ".openclaw" / "sessions"
                self.assertEqual(conversation_extractor.default_sessions_dir(), expected)

    @unittest.skipUnless(os.name == "nt", "WindowsPath cannot be instantiated on POSIX")
    def test_default_sessions_dir_uses_appdata_on_windows(self) -> None:
        env = {"APPDATA": "C:\\Users\\demo\\AppData\\Roaming"}
        with mock.patch.dict(os.environ, env, clear=True):
            result = conversation_extractor.default_sessions_dir()
        self.assertEqual(result.parts[-2:], ("openclaw", "sessions"))

    def test_discover_session_files_finds_supported_extensions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "a.json").write_text("{}", encoding="utf-8")
            (tmp_path / "b.jsonl").write_text("{}\n", encoding="utf-8")
            (tmp_path / "c.ndjson").write_text("{}\n", encoding="utf-8")
            (tmp_path / "ignored.txt").write_text("nope", encoding="utf-8")
            (tmp_path / ".hidden.json").write_text("{}", encoding="utf-8")
            nested = tmp_path / "sub"
            nested.mkdir()
            (nested / "deep.json").write_text("{}", encoding="utf-8")

            discovered = conversation_extractor.discover_session_files(tmp_path)

        names = {path.name for path in discovered}
        self.assertEqual(names, {"a.json", "b.jsonl", "c.ndjson", "deep.json"})
        self.assertNotIn(".hidden.json", names)


class LoaderTests(unittest.TestCase):
    def test_load_session_normalizes_anthropic_style_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session_path = Path(tmp) / "demo.json"
            session_path.write_text(json.dumps(SAMPLE_SESSION), encoding="utf-8")
            transcript = conversation_extractor.load_session(session_path)

        self.assertEqual(transcript.session_id, "demo-session")
        self.assertEqual(transcript.metadata.get("model"), "openclaw-1")
        roles = [turn.role for turn in transcript.turns]
        self.assertEqual(roles, ["user", "assistant", "tool", "assistant", "tool"])
        assistant_turn = transcript.turns[1]
        self.assertEqual(len(assistant_turn.tool_calls), 1)
        self.assertEqual(assistant_turn.tool_calls[0].name, "Shell")
        self.assertEqual(assistant_turn.tool_calls[0].arguments, {"command": "pytest -q"})
        failing_tool = transcript.turns[2]
        self.assertEqual(failing_tool.tool_results[0].status, "error")
        self.assertEqual(failing_tool.tool_results[0].exit_code, 1)
        self.assertIsNotNone(transcript.started_at)
        self.assertIsNotNone(transcript.ended_at)
        self.assertGreater(transcript.duration_seconds or 0, 0)

    def test_load_session_handles_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session_path = Path(tmp) / "events.jsonl"
            session_path.write_text(JSONL_SESSION, encoding="utf-8")
            transcript = conversation_extractor.load_session(session_path)

        self.assertEqual(len(transcript.turns), 2)
        self.assertEqual(transcript.turns[0].role, "user")
        self.assertEqual(transcript.turns[1].content, "hello")

    def test_load_session_falls_back_to_jsonl_for_misnamed_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session_path = Path(tmp) / "stream.json"
            session_path.write_text(JSONL_SESSION, encoding="utf-8")
            transcript = conversation_extractor.load_session(session_path)

        self.assertEqual(len(transcript.turns), 2)

    def test_load_session_strips_bom_and_handles_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            empty_path = Path(tmp) / "empty.json"
            empty_path.write_text("", encoding="utf-8")
            transcript = conversation_extractor.load_session(empty_path)
            self.assertEqual(transcript.turns, [])

            bom_path = Path(tmp) / "bom.json"
            bom_path.write_bytes(b"\xef\xbb\xbf" + json.dumps(SAMPLE_SESSION).encode("utf-8"))
            transcript = conversation_extractor.load_session(bom_path)
            self.assertEqual(transcript.session_id, "demo-session")

    def test_invalid_jsonl_raises_value_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session_path = Path(tmp) / "broken.jsonl"
            session_path.write_text("{not json}", encoding="utf-8")
            with self.assertRaises(ValueError):
                conversation_extractor.load_session(session_path)


class ExtractionTests(unittest.TestCase):
    def _extract_sample(self) -> conversation_extractor.ExtractionResult:
        with tempfile.TemporaryDirectory() as tmp:
            session_path = Path(tmp) / "demo.json"
            session_path.write_text(json.dumps(SAMPLE_SESSION), encoding="utf-8")
            transcript = conversation_extractor.load_session(session_path)
        return conversation_extractor.extract_session(transcript)

    def test_extract_session_collects_messages_and_tools(self) -> None:
        result = self._extract_sample()
        self.assertEqual(len(result.user_messages), 1)
        self.assertEqual(len(result.assistant_messages), 2)
        self.assertEqual(len(result.tool_calls), 1)
        self.assertEqual(len(result.tool_results), 2)

    def test_error_patterns_capture_traceback_and_failures(self) -> None:
        result = self._extract_sample()
        labels = {entry["label"] for entry in result.error_patterns}
        self.assertIn("python_traceback", labels)
        self.assertIn("test_failure", labels)
        self.assertIn("exception", labels)

    def test_tool_failure_label_added_for_silent_errors(self) -> None:
        session = {
            "session_id": "silent-failure",
            "messages": [
                {
                    "role": "tool",
                    "name": "deploy",
                    "exit_code": 2,
                    "content": "",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "s.json"
            path.write_text(json.dumps(session), encoding="utf-8")
            transcript = conversation_extractor.load_session(path)
            result = conversation_extractor.extract_session(transcript)
        labels = [entry["label"] for entry in result.error_patterns]
        self.assertIn("tool_failure", labels)

    def test_success_patterns_capture_passing_tests_and_build(self) -> None:
        result = self._extract_sample()
        labels = {entry["label"] for entry in result.success_patterns}
        self.assertIn("tests_passed", labels)
        self.assertIn("build_success", labels)

    def test_learning_patterns_collect_assistant_reflections(self) -> None:
        result = self._extract_sample()
        labels = {entry["label"] for entry in result.learnings}
        self.assertIn("root_cause", labels)
        self.assertIn("fix_summary", labels)
        self.assertIn("note_to_self", labels)
        self.assertIn("next_time", labels)

    def test_extract_directory_skips_invalid_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "good.json").write_text(json.dumps(SAMPLE_SESSION), encoding="utf-8")
            (tmp_path / "bad.jsonl").write_text("{not json}\n", encoding="utf-8")
            results = conversation_extractor.extract_directory(tmp_path)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].transcript.session_id, "demo-session")


class ReportingTests(unittest.TestCase):
    def _build_report(self, tmp_path: Path) -> dict:
        (tmp_path / "demo.json").write_text(json.dumps(SAMPLE_SESSION), encoding="utf-8")
        results = conversation_extractor.extract_directory(tmp_path)
        return conversation_extractor.build_aggregate_report(results, tmp_path)

    def test_aggregate_report_has_expected_structure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = self._build_report(Path(tmp))
        self.assertEqual(report["session_count"], 1)
        self.assertGreater(report["totals"]["turns"], 0)
        self.assertIn("Shell", report["tool_usage"])
        self.assertIn("error", report["tool_usage"]["Shell"])

    def test_markdown_report_contains_key_sections(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = self._build_report(Path(tmp))
            markdown = conversation_extractor.render_markdown_report(report)
        self.assertIn("# OpenClaw Conversation Extraction Report", markdown)
        self.assertIn("## Totals", markdown)
        self.assertIn("## Error Pattern Labels", markdown)
        self.assertIn("## Tool Usage", markdown)


class CLITests(unittest.TestCase):
    def test_main_writes_json_and_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            sessions_dir = tmp_path / "sessions"
            sessions_dir.mkdir()
            (sessions_dir / "demo.json").write_text(json.dumps(SAMPLE_SESSION), encoding="utf-8")

            output_path = tmp_path / "report.json"
            markdown_path = tmp_path / "report.md"

            exit_code = conversation_extractor.main(
                [
                    "--sessions-dir",
                    str(sessions_dir),
                    "--output",
                    str(output_path),
                    "--markdown",
                    str(markdown_path),
                    "--logs-dir",
                    str(tmp_path / "logs"),
                    "--quiet",
                ]
            )

            self.assertEqual(exit_code, 0)
            self.assertTrue(output_path.exists())
            self.assertTrue(markdown_path.exists())
            report = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(report["session_count"], 1)

    def test_main_returns_error_when_dir_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "does-not-exist"
            exit_code = conversation_extractor.main(
                [
                    "--sessions-dir",
                    str(missing),
                    "--logs-dir",
                    str(Path(tmp) / "logs"),
                    "--quiet",
                ]
            )
        self.assertEqual(exit_code, 1)


if __name__ == "__main__":
    unittest.main()
