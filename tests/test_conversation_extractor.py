"""Tests for scripts/conversation_extractor.py."""

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parent.parent / "scripts" / "conversation_extractor.py"
SPEC = importlib.util.spec_from_file_location("conversation_extractor", MODULE_PATH)
conversation_extractor = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules["conversation_extractor"] = conversation_extractor
SPEC.loader.exec_module(conversation_extractor)


class ConversationExtractorTests(unittest.TestCase):
    def test_parse_transcript_json_messages_openai_style(self) -> None:
        payload = json.dumps(
            {
                "messages": [
                    {"role": "assistant", "content": "Decision: use PostgreSQL for persistence."},
                    {"role": "user", "content": "Sounds good."},
                ]
            }
        )
        segs = conversation_extractor.parse_transcript(payload, json_file=False)
        roles = [r for _, r, _ in segs if r]
        self.assertIn("assistant", roles)
        digest = conversation_extractor.analyze_segments(segs, "test")
        self.assertTrue(any("PostgreSQL" in d for d in digest.decisions))

    def test_parse_transcript_openai_choices_envelope(self) -> None:
        payload = json.dumps(
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "Learning: always validate input at the boundary.",
                        }
                    }
                ]
            }
        )
        segs = conversation_extractor.parse_transcript(payload, json_file=False)
        digest = conversation_extractor.analyze_segments(segs, "test")
        self.assertTrue(any("validate" in L.lower() for L in digest.learnings))

    def test_stdin_json_without_temp_json_suffix(self) -> None:
        """JSON on stdin must be parsed as JSON (regression: .txt temp broke this)."""
        payload = json.dumps(
            {
                "messages": [
                    {"role": "assistant", "content": "invoke tool grep for search."},
                ]
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            mem = Path(tmp)
            out_md, out_js = conversation_extractor.run_extraction_from_payload(
                payload,
                mem,
                conversation_extractor.repo_root(),
                source_label="<stdin>",
                stem="stdin",
            )
            self.assertTrue(out_md.exists())
            data = json.loads(out_js.read_text(encoding="utf-8"))
            tools = dict(data.get("tools_from_text_heuristic") or {})
            self.assertIn("grep", tools)

    def test_parse_text_role_prefixes(self) -> None:
        raw = "turn 1\nuser: hello\nassistant: Decision: ship the MVP first.\n"
        segs = conversation_extractor.parse_transcript(raw, json_file=False)
        self.assertTrue(any("MVP" in t for _, r, t in segs if r == "assistant"))

    def test_invalid_json_file_falls_back_to_text_lines(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            f.write("not json at all\nturn 2\nassistant: ok\n")
            path = Path(f.name)
        try:
            segs = conversation_extractor.parse_session_log(path)
            self.assertTrue(any(t == "ok" for _, r, t in segs if r == "assistant"))
        finally:
            path.unlink(missing_ok=True)

    def test_tool_calls_array_in_message(self) -> None:
        payload = json.dumps(
            {
                "messages": [
                    {
                        "role": "assistant",
                        "tool_calls": [{"function": {"name": "read_file", "arguments": "{}"}}],
                    },
                ]
            }
        )
        segs = conversation_extractor.parse_transcript(payload, json_file=False)
        tool_names = [t for _, r, t in segs if r == "tool"]
        self.assertIn("read_file", tool_names)
        digest = conversation_extractor.analyze_segments(segs, "t")
        self.assertGreater(digest.tool_structured.get("read_file", 0), 0)


if __name__ == "__main__":
    unittest.main()
