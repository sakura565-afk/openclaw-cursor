import json
import unittest

from src.self_improvement.conversation_extractor import (
    ConversationExtractor,
    analyze_messages,
    format_markdown_report,
    parse_transcript,
)


class ConversationExtractorTests(unittest.TestCase):
    def test_json_array_parse_and_success_signal(self):
        payload = json.dumps(
            [
                {"role": "user", "content": "Fix the flaky test in foo.py."},
                {
                    "role": "assistant",
                    "content": "I updated the mock and all tests pass now. The issue is resolved.",
                },
            ]
        )
        fmt, messages, warnings = parse_transcript(payload)
        self.assertEqual(fmt, "json_array")
        self.assertEqual(len(messages), 2)
        self.assertFalse(any("Skipping" in w for w in warnings))
        report = analyze_messages(messages)
        report.format_detected = fmt
        self.assertGreaterEqual(len(report.successful_approaches), 1)
        md = format_markdown_report(report)
        self.assertIn("Conversation extraction report", md)
        self.assertIn("Successful approaches", md)

    def test_markdown_role_blocks(self):
        raw = """User:

Need help with regex.

Assistant:

Here is a step-by-step plan:
1. Anchor the pattern
2. Escape special characters
Finally, run the tests — all tests pass.
"""
        fmt, messages, _ = parse_transcript(raw)
        self.assertEqual(fmt, "markdown_roles")
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0].role, "user")

    def test_jsonl_roundtrip(self):
        lines = [
            json.dumps({"role": "user", "content": "hello"}),
            json.dumps({"role": "assistant", "content": "Hi there!"}),
        ]
        raw = "\n".join(lines)
        fmt, messages, _ = parse_transcript(raw)
        self.assertEqual(fmt, "jsonl")
        self.assertEqual(len(messages), 2)

    def test_plain_fallback_single_block(self):
        raw = "Just some unstructured notes without roles."
        fmt, messages, warnings = parse_transcript(raw)
        self.assertEqual(fmt, "plain_single")
        self.assertEqual(len(messages), 1)
        self.assertTrue(any("fallback" in w.lower() for w in warnings))

    def test_extractor_json_output_shape(self):
        ext = ConversationExtractor()
        report = ext.extract(
            json.dumps(
                [
                    {"role": "user", "content": "Traceback in module x"},
                    {"role": "assistant", "content": "Sorry — that was an AttributeError. Try getattr."},
                ]
            )
        )
        d = report.as_dict()
        self.assertIn("error_patterns", d)
        self.assertIn("statistics", d)

    def test_pair_skips_system_between_user_and_assistant(self):
        raw = json.dumps(
            [
                {"role": "user", "content": "Question"},
                {"role": "system", "content": "Reminder"},
                {"role": "assistant", "content": "Answer with detail " + ("word " * 200)},
            ]
        )
        fmt, messages, _ = parse_transcript(raw)
        self.assertEqual(fmt, "json_array")
        report = analyze_messages(messages)
        stats = report.statistics
        self.assertEqual(stats["user_assistant_pairs"], 1)


if __name__ == "__main__":
    unittest.main()
