from __future__ import annotations

import unittest

from scripts import conversation_extractor


class ConversationExtractorTests(unittest.TestCase):
    def test_extract_conversation_insights_captures_signals(self) -> None:
        messages = [
            {"role": "user", "content": "We should refactor the API helper and add tests next step."},
            {"role": "assistant", "content": "Agreed. Decision: go with incremental refactor and ship in two PRs."},
            {"role": "user", "content": "I learned the failing path comes from missing retry logic."},
            {"role": "assistant", "content": "Great, that insight means we can implement retries and document behavior."},
        ]

        result = conversation_extractor.extract_conversation_insights(messages)

        self.assertGreater(result["score"]["overall"], 0)
        self.assertIn("engineering", result["tags"]["topics"])
        self.assertIn("testing", result["tags"]["topics"])
        self.assertEqual(result["tags"]["mood"], "positive")
        self.assertTrue(any("Decision:" in item for item in result["decisions"]))
        self.assertTrue(any("learned" in item.lower() for item in result["learnings"]))

    def test_format_list_output_numbers_items(self) -> None:
        rendered = conversation_extractor.format_list_output(
            ["Decide on parser update", "Document fallback behavior"], "Decisions"
        )
        self.assertIn("Decisions:", rendered)
        self.assertIn("1. Decide on parser update", rendered)
        self.assertIn("2. Document fallback behavior", rendered)

    def test_empty_list_output_has_placeholder(self) -> None:
        rendered = conversation_extractor.format_list_output([], "Learnings")
        self.assertEqual(rendered, "Learnings:\n- (none)")


if __name__ == "__main__":
    unittest.main()
