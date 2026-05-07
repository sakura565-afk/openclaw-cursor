import json
import tempfile
import unittest
from pathlib import Path

from src.self_improvement.conversation_extractor import ConversationExtractor


class ConversationExtractorTests(unittest.TestCase):
    def test_extracts_json_turns_and_insights(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            logs = root / "logs"
            logs.mkdir(parents=True, exist_ok=True)
            (logs / "session.json").write_text(
                json.dumps(
                    {
                        "messages": [
                            {"role": "user", "content": "We should choose to use caching. TODO: verify."},
                            {
                                "role": "assistant",
                                "content": "Decision made. I will call functions.ReadFile then functions.Shell.",
                            },
                            {"role": "assistant", "content": "Command failed with timeout error."},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            output_dir = root / "memory" / "extracted_conversations"
            extractor = ConversationExtractor(input_dirs=[logs], output_dir=output_dir)

            written = extractor.extract_all()

            self.assertEqual(len(written), 1)
            payload = json.loads(written[0].read_text(encoding="utf-8"))
            self.assertEqual(payload["summary"]["total_turns"], 3)
            self.assertIn("ReadFile", payload["tools_used"])
            self.assertIn("Shell", payload["tools_used"])
            self.assertGreaterEqual(payload["summary"]["decisions_count"], 1)
            self.assertGreaterEqual(payload["summary"]["errors_count"], 1)
            self.assertGreaterEqual(payload["summary"]["follow_ups_count"], 1)

    def test_extracts_plaintext_role_markers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            logs = root / "logs"
            logs.mkdir(parents=True, exist_ok=True)
            (logs / "chat.log").write_text(
                "\n".join(
                    [
                        "[2026-05-07T12:00:00Z] user: We decided to ship v1.",
                        "assistant: Next step is follow-up with QA.",
                        "tool: ApplyPatch wrote file.",
                        "assistant: Build failed with non-zero exit code.",
                    ]
                ),
                encoding="utf-8",
            )
            output_dir = root / "memory" / "extracted_conversations"
            extractor = ConversationExtractor(input_dirs=[logs], output_dir=output_dir)

            written = extractor.extract_all()
            payload = json.loads(written[0].read_text(encoding="utf-8"))

            self.assertEqual(payload["summary"]["total_turns"], 4)
            self.assertIn("ApplyPatch", payload["tools_used"])
            self.assertEqual(payload["turns"][0]["timestamp"], "2026-05-07T12:00:00Z")

    def test_extracts_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            logs = root / "logs"
            logs.mkdir(parents=True, exist_ok=True)
            (logs / "session.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps({"role": "user", "content": "Please do this."}),
                        json.dumps({"role": "assistant", "content": "Using tool", "tool_name": "Shell"}),
                    ]
                ),
                encoding="utf-8",
            )
            output_dir = root / "memory" / "extracted_conversations"
            extractor = ConversationExtractor(input_dirs=[logs], output_dir=output_dir)

            written = extractor.extract_all()
            payload = json.loads(written[0].read_text(encoding="utf-8"))

            self.assertEqual(payload["summary"]["total_turns"], 2)
            self.assertIn("Shell", payload["tools_used"])


if __name__ == "__main__":
    unittest.main()
