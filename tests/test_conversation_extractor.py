import json
import tempfile
import unittest
from pathlib import Path

from src.self_improvement.conversation_extractor import ConversationExtractor


SAMPLE_TRANSCRIPT = """
<timestamp>Wednesday, May 6, 2026, 3:08 PM (UTC)</timestamp>
<user_query>
Implement or improve the conversation extractor in src/self_improvement/conversation_extractor.py.
</user_query>
assistant: I will inspect the existing extractor and then implement updates.
{"recipient_name":"functions.ReadFile","parameters":{"path":"/workspace/src/self_improvement/conversation_extractor.py"}}
assistant: I decided to parse role prefixes and XML blocks for robust extraction.
assistant: Extraction completed successfully and tests passed.
""".strip()


class ConversationExtractorTests(unittest.TestCase):
    def test_extracts_messages_requests_tool_calls_and_metadata(self):
        extractor = ConversationExtractor()
        summary = extractor.extract_from_text(SAMPLE_TRANSCRIPT, source="sample.txt").as_dict()

        self.assertEqual(summary["source"], "sample.txt")
        self.assertTrue(summary["conversation_id"])
        self.assertEqual(summary["metadata"]["conversation_type"], "coding")
        self.assertEqual(summary["metadata"]["tool_call_count"], 1)
        self.assertEqual(summary["metadata"]["primary_user_request"].startswith("Implement or improve"), True)
        self.assertEqual(len(summary["user_requests"]), 1)
        self.assertGreaterEqual(len(summary["agent_responses"]), 2)
        self.assertEqual(summary["tool_calls"][0]["tool_name"], "ReadFile")
        self.assertIn("decided", " ".join(summary["key_decisions"]).lower())
        self.assertIn("completed", " ".join(summary["outcomes"]).lower())
        self.assertIn("Type: coding", summary["summary"])

    def test_extract_from_file_and_json_serializable(self):
        extractor = ConversationExtractor()
        with tempfile.TemporaryDirectory() as temp_dir:
            transcript_path = Path(temp_dir) / "session.txt"
            transcript_path.write_text(SAMPLE_TRANSCRIPT, encoding="utf-8")
            result = extractor.extract_from_file(transcript_path).as_dict()
            payload = json.dumps(result)
            self.assertTrue(payload)
            self.assertEqual(result["source"], str(transcript_path))


if __name__ == "__main__":
    unittest.main()
