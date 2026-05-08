"""Tests for conversation log parsing and aggregate analysis."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from skills import conversation_analyzer as ca  # noqa: E402


SAMPLE_JSONL = """\
{"role":"user","content":"Run tests and fix the ValueError in parser.py"}
{"role":"assistant","content":"I'll read the file and run tests.","tool_calls":[{"function":{"name":"read_file"}}]}
{"role":"tool","type":"tool_result","content":"Error: ValueError at line 42"}
{"role":"assistant","content":"Retry with a fallback patch approach."}
"""

SAMPLE_MD = """\
## User:
Ship the feature.

## Assistant:
I'll verify with tool call: function: grep
Done — success.
"""


class ConversationAnalyzerTest(unittest.TestCase):
    def test_parse_jsonl_extracts_tools_and_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "run.jsonl"
            p.write_text(SAMPLE_JSONL, encoding="utf-8")
            sessions = ca.parse_log_file(p)
            self.assertEqual(len(sessions), 1)
            s = sessions[0]
            self.assertGreaterEqual(len(s.tool_sequence), 1)
            recovery = ca.recovery_events(s)
            self.assertTrue(any(r.get("type") == "error_recovery" for r in recovery))

    def test_analyze_sessions_counts_bigrams(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "run.jsonl"
            p.write_text(SAMPLE_JSONL, encoding="utf-8")
            sessions = ca.parse_log_file(p)
            report = ca.analyze_sessions(sessions)
            self.assertGreaterEqual(report.session_count, 1)
            self.assertIn("tool_bigrams", report.to_dict())

    def test_markdown_transcript_roles(self) -> None:
        turns = ca.parse_markdown_transcript(SAMPLE_MD)
        roles = [t.role for t in turns]
        self.assertIn("user", roles)
        self.assertIn("assistant", roles)

    def test_effective_behaviors_requires_support(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "a.jsonl"
            p.write_text(SAMPLE_JSONL, encoding="utf-8")
            sessions = ca.parse_log_file(p)
            behaviors = ca.effective_behaviors(sessions, min_support=1)
            self.assertIsInstance(behaviors, list)


class ConversationExtractorIntegrationTest(unittest.TestCase):
    def test_build_knowledge_base_writes_serializable_json(self) -> None:
        import importlib.util

        script = ROOT / "scripts" / "conversation_extractor.py"
        spec = importlib.util.spec_from_file_location("conversation_extractor", script)
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        with tempfile.TemporaryDirectory() as tmp:
            logs = Path(tmp) / "logs"
            logs.mkdir()
            (logs / "sample.jsonl").write_text(SAMPLE_JSONL, encoding="utf-8")
            kb = mod.build_knowledge_base(logs)
            json.dumps(kb)
            self.assertGreaterEqual(kb["files_processed"], 1)
            self.assertIn("aggregate_analysis", kb)


if __name__ == "__main__":
    unittest.main()
